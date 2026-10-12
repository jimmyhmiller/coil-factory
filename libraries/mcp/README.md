# mcp

A Model Context Protocol server that offers the tools of an `agent.tool`
`ToolRegistry` over stdio (newline-delimited JSON-RPC 2.0), and a client for a
server run as a program. The server gives an agent program you do not control,
such as OpenCode, tools implemented in Coil; the client gives a Coil agent the
tools of any MCP server.

```coil
(import "mcp.server" :as mcp)

(mcp/serve-stdio a (mut registry) (mcp/ServerInfo :name "factory" :version "0.1.0")
                 (box! a mcp/NoHooks (mcp/NoHooks :unused 0)))
```

- Methods: `initialize`, `ping`, `tools/list`, `tools/call`. Notifications and
  responses from the client are never answered; batches are.
- Protocol versions `2025-11-25`, `2025-06-18`, `2025-03-26`, `2024-11-05`: a
  client asking for one gets it, and any other request is answered with the newest.
- Arguments are validated against the tool's schema before it runs, as
  `agent.loop` does, except for a `self-checked` tool, which checks its own. Invalid arguments and a `ToolFailed` are results with
  `isError: true`; an unknown tool is a JSON-RPC error (-32602).
- `ServerHooks/on-initialize` is called when a client initializes, so an owner
  can tell that the server actually came up.
- `handle-message` answers one line and is how the tests drive the server.

## Client

```coil
(import "mcp.client" :as mcp)

(match (mcp/mcp-connect a ["/bin/sh" "-c" "my-server --stdio"] cwd "my-server" 60000 context)
  (Ok [client] … (mcp/mcp-list-tools! client a 60000 context)
               … (mcp/mcp-call-tool! client a "search" arguments 600000 context)
               (mcp/mcp-close! client))
  (Err [message] …))
```

- The server runs in its own process group; `mcp-close!` closes its input,
  waits briefly, and stops the whole group.
- While a request waits, the client keeps writing what it has queued and
  reading standard output and standard error, so a server that logs while it
  reads cannot deadlock it. Requests from the server are answered (`ping`) or
  refused (-32601); notifications are ignored.
- Failures say why: the server's own error, that it exited with the last 4 KB
  of its standard error, a timeout, or a cancellation through the
  `ToolContext` (passed on as `notifications/cancelled`).
- `mcp-list-tools!` follows `nextCursor`; a tool's result is its text content,
  with a note for content that is not text.
- Depends on `agent-tools` for its SIGPIPE-safe pipe write and process-group
  helpers.

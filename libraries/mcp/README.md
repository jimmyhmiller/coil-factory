# mcp

A Model Context Protocol server that offers the tools of an `agent.tool`
`ToolRegistry` over stdio (newline-delimited JSON-RPC 2.0). It gives an agent
program you do not control, such as OpenCode, tools implemented in Coil.

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
  `agent.loop` does. Invalid arguments and a `ToolFailed` are results with
  `isError: true`; an unknown tool is a JSON-RPC error (-32602).
- `ServerHooks/on-initialize` is called when a client initializes, so an owner
  can tell that the server actually came up.
- `handle-message` answers one line and is how the tests drive the server.

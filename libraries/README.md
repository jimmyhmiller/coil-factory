# Reusable Coil libraries

These packages have no dependency on the factory app; each has its own
`Coil.toml`, tests, and README. The [factory app](../) uses all of them, and
[coil-agent-harness](../../coil-agent-harness/) uses `sse` and `http-app`.

| Package | Import | Scope |
| --- | --- | --- |
| `sse` | `sse.core` | Bounded Server-Sent Events decoding and encoding |
| `json-schema` | `json_schema.validate` | A documented, fail-closed JSON Schema subset over `coil.json` documents |
| `http-app` | `http_app.router`, `.server`, `.sse`, `.json` | Routing, a threaded server with streaming responses and SSE, JSON errors |
| `ai` | `ai.chat`, `ai.openai` | Provider-neutral chat interface with an OpenAI-compatible HTTP adapter |
| `agent` | `agent.tool`, `agent.loop`, `agent.json` | Tool-calling agent loop: steering messages, pause/cancel, retries, schema-validated arguments, context compaction, checkpoints |
| `agent-tools` | `agent_tools.files`, `.shell`, `.sandbox`, `.workspace`, `.bundle` | Workspace-confined file tools and a shell tool, optionally under `sandbox-exec` or `bwrap` |
| `durable` | `durable.jsonl`, `.atomic`, `.lock`, `.ids` | Multi-writer JSONL journal with byte cursors; atomic writes; locks and pid files; ids |
| `mddoc` | `mddoc.doc` | Markdown front matter, heading sections, and settings blocks |
| `cli` | `cli.args`, `cli.style`, `cli.prompt` | Subcommands and flags with suggestions, terminal styling and tables, prompts |
| `tui` | `tui.term`, `.keys`, `.canvas`, `.widgets`, `.width`, `.app` | Full-screen terminal UI toolkit |
| `ssh` | `ssh.target`, `ssh.exec`, `ssh.tunnel` | Remote commands and supervised local port forwards |

For an application next to this checkout:

```toml
[dependencies]
sse = { path = "../coil-factory/libraries/sse" }
json_schema = { path = "../coil-factory/libraries/json-schema" }
http_app = { path = "../coil-factory/libraries/http-app" }
ai = { path = "../coil-factory/libraries/ai" }
```

Coil also supports Git dependencies with `subdir` for each package. Pin them to
a commit for a reproducible build.

These libraries deliberately do not duplicate `coil.http.client`,
`coil.http.server`, `coil.json`, `coil.process`, or `coil.time`.

## SSE

`sse.core` accepts arbitrary byte chunks with `sse-decoder-feed!`, calls a
`SseEventSink` synchronously, and returns `false` on a bound violation or when
the consumer stops. It handles LF, CRLF, and CR line endings, multiline `data`,
event names, persistent IDs, empty ID resets, and retry hints. A decoder owns
its pending buffer and copied last ID; call `sse-decoder-free!` when done.
`sse-encode` returns allocator-owned bytes for one event. `id-present` controls
whether an empty ID reset is emitted. `retry-ms = -1` omits the field.

## JSON Schema

`json_schema.validate/validate` accepts two parsed `coil.json` documents and
returns `Validation { ok, path, message }`. Both source byte slices must stay
alive during validation. Supported keywords are `type` (one named type),
`properties`, `required`, boolean `additionalProperties`, one schema in
`items`, `enum`, `anyOf`, `title`, and `description`. Nesting is limited to 64.
Unknown keywords and malformed schema shapes are errors even in branches not
visited by the value. This is a subset, not a claim of full JSON Schema
conformance.
Numeric `enum` values currently compare by JSON spelling, and `integer`
currently accepts only integer spellings; exact JSON Schema numeric semantics
remain open work.

## HTTP application layer

`http_app.router` accepts requests parsed by `coil.http.server`. Register a
method and path pattern with `route-add!`, then call `dispatch`. `{name}` captures
one nonempty path segment, available through `param`; `path` and `query` are raw
views of the request target. A handler returns a `Response` containing status,
content type, and a buffered body. `encode-response` renders an HTTP/1.1 response
with `Content-Length` and `Connection: close`. The caller owns the listener,
authentication, and socket lifecycle. Pattern and request slices are borrowed;
keep the route strings and concrete handlers alive as long as the router.

## AI chat

`ai.chat` defines `ChatProvider`, `StreamingChatProvider`, request/result types,
and an optional delta callback. `ai.openai` provides an OpenAI-compatible Chat
Completions adapter over `coil.http.client` with an injectable HTTP transport.
`messages` and `tools` are `coil.serde.value/JVal`, allowing complete tool call
and result messages to be carried without forcing them into text. `tool-calls`
and streamed tool-call fragments are returned to the caller; the library does
not execute tools or manage an agent loop. Response values and stream deltas
borrow the caller's allocator; use an arena for a request and release it after
consuming the result. The adapter sends the supplied API key in a Bearer header
and uses the supplied Chat Completions URL.

## Development gates

In each package, run `coil fmt --check`, `coil lint`, and `coil test`.
`coil verify` currently assumes `src/main.coil` even for a library package with
no entry; this is recorded in
the `coil-bugs` pad.

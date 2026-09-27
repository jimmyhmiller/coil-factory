# agent

A provider-neutral tool-calling agent loop over `ai.chat`.

- `agent.tool`: the `Tool` trait (`tool-spec`, `tool-summary`, `tool-run`),
  `ToolCall`, `ToolOutcome` (`ToolOk` / `ToolFailed`), `ToolContext`
  (`tool-cancelled?`), and `ToolRegistry` (unique names). Argument helpers
  `call-string`, `call-int`, `call-bool`, `call-has?` read the parsed arguments;
  arguments are already validated against the tool's schema when `tool-run` is
  called, so required fields are present with the declared types.
- `agent.loop`: `Agent`, created with `agent-new` from a provider, an
  `AgentConfig` (`agent-config model stop-tool`), a registry, an `AgentObserver`,
  and an `AgentControl`. `agent-run!` runs turns until one of:
  - `Finished call result`: the model called the stop tool;
  - `Replied text`: the model answered without calling a tool;
  - `Cancelled`: the control said so (checked between turns and while tools run);
  - `Failed error`: a non-transient model failure, or retries ran out.

  The caller decides what each means and may add messages and call `agent-run!`
  again. Messages from outside (`control-inbox`) are delivered at the start of each
  turn as `[from] text`; `Pause` blocks between turns. Every step is reported to
  the observer (`TurnStarted`, `MessageDelivered`, `Thought`, `Said`,
  `UsageReported`, `ToolStarted`, `ToolEnded`, `ModelRetrying`, `PausedEvent`,
  `ResumedEvent`, `Compacted`).
- Transient failures (transport, 408/409/425/429/5xx, and a request body the
  provider saw cut off) are retried with exponential backoff, cancellable.
- Tool arguments are validated with `json-schema` before execution; a bad call is
  reported to the model, not executed. Tool schemas must use that library's subset.
- Provider message fields such as `reasoning_content` are kept, so thinking models
  continue tool-call chains correctly.
- Each turn's request, response, and tool work live in a scratch arena; only what
  the conversation keeps is copied out.
- `context-budget-bytes` / `keep-recent`: when the conversation's text passes the
  budget, the oldest tool output and reasoning are elided (the system prompt, user
  messages, and the newest messages never are).
- `agent-transcript-json` / `agent-restore!` checkpoint and resume a conversation.

```coil
(let [(mut agent) (agent-new a provider (agent-config "deepseek-v4-flash" "finish")
                             (mut registry) observer control)]
  (agent-add-system! (mut agent) "You are …")
  (agent-add-user! (mut agent) "Begin.")
  (match (agent-run! (mut agent))
    (Finished [call result] (call-string call "summary"))
    (Replied [text] text)
    (Cancelled [] "cancelled")
    (Failed [e] "failed")))
```

`examples/live` runs the loop against DeepSeek with a small arithmetic tool.

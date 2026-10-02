# opencode

[OpenCode](https://opencode.ai) driven headlessly as an `agent.harness`
`Harness`. Each `harness-run!` is one `opencode run --format json --auto`
process; later runs continue the same session with `-s`. Verified against
OpenCode 1.18.

```coil
(opencode-new a (OpenCodeOptions :program "/Users/me/.opencode/bin/opencode"
                                 :launcher no-launcher   ; an empty slice, or a sandbox argv
                                 :workdir workspace
                                 :state-dir …            ; sessions (XDG_DATA/STATE_HOME)
                                 :config-home …          ; XDG_CONFIG_HOME
                                 :cache-dir …            ; XDG_CACHE_HOME
                                 :config-path …          ; the generated opencode.json
                                 :model "opencode/big-pickle"
                                 :agent-name "factory-build"
                                 :permission permission  ; OpenCode `permission` object
                                 :mcp mcp                ; OpenCode `mcp` object
                                 :plugins plugins        ; OpenCode `plugin` array
                                 :stop-tool "factory_finish"
                                 :stop-tool-as "finish"
                                 :ready-file …
                                 :idle-timeout-ms 1800000)
              observer control)
```

- The system prompt becomes the `prompt` of a generated OpenCode agent, which
  replaces OpenCode's own system prompt.
- The event stream (`step_start`, `text`, `reasoning`, `tool_use`,
  `step_finish`, `error`) becomes `AgentEvent`s: turns, text, thoughts, tool calls
  with one-line summaries and elapsed time, and token usage (input includes cache
  reads and writes; output includes reasoning).
- The stop tool is an MCP tool the caller serves; OpenCode calls it
  `<server>_<tool>`. When the agent calls it, the process is stopped at the end of
  that step and the run returns `Finished` with the tool renamed `stop-tool-as`.
  If `ready-file` is set, the stop tool's server must create it on initialize; a
  run that ends without it is `Failed`, since OpenCode silently drops a server
  that does not start.
- Messages from the control and pauses reach the agent at step boundaries: the
  process is stopped once its step ends and the next run delivers them.
- A run that ends without the stop tool is `Replied` with the last text. An
  `error` event, a nonzero exit, no session, or no events for `idle-timeout-ms`
  is `Failed` with kind `harness`.
- The checkpoint is `{"harness": "opencode", "session", "system", "pending",
  "turns", "messages"}`; `messages` mirrors the conversation as chat messages.

OpenCode behaviour this depends on:

- The prompt goes on stdin: OpenCode wraps argv words that contain spaces in
  literal quotes.
- The working directory is taken from `$PWD` as well as `--dir`, and continuing a
  session from another directory hangs, so both are set on every run. `--agent`
  and `-m` must also be repeated on every run.
- `OPENCODE_DISABLE_CLAUDE_CODE` and `OPENCODE_DISABLE_EXTERNAL_SKILLS` are set;
  without them OpenCode puts `~/.claude/CLAUDE.md` and your skills in the prompt.
- OpenCode dies on SIGTERM without stopping its shell commands, which run in their
  own process groups; `agent_tools.stream` stops those too.
- OpenCode Zen's free models reject requests that do not offer the `bash` and
  `read` tools, and a denied permission removes the tool from the request. To
  forbid them on those models, refuse the calls in a plugin
  (`tool.execute.before`) instead of denying them.
- OpenCode writes to `~/.opencode` (its plugin package) on every run; only a
  different `HOME` avoids that.

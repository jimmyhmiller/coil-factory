# Writing a standalone agent

A standalone agent is one Markdown file. It gets one input (text) and works in
one conversation with a model. Its output is the calls it makes to the tools
its file defines. There is no branch and no review step: you run it and get
JSON back.

## The workflow

```sh
factory agent new NAME          # writes .factory/agents/NAME.md in this git repository
                                # (--site: ~/.factory/agents/, for every project)
$EDITOR .factory/agents/NAME.md
factory agent check NAME        # every mistake, with its line number; fix and repeat
factory agent run NAME "the input" -v     # -v shows each tool call on stderr
factory agent run NAME --input-file F     # or --input TEXT, or pipe it on stdin
factory agents                  # every agent a name reaches from here
```

Run `factory agent check` until it passes before you run the agent. It refuses
anything the runner could not use.

## Where agent files live

A name is looked up in this order. The first one found wins:

1. a path (anything with a `/` or ending in `.md`);
2. `<project>/.factory/agents/NAME.md`. The project is the nearest directory
   with `.factory/agents/`, searching up from the current directory and
   stopping at the root of the git repository. `--project PATH` searches from
   elsewhere;
3. `~/.factory/agents/NAME.md`, the site's agents, for every project;
4. bundled agents (`grammar`).

## A complete example

````markdown
---
name: grammar
description: Find grammar, spelling, and punctuation problems in a text.
input: The text to check.
tools: none
gate: grep -q '"report_issue"' || { echo 'you reported nothing; the text has problems'; exit 1; }
attempts: 2
---

You are a careful copy editor. Report each problem in the input with
`report_issue`, one call per problem, in order. Look a word up with
`dictionary` when you are not sure of its spelling. When you have reported
every problem, call `done` with a one-sentence summary.

## Tool: report_issue
verbatim: original

Report one grammar, spelling, or punctuation problem in the input.

```json
{
  "type": "object",
  "properties": {
    "original": {"type": "string", "description": "The exact words from the input."},
    "suggestion": {"type": "string", "description": "What should replace them."},
    "kind": {"type": "string", "enum": ["grammar", "spelling", "punctuation"]}
  },
  "required": ["original", "suggestion", "kind"],
  "additionalProperties": false
}
```

## Tool: dictionary
run: ./tools/lookup.sh

Look up whether a word is spelled correctly. Prints the word's entry, or
"not found".

```json
{"type": "object", "properties": {"word": {"type": "string"}}, "required": ["word"]}
```

## Tool: style
mcp: npx -y some-style-mcp-server
only: check_style
````

## Front matter

| key | value | default |
| --- | --- | --- |
| `name` | required; lowercase letters, digits, and dashes | |
| `description` | one line, shown by `factory agents` | |
| `input` | what the input is; the model is told this | |
| `model` | `provider/model` (`deepseek/…`, `openrouter/…`, `openai/…`, `vercel/…`, `zen/…`, `chatgpt/…`); for opencode or codex, the model as that program names it | the site's `default_model` (`opencode_model`, `codex_model`) |
| `harness` | `builtin`, `opencode`, or `codex` | the site's `default_harness`, else `builtin` |
| `tools` | `none`, or standard groups: `files` (read and edit files), `read` (read only), `shell` | `none` |
| `gate` | a command that checks the results when the agent calls `done` (see below) | none |
| `attempts` | how many times a failing gate sends the agent back, 1 to 20 | 3 |

Any other key is an error. `--model` and `--harness` on `agent run` override
`model` and `harness`.

The site's settings are in `~/.factory/site.json` (the site is this machine's
factory setup; `FACTORY_HOME` moves it): `default_model`, `opencode_model`,
`codex_model`, `default_harness`, and `isolation` (`none`, or `sandbox` to
confine commands). `factory agent check NAME` shows what the agent would run on
here. With no model anywhere, a run is refused and says where to set one.

## Instructions

Everything after the front matter that is not a tool section is the agent's
instructions, including `##` sections that come after the tools. Say which tool
to call for what, and when to call `done`. The runner adds to them:

- how the agent's output is read;
- the `verbatim:` rules;
- that the input arrives in the next message between `<input>` and
  `</input>`.

## Tool sections

Each tool is a level-2 heading `## Tool: NAME`. The first lines under it can be
settings, as `key: value` lines; the settings end at the first blank line after
them. The settings decide its kind:

| kind | settings | what a call does |
| --- | --- | --- |
| collect | none (the default); optional `verbatim:` | records the call as output |
| run | `run: COMMAND`; optional `verbatim:` | runs the command; its output is the result |
| mcp | `mcp: COMMAND`; optional `only:` | offers the tools of an MCP server |

**Names.** A collect or run tool's name is lowercase letters, digits, and
underscores, starts with a letter, and has at most 64 characters. An mcp
section's name names the server: lowercase letters, digits, and dashes. Three
names are taken: `done`, `finish`, and `retract`.

**Collect and run tools** need:

- a description: the prose under the heading, which is what the model reads;
- exactly one fenced `json` block: the JSON Schema of the arguments. It must
  be `"type": "object"` and use only `type`, `properties`, `required`,
  `additionalProperties` (true or false), `items`, `enum`, `anyOf`,
  `description`, and `title`.

Arguments that don't fit the schema are refused, and the model is told why.
Other fenced blocks stay in the description.

**`verbatim: a, b`** names string properties that must appear word for word in
the input. A call that quotes text not in the input is refused, and the model
is told so. Use it for any field that quotes the input.

**Collect tools** are the agent's output. Each call that passes its checks is
recorded with a number (`Recorded as #3`). When an agent has any collect tool,
it also has `retract` (`{"number", "reason"}`), which takes a report back.

**Run tools** run `COMMAND` through `/bin/sh -c`:

- Its standard input is the arguments as JSON, exactly as the model wrote
  them.
- Exit 0: its standard output (up to 64 KB) is the result.
- Any other exit, or a timeout of 5 minutes: the result is an error showing
  the status, its output, and its standard error.

**MCP servers.** `mcp: COMMAND` starts a server that speaks MCP over stdio
when the agent starts, and stops it when the agent ends.

- All of the server's tools are offered under the server's own names. `only:
  a, b` offers just those, and names the server doesn't offer are an error.
  A tool whose name models cannot call (anything but letters, digits, `_` and
  `-`) is an error unless `only:` leaves it out.
- The server checks its own arguments, so no schema or `verbatim:` goes here.

**Where commands run.** This applies to run tools, mcp servers, and the gate.

- They run in the agent's home: the project root for a project's agent; the
  file's directory for a site agent or a path; for a bundled agent, the work
  directory. So `./tools/lookup.sh` in a project agent means
  `<project>/tools/lookup.sh`.
- `$FACTORY_WORKDIR` is the project the agent was run from, and
  `$FACTORY_AGENT_HOME` is the home.
- Under the site's `isolation: sandbox`, a command can write only inside the
  work directory.

## Ending, and the gate

The agent ends by calling `done` with `{"summary": "…"}`. If it replies without
calling a tool, it is reminded once that only tool calls are kept; a second
reply like that is a failure.

When the agent calls `done`, the `gate:` command, if there is one, gets this on
standard input:

```json
{"agent": "grammar", "summary": "…", "reports": [{"number": 1, "tool": "report_issue", "arguments": {}}]}
```

- Exit 0 accepts the results.
- Any other exit sends what the gate printed back to the agent, which goes on
  working and calls `done` again.
- After `attempts` failures, the run fails.
- The gate has 30 minutes.

## What a run returns

`factory agent run` prints one JSON object on standard output:

```json
{"agent": "grammar", "ok": true, "summary": "…",
 "reports": [{"number": 1, "tool": "report_issue", "arguments": {}}],
 "model": "deepseek/deepseek-v4-flash", "usage": {"input_tokens": 0, "output_tokens": 0}}
```

- `reports` holds the collect-tool calls, in order, without retracted ones.
- When `ok` is false, a `failure` says why, the exit code is 1, and `reports`
  holds what the agent had reported by then.
- A failure is never an empty `reports` that looks like success.
- `usage` counts every model request the harness could count. When it could
  not count them all, `usage` also has `"incomplete": "why"` and the numbers
  are too low. This happens on Codex, which reports a turn's usage only when
  the turn ends: a run stopped before then is read from Codex's session log,
  and a request still under way when it was stopped is in no log.

From Coil, `factory.agentkit`'s `run-agent!` does the same as a library call
(`parse-agent` the text, then `run-agent!` with an `AgentCall`).

## Harnesses

- **`builtin`** calls the model's API directly. Use it unless you need a model
  that only OpenCode can reach.
- **A ChatGPT subscription** is a builtin model too: `chatgpt/gpt-6-luna` and
  the other models of your plan, with the login `codex login` made. The agent
  gets only its own prompt and tools. That is much cheaper than running it
  under the codex harness, and the model reads the instructions it was given
  and no others.
- **`opencode`** and **`codex`** run the agent in that program, and the
  agent's tools reach it over MCP. OpenCode puts `factory_` in front of the
  tool names, and the instructions the runner adds say so.
- Codex cannot take away its own shell. With `tools: none`, a Codex agent
  still has a read-only shell.
- Codex's own extras are off for every agent: the ChatGPT apps of the account
  it is logged in to, plugins, skills, web search, browser and computer use,
  image generation. Codex still reads the global `AGENTS.md` in its home
  (`~/.codex`), and for some models it adds its own multi-agent instructions;
  neither can be turned off from the command line.

# Factory — design

A software factory is a Markdown recipe of *stations*. Each station is one agent with
tools, working in an isolated git worktree, that is not done until it says so **and** its
gate command passes. You run a factory anywhere — this laptop, a second machine, a
sandbox on a server — and watch, steer, pause, and talk to every agent from one place.

Everything is written in Coil. Reusable pieces live in `libraries/`; this app composes
them.

## The shape of it

```
                ┌──────────────────────── your laptop ─────────────────────────┐
  factory CLI ─▶│  hub  (factory up)                                            │
  dashboard   ─▶│   ├─ local site ── run processes ── agents ── worktrees       │
  Claude Code ─▶│   ├─ overseer (watches every run on this site)                │
  (skills)      │   └─ ssh tunnel ─────────────┐                                │
                └──────────────────────────────┼────────────────────────────────┘
                                               ▼
                ┌──────── metaphysics (ssh site, sandboxed) ────────┐
                │  site daemon ── run processes (bwrap) ── overseer  │
                └───────────────────────────────────────────────────┘
```

* **Site** — a machine + state directory running `factory daemon`. Every site is
  autonomous: its runs, journals, and overseer keep going when your laptop sleeps.
* **Hub** — the site on your laptop is also the hub. The CLI, the dashboard, and the
  Coil client only ever talk to the hub; the hub keeps SSH tunnels to remote sites and
  proxies to them. One endpoint, every machine.
* **Run** — one execution of a factory against a project with a brief. Its own
  directory, its own branch, its own process.
* **Agent** — one conversation (one per station). Always addressable as
  `<run>/<station>`, always talk-to-able, even after the run is finished.

## The filesystem is the database

```
~/.factory/
  token                      bearer token for this site's API (0600)
  site.json                  name, capacity, isolation, providers, overseer policy
  sites.json                 (hub) remote sites: ssh target, remote token
  factories/*.md             the factory library of this site
  mirrors/<repo>.git         bare mirrors of projects pushed from elsewhere
  runs/<run-id>/
    run.json                 immutable spec: factory text, brief, project, model, site
    events.jsonl             append-only journal — the single source of truth
    agents/<station>.json    resumable transcript checkpoint (chat messages)
    workspace/               git worktree on branch factory/<run-id>
    process.log              stdout/stderr of the run process
    pid                      live run process, if any
```

`events.jsonl` has **multiple writers** — the run process, the daemon (operator
messages, control), the overseer (findings) — made safe by `O_APPEND` + `flock` per
record in `libraries/durable`. The cursor is a **byte offset**, so there is no shared
sequence counter to coordinate. Steering messages, pause/cancel, findings, tool calls,
model usage: all of it is one ordered log you can `tail -f`.

Every record: `{"t": <ms>, "kind": "...", "agent": "<station>"?, ...}`.

| kind | writer | meaning |
| --- | --- | --- |
| `run.created` `run.started` `run.finished` `run.lost` | daemon / run | lifecycle; `finished` carries `status` |
| `station.started` `station.finished` | run | `finished` carries `outcome`, `summary` |
| `gate.started` `gate.finished` | run | gate command, exit code, output tail |
| `agent.turn` | run | a model turn began (`turn` number) |
| `agent.text` | run | assistant prose |
| `agent.usage` | run | input/output tokens for one model call |
| `tool.started` `tool.finished` | run | `call`, `tool`, human `summary`, `ok`, output preview |
| `message.sent` | daemon | operator/overseer → agent (`from`, `text`) |
| `message.received` | run | the agent consumed a message (`ref` = offset of `message.sent`) |
| `control` | daemon | `action`: `pause` \| `resume` \| `cancel` |
| `finding` | overseer | `severity`, `code`, `message`, `evidence` |

The folded view (`factory.runstate`) derives status, current station, what each agent
is doing *right now*, token totals, pending messages, and open findings — the same fold
in the daemon, the dashboard, and tests.

## Run lifecycle

```
queued ─▶ running ─▶ succeeded
             │  ▲ ─▶ failed        (infrastructure error)
             │  │ ─▶ cancelled
             ▼  │
          blocked ──(a message)──▶ running
          paused  ──(resume)─────▶ running
          lost    ──(resume)─────▶ running   (the process died; resume from checkpoint)
```

A station ends when its agent calls `finish`. `finish(done)` runs the station's gate; a
failing gate is handed back to the same agent with the output, up to `attempts`. When
attempts run out, or the agent calls `finish(blocked)`, the run becomes **blocked**: it
waits for a human rather than failing. Saying something to a blocked agent resumes it.
Saying something to an agent of a *finished* run starts a conversation with it in its
workspace — ask it why it did something, or ask for one more change.

Every accepted station is committed on the run branch (`<station>: <summary>`).

## Factories

```markdown
---
name: issue-fixer
description: Fix one reported issue and prove it with tests.
input: The issue to fix, in plain language.
model: deepseek/deepseek-v4-flash
tools: files, shell
---

Shared context for every station (repository conventions, constraints, taste).

## Station: implement
gate: coil test
attempts: 3

Reproduce the issue, fix it, add a focused test…

## Station: review
tools: files

Read the diff on this branch as a skeptical reviewer…
```

Front matter keys: `name` (required), `description`, `input` (what the brief is),
`model`, `tools` (`files`, `shell`), `attempts`. Station settings are `key: value`
lines directly under the heading: `gate`, `attempts`, `model`, `tools`. Everything else
is prose for the model.

## Where runs execute

| site | reach | isolation |
| --- | --- | --- |
| `local` | hub process spawns runs | `none` or `sandbox` (macOS `sandbox-exec`: writes confined to the workspace) |
| `ssh` | hub tunnels to the remote daemon | `none` or `sandbox` (Linux `bwrap`: read-only system, writable workspace only) |

File tools are always confined to the workspace in Coil (no absolute paths, no `..`,
no symlink escapes). `sandbox` additionally wraps every shell command.

Machines are added with `factory site install` (compiles `factory` for the remote
platform here with `coil emit-obj --target`, ships the object over ssh, and links it
there with `cc -lcurl`, so the remote needs no Coil toolchain) and `factory site add`
(starts the remote daemon, fetches its token, registers it with the hub).

Projects move with git. `factory start … --project .` against a remote site pushes the
current HEAD to the site's bare mirror over ssh; the run works on a worktree of the
mirror; `factory pull <run>` fetches the run branch back. Nothing but git and ssh.

## Models

`provider/model`, resolved on the site that runs the agent:
`deepseek/…` (`DEEPSEEK_API_KEY`), `openrouter/…` (`OPENROUTER_API_KEY`), `openai/…`
(`OPENAI_API_KEY`), `vercel/…` (Vercel AI Gateway, `VERCEL_AI_GATEWAY_KEY`), `zen/…`
(OpenCode Zen, `OPENCODE_KEY`), and any OpenAI-compatible server declared in `site.json`
(`providers: [{name, base_url, key_env}]`). Keys come from the environment or
`~/.factory/secrets.env` on that site.

## The overseer

Every site runs one. Every 20 s it reads the tail of each active run and checks:

* **stalled** — no journal activity for too long while running;
* **looping** — the same tool call repeated with nothing changing between;
* **flailing** — a streak of failing tool calls, or a gate failing again and again;
* **suspicious** — deleted or skipped tests, silently-returning stubs, weakened gates
  (read from the worktree diff);
* **judgment** — periodically, a reviewer model reads the recent transcript and the
  diff stat and returns `on_track | drifting | stuck | cheating` with a reason.

Findings are journal records. Policy per site: `observe` (record only), `nudge` (also
send the advice to the agent as an `[overseer]` message), `pause` (alerts pause the run
for a human).

## API

JSON over HTTP, loopback only, bearer token. `GET /` returns a zero-context guide for
agents. Errors are `{"error": {"code": "...", "message": "..."}}`.

```
GET  /v1/overview                          every site, run, and open finding (dashboard feed)
GET  /v1/sites            POST /v1/sites    remote sites
GET  /v1/factories        POST /v1/factories/check
GET  /v1/runs             POST /v1/runs
GET  /v1/runs/{run}                         folded state
GET  /v1/runs/{run}/events?after=&limit=    journal page
GET  /v1/runs/{run}/events/stream           SSE; id = byte cursor; Last-Event-ID resumes
POST /v1/runs/{run}/pause | resume | cancel
GET  /v1/runs/{run}/agents/{agent}          transcript
POST /v1/runs/{run}/agents/{agent}/messages {text}
GET  /v1/runs/{run}/diff
```

Remote runs are the same paths on the hub; the hub knows which site owns a run.
Run arguments accept any unique prefix (`7f3a`), and agents are `7f3a/implement`.

## CLI

```
factory up | down | status        start / stop the hub (and remote tunnels)
factory                           the dashboard (overview → run → chat)
factory sites                     sites and their health
factory site add NAME --ssh HOST [--sandbox]
factory site install NAME --ssh HOST
factory site secrets NAME VAR...
factory new NAME                  interactive factory designer
factory check FACTORY             validate a factory (name or path)
factory factories                 the library: yours, then bundled
factory start FACTORY [--on SITE] [--project PATH] [--brief TEXT | --brief-file F] [--model M]
factory ps                        runs everywhere
factory show RUN                  stations, agents, findings
factory watch RUN                 live, human-readable event stream
factory transcript RUN/AGENT      an agent's whole conversation
factory diff RUN                  the run branch's changes
factory chat RUN/AGENT            talk to an agent (interactive)
factory ask RUN/AGENT "…"         one message, wait for the reply, print it (for scripts and agents)
factory say RUN/AGENT "…"         one message, don't wait
factory pause | resume | stop RUN
factory pull RUN                  fetch the run branch into this repository
factory skills install            install the Claude Code skills
```

## Libraries

| library | namespaces | what |
| --- | --- | --- |
| `durable` | `durable.jsonl`, `durable.atomic` | multi-writer JSONL journal with byte cursors; atomic file replace |
| `mddoc` | `mddoc.doc` | Markdown front matter + heading sections |
| `agent` | `agent.tool`, `agent.loop`, `agent.transcript` | provider-neutral agent loop: tools, steering, pause/cancel, checkpoints |
| `agent-tools` | `agent_tools.files`, `agent_tools.shell`, `agent_tools.sandbox` | workspace-confined file tools and a sandboxable shell |
| `cli` | `cli.args`, `cli.style`, `cli.prompt` | subcommands and flags, terminal styling, interactive prompts |
| `tui` | `tui.term`, `tui.keys`, `tui.canvas`, `tui.widgets`, `tui.width` | full-screen terminal UI toolkit |
| `ssh` | `ssh.exec`, `ssh.tunnel` | remote commands and supervised port forwards |
| `http-app` | `http_app.router`, `http_app.server` | routing, a threaded server, streaming responses |
| `ai`, `sse`, `json-schema` | (existing) | chat providers, SSE codec, schema validation |

## Long conversations

Every model turn resends the conversation. When its text passes a byte budget
(360 KB by default), the agent loop elides the oldest tool output and reasoning,
never the system prompt, your messages, or the newest dozen messages, and writes
the elision into the message itself so the model knows what it no longer sees.
The journal records `agent.compacted`. Transcript checkpoints are written before
every turn along with the journal cursor they reflect, so a run that dies resumes
from its last whole turn and a client can merge a transcript with later records
exactly.

## What was verified end to end

- Local runs of multi-station factories with gates, one commit per passed station.
- A gate that could not pass: the agent refused to fake it and blocked with a
  question; answering it (`factory say`) resumed the station, which then passed.
- `factory ask` of a finished agent, answered in its workspace.
- `pause` / `resume`; a run process killed with SIGKILL marked `lost`, then resumed
  from its checkpoint with `factory resume`.
- A Linux server added as a sandboxed site: `site install`, `site add --sandbox`,
  `site secrets`, a run started from a local repository with `--on`, watched from
  the laptop, its branch fetched with `factory pull`; a write outside the
  workspace failed there with "Read-only file system".

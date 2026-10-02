# factory

Run software factories anywhere. Watch every agent. Talk to any of them.

A factory is a Markdown file of stations. Each station is one agent with tools,
working on its own git branch, and it is not done until it calls `finish` and its
gate command passes. You start runs on this machine, another machine, or a
sandbox on a server, and you see and steer all of them from one place. Written
entirely in Coil; the design is in [DESIGN.md](DESIGN.md).

## Build

```sh
coil build                         # writes build/release/factory; put it on your PATH
```

Models are named `provider/model` and read their keys from the environment (or
`~/.factory/secrets.env`): `deepseek/` (`DEEPSEEK_API_KEY`), `openrouter/`,
`openai/`, `vercel/` (Vercel AI Gateway, `VERCEL_AI_GATEWAY_KEY`), and `zen/`
(OpenCode Zen, `OPENCODE_KEY`). Free models work like any other, e.g.
`--model vercel/poolside/laguna-s-2.1-free`.

There is no built-in model. Every station needs one, from `--model`, `model:` in
the factory or station, or the site's default in `~/.factory/site.json`
(`default_model`, and `opencode_model` for the opencode harness); a run that would
leave a station without one is refused when it starts, saying where to set it.

## Harnesses

A station's agent runs in a *harness*. `builtin` is the factory's own agent loop,
calling the model's API directly. `opencode` drives the [OpenCode](https://opencode.ai)
CLI headlessly, which reaches models that only answer requests from OpenCode
itself, such as OpenCode Zen's free ones (no key needed):

```sh
factory start haiku "a lighthouse in fog" --harness opencode --model opencode/big-pickle
factory start fix --project . --harness opencode --model opencode/hy3-free "…"
```

`codex` drives the Codex CLI headlessly, which reaches the models of your Codex
login:

```sh
factory start fix --project . --harness codex --model gpt-6.1-sol "…"
```

Pick one per run (`--harness`), per factory or station (`harness:`), or per site
(`default_harness` in `site.json`; otherwise `builtin`). With `opencode` and
`codex`, models are named the way that program names them. Gates, `finish`,
steering, pause, cancel, resume, and conversations after a run all work the same
in all three; see [DESIGN.md](DESIGN.md#harnesses) for how.

## A person approves

`approve: yes` makes a person the last step: a summarizer turns the station's
work, or a stopped agent's claims and questions, into separate proposals; the
factory attaches the raw material (the agent's words, the diff, the gate's and
reviewers' output) and posts it to an approvals page (`site.json` `approvals`);
the run waits. Only a person signed in with a passkey can decide there. Every
rejection's reason and every answer goes back to the agent as its next
instruction. See [DESIGN.md](DESIGN.md#approval-by-a-person).

## Reviews that send work back

A station can require reviewers' approval before it passes (`review:` on the
station or in the front matter). After the agent finishes and the gate passes,
each reviewer reads the station's diff and its workspace, and any verdict that is
not `ok` sends the work back to the same agent with every reviewer's reasons. The
station passes only when all of them approve; a reviewer that gives no verdict
blocks the station for a person rather than letting it through.

## Five minutes

```sh
factory up                                   # the hub on this machine
factory start haiku "a lighthouse in fog" -w --model deepseek/deepseek-v4-flash
                                             # a tiny two-station run, watched live
factory                                      # the dashboard: every run, every agent
```

Any model works; with no key at all, `--harness opencode --model
opencode/big-pickle` uses OpenCode's free one. Put your usual choice in
`~/.factory/site.json` (`{"default_model": "…"}`) to stop passing `--model`.

```
◆ haiku-fc0c  started on laptop
  project   empty workspace → factory/haiku-fc0c
  stations  write → critique

22:44:30  write       ls .
22:44:32  write       write poem.md
22:44:35  write       ⛩ gate test -s poem.md
22:44:35  write       ⛩ gate passed
22:44:35  write       ✓ passed  Created poem.md: a three-line haiku, 5/7/5 syllables…
22:44:35  critique    ▶ station critique
22:44:44  run         ✓ succeeded
```

Then talk to the agents; they remember everything they did:

```sh
factory ask fc0c/write "Why that image?"     # prints the reply
factory diff fc0c                            # what the run changed
```

## Real work

```sh
factory start fix --project ~/code/app "The parser crashes on an empty file"
factory start feature --project . --brief-file ISSUE.md --watch
factory ps                                   # everything, everywhere
factory show 7f3a                            # stations, agents, overseer findings
factory say 7f3a/build "Use the existing Json helpers"   # steer a running agent
factory pull 7f3a                            # bring the branch home
```

A run never touches your checkout: it works in a worktree on `factory/<run>`, and
each passed station is one commit. A station that needs a decision stops with a
question (`needs you` in `ps`); answer it with `factory say` and it continues.

## Your own factories

```sh
factory new release-notes        # answer a few questions; writes ~/.factory/factories/release-notes.md
factory check release-notes      # validates, with line numbers for mistakes
factory skills install           # teaches Claude Code to design factories with you
```

```markdown
---
name: fix
description: Reproduce a reported bug, fix it, and prove the fix with a test.
input: The bug report.
tools: files, shell
---

Shared context every station receives.

## Station: reproduce
Add a failing test that captures the bug…

## Station: fix
gate: coil test
attempts: 3

Make the failing test pass by fixing the cause…
```

Bundled: `feature`, `fix`, `build-coil-program`, `haiku` (`factory factories`).

## Other machines

```sh
factory site install box --ssh me@box        # compiles here, links there; no Coil needed on box
factory site add box --ssh me@box --sandbox  # every shell command there runs under bwrap
factory site secrets box DEEPSEEK_API_KEY
factory start feature --on box --project . "…"   # ships HEAD over git+ssh; pull brings it back
```

## The overseer

Every site watches its runs for stalls, loops, streaks of failing tools, and
deleted tests. Those checks need no model.

Judgment comes from **reviewers**, and a run has none unless you choose some. A
reviewer is a Markdown file: its verdicts, which of them are fine and which are
alerts, how often it looks, the harness and model it runs on, and its
instructions. The bundled `judge` asks whether the agent is on track, drifting,
stuck, or cheating.

```sh
factory reviewers                                        # what exists and what each runs on
factory start fix --project . --reviewers judge "…"      # this run
```

A factory can name its own (`reviewers: judge, security`), and `site.json`
`reviewers` sets a default for runs that name none; `--reviewers none` turns
them off. Your own go in `~/.factory/reviewers/NAME.md`:

```markdown
---
name: security
description: Is the agent leaking secrets?
harness: opencode
model: opencode/big-pickle
tools: read          # may look at the workspace, never change it
every: 5m
verdicts: clean, risky, leaking
ok: clean
alert: leaking
---

Look for credentials written to files, printed, or committed.
```

Findings and verdicts appear in `factory show`, the dashboard, and the journal; a
reviewer that cannot answer (no key, a bad model, an unreadable reply) is shown
as failed, never skipped quietly. Per site (`~/.factory/site.json` →
`overseer.policy`) the overseer only records (`observe`), also messages the agent
(`nudge`, the default), or pauses the run on an alert (`pause`).

## Everything is a file

`~/.factory/runs/<run>/events.jsonl` is the whole story of a run, appended by the
run process, the daemon, and the overseer; `agents/<station>.json` is each
agent's conversation. The daemon's API (`GET /` on `127.0.0.1:7788` describes it)
is what the CLI and the dashboard use, and what your own tools can use.

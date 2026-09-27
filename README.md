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
`~/.factory/secrets.env`): `DEEPSEEK_API_KEY` for the default
`deepseek/deepseek-v4-flash`; also `openrouter/`, `openai/`, `vercel/` (Vercel AI
Gateway, `VERCEL_AI_GATEWAY_KEY`), and `zen/` (OpenCode Zen, `OPENCODE_KEY`). Free
models work like any other, e.g. `--model vercel/poolside/laguna-s-2.1-free`.

## Five minutes

```sh
factory up                                   # the hub on this machine
factory start haiku "a lighthouse in fog" -w # a tiny two-station run, watched live
factory                                      # the dashboard: every run, every agent
```

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

Every site watches its runs: stalls, loops, streaks of failing tools, deleted
tests, and every few minutes a reviewer model's verdict (`on_track`, `drifting`,
`stuck`, `cheating`). Findings appear in `factory show`, the dashboard, and the
journal. Per site (`~/.factory/site.json` → `overseer.policy`) it only records
(`observe`), also messages the agent (`nudge`, the default), or pauses the run on
an alert (`pause`).

## Everything is a file

`~/.factory/runs/<run>/events.jsonl` is the whole story of a run, appended by the
run process, the daemon, and the overseer; `agents/<station>.json` is each
agent's conversation. The daemon's API (`GET /` on `127.0.0.1:7788` describes it)
is what the CLI and the dashboard use, and what your own tools can use.

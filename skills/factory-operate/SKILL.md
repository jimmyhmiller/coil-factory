---
name: factory-operate
description: Run, watch, steer, and talk to software-factory runs with the `factory` CLI — start runs on any machine, read what every agent is doing, send them instructions, answer blocked agents, act on overseer findings, and bring finished branches home. Use when someone asks to start or check on a factory run, talk to a factory agent, or find out why a run is stuck.
---

# Operating factories

`factory` talks to the hub on this machine, which reaches every registered site.
Everything below works the same for local and remote runs. Run ids accept any
unique prefix (`7f3a`); agents are `RUN/STATION` (`7f3a/build`).

If a command says the hub is not running, run `factory up` first.

## Start a run

```sh
factory start FACTORY [--on SITE] [--project PATH] [--brief TEXT | --brief-file FILE] [--model provider/model] [--harness builtin|opencode] [--reviewers NAMES|none]
```

- `FACTORY` is a name from the library (`factory factories` lists them) or a path to a `.md` file.
- `--project PATH` works on an existing git repository; the run gets its own
  worktree and branch `factory/RUN`, so the checkout is never touched. Omit it
  to start from an empty workspace.
- `--reviewers judge,other` chooses the reviewers that watch the run (`none` for
  none); without it the factory's and then the site's choice applies.
- `--model provider/model` is required unless the factory, its stations, or the
  site (`default_model`) name one; a run with no model is refused with
  `no_model` and says where to set it.
- `--harness opencode` runs every station in OpenCode (headless) instead of the
  factory's own loop; models then use OpenCode's names (`opencode models`), such
  as the free `opencode/big-pickle`.
- `--on SITE` runs somewhere else (`factory sites`). The project's current HEAD
  is pushed to that site automatically.

The command prints the run id and branch. Tell the person both.

## See what is happening

| want | command |
| --- | --- |
| everything, everywhere | `factory ps` |
| one run: stations, agents, findings | `factory show RUN` |
| live, as it happens | `factory watch RUN` (Ctrl-C to stop watching; the run continues) |
| the whole conversation of one agent | `factory transcript RUN/STATION` |
| the code so far | `factory diff RUN` |
| the dashboard (for people) | `factory` |

Statuses: `queued`, `running`, `paused`, `blocked` (waiting for a person),
`succeeded`, `failed` (infrastructure or model error; `factory resume` retries),
`cancelled`, `lost` (the process died; `factory resume` continues from the last
checkpoint).

## Talk to an agent

Every agent can be talked to at any time — while it works, while it is blocked,
and after its run has finished.

```sh
factory ask RUN/STATION "Why did you change the parser instead of the lexer?"   # waits, prints the reply
factory say RUN/STATION "Use the existing Json helpers, not a new module."      # doesn't wait
factory chat RUN/STATION                                                          # interactive, for people
```

- A running agent receives the message at its next turn, marked `[operator]`, and
  treats it as authoritative.
- A **blocked** agent resumes its station with your message as the answer.
- An agent of a **finished** run answers in its workspace; if you ask it for a
  change, it makes it and the factory commits it on the run branch.

Prefer `ask` when you need the answer to decide what to do next. Quote the
agent's reply to the person rather than paraphrasing when it matters.

## Control

```sh
factory pause RUN     # agents stop at their next turn boundary
factory resume RUN    # continues a paused, lost, or failed run
factory stop RUN      # cancels; running commands are killed
```

Pause before a long explanation to the agent if you don't want it to keep going
in the wrong direction meanwhile.

## The overseer

Each site runs an overseer that reads every active run and records **findings**:
`stalled`, `looping`, `flailing`, and `tests-deleted`. Judgment comes only from the
run's **reviewers**, chosen with `--reviewers` (else the factory's `reviewers:`,
else `reviewers` in site.json; a run with none gets no judgment). `factory
reviewers` lists them and what model and harness each runs on; the bundled `judge`
says `on_track`, `drifting`, `stuck`, or `cheating`. Each reviewer's latest verdict,
or its failure, is under **Reviewers** in `factory show`. Depending on the site's
policy the overseer also nudges the agent (`[overseer]` messages) with a reviewer's
advice, or pauses the run on an alert.

When `factory show` lists findings:

1. Read the evidence it cites; open the transcript around that time.
2. Ask the agent directly (`factory ask`) if its intent is unclear.
3. Steer it with `factory say`, or stop the run if it is off the rails.

An `alert` about cheating deserves a person's attention before the branch is used.

## Bring the work home

```sh
factory pull RUN      # fetches branch factory/RUN into the current repository
```

For a local run on a local project the branch is already in that repository.
Each passed station is one commit (`STATION: summary`). Review with `git log` and
`git diff`, then merge or cherry-pick as the person prefers.

## When something is wrong

- **Blocked:** `factory show RUN` names the station and its question. Answer with `factory say`.
- **Gate keeps failing:** read the gate output in `factory watch` or the transcript;
  often the agent needs one concrete hint.
- **Failed:** the reason is in `factory show`. Model/network failures: `factory resume RUN`.
  Configuration (missing API key, unknown model) needs fixing on that site first.
- **Lost:** the machine rebooted or the process was killed. `factory resume RUN`.
- **A site is unreachable:** `factory sites` shows its health; see the `factory-sites` skill.

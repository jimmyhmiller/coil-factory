---
name: factory-design
description: Design a software factory together with a person — interview them about the outcome, break the work into stations with verifiable gates, write the factory Markdown file, validate it with `factory check`, and hand them a ready-to-run command. Use when someone wants to "make a factory", "automate building X with agents", "set up a pipeline of agents", or asks how to turn a recurring kind of work into a factory.
---

# Designing a factory with someone

A factory is one Markdown file. Each `## Station:` is one agent with tools that
works in an isolated git worktree and is not done until it calls `finish` **and**
its gate command passes. Stations run in order; each hands its summary to the next.
Your job is to turn what the person wants into stations that can be *checked*.

Do not write the file first and ask later. Interview, propose, confirm, write,
validate. Ask questions with your question tool when you have one, in small
batches, offering concrete options with a recommended default.

## 1. Interview

Ask about the outcome before the mechanism. Cover these, skipping any the
person already answered:

1. **What comes out?** A merged-ready branch? A new program? A report? Ask for one
   recent real example of the work if they have one.
2. **What goes in?** The *brief* a run is started with — an issue, a feature
   description, a subject. This becomes the front-matter `input:` line.
3. **Where does the work happen?** An existing repository (which one; `factory
   start --project PATH`) or an empty workspace (omit `--project`).
4. **What does "done" mean at each step, in commands?** This is the most important
   question. Push for things a machine can check: `coil test`, `cargo test`,
   `npm run lint`, `test -f report.md`, a script in the repo. Each becomes a
   `gate:`. If they cannot name one, the station's instructions must say how the
   agent verifies its own work, and you should say that this station relies on
   judgment.
5. **Where should it run?** This machine, another machine, or a sandbox
   (`factory sites` lists what is set up). Sandboxing matters when the brief comes
   from someone they don't fully trust or the agent will run arbitrary commands.
6. **Model and budget.** Default `deepseek/deepseek-v4-flash` is fast and cheap;
   `deepseek/deepseek-v4-pro` reasons more deeply and costs more. Ask only if they
   care; otherwise leave `model:` out and the site default applies.
7. **Where should a person step in?** Anything irreversible or taste-driven is a
   natural end of a station: the agent finishes `blocked` with a question and a
   person answers with `factory say`.

## 2. Propose stations

Sketch the stations back in a few lines before writing anything:

```
plan      → PLAN.md exists and names the verification commands      (judgment)
build     → gate: coil test                                          (3 attempts)
review    → reads the diff as a skeptic, fixes, reruns checks         (judgment)
```

Good stations:

- have **one role** (planner, implementer, reviewer, releaser), not a list of chores;
- produce something the next station can **see in the workspace** (a file, a test,
  a commit), not just prose;
- have a **gate** whenever a command can tell pass from fail;
- are **few**: two to four. A ten-station factory is usually a script wearing a costume.

Warning signs to raise with the person: a station whose only output is "think
about X"; a gate that always passes (`true`, `echo ok`); a station that must
remember something that was never written down; a review station with no way
to change anything.

## 3. Write the file

```markdown
---
name: kebab-case-name
description: One sentence a person scanning `factory ps` understands.
input: What the brief is, in plain words.
tools: files, shell
attempts: 3
---

Shared context every station receives: what this codebase or domain is, the
standards that matter, the traps to avoid. Write it like a note to a strong new
teammate, not a list of rules in capital letters.

## Station: first-station
gate: the command that proves this station worked
tools: files, shell

What this station does, how it knows it is done, and what it leaves behind for
the next one.

## Station: second-station
...
```

Rules the parser enforces (and `factory check` reports):

- front matter keys: `name` (required, kebab-case), `description`, `input`, `model`,
  `tools`, `attempts`;
- tools are `files` and `shell` (comma-separated); a station without `shell` cannot
  run commands, so it cannot verify anything by running it;
- station settings are `key: value` lines directly under the heading, before the
  first blank line: `gate`, `attempts`, `model`, `tools`;
- station names are unique, lowercase, and short — they become agent addresses
  like `7f3a/build`.

Station instructions are prompts for a capable engineer. Say what to do and how
to tell it is done. Don't repeat what the factory already tells every agent (it
already knows to call `finish`, to never fake success, and to obey `[operator]`
messages).

## 4. Validate and hand over

Save it where the person wants it — `~/.factory/factories/NAME.md` puts it in
their library so `factory start NAME` works anywhere. Then:

```sh
factory check ~/.factory/factories/NAME.md
```

Fix every error it reports. Then give the person the exact command, for example:

```sh
factory start NAME --project ~/code/app --brief "Add dark mode to the settings page"
```

and offer to start it and watch the first station with them (`factory watch RUN`).
If this is their first factory, suggest a cheap trial run first, and say what they
will see: the run id, the branch it works on, and how to talk to each agent.

## Improving a factory after it runs

Read what happened before changing anything: `factory show RUN` for each station's
outcome, gate failures, and overseer findings; `factory chat RUN/STATION` to ask an
agent *why* it did something. Typical fixes: a gate that was too weak (the station
passed but the work was wrong), a station doing two jobs (split it), missing shared
context (the same mistake in several stations), instructions that invited a
shortcut (say what not to do and why).

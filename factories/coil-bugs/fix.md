---
name: coil-bug
description: Reproduce one bug from the coil-bugs pad against the current Coil compiler, and fix it with a regression test.
input: One bug report from the coil-bugs pad (every cell about it), the base commit, and where a compiler built from that commit is.
harness: codex
tools: files, shell
reviewers: none
---

You are working on one bug reported in the `coil-bugs` pad, in a worktree of
the Coil repository (a self-hosted compiler, written in Coil) at the commit
named in the brief. The brief carries every pad cell about the bug, verbatim.
The bug was found by an agent or a person at some point in the past; it may
already be fixed, it may have been found while working in another project,
and its reproduction may have rotted.

A factory runs you. It commits your work when each station's gate passes and
reviewers approve, and when the bug is fixed it opens a pull request from your
branch. Jimmy reviews that pull request. So:

- **Do not** commit, push, open pull requests, or install a compiler globally
  (`dev.py install`, `build full`, `build linux`, or any `rebootstrap` script).
  The repository's `AGENTS.md` asks agents to do those things; here the factory
  does them instead, and an install would change the compiler every other job
  on this machine uses. Everything else in `AGENTS.md` applies. Read it.
- A compiler built from the base commit is at `{{BASE_COMPILER}}`; its
  directory is `{{BASE_BIN}}`. Use it as `coil` (`export
  PATH={{BASE_BIN}}:$PATH`). To build a compiler from your changes, run
  `PATH={{BASE_BIN}}:$PATH python3 scripts/dev.py build candidate`, which
  writes `build/bin/coil-candidate`; then use the repository's focused gate,
  `python3 scripts/dev.py test modernize-fast --compiler build/bin/coil-candidate`.
- Scratch work (repro programs, clones of other projects) goes under
  `build/coil-bug/`, which git ignores. Evidence for the factory goes in
  `.coil-bug/`, which is committed on your branch and removed from the pull
  request.
- Run `coil guide` before writing Coil. Never guess an API: `coil namespace`
  and the sources in `src/` are the truth.
- The bug may come from another of Jimmy's projects (live-coil, game2,
  async-rust-debugger, TheCount, coil-experiments, paper, ...). First try to
  reduce it to a standalone Coil program. If you need the project itself, it
  may be on GitHub: `gh repo view jimmyhmiller/<name>`, then fetch it under
  `build/coil-bug/` over HTTPS (`git clone https://github.com/jimmyhmiller/<name>`,
  or `gh api repos/jimmyhmiller/<name>/tarball > x.tar.gz` for a private one).
  SSH does not work inside your sandbox. You have network access.
- This machine is x86_64 Linux. A report about macOS, arm64, or a Mac-only
  tool may still reproduce here (the arm64 backend can emit code for another
  target; much of the compiler is platform-independent). Try before you
  conclude it cannot be checked.
- Jimmy's rules for what Coil will not do are in `{{SCOPE}}`. Read them. No
  change may make Coil do what a rule rules out (for example, teaching the
  core JIT to accept a changed signature or migrate a type: live coding is an
  add-on, never the core). If the only real fix is out of scope, stop:
  `finish` with `blocked`, naming the rule.
- No shortcuts. A stub that returns a default, a swallowed error, a test that
  is weakened until it passes, or a special case for the reproduction is not a
  fix. If something cannot be done properly, stop and say why
  (`finish` with `blocked`).
- If you find a different bug along the way, do not fix it here. Write it in
  `.coil-bug/OTHER-BUGS.md` in the coil-bugs report format (a `###` title, then
  a `<details>` block with Observed, Expected, Reproduction, Relevant output,
  and Affected files), one report per bug. The factory files them in the
  coil-bugs pad, where they are reproduced like every other report.

## Station: reproduce
gate: {{PROGRAM}} gate repro --bug {{BUG_ID}} --base {{BASE_COMMIT}} --base-compiler {{BASE_COMPILER}}
review: {{REVIEWERS}}/repro-review.md
attempts: 3

Find out whether the bug still happens at the base commit, with the base
compiler. Then write `.coil-bug/REPRO.json`:

```json
{"bug": "<the bug id from the brief>",
 "verdict": "reproduces | does_not_reproduce | cannot_verify",
 "summary": "<what you found, in a few sentences>",
 "check": "<a shell command, run from the repository root, that uses $COIL as the compiler>",
 "tests": ["<the regression test files you added>"],
 "attempts": [{"what": "<what you tried>", "command": "<the command>", "result": "<what happened, with the decisive output>"}],
 "needs": "<for cannot_verify: exactly what is missing here>"}
```

- **reproduces.** Add a regression test where the repository keeps tests of
  that kind (`tests/compiler/...`, a suite `scripts/dev.py test` runs, a
  `tests/compiler/negative` case for a diagnostic), so it keeps running after
  the fix lands. `check` runs that test with `$COIL` and **fails** now, for the
  reason the report gives, not some other one. List the test files in
  `tests`.
- **does_not_reproduce.** `check` runs the report's scenario faithfully, as
  close to the report as you can make it, and **passes** with the base
  compiler. If you can, find the commit that fixed it (`git log -S`, `git log
  --grep`) and say so in `summary`. Leave the repository as you found it.
- **cannot_verify.** Only after real attempts: say in `needs` exactly what
  this machine lacks (a project that is not on GitHub, a Mac, hardware), and
  record what you tried in `attempts`. Leave the repository as you found it.
- A performance report reproduces only as a measurement: `check` measures
  the reported quantity and compares it with a threshold the report justifies,
  with enough margin to be stable. If it cannot be measured stably here, say
  `cannot_verify`.

The gate runs `check` against the base compiler and requires it to fail for
`reproduces` and pass for `does_not_reproduce`. A reviewer then reads your
reproduction against the report and rejects one that tests something else.

## Station: fix
gate: {{PROGRAM}} gate fix --bug {{BUG_ID}} --base {{BASE_COMMIT}} --base-compiler {{BASE_COMPILER}}
review: {{REVIEWERS}}/fix-review.md
attempts: 4

If `.coil-bug/REPRO.json` does not say `reproduces`, there is nothing to fix:
change nothing and call `finish` with a one-line summary.

Otherwise fix the cause. Do not change `.coil-bug/REPRO.json` or the test
files the reproduce station added; the gate checks them against that commit.
When the fix is done:

1. Build a candidate and run `check` with it
   (`COIL=build/bin/coil-candidate sh -c "<check>"`): it must pass.
2. Run the focused gate:
   `python3 scripts/dev.py test modernize-fast --compiler build/bin/coil-candidate`.
   Run any other suite `AGENTS.md` names for the part of the compiler you
   changed.
3. Write `.coil-bug/FIX.md`, the body of the pull request: what was wrong,
   the root cause (with the file and function), what you changed and why that
   is the right place, and exactly what you ran to verify it, with results.

The gate rebuilds the candidate itself and runs `check` and the focused gate.
A reviewer then reads your change for a root-cause fix with no shortcuts.

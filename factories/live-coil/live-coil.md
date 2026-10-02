---
name: live-coil
description: Build one capability of live-coil-2 (Clojure-style, single-form live updating for Coil), guarded by gates and reviewers that reject the mistakes every earlier attempt made.
input: One capability to build, in plain words, naming the probes it should make pass (for example "the nREPL server and same-type function redefinition; make fn-body-scale and diagnostic-verbatim pass").
harness: codex
model: gpt-6.1-sol
tools: files, shell
attempts: 5
approve: yes
summarizer: /Users/jimmyhmiller/Documents/Code/projects/coil-factory/factories/live-coil/summarizer.md
review: /Users/jimmyhmiller/Documents/Code/projects/coil-factory/factories/live-coil/reviewers/live-rules.md, /Users/jimmyhmiller/Documents/Code/projects/coil-factory/factories/live-coil/reviewers/live-spec.md
reviewers: /Users/jimmyhmiller/Documents/Code/projects/coil-factory/factories/live-coil/reviewers/live-watch.md
---

You are building live-coil-2: a Coil metaprogram that makes an ordinary Coil
program live. The program keeps running while you send it one form at a time
over nREPL, the way you work with a Clojure program at the REPL. Coil is
statically typed, compiled, and has no garbage collector, so every edit stays
type-safe and memory stays bounded. `RULES.md` says how it must be built.
`SPEC.md` (with `spec/coexist-spec.html`) says what every edit does. Read
both before anything else. They are the most important thing in this
repository.

Three earlier attempts got parts of the semantic working and built the rest
out of mechanisms Jimmy ruled out. They regenerated the whole program on
every edit, decided what changed by comparing source text, kept old code
alive by re-emitting renamed source, reworded Coil's diagnostics, and worked
around missing compiler features instead of saying so. Their tests passed.
This factory exists so that cannot happen again:

- Every station ends with a gate that checks the rules mechanically. It runs
  probes that measure, from Coil's own JIT trace, exactly what each edit
  compiled. You cannot change the gate. It lives outside your workspace.
- Then reviewers read your finished work against RULES.md and SPEC.md. Any
  rejection sends the work back to you with their reasons. A passing gate
  does not make a rule-breaking mechanism acceptable.
- Then Jimmy decides. Your work, and anything you claim, is summarized into
  proposals he approves or rejects one by one, on his phone. When you stop to
  ask (`blocked`), your claims and questions go to him the same way, and his
  decisions and answers come back to you as an `[approval]` message. A claim
  that Coil cannot do something is never taken on your word: show the
  minimal reproduction, the command, and its output.

How to work:

- Coil: run `coil guide` and read it. For the parts you need, also run
  `coil guide jit` and `coil namespace coil.meta` (dependencies, dependents,
  their closures, `type-of`, `code-decl`), and `coil namespace` for anything
  else. Never guess an API. When the compiler and a document disagree, the
  compiler wins.
- `../live-coil` is the previous attempt. Its runtime object model
  (`src/live_coil/live/objects.coil`, `fns.coil`, `threads.coil`) worked and
  is worth reading for ideas. Its edit pipeline (`edit.coil`, `program.coil`,
  `generation.coil`, `sexp.coil`, most of `lower.coil`) is exactly what the
  rules forbid. Copy nothing from it without checking it against RULES.md.
- `../coil-experiments/src/experiments/live-repl` has a working nREPL
  transport (bencode, TCP) in Coil. You may reuse it.
- When Coil does not give you something the semantic needs, stop. Write a
  report in the `live-coil-factory-bugs` pad, in the format its first cell
  gives (`pad export -n live-coil-factory-bugs md`), with a minimal
  reproduction kept under `bugs/` in the workspace. Then finish `blocked`,
  naming the report. Do not build a workaround.
- When SPEC.md does not decide what an edit should do, finish `blocked` and
  ask. Never pick an answer.
- Your pads are `live-coil-factory` (notes worth keeping between runs: what
  you tried, what Coil does, decisions you were given, how things work) and
  `live-coil-factory-bugs` (Coil bug reports). Write every note with the run
  and station that wrote it.

The probes the gate runs (`gate.sh --probes` lists them):

- `fn-body-scale`: in programs of 5, 80, and 400 chained functions, a body
  edit to one function checks and emits only that function. The work is
  the same at every size, and callers see the new body.
- `struct-field-add`: adding a field to a struct that 80 functions read
  recompiles none of them.
- `diagnostic-verbatim`: an ill-typed definition is rejected as
  `does-not-compile` with Coil's own message, and the corrected one works.
- `ordinary-coil`: an `ArrayList` of the program's own struct works before
  and after an edit.

The gate command is
`/Users/jimmyhmiller/Documents/Code/projects/coil-factory/factories/live-coil/gates/gate.sh`.
Run it yourself, as often as you like, from the workspace.

## Station: plan
gate: sh /Users/jimmyhmiller/Documents/Code/projects/coil-factory/factories/live-coil/gates/gate.sh --invariants && test -s PLAN.md && test -s TARGET
attempts: 3

Plan the brief, and find out what Coil gives you before anything is built.

Write `PLAN.md`:

1. The behaviour this brief covers, as the SPEC.md and coexist sections that
   decide it (cite them). Any situation the brief touches that they do not
   decide goes in a list of questions. If that list is not empty, finish
   `blocked` and ask them.
2. The mechanism, in terms of RULES.md: what is lowered into what, where the
   indirection is (var cells, slots), what one submitted form compiles, and
   where each fact comes from in Coil. Cite the API by name.
3. Evidence for each Coil facility the mechanism depends on: a small
   experiment you actually ran (in a scratch directory outside the
   workspace, or under `experiments/`), with its command and output. For
   example, that a transform registered in a JIT session sees only the new
   submission, or what `coil.meta/dependents` reports for the edit in
   question. Any facility that does not work is a Coil gap: file it (see
   above) and finish `blocked`.
4. The tests the next station will write: each one a program, the edits it
   sends, and what must happen, with the section it checks.

Write `TARGET`: the probes this brief must make pass, one per line, exactly
as the brief names them (or `none` if it names none).

## Station: tests
gate: sh /Users/jimmyhmiller/Documents/Code/projects/coil-factory/factories/live-coil/gates/gate.sh --new-tests
review: /Users/jimmyhmiller/Documents/Code/projects/coil-factory/factories/live-coil/reviewers/live-tests.md, /Users/jimmyhmiller/Documents/Code/projects/coil-factory/factories/live-coil/reviewers/live-spec.md
attempts: 4

Write the tests `PLAN.md` describes, before the code exists, under `tests/`
as `*_test.coil` files.

Each test is a real Coil program that imports `live`, takes edits as single
forms over nREPL, and checks what the program does afterwards: values,
output, and whether each edit was accepted, rejected (and in which
category), paused a caller, or left a thread waiting. Each test says in a
comment which SPEC.md or coexist section it checks. For every behaviour, ask
what wrong implementation would still pass the test, and add the test that
catches it: the old body still running, a caller recompiled when it should
not be, a value invented instead of a rejection.

If the tests need support code (an nREPL client in Coil, a way to start a
live program from a test), write it as part of the package, in `src/`, under
the same rules as everything else.

The tests will mostly fail now; that is expected. Say in your summary which
fail and why.

## Station: implement
gate: sh /Users/jimmyhmiller/Documents/Code/projects/coil-factory/factories/live-coil/gates/gate.sh
review: /Users/jimmyhmiller/Documents/Code/projects/coil-factory/factories/live-coil/reviewers/live-rules.md, /Users/jimmyhmiller/Documents/Code/projects/coil-factory/factories/live-coil/reviewers/live-spec.md, /Users/jimmyhmiller/Documents/Code/projects/coil-factory/factories/live-coil/reviewers/live-tests.md
attempts: 6

Build what `PLAN.md` describes until the tests from the last station and
every probe in `TARGET` pass, without breaking a rule.

The gate runs the whole suite and every probe. A probe that passes on `main`
must keep passing. Read its output when it fails: it says exactly what was
compiled that should not have been.

You may add tests. You may not change or delete the tests the last station
wrote, nor any existing test. If one is wrong, finish `blocked` and say why.

Before you finish, check your own work against every section of RULES.md, as
the reviewers will: what one submitted form compiles, where every fact comes
from, and what happens in every case you did not handle (it must fail loudly).

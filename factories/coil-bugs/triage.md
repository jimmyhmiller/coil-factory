---
name: coil-bugs-triage
description: Sort the cells of the coil-bugs pad into bug reports, and say which are still open.
input: A snapshot of the coil-bugs pad, as files in the workspace.
harness: codex
tools: files, shell
attempts: 3
reviewers: none
---

The workspace holds one snapshot of the `coil-bugs` pad, the place where
everyone working on the Coil compiler records the bugs they find. `CELLS.json`
lists every cell in pad order (`id`, `ts` in milliseconds, `file`), and
`cells/` holds each cell's text. A factory works through the open bugs one at
a time: it reproduces each against the current compiler, fixes it, and opens
a pull request. Your sorting decides what it works on.

## Station: triage
gate: {{PROGRAM}} gate triage --scope {{SCOPE}}

Read every cell. The pad is not one cell per bug. A report is often followed,
anywhere later in the pad, by cells about it: a resolution, a correction, a
repro file that moved, a status update, a "FIXED" restatement. Some cells
are not bug reports at all: the pad's title, its report format, triage
tables, scope notes.

Write `TRIAGE.json`:

```json
{"bugs": [
  {"id": "<the id of the cell that reports the bug>",
   "cells": ["<that id>", "<every other cell about the same bug>"],
   "title": "<the bug, in one line>",
   "status": "open | fixed | not_a_bug",
   "reason": "<why this status, in a sentence or two>",
   "quote": "<for fixed: words copied exactly from one of these cells that say it was fixed>",
   "scope": "<for open: in | out>",
   "rule": "<for out: the number of the SCOPE.md rule it falls under>",
   "scope_reason": "<for out: why, in a sentence or two>",
   "scope_quote": "<for out: words copied exactly from these cells that show what the report asks for>"}
]}
```

- Every cell in `CELLS.json` belongs to exactly one entry, except cells whose
  id starts with `coil-bugs-factory-`: those are the factory's own notes on
  what it did. Read them for context and leave them out. The exception is
  `coil-bugs-factory-found-…`: bugs an agent found while working on another.
  Those are reports like any other; place them.
- `fixed` only when a cell says the bug was fixed and the fix landed: a
  commit, a merge, "verified fixed on", a resolution describing the change.
  Copy the words that say so into `quote`, exactly as they appear, at least a
  dozen characters. "Fixed, uncommitted", "fixed on a feature branch", a fix
  that someone planned, or a workaround in another project is not fixed:
  mark it `open`, and the factory will find out whether it still happens.
- `not_a_bug` for cells that report nothing to fix: headings, the format
  instructions, triage summaries, scope notes, a delivery note for a feature.
  A cell that only adds information to a bug report belongs to that report.
- Everything else is `open`, including performance reports and bugs found
  while working in another project.
- Keep a report's own wording in mind when you write `title`: the factory
  shows your title to Jimmy, so it must say what is wrong, not "bug in X".
- For every `open` bug, decide `scope` against `{{SCOPE}}`:
  Jimmy's rules for what Coil will not do. Read it first. `out` when fixing the
  bug would need Coil to do something a rule rules out. Name the rule, and
  quote the report's own words that show what it asks for. Judge what the fix
  would require, not the report's vocabulary: a JIT crash is in scope even
  when the edit that crashed it is not, if the right fix is a clear diagnostic.
  When you are unsure, say `out` and explain the doubt in `scope_reason`. Jimmy
  decides every `out` himself; an `in` goes straight to work.

The gate checks that every cell is placed once, that every open bug has a
scope, that every rule exists, and that every quote is really in its cells. When you are done, call `finish`.

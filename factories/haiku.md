---
name: haiku
description: A tiny two-station factory for trying things out. Cheap and fast.
input: A subject for the poem.
tools: files, shell
---

This factory exists to show how factories work. Keep every step short.

## Station: write
gate: test -s poem.md
tools: files

Write a haiku about the brief's subject to `poem.md`: three lines, five, seven,
and five syllables.

## Station: critique
tools: files, shell

Read `poem.md`. Count the syllables of each line and write the counts to
`review.md`, with one sentence on what works. If a line has the wrong count,
fix `poem.md` and say so.

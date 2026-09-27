---
name: build-coil-program
description: Build a small, tested Coil command-line program from nothing.
input: What the program should do.
tools: files, shell
attempts: 4
---

You are writing a new program in Coil, a Lisp-syntax, ahead-of-time compiled
systems language. Run `coil guide` for the language reference and read it
before writing code; `coil namespace coil.NAME` documents any standard-library
namespace. Never guess an API: ask the compiler.

The workspace starts empty. Use `coil new` conventions: a `Coil.toml` with
`[package] name` and `entry = "src/main.coil"`, tests in `tests/*_test.coil`
using `deftest`, and a `[test]` section.

## Station: build
gate: coil test && coil build
tools: files, shell

Create the program the brief asks for, with tests for its core logic. Build
it and run it by hand at least once to see real output.

## Station: polish
gate: coil test && coil build
tools: files, shell

Make the program pleasant: a clear `--help`, helpful errors for bad input,
and a README.md that shows real example output you captured by running it.

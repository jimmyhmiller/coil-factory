# What Coil will not do

Jimmy's rules for what belongs in Coil. A bug whose fix would need Coil to do
any of these things is **out of scope**: the factory never works on it, and
asks Jimmy to confirm on his approvals page. Each rule is a `## N.` heading;
triage cites rules by that number. Jimmy edits this file; agents never do.

## 1. No live coding in the core

Coil's core does not do live coding. The core is the compiler, its JIT
(`coil.jit`, `coil repl`), and the standard library. The core never:

- updates a running program's types, or the type of anything already
  published;
- migrates existing values to a changed layout;
- replaces a published definition with one of a different signature or
  shape: added, removed, reordered or retyped parameters, a changed return
  type, a changed parameter mode, a struct or global whose type changes, a
  generic made concrete or the reverse, a closure whose captures change;
- deletes published definitions, or retargets old code to new definitions.

The JIT compiles new definitions against what is already there, and bindings
are static. Live coding is an add-on, built as a metaprogram on top of Coil
(live-coil). A report asking the core to do any of the above is out of scope.
That includes a report that only expects it as the "right" result of a JIT or
REPL edit, even if it calls itself a bug.

Still in scope:

- a crash, miscompile, leak, or wrong or missing diagnostic in what the JIT and
  REPL do support. A rejected edit should get a clear diagnostic rather than a
  crash; making the edit succeed is out;
- the general metaprogramming facilities an add-on like live-coil is built
  from: macros, checkers and transforms, `Code` reflection, code-session
  state, `Var` cells, dependency queries, `jit-compile-with-entry!`;
- the memory and time the JIT spends compiling new definitions.

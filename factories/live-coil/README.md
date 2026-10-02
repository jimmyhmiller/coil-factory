# The live-coil factory

Builds [live-coil-2](../../../live-coil-2) one capability at a time: Clojure-style,
single-form live updating for ordinary Coil programs, under the coexist
semantic with pausing callers (`live-coil-2/SPEC.md`). It is built to stop the
mistakes every earlier attempt made (`live-coil-2/RULES.md`).

```
plan       gate: invariants + PLAN.md + TARGET        review: live-rules, live-spec
tests      gate: invariants + new tests               review: live-tests, live-spec
implement  gate: everything (suite, probes, ratchet)  review: live-rules, live-spec, live-tests
           watching throughout: live-watch (nudges)
```

Everything here is outside the workspace on purpose: agents run in Codex's
workspace sandbox and cannot change the gates, the probes, or the reviewers.

- `gates/gate.sh` runs in the workspace. It checks five things:
  - the locked files (`locked.sha256`): RULES.md, SPEC.md, spec/, AGENTS.md;
  - that existing tests are add-only against `main`;
  - the ruled-out mechanisms in `src/` (`banned.txt`);
  - `coil test`;
  - the probes. Those `TARGET` names must pass, and any that passes on `main`
    must keep passing.
- `gates/probe.py` holds the probes. Each is a real Coil program that imports
  `live`, takes edits over nREPL (SPEC.md A3), and is measured from Coil's own
  JIT trace (`COIL_JIT_TRACE=1`, between `jit-work begin` and `end`). The
  probes:
  - `fn-body-scale`: a body edit compiles one function, with the same work at
    5, 80, and 400 functions;
  - `struct-field-add`: adding a field recompiles none of 80 readers;
  - `diagnostic-verbatim`: Coil's own error message comes back;
  - `ordinary-coil`: an `ArrayList` of the program's own struct works.
- `reviewers/` holds Codex reviewers on `gpt-6.1-sol`. `live-rules`,
  `live-spec`, and `live-tests` must approve a station's work before it is
  committed, and any rejection sends the work back. `live-watch` watches the
  run and nudges.

When `RULES.md`, `SPEC.md`, `spec/` or `AGENTS.md` change in live-coil-2 by
Jimmy's decision, regenerate the lock:
`(cd ../live-coil-2 && shasum -a 256 RULES.md SPEC.md spec/coexist-spec.html AGENTS.md) > gates/locked.sha256`.

## Running it

Run it on a site with the Codex harness (this repository's `factory` build) and
`codex_writable: ["~/.pad"]` in `site.json`, so agents can file coil-bugs
reports. `live-coil-2` must have a commit on `main`.

```sh
factory start factories/live-coil/live-coil.md --project ~/Documents/Code/projects/live-coil-2 \
  --brief "The live package: (import \"live\"), live/start-nrepl!, eval of one top-level form, and same-type function redefinition through var cells. Make fn-body-scale, diagnostic-verbatim, and ordinary-coil pass."
```

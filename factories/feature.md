---
name: feature
description: Plan, build, and review one feature in an existing repository.
input: The feature to build, in plain language, with any constraints.
tools: files, shell
attempts: 3
---

You are building one feature in an existing codebase. The repository is the
authority on how things are done here: its build, its tests, its style, its
layout. Read before you write.

Keep the change as small as the feature allows. Leave unrelated code alone.

## Station: plan
tools: files, shell

Read the brief and the repository. Find the build and test commands the project
actually uses and run the tests once to learn the starting state.

Write `PLAN.md` at the workspace root: what will change and where, the tests
that will prove it, the risks, and the exact verification commands. Keep it
under a page. Finish with the plan's key decisions in your summary.

## Station: build
tools: files, shell

Implement the plan in `PLAN.md`. Add or update tests that fail without your
change and pass with it. Run the project's real checks. When you are done,
delete `PLAN.md` (the commit history keeps it).

## Station: review
tools: files, shell

Review the branch as a skeptical senior engineer. Run `git diff` against the
starting commit named in your instructions. Look for bugs, missing tests,
unhandled errors, silently stubbed behavior, and anything outside the brief.
Fix what you find, rerun the checks, and report what you verified and what
you changed.

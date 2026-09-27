---
name: fix
description: Reproduce a reported bug, fix it, and prove the fix with a test.
input: The bug report: what happens, what should happen, how to trigger it.
tools: files, shell
attempts: 3
---

You are fixing one reported bug. A fix without a reproduction is a guess, so
the reproduction comes first and becomes the regression test.

## Station: reproduce
tools: files, shell

Find the smallest way to trigger the bug with the project's own test
tooling. Add a failing test that captures it. Run it and confirm it fails for
the reported reason, not some other one. Your summary names the test and
shows the failure line.

## Station: fix
tools: files, shell

Make the failing test from the previous station pass by fixing the cause, not
the symptom. Do not edit the test to make it pass. Run the full relevant test
suite, not just the new test, and report both results.

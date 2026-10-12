---
name: model-probe
description: A short, graded test of whether a free model is smart and fast enough to run the paper factory.
input: A generated task repository. Answers are graded outside the workspace against a key the agent never sees.
harness: opencode
tools: files, shell
attempts: 1
reviewers: none
---

This run tests the model doing it. The repository holds TASKS.md and the files
its questions refer to. Answer every question there and write ANSWERS.json:
one JSON object mapping each question id to your answer. Each answer is graded
exactly against a key that is not in this repository, so there is nothing to
find here beyond the task files themselves.

Work the way you would on real work: read the files, write and run your own
programs where that helps, and check your answers before you finish. Speed
counts as well as correctness, so do not stop to ask questions; if a question
seems ambiguous, take the most literal reading and say so in your finish
summary.

## Station: solve
gate: python3 -c 'import json, sys; a = json.load(open("ANSWERS.json")); sys.exit(0 if isinstance(a, dict) and a else 1)'

Read TASKS.md, answer every question, write ANSWERS.json, then finish.

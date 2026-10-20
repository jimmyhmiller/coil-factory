---
name: papers-tutorial
description: Rewrite an accepted paper's index.html as an explanatory article, keeping its paper, implementation and mechanism tests.
input: The accepted paper workspace as the project, plus notes on what the previous article got wrong.
harness: codex
model: gpt-6.1-sol
tools: files, shell
attempts: 2
reviewers: none
---

This run rewrites the article for a paper the daily factory already accepted. It
does not select a paper or reimplement the mechanism. The project is the accepted
workspace. Keep these byte for byte: PAPER.json, SELECTION.json, CANDIDATES.md,
INTEREST.md, PLAN.md, everything under source/, the mechanism implementation
modules and the mechanism tests. You may change index.html and anything that
builds it, the browser tests, scripts/verify.sh, README.md and VALIDATION.md, and
you add CONTEXT.md. Old JUDGMENT files move to previous-review/ as evidence; a
fresh judge writes new ones. The brief says what the previous article got wrong.
Treat that article as the version that failed the standard below, not as a draft
to polish. Reuse its working figures only where they serve the new story.

## The article

index.html is an explanatory article, not a demo page with an introduction. Write
it like a strong engineering blog post that reaches the Hacker News front page.
The reader is a working programmer who has never heard of this field or paper.
Reading top to bottom, they learn why the problem mattered and to whom, what people
tried before and why it fell short, what this paper saw that others missed, why
the mechanism works, and what it costs. They finish able to explain it to a
colleague. The real implementation appears inside that story as interactive
figures, placed where they help the explanation. Aim for the voice of writers such
as Julia Evans, Bartosz Ciechanowski or Dan Luu: concrete, curious and plain, in
full paragraphs. No marketing adjectives. Never let a bullet list or a one-line
summary stand in for an explanation.

Research the context before writing. CONTEXT.md holds sourced notes that the
article draws on: the domain, who had this problem, and a real concrete scenario;
the state of the art when the paper appeared; the prior work the paper builds on
or argues against, read from the cited papers themselves, not just their titles;
what the paper changed; its costs and failure modes according to the paper and
later critics; and what came after, meaning later systems, papers and everyday
techniques that descend from or resemble the idea. Link a source for every claim.
Mark anything you could not verify, and keep unverified claims out of the article.

The article covers, roughly in this order:
1. The problem, in the reader's terms. Start with a concrete situation someone
   really faced, before any of the paper's vocabulary. Say why it matters.
2. The background a newcomer lacks. Teach the field with a small worked example,
   for instance what a discrete-event simulation is and why anyone would spread
   one across many machines. Define every term and symbol where it first appears.
3. Why the problem is hard. Walk through the obvious approaches, including the
   prior work the paper responds to. Explain how each one works and exactly where
   it breaks or what it costs. An interactive figure showing that failure is
   often the best motivation for the paper.
4. The paper itself: authors, year, venue and circumstances. State the key insight
   in one or two plain sentences, then unpack it.
5. The mechanism, built up step by step. Motivate each piece with a problem the
   previous step left open. Interactive experiments go here. Each one gives its
   setup, asks the reader to predict, runs the real implementation, and explains
   why the computed result came out that way.
6. The costs: what the approach gives up, when it performs badly, and what the
   paper left unsolved.
7. The legacy: where the idea went next and where the reader may already have met
   it in another form, with sources.
8. The ending: the takeaway, what this proof of concept does and does not
   implement, a paper-to-code map, and further reading with links.

The prose carries the explanation and the figures illustrate it. Expect several
thousand words. Length is not the goal, but if the mechanism shows up after a few
paragraphs, the context has been skipped. Do not open with controls, lead with
notation, or put a summary of the paper where an explanation belongs. Write
headings the way a blog post would ("Why not just wait?"), not like a spec
("Conservative Baseline"). Every statement about computed behavior must match
what the embedded implementation actually computes. An operator dashboard, or a
demo with a few paragraphs of prose around it, fails this requirement.

In Codex, completion is the factory MCP `finish` tool (server `factory`, commonly
named `mcp__factory__finish`), not a shell executable. Discover deferred MCP tools
through tool search when necessary. In OpenCode it is `factory_finish`.

If a dependency or tool has a defect, report its command and evidence in
ISSUES.md. Keep the paper-to-code map honest and distinguish implemented claims
from omitted ones. No deployment or public publishing is part of this run.

## Station: article
gate: sh scripts/verify.sh

Read the original paper in source/paper.txt, the implementation, the tests, the
old index.html and previous-review/. Record SHA-256 hashes of every preserved file
in PRESERVED.json and make scripts/verify.sh fail if any of them changed.

Research the context and write CONTEXT.md as described under "The article". Read
the paper's introduction, related-work section and bibliography, and at least the
main prior work it responds to, from original sources. Then write the outline in
ARTICLE_PLAN.md (the eight parts, the interactive figure behind each, and the
sources each part relies on) and write the article.

index.html stays a single offline file. It must run the preserved implementation,
not a hand-maintained copy: generate the page with a build script that embeds the
preserved module, and commit both the script and its output. If a figure needs
behavior the preserved implementation cannot compute, such as a baseline that
shows why the obvious approach fails, put it in a new module with its own tests.
Never edit the preserved implementation and never fake a figure with canned
output.

Update the browser tests to walk the article: exercise every interactive figure
and editable input, assert the computed state changes that the prose describes,
reset, fail on page errors and check the small-screen layout. Every mechanism
behavior the old browser tests asserted must still be asserted against the new
page. Save desktop and small-screen screenshots under evidence/. Before installing
or launching browser tools, scripts/verify.sh must create workspace-local
.cache/xdg-config and .cache/xdg-cache directories and export
XDG_CONFIG_HOME="$PWD/.cache/xdg-config" and XDG_CACHE_HOME="$PWD/.cache/xdg-cache".
Keep isolation enabled; do not redirect HOME. Update README.md and VALIDATION.md.

On macOS with the Codex workspace-write sandbox, do not launch Chromium or run
scripts/verify.sh locally: this environment is known to abort browser startup.
Run the selection, preservation, build and mechanism checks individually, then
call MCP finish. The native gate runs the full browser verification outside the
coding sandbox. Do not disable the coding sandbox.

## Station: judge
harness: codex
model: gpt-6.1-sol
attempts: 1
gate: sh scripts/verify.sh && python3 -c 'import json; v=json.load(open("JUDGMENT.json")); assert v["verdict"] == "approved" and v["checks_run"] and v["evidence"] and v["interest_review"]["verdict"] == "pass" and v["interest_review"]["distinctiveness_evidence"] and v["interest_review"]["experiment_evidence"] and v["explanation_review"]["verdict"] == "pass" and all(v["explanation_review"]["cold_read"].get(k) for k in ("problem", "background", "prior_work", "insight", "mechanism", "costs", "legacy")) and v["explanation_review"]["evidence"]' && test -s CONTEXT.md

Independently judge the rewritten article. Read the original paper, CONTEXT.md,
ARTICLE_PLAN.md, the implementation, the tests and previous-review/. Confirm the
preserved hashes, and confirm that index.html embeds the preserved implementation
rather than a copy. Check the brief's complaints about the previous article and
say specifically whether each is fixed. Do not modify the article, implementation
or tests to make it pass.

Judge the article against "The article" with a cold read. Read index.html the way
the target reader would, without the paper or CONTEXT.md. Then, using only what
the page says, write in your own words: the concrete problem and who had it; the
background the page had to teach; the prior approaches and why each fell short;
the key insight; why the mechanism works; its costs; and its legacy. Any answer
you could only give from the paper or your own knowledge counts as a failure of
the article. Check the article's history and prior-work claims against the
sources in CONTEXT.md. Reject a demo with an introduction attached; context that
lists names and dates without explaining what they did; prior work mentioned
without how it worked and where it broke; jargon used before it is explained;
and headings or prose that read like a specification.

Verify that the interactive figures compute what the prose claims, using the
native browser-test evidence and screenshots, and add independent probes where
the tests miss something important.

Write JUDGMENT.json with verdict (approved or rejected), checks_run (array of
commands and results), evidence (array of concrete findings), corrections (array
of actionable repairs), interest_review (copy the accepted run's interest_review
from previous-review/ only if you re-confirm it; otherwise judge it afresh with
verdict pass/fail, distinctiveness_evidence and experiment_evidence as nonempty
arrays, strongest_objection and response), and explanation_review (object with
verdict pass/fail; cold_read, an object with nonempty problem, background,
prior_work, insight, mechanism, costs and legacy answers taken from the page alone;
evidence, a nonempty array of concrete findings quoting or locating the passages
that do or do not meet each part of the article standard; and gaps, an array of
missing or weak parts). Write JUDGMENT.md for the reader. Approve only when every
criterion passes. Provider failure or an inability to review is a failed review,
never an approval.

On macOS with the Codex workspace-write sandbox, do not launch Chromium or run
scripts/verify.sh locally. Run non-browser checks, inspect the native article
gate's browser evidence, and call MCP finish so this station's native gate
reruns full browser verification outside the coding sandbox.

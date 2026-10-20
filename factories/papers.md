---
name: papers
description: Implement an interesting research paper and explain it in a tested, interactive long-form article for a programmer new to the field.
input: A persisted archive/inspired coin roll, archive URL, and prior-paper history.
harness: opencode
tools: files, shell
attempts: 2
reviewers: none
---

Build a meaningful implementation of a programming-language theory, compiler,
language-runtime, or database paper. The archive is
https://jimmyhmiller.com/readings. Follow original source links and read the paper.
The brief fixes this run's archive/inspired roll; do not reroll on a retry.
For an inspired choice, identify a real related paper outside the archive and
explain its relationship to an archive seed. Do not repeat a paper in the supplied
history. The user's priority is a rare, surprising idea: something that makes an
experienced programmer say "I did not know you could do that." Correctness,
historical importance, citation counts, and ease of implementation do not establish
interest. An old precursor to a familiar technique is not a distinctive discovery.

Reject incremental variants in crowded families. In particular, do not select
CRDTs, collaborative editing, operational transformation, or their precursors;
the WOOT run was explicitly disliked. Do not replace it with another member of
that family. Apply the same scrutiny to other crowded topics: a new acronym,
minor optimization, or routine combination of known techniques is insufficient.
A paper may share a broad field with others, but its central mechanism must offer
a substantially different way to think or do something. Age alone is neither a
positive nor a negative. Seek conceptual originality, not merely obscurity.

Before selecting, compare at least five plausible candidates under the fixed mode.
Use the archive to infer the user's taste; do not simply pick the nearest related
paper or the easiest demo. For each serious finalist, search for its closest
related work, read original sources, and explain why the idea is distinct from
those neighbors. Do not claim "nothing else like it" without evidence or confuse
a failed search with uniqueness. Choose only a candidate with a defensible wow
payoff that the proof of concept can actually demonstrate. If none qualifies,
finish blocked with the shortlist and reasons instead of lowering the bar.

The unattended brief starts with a controller request tag, then a JSON object.
Its `selection` object is the authoritative record. The controller has already
written it to SELECTION.json in the workspace; read it from there and never edit,
rewrite or regenerate that file. History entries (`prior_papers`) are paper
metadata objects: compare titles and source URLs to avoid repeats.
If `locked_paper` is present, implement that same paper; never replace it on a
retry. The controller owns scheduling, escalation and history. Do not edit them.
Use the existing coil-factory-papers pad for notes; do not create new run pads.
If pad is unavailable on the execution host, retain notes in README.md and
ISSUES.md; the controller's served library and journal provide remote status.

Use the language most suitable for the mechanism. Deliver a standalone index.html
that runs the implemented mechanism with editable inputs, examples, stepwise
traces, explanations and limitations. Browser inputs must change computed results;
do not substitute canned outputs, decorative animation, or a renamed generic demo.
Keep dependencies small and setup reproducible. Never weaken tests to pass.

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
ISSUES.md. Keep an honest paper-to-code mapping and distinguish implemented claims
from omitted claims. No deployment or public publishing is part of this run.

## Station: select
gate: python3 scripts/check_selection.py

Read the archive and research a shortlist under the brief's selection mode.
Write CANDIDATES.md documenting at least five candidates, original source links,
central ideas, and concrete rejection/selection reasons. For the chosen paper,
compare at least three closest related papers or systems using original sources;
record their links and the material difference. Favor a different mental model,
a surprising capability, or an unexpected mechanism with a compelling consequence.
Reject novelty pitches based solely on being early, niche, clever, fast, or useful.
A conventional demo does not become distinctive because the paper is obscure.

Write INTEREST.md with:
- The specific surprising claim, in plain language, without marketing adjectives.
- The conventional expectation it challenges and why an experienced programmer
  could find it surprising.
- The closest related work and substantive differences, grounded in sources.
- A concrete runnable experiment: what the reader predicts, what actually happens,
  the comparison baseline, and how this exposes the unusual idea.
- The strongest argument against selecting this paper, and an honest response.
- Why this candidate beats the other candidates for this user's taste.

Then choose the paper under the brief's selection mode. The moment you have
chosen, before downloading anything, write CHOSEN.json containing only the
chosen paper's title and url as strings; the library page shows it while you
work. If a later finding makes you change your choice, rewrite CHOSEN.json
first. Then download its source to source/paper.pdf, and extract readable text to source/paper.txt using
pdftotext or a PDF library. Read the relevant sections rather than the abstract
alone. Write PAPER.json with title, authors (array), year, url, mode, seed_title,
seed_url, relationship, interestingness, scope and omitted_claims (array).
Interestingness must state the actual unusual mechanism and observable payoff,
not generic praise. Include wow_claim, conventional_expectation, distinctive_idea,
closest_related_work (array of at least three objects with title, url, difference),
and wow_experiment (object with prediction, observed_result, baseline).
Write PLAN.md with precise acceptance experiments, edge cases, mechanism-to-paper
section mapping, intended interactive controls, and an outline of the article
following the eight parts above, naming which interactive figure supports
which part. Choose a scope substantial
enough to demonstrate the paper's contribution and small enough for a proof of
concept. Preserve exact source URLs and do not claim to implement a whole system
when the paper's central contribution can be isolated.

Create scripts/check_selection.py using Python's standard library. It must check
the required metadata types and nonempty values, a nonempty real PDF and readable
source text, the persisted selection mode, and the plan. Also validate CANDIDATES.md and
INTEREST.md exist and are nonempty, the extra interest metadata is well typed and
nonempty, and closest_related_work has at least three entries with required fields.
These structural checks do not prove interest; the independent judge must assess
its substance. It must exit nonzero on
invalid input. Run it before finishing. Check prior-paper history from SELECTION.json.

On a paid repair, `recovery_snapshot` may name a read-only snapshot of the previous
attempt. First copy its files into this new workspace, excluding .git, caches,
dependencies and SELECTION.json (the workspace already has the controller's).
Preserve previous-review/ as rejection evidence; a new judge must write fresh
JUDGMENT files. If PAPER.json and sources already form a valid selection,
retain that paper, sources and plan, and repair only their actual defects. Read the
brief's previous_findings and previous-review/ to address rejected work. If there
is no valid selection, complete selection under the same persisted roll.
Never copy factory operational state or modify the snapshot.

## Station: build
gate: sh scripts/verify.sh

Implement PLAN.md faithfully. Before writing the article, research the paper's
context and write CONTEXT.md as described under "The article". Read the paper's
introduction, related-work section and bibliography, and at least the main prior
work it responds to. Then write index.html to that standard. The article is the
deliverable; the implementation and controls serve it. Make the central wow
experiment a guided part of the story:
ask for a prediction, run the actual mechanism and baseline, reveal the surprising
consequence, and explain why it follows. Do not bury the distinctive contribution
behind routine controls, background history, or a generic implementation demo. Add tests of the actual mechanism, including an
independent reference or paper examples where possible, invariants, failure cases,
and at least one experiment that distinguishes the mechanism from a simpler
baseline. Implement index.html as a single offline HTML file with embedded assets
and code; preserve a readable implementation module if that helps testing.

Create scripts/verify.sh that stops on failure, validates selection, runs all
mechanism tests, and runs browser interaction tests in headless Chromium through
Playwright or another real browser driver. Browser tests must load the local HTML,
exercise controls and editable inputs, inspect computed changes, and fail on page
errors. Save a screenshot and a small-screen screenshot under evidence/.
Do not count file existence, screenshot creation, or HTML text checks as browser
interaction validation. Install test dependencies reproducibly if needed.
Before installing or launching browser tools, scripts/verify.sh must create
workspace-local .cache/xdg-config and .cache/xdg-cache directories and export
XDG_CONFIG_HOME="$PWD/.cache/xdg-config" and XDG_CACHE_HOME="$PWD/.cache/xdg-cache".
The Linux native gate has a read-only host home: Chromium crash-handler configuration
must be writable within the workspace. Keep isolation enabled; do not redirect
HOME or grant writes to the host home.
Write README.md with setup and exploration instructions, paper mapping, experiments,
and limitations. Write VALIDATION.md with commands and actual results. Invoke the
native gate by calling finish; follow the macOS execution rule below.

On macOS with the Codex workspace-write sandbox, do not launch Chromium or run
scripts/verify.sh locally: this environment is known to abort browser startup
in macOS application registration and produces user-visible crash reports.
Run selection, build, and mechanism checks individually, then call MCP finish.
The native site's gate must execute the unchanged full verification, including
real browser assertions, outside the coding sandbox before this station passes.
Do not retry the known forbidden launch or disable the coding sandbox.

## Station: judge
harness: codex
model: gpt-6.1-sol
gate: sh scripts/verify.sh && python3 -c 'import json; v=json.load(open("JUDGMENT.json")); assert v["verdict"] == "approved" and v["checks_run"] and v["evidence"] and v["interest_review"]["verdict"] == "pass" and v["interest_review"]["distinctiveness_evidence"] and v["interest_review"]["experiment_evidence"] and v["explanation_review"]["verdict"] == "pass" and all(v["explanation_review"]["cold_read"].get(k) for k in ("problem", "background", "prior_work", "insight", "mechanism", "costs", "legacy")) and v["explanation_review"]["evidence"]' && test -s CONTEXT.md
attempts: 1

Independently judge the paper choice as well as the finished implementation.
Read CANDIDATES.md and INTEREST.md; verify the claimed differences against the
paper and the cited nearest neighbors. Veto an incremental crowded-family paper,
a familiar technique's historical precursor, or an implementation whose experiment
does not reveal the claimed surprising contribution. CRDT/collaborative-editing/OT
papers are explicitly out of scope for this user's taste. A correct implementation
and polished tutorial cannot compensate for an uninteresting choice. Do not assume
that the selector's enthusiasm or the paper's reputation establishes a wow payoff.
State what is genuinely different, what conventional expectation it overturns,
and what observed experiment substantiates it. If those answers are weak, reject.
Do not request cosmetic prose to rescue a fundamentally unsuitable paper; say the
selection is unsuitable. A failed day is preferable to publishing another dull paper.

Read the original paper text,
PLAN.md, source code and tests. Run verification yourself as permitted by the macOS rule below and inspect the
browser screenshots; add independent probes where existing tests miss important
cases. Do not change implementation, tests, scope or acceptance criteria to make
the submission pass. Judge paper fidelity, correctness, interest, scope honesty,
and whether the tutorial teaches the actual mechanism to an unfamiliar reader.
Check prerequisite explanations, problem motivation, worked examples, progressive
lessons, predictions and explanations tied to real computed state, and exercises.
Reject a dashboard with unexplained jargon or unconnected prose.

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
and headings or prose that read like a specification. Verify guided
steps through successful native browser-test evidence and identify concrete
evidence of what each lesson teaches. Add independent probes where important cases
are absent; do not change tests or implementation to make the submission pass.

Write JUDGMENT.json with verdict (approved or rejected), checks_run (array of
commands and results), evidence (array of concrete findings), and corrections
(array of actionable repairs), and interest_review (object with verdict pass/fail,
distinctiveness_evidence and experiment_evidence as nonempty arrays of concrete
findings, plus strongest_objection and response), and explanation_review (object
with verdict pass/fail; cold_read, an object with nonempty problem, background,
prior_work, insight, mechanism, costs and legacy answers taken from the page alone;
evidence, a nonempty array of concrete findings quoting or locating the passages
that do or do not meet each part of the article standard; and gaps, an array of
missing or weak parts). Write JUDGMENT.md for the reader.
Approval requires a passing interest_review supported by actual source comparisons
and computed experiment outcomes, and a passing explanation_review whose cold read
the page alone supports; missing evidence is a rejection. Approve only when
all criteria pass. If rejected, preserve the rejection and finish blocked with
the evidence; a paid repair will continue from this workspace and receive a new
independent judgment. Provider failure or an inability to review is a failed
review, never an approval.

On macOS with the Codex workspace-write sandbox, do not launch Chromium or run
scripts/verify.sh locally. Run non-browser checks and inspect the browser evidence
from the successful native build gate. Call MCP finish to have this judge's native
gate independently rerun full browser verification outside the coding sandbox.
Approval cannot override a failed native browser run.

# Daily paper tutorials

`factories/papers.md` defines selection, implementation/tutorial, and independent
judgment. `scripts/paper_factory` supplies unattended orchestration over the native
factory API. Python 3.11+ and its standard library are sufficient for the controller.

Each day at **09:00 America/Indiana/Indianapolis**, a systemd heartbeat creates one
logical run. A fair coin chooses an archive paper or a paper inspired by the archive
at <https://jimmyhmiller.com/readings>. Papers must concern programming languages,
compilers, runtimes or databases and have an interesting implementable mechanism.
The selection is persisted before any model work and reused throughout retries.

The result is a tested proof of concept and a standalone offline `index.html`
article for a reader with no background in the field (see below). A dashboard,
or a demo with a few paragraphs around it, fails review.

## Selection taste: distinctive ideas with a demonstrable wow payoff

Selection now researches at least five candidates and documents why the winner
beats them. `INTEREST.md` states the surprising claim, conventional expectation,
source-grounded comparisons with at least three nearest neighbors, strongest
objection, and a runnable experiment that exposes the unusual contribution.
`PAPER.json` records the same core criteria. Obscurity, age, citations and speed
alone do not qualify. CRDTs, collaborative editing, operational transformation,
and their historical precursors are excluded after explicit user feedback.

The independent judge has a paper-choice veto. Native acceptance additionally
requires `interest_review.verdict == "pass"` with distinctiveness and experiment
evidence. A correct, polished implementation of a dull idea is rejected. If no
candidate meets the bar, the selector blocks with evidence rather than publishing
a weak choice. Once selected, existing repair rules preserve the paper; a bad
selection can therefore exhaust the day's budget without publishing a tutorial.
The archive/inspired coin and daily schedule remain unchanged.

## The article: a blog post, not a demo page

`index.html` is written like an engineering blog post that reaches the Hacker News
front page, for a programmer who has never met the field. It opens with the
problem in the reader's terms, teaches the missing background, walks through the
obvious approaches and the prior work the paper answers and shows where they
fail, then introduces the paper's insight, builds the mechanism up step by step
with interactive figures running the real implementation, covers costs, and
traces the idea's legacy. The build station first writes `CONTEXT.md`, sourced
research notes on the domain, prior work, and what came after.

The judge does a cold read: from the page alone it must answer what the problem
was and who had it, what background was needed, what came before and why it fell
short, the insight, why the mechanism works, its costs, and its legacy. The native
judge gate requires `explanation_review.verdict == "pass"` with all seven
`cold_read` answers, evidence, and a nonempty `CONTEXT.md`.

`factories/papers-tutorial.md` rewrites the article for an already accepted paper
using the same standard, which it carries verbatim. It preserves the paper
metadata, sources, mechanism implementation and mechanism tests by hash, embeds the
preserved module rather than a copy, and gets a fresh independent judgment. Run it
with the accepted workspace (a clone of it) as `--project`.

## Models and recovery

Selection and implementation first use the top-ranked free model (see *Free models*
below). If none qualifies, the run starts on the paid model and says why. Judgment always uses
an independent Codex `gpt-6.1-sol` conversation. Provider failures, failed native gates,
blocked work, judge rejection, and bounded lack of progress trigger automatic paid
Codex repair, with at most **two paid attempts** per day. Codex uses the existing
account login; this is the paid/subscription fallback, not a new API key or billing
account. No result is published after the repair budget is exhausted.

The controller cancels through the native API and waits for the worker to exit
before taking a source snapshot or starting the fallback. Snapshots include
unfinished source and exclude Git metadata, dependencies and caches. An already
valid paper selection is locked. Earlier judgments are retained under
`previous-review/`, and the paid result receives a fresh independent judgment.

Defaults in `config.json`:

| Limit | Value |
| --- | --- |
| Free agent inactivity | 15 minutes |
| Paid agent/judge inactivity | 15 minutes |
| Native gate inactivity | 30 minutes |
| Free whole-attempt duration | 90 minutes |
| Paid whole-attempt duration | 3 hours |
| Paid attempts | 2 |

Agent/tool/station progress resets inactivity, including reasoning and text the model
is still streaming (`agent.streaming`). Polling and token usage alone do not. Free
models get the same 15 minutes as paid ones because OpenCode Zen delivers a tool call
whole: while a model writes a large file the connection is silent (a 21 KB `write`
arrived after 35 s of nothing), so a shorter limit kills models that are working.
Operator pauses are respected; an external cancellation ends that daily job rather
than starting a repair. A cancellation that leaves a worker alive blocks further
daily work, so the controller cannot accumulate overlapping model processes.

## Free models

`free_model: "auto"` starts each free attempt on the top-ranked model of the
free-model registry (`paper_factory.models`), recorded in the attempt as
`model`. The registry keeps an up-to-date list of free models and decides
which are good enough, on its own heartbeat (`paper-models.timer`, every
minute) and its own factory site (`paper-models-site.service`, port 7798), so
its runs never hold up a paper run. Every model and the reason it is in or out
is on `/papers/models.html`.

- **Discovery** (hourly): refresh OpenCode's catalogue and list every model at
  zero input and output cost with tool calls and at least 128K context.
- **Denied** models (`models.denied`, a person's decision) are listed with the
  reason and never tested or used. Big Pickle is denied: an old
  stealth model that stalled at the same step in both daily runs.
- **Test** (each model daily, new models first): the only test, and it goes
  through the same harness paper runs use, so whatever a model would do in a
  paper run happens here and is recorded in the run's own words: refusing the
  harness, being retired while still listed, stalling, or slow turns. Nothing
  in the factory knows about any particular model. The run is
  `factories/model-probe.md` on tasks generated from a fresh seed
  (`probe_tasks.py`): a ~50K-token paper archive with questions that combine
  far-apart entries, a program whose output must be worked out because running
  it would take far too long, and an invented algorithm given only in prose.
  Seven answers are graded against a key that never enters the workspace; no
  model-written code runs outside the sandbox. A model is out if it never
  answers, takes a median of more than 30 seconds per turn, makes no progress
  for 3 minutes, takes more than 15 minutes in all, or gets fewer than 5 right.
- **Research** (once per new model that answered its test): a native run of
  `factories/model-research.md` on the paid model finds what the model is, its
  parameter counts, a published independent capability score and its data
  terms, with sources, checked by `check_research.py`. The 2026-10-09 research
  was imported from `research-2026-10-09.json`.
- **Real runs count**: a free attempt that fails in a paper run for any reason
  (stall, time limit, provider error, failed gate) puts its model out for 3 days.
- **Rank**: models that pass everything, by test score, then published score,
  then test time. Measured results decide; research only breaks ties. A ranking
  older than 2 hours is not trusted, and the paper run starts on the paid model.

```sh
PYTHONPATH="$HOME/.local/share/paper-factory/runtime" python3 -m paper_factory.models \
  --config "$HOME/.local/share/paper-factory/config.json" --import-research FILE.json
```

## Durable state and acceptance

Daily state is atomically written and fsynced. Controller locking prevents
overlapping heartbeats. Each native request has a persisted unique tag; after an
interrupted or ambiguous create request the controller finds the native `run.json`
receipt before creating another run. It never rewrites native factory journals.

Paper history includes accepted papers and valid selections from failed days.
Normalized titles and source URLs prevent repeat selections. Mode, coin roll,
date, archive URL and history must match the persisted selection at acceptance.
The controller additionally requires actual paper sources, all native stations
passed, a fresh approved judgment with evidence, and committed HTML/artifacts.
These metadata checks supplement the native mechanism and real-browser gates;
they do not replace them.

Accepted artifacts are exported atomically to `/srv/paper-factory/YYYY-MM-DD/`
(`YYYY-MM-DD-N/` for a requested extra paper).
Each directory contains the tutorial, source, implementation, tests, judgment,
run receipt, checksums, and `bundle.zip`. `/srv/paper-factory/index.html`
is the tutorial library. Failed/in-progress days appear in the library without
links to unapproved work. Transcripts remain under the dedicated site's `runs/`.
Server status is available in the library and systemd journal. Deployment notes live in the project's `coil-factory-papers` pad.

## Server deployment

The production host is **computer.jimmyhmiller.com** (hostname `metaphysics`).
The runtime, native binary, configuration and durable state live under
`~/.local/share/paper-factory/`. The site's API listens locally on port 7799.
User systemd lingering keeps the services running without an SSH session.
The Mac daily LaunchAgent is disabled to avoid duplicate daily jobs.

`paper_factory.install_linux` installs the dedicated site and optional existing
Signal daemon. It leaves the controller disabled until hosting and a live fallback
run have been verified. The runtime must first contain `paper_factory` and the
recipe under `runtime/factories/papers.md`; the native binary must target Linux.

```sh
PYTHONPATH="$HOME/.local/share/paper-factory/runtime" python3 -m paper_factory.install_linux \
  --binary /absolute/path/to/linux/factory \
  --signal-executable /absolute/path/to/signal-cli \
  --signal-env /absolute/path/to/existing/signal/.env
```

After verification, enable `config.json` and start
`systemctl --user enable --now paper-factory-daily.timer paper-factory-requests.path`.
The timer invokes a bounded heartbeat (about 70 ms) every 5 seconds; the controller starts a new daily job after
09:00 Indianapolis time, with DST handled by the named timezone. After downtime,
it catches up for the current day without backfilling every missed day.
The accepted WOOT tutorial for 2026-10-07 was migrated into history.

Gatekeeper's authenticated `/papers` static route serves `/srv/paper-factory`:
<https://computer.jimmyhmiller.com/papers/>. Accepted tutorials and ZIP downloads
are available there; unauthenticated requests require Gatekeeper login.

## Another paper today

The library page has an **Another paper today** button. It asks for one more
paper beyond the scheduled one; earlier papers, including today's, are kept.

The button POSTs to `/papers/request`, Gatekeeper's `paper-requests` function
(in the `gatekeeper-functions` repository, `funcs/paper-requests`). The route is
longer than `/papers`, so it wins over the static library for that path only.
The function accepts a request only from a passkey browser session, as JSON with
`X-Paper-Request: 1`; device and bootstrap tokens, which agents can reach, can
never spend model time this way. It writes each request atomically as
`/var/lib/paper-requests/<id>.json` and never deletes one. That directory is a
second gatekeeper `StateDirectory` (drop-in `50-paper-requests.conf`), because
the passkey store keeps `/var/lib/gatekeeper` at `0700`.

The page adds the requested row the moment the function answers. A user
systemd path unit, `paper-factory-requests.path`, starts a heartbeat as soon as
the request file is renamed into place, so the job normally starts within a
second or two; the 5-second timer covers anything the path unit misses.
On each heartbeat the controller reads that directory (`request_dir`) and
queues one job per request it has not seen, recording the request in the job.
Requested jobs are `DATE-2`, `DATE-3`, ... by the local date of the request;
the scheduled job keeps the bare date, and an id whose export directory already
exists is skipped. Queued jobs show as `queued` in the library and start one at
a time when nothing is running, oldest first, after any scheduled job that is
due. Each gets its own coin roll, the full paper history (so it cannot repeat
today's paper), and the same free-then-paid repair budget. Setting `enabled`
false holds queued requests as well as new days. A malformed request or an
unexpected file in the directory is a heartbeat error, not something skipped.

`request_dir` and `request_url` are configured together; without them the
page has no button.

## Live library

While any job is queued or running, the library page re-reads itself every
2 seconds and replaces its table in place; the controller rewrites the page on
every heartbeat. A running row shows its stage (choosing a paper, implementing
and writing, independent review, and whether a paid model is repairing it).

The paper's title appears as soon as the selector decides. The recipe has it
write `CHOSEN.json` (`title`, `url`) the moment it chooses, before downloading
the source; the row shows that title marked "chosen, not yet checked". It is
for display only: it never enters paper history, and the reserved paper still
comes from the validated `PAPER.json` once the select gate passes.

## Signal readiness notifications

The server's existing Signal configuration supplies the account, UNIX socket and
recipient. Exactly one allowed sender is required as the notification recipient.
No phone numbers or credentials are stored in tutorial exports. The daemon uses
the existing account store and restrictive socket permissions.

An accepted, published tutorial enters a durable notification outbox. The message
contains its paper title, date and Gatekeeper link. Successful Signal RPC delivery
acknowledgment marks it sent; failures retry with exponential delay up to one hour.
Delivery is at least once: an interrupted acknowledgment could cause a duplicate.
Notification failures never discard a successful tutorial.

At migration, the existing Signal account was **unregistered**. Notifications are
pending until the account is registered or linked from Signal on a phone. An
available socket alone does not establish successful delivery.

## Status and controls

```sh
systemctl --user status paper-factory-site.service paper-factory-daily.timer
journalctl --user -u paper-factory-daily.service -n 50

PYTHONPATH="$HOME/.local/share/paper-factory/runtime" python3 -m paper_factory.controller \
  --config "$HOME/.local/share/paper-factory/config.json" --status

FACTORY_HOME="$HOME/.local/share/paper-factory/site" \
  "$HOME/.local/share/paper-factory/bin/factory" ps --all
```

An inactive oneshot heartbeat between ticks is normal. Its last exit status
should be zero. State lives under `~/.local/share/paper-factory/state/`.
Set `enabled` false to prevent new days; existing runs continue being monitored.
Native `factory pause RUN`, `resume RUN` and `stop RUN` preserve operator control.

For updates, disable new days and wait for active work to finish. Stop the daily
timer and site service before replacing the runtime/binary. Preserve state and
site journals. Reinstallation deliberately leaves scheduling disabled until the
updated deployment is verified again.

The separate `paper_factory.install` module remains an optional Mac installer;
it is not the production scheduler.

## Browser execution on Linux

The native Bubblewrap gate exposes the host home read-only. Verification scripts
must create writable configuration/cache directories in their workspace and set
`XDG_CONFIG_HOME` and `XDG_CACHE_HOME` to their absolute paths. Chromium's crash
handler otherwise aborts before the browser assertions. This is environment setup;
the coding sandbox and native browser assertions remain enabled.

## Browser execution on macOS

Codex workspace-write sandboxed Chrome launches abort in macOS application
registration before page load. Workers must never attempt these known failing
launches. They run non-browser checks locally and call MCP `factory.finish`.
The native host gate runs the full unchanged browser tests outside that coding
sandbox. Gate failures still block acceptance. No Chrome crash is considered a
successful browser test, and the coding sandbox is not disabled.

## Verification

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests/paper_factory -v
factory check factories/papers.md
```

The tests cover local-time scheduling and wake catch-up, interrupted creation,
persisted rolls, history and selection tampering, provider errors, stalls,
cancellation ordering, operator controls, bounded repairs, safe snapshots, native
station/judgment acceptance, and idempotent exports.

An optional live smoke test induces a free-station failure and automatically
falls back to a paid worker that copies the previously accepted WOOT tutorial,
then obtains a fresh independent judgment and passes its real native browser
gate. Its history/exports are separate from the daily schedule. It revalidates an
existing implementation; it does not claim a new paper implementation.

```sh
PYTHONPATH=scripts python3 -m paper_factory.smoke \
  --config "$HOME/.local/share/paper-factory/config.json" \
  --source /absolute/path/to/accepted/native/workspace \
  --root build/paper-scheduler-smoke
```

Deployment verification on October 7, 2026: 35 controller/Signal tests passed on
Mac and Linux. Native Linux run `paper-scheduler-smoke-dfc6` passed automatic
free-to-paid fallback, fresh independent judgment, full browser assertions and
atomic export. The server timer is enabled; its first heartbeat exited zero.
The Mac daily/site LaunchAgents are unloaded. Signal delivery remains pending
phone linking; no successful notification has yet been acknowledged.

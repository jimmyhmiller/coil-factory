# The coil-bugs factory

Every day, open bugs in the `coil-bugs` pad are reproduced and fixed by Codex
agents on computer.jimmyhmiller.com. A fixed bug becomes a pull request
against `jimmyhmiller/coil`. Every outcome is written back into the pad. The
pad lives on the Mac, which may be asleep for days, so the work is split:

```
Mac (launchd, every 15 min, on wake)          computer.jimmyhmiller.com
┌──────────────────────────────┐   HTTPS   ┌─────────────────────────────────────┐
│ coil-bugs mac                │ ────────▶ │ Gatekeeper /coil-bugs (Rust function)│
│  1. write notes into the pad │           │   /var/lib/coil-bugs/snapshots       │
│  2. first beat of the day:   │ ◀──────── │   /srv/coil-bugs/notes               │
│     post the coil-bugs pad   │           └──────────────┬──────────────────────┘
└──────────────────────────────┘                          │ files
                                             ┌────────────▼─────────────────────┐
                                             │ coil-bugs controller (10 s timer) │
                                             │  triage run → one bug run at a    │
                                             │  time → PR / note                 │
                                             │ native factory site :7797 (codex) │
                                             └───────────────────────────────────┘
```

Nothing connects the two machines except the Gatekeeper route. The Mac never
holds a session open, and the server never needs the Mac to be awake.

All of it is Coil, in `apps/coil-bugs` (one program, `coil-bugs`), except
the Gatekeeper function. Gatekeeper functions are Rust cdylibs
(`gatekeeper-functions/funcs/coil-bugs`).

## What happens to a bug

1. **Snapshot.** On its first heartbeat of each local day, the Mac posts
   every live cell of the pad. `launchd` runs the heartbeat every 15 minutes
   while the Mac is awake, at login, and as soon as it wakes, so a day the
   Mac was asleep at any given time is still covered.
2. **Triage** (`factories/coil-bugs/triage.md`). When a snapshot's cells
   differ from the last one triaged (the factory's own notes don't count), a
   Codex agent sorts the cells into bug reports. Each one is `open`, `fixed`
   or `not_a_bug`. A `fixed` entry must quote words from its own cells that
   say so; its gate (`coil-bugs gate triage`) checks the quote, and that
   every cell is placed exactly once. The controller repeats that check. Open
   bugs nobody has attempted are queued in pad order.
3. **Base compiler.** Before each bug, `main` is fetched. When it has moved,
   `coil-bugs build-base` builds a compiler from it using the committed seeds
   (`rebootstrap-linux.sh`), in its own systemd unit (about 5 minutes, 5 GB).
   It then installs that compiler with its library at a prefix of its own,
   `state/compilers/<sha>/bin/coil` (`dev.py install --dest … --no-warm-unit`).
   The user's `coil` is never touched. If `main` doesn't build, the last good
   base is used.
4. **One bug at a time** (`factories/coil-bugs/fix.md`):
   - *reproduce*: the agent writes `.coil-bug/REPRO.json` with a verdict and
     a `check` command. The gate runs the check against the base compiler.
     It must fail for `reproduces`, where a regression test must also be
     added, and pass for `does_not_reproduce`. `cannot_verify` must say what
     this machine lacks. A reviewer rejects a check that tests something
     other than the report.
   - *fix*: the gate builds a candidate compiler from the workspace and
     requires the check to pass with it, plus `dev.py test modernize-fast`.
     The regression test and `REPRO.json` must be untouched since the
     reproduce station, and `.coil-bug/FIX.md` must explain the fix. A
     reviewer rejects symptom fixes, stubs, swallowed errors and weakened
     tests.
5. **Outcome.** The controller, never an agent, squashes the branch (minus
   `.coil-bug/`), pushes `coil-bugs/<bug>`, and opens the pull request. It
   then leaves a note that the Mac writes into coil-bugs as the cell
   `coil-bugs-factory-<bug cell id>`. The note is one of: the pull request,
   *unable to reproduce* (with the passing check), *could not verify*
   (with what is missing), or *did not finish* (with why).

Bugs an agent finds on the way go in `.coil-bug/OTHER-BUGS.md`. The
controller files them in coil-bugs as `coil-bugs-factory-found-<bug>`. Unlike
the outcome notes, triage treats that cell as an ordinary report, so the
factory reproduces it like any other report instead of taking the agent's
word for it.

A bug is attempted once. Its note in the pad also records that, so lost
controller state can't cause a second attempt. Nothing is removed from the
pad: removing reports is for a person to decide.

## What Coil will not do

`factories/coil-bugs/SCOPE.md` holds Jimmy's rules for what is out of Coil's
scope. Rule 1: no live coding in the core. The JIT never updates types,
migrates values, or accepts a changed signature; live coding is an add-on,
built as a metaprogram. Jimmy edits that file; agents read it.

- **Triage** judges every open bug `in` or `out` against those rules. An `out`
  must name a rule that exists and quote the report's own words; its gate
  checks both. When unsure, triage says `out`, so that Jimmy decides.
- **An out-of-scope bug is never worked on.** It waits as "awaiting Jimmy's
  scope decision" while the controller asks Jimmy on his approvals page
  (https://computer.jimmyhmiller.com/approvals): one proposal, with the report
  and SCOPE.md attached. Approved means a note in coil-bugs marks it out of
  scope. Rejected means it joins the queue, and Jimmy's comment goes into the
  agent's brief.
- **The fix agent and its reviewer read the same rules.** A fix that makes the
  core do what a rule rules out is rejected however well it works.
- The server posts to the approvals page with its own Gatekeeper device token
  (`~/.config/gatekeeper/token`, from `gatekeeper-login`). Without one, scope
  questions wait, and the bugs are still never worked on.

## When it stops

- **Agent-level failures** consume the bug and are noted: a stall (45
  minutes without progress, 2 hours inside a gate), the 10-hour limit, an
  agent that stops to ask (nobody answers here; its question goes into the
  note), or a result whose re-checks fail.
- **Infrastructure failures** halt the factory and keep the bug queued: a run
  that ends `failed` or `lost`, or a run the site refuses (for example a
  model Codex won't serve). Fix the cause, then run
  `coil-bugs controller --config ~/.local/share/coil-bugs/config.json --resume`.
  The status pad shows the halt.

## Where things are

| | Mac | Server |
|---|---|---|
| program | `~/.local/share/coil-bugs-factory/bin/coil-bugs` | `~/.local/share/coil-bugs/bin/coil-bugs` |
| config | `~/.local/share/coil-bugs-factory/config.json` | `~/.local/share/coil-bugs/config.json` |
| state | `…/coil-bugs-factory/state/state.json` | `…/coil-bugs/state/` (`state.json`, `status.json`, `last-error.json`) |
| logs | `…/coil-bugs-factory/logs/heartbeat.log` | `journalctl --user -u coil-bugs.service` |
| scheduler | `~/Library/LaunchAgents/com.jimmyhmiller.coil-bugs-factory.plist` | `coil-bugs.timer`, `coil-bugs-snapshots.path`, `coil-bugs-site.service` |

- Native runs: `FACTORY_HOME=~/.local/share/coil-bugs/site ~/.local/share/coil-bugs/bin/factory ps`
  (and `show`, `watch`, `transcript`, `stop`).
- Live status: the `coil-factory-coil-bugs` pad, which the factory writes
  whenever the status changes.
- Models: `model` and `review_model` in the server's `config.json`, chosen at
  install. There is no default. The site uses the Codex harness with network
  access, so agents can clone the projects a report came from.

## Building and installing

```sh
# Mac
cd apps/coil-bugs && coil test && coil build
./build/release/coil-bugs install-mac

# server (x86_64 Linux); --target is explicit, see the coil-bugs pad report
# "Without --target, primitive/target-arch is aarch64 on an x86_64 Linux host"
coil build --target x86_64-unknown-linux-gnu                   # at the repository root: factory
(cd apps/coil-bugs && coil build --target x86_64-unknown-linux-gnu)
apps/coil-bugs/build/release/coil-bugs install-linux --factory build/release/factory \
  --recipes factories/coil-bugs --model gpt-6.1-sol --review-model gpt-6.1-sol --enable
```

On the Mac, `/etc/hosts` maps computer.jimmyhmiller.com to its LAN address,
so macOS treats the heartbeat's requests as Local Network access. The
installed binary is signed as `com.jimmyhmiller.coil-bugs`. It needs Local
Network permission (System Settings › Privacy & Security › Local Network),
granted again after a reinstall changes the binary. Until then every
heartbeat fails with `curl 7: Could not connect to server` in
`logs/heartbeat.log`.

The Gatekeeper side is set up once, with sudo: the function's library in
`/etc/gatekeeper/funcs`, its route in `/etc/gatekeeper/gatekeeper.toml`, the
`StateDirectory=coil-bugs` drop-in, and `/srv/coil-bugs/notes` owned by the
controller's user (see `gatekeeper-functions/funcs/coil-bugs/README.md`).

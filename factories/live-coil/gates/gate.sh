#!/bin/sh
# The gate for live-coil-2 work. Run in the workspace (a worktree of
# live-coil-2). It lives outside the workspace so no agent can change what it
# checks. Every check runs and reports; any failure fails the gate.
#
#   locked     RULES.md, SPEC.md, spec/ and AGENTS.md are exactly as Jimmy left them
#   tests      tests that exist on main are unchanged (tests are add-only)
#   banned     src/ uses none of the mechanisms RULES.md rules out
#   suite      `coil test` passes
#   probes     every probe named in TARGET passes, and every probe that passes
#              on main still passes (nothing that worked may stop working)
#
# Usage: gate.sh               (from the workspace)
#        gate.sh --invariants  (only locked, tests and banned: for stations
#                               that plan or write tests ahead of the code)
#        gate.sh --new-tests   (--invariants, and the branch adds at least
#                               one test file under tests/)
#        gate.sh --probes      (only print every probe's result)

GATES="$(cd "$(dirname "$0")" && pwd)"
WS="$(pwd)"
FAILED=0

say() { printf '%s\n' "$*"; }
fail() { say "FAIL $1"; FAILED=1; }
pass() { say "PASS $1"; }

if [ "$1" = "--probes" ]; then
  python3 "$GATES/probe.py" "$WS"
  exit $?
fi

MODE="${1:-full}"

BASE="$(git merge-base HEAD main 2>/dev/null)"
if [ -z "$BASE" ]; then
  say "FAIL setup: cannot find where this branch left main (git merge-base HEAD main)"
  exit 1
fi

# --- locked -----------------------------------------------------------------
if (cd "$WS" && shasum -a 256 -c "$GATES/locked.sha256" >/tmp/live-gate-locked.$$ 2>&1); then
  pass "locked: RULES.md, SPEC.md, spec/, AGENTS.md unchanged"
else
  fail "locked: these files are Jimmy's and must not change:"
  grep -v ': OK$' /tmp/live-gate-locked.$$ | sed 's/^/  /'
fi
rm -f /tmp/live-gate-locked.$$

# --- tests are add-only -----------------------------------------------------
CHANGED="$(git diff --name-status "$BASE" -- tests matrix | grep -v '^A' )"
if [ -z "$CHANGED" ]; then
  pass "tests: no existing test was changed or deleted"
else
  fail "tests: existing tests are add-only (RULES.md §9); these changed:"
  printf '%s\n' "$CHANGED" | sed 's/^/  /'
fi

# --- banned mechanisms ------------------------------------------------------
BANNED_HITS=""
if [ -d "$WS/src" ]; then
  while IFS='	' read -r pattern why; do
    case "$pattern" in ''|'#'*) continue ;; esac
    hits="$(grep -rnE -- "$pattern" "$WS/src" 2>/dev/null)"
    if [ -n "$hits" ]; then
      BANNED_HITS="$BANNED_HITS
  $why
$(printf '%s\n' "$hits" | sed "s|$WS/||; s/^/    /")"
    fi
  done < "$GATES/banned.txt"
fi
if [ -z "$BANNED_HITS" ]; then
  pass "banned: src/ uses no ruled-out mechanism"
else
  fail "banned: src/ uses mechanisms RULES.md rules out:$BANNED_HITS"
fi

if [ "$MODE" = "--invariants" ] || [ "$MODE" = "--new-tests" ]; then
  if [ "$MODE" = "--new-tests" ]; then
    ADDED="$( (git diff --name-only --diff-filter=A "$BASE" -- tests; git ls-files --others --exclude-standard -- tests) | grep '_test\.coil$' )"
    if [ -n "$ADDED" ]; then
      pass "new tests: $(printf '%s' "$ADDED" | tr '\n' ' ')"
    else
      fail "new tests: this branch adds no tests/*_test.coil file"
    fi
  fi
  if [ "$FAILED" -eq 0 ]; then say "gate passed"; else say "gate failed"; fi
  exit "$FAILED"
fi

# --- the test suite ---------------------------------------------------------
if out="$(cd "$WS" && coil test 2>&1)"; then
  pass "suite: coil test"
else
  fail "suite: coil test failed:"
  printf '%s\n' "$out" | tail -40 | sed 's/^/  /'
fi

# --- probes -----------------------------------------------------------------
if [ ! -f "$WS/TARGET" ]; then
  fail "probes: there is no TARGET file. List the probes this brief must make pass, one per line (see \`gate.sh --probes\` for the names), or the single word none."
  TARGETS=""
else
  TARGETS="$(grep -v '^[[:space:]]*#' "$WS/TARGET" | tr -s ' \t' '\n' | grep -v '^$' | grep -vx none)"
fi

CACHE="${TMPDIR:-/tmp}/live-coil-gate-cache"
mkdir -p "$CACHE"
BASE_RESULTS="$CACHE/$BASE.json"
if [ ! -f "$BASE_RESULTS" ]; then
  BASE_TREE="$(mktemp -d)"
  git archive "$BASE" | tar -x -C "$BASE_TREE"
  PROBE_FORMAT=--json python3 "$GATES/probe.py" "$BASE_TREE" > "$BASE_RESULTS.tmp" 2>/dev/null
  mv "$BASE_RESULTS.tmp" "$BASE_RESULTS"
  rm -rf "$BASE_TREE"
fi
NOW_RESULTS="$(mktemp)"
PROBE_FORMAT=--json python3 "$GATES/probe.py" "$WS" > "$NOW_RESULTS" 2>/dev/null

python3 - "$BASE_RESULTS" "$NOW_RESULTS" "$TARGETS" <<'EOF'
import json, sys
def load(path):
    try:
        return json.load(open(path))
    except Exception:
        return {}
base, now = load(sys.argv[1]), load(sys.argv[2])
targets = [t for t in sys.argv[3].split() if t]
bad = False
unknown = [t for t in targets if t not in now]
if unknown:
    print("FAIL probes: TARGET names unknown probes: %s (the probes are %s)" % (", ".join(unknown), ", ".join(sorted(now))))
    bad = True
for name in sorted(now):
    r = now[name]
    tag = "PASS" if r["ok"] else "FAIL"
    required = name in targets or base.get(name, {}).get("ok", False)
    why = "" if r["ok"] else ": " + r["why"].replace("\n", "\n      ")
    if r["ok"]:
        print("PASS probe %s%s" % (name, " (targeted)" if name in targets else ""))
    elif required:
        reason = "targeted by TARGET" if name in targets else "passes on main, so it must keep passing"
        print("FAIL probe %s (%s)%s" % (name, reason, why))
        bad = True
    else:
        print("info probe %s does not pass yet (not targeted)%s" % (name, why))
sys.exit(1 if bad else 0)
EOF
[ $? -eq 0 ] || FAILED=1
rm -f "$NOW_RESULTS"

if [ "$FAILED" -eq 0 ]; then
  say "gate passed"
else
  say "gate failed"
fi
exit "$FAILED"

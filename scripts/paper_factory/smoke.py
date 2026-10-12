"""Live fallback test; revalidates the accepted tutorial, separate from daily history."""
import argparse
import datetime as dt
import json
import logging
from pathlib import Path
import shlex
import shutil
import subprocess
import time

from .controller import Controller, atomic_json, read_json


def provision(args):
    source, target = args.source.resolve(), args.target.resolve()
    paths = subprocess.check_output(["git", "ls-files", "-z"], cwd=source).decode().split("\0")
    for name in filter(None, paths):
        if name in {"JUDGMENT.json", "JUDGMENT.md"}:
            continue
        p = source / name
        if p.is_symlink() or not p.is_file() or not p.resolve().is_relative_to(source):
            raise ValueError("nonregular source artifact")
        out = target / name
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, out)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--root", type=Path)
    parser.add_argument("--provision", action="store_true")
    parser.add_argument("--target", type=Path)
    args = parser.parse_args()
    if args.provision:
        provision(args)
        return
    if not args.config or not args.root:
        parser.error("--config and --root required")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    root = args.root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    config = read_json(args.config)
    config.update(state_dir=str(root / "state"), output_dir=str(root / "output"),
                  enabled=False, recipe=str(root / "recipe.md"), pad=None,
                  signal=None, free_idle_seconds=60, free_max_seconds=180, paid_attempts=1)
    source = args.source.resolve()
    helper = Path(__file__).resolve()
    command = "PYTHONPATH=" + shlex.quote(str(helper.parents[1])) + " python3 -m paper_factory.smoke --provision --source " + shlex.quote(str(source)) + " --target ."
    recipe = '''---
name: paper-scheduler-smoke
description: Controlled free failure followed by paid verification of accepted WOOT.
harness: opencode
model: opencode/big-pickle
tools: files, shell
attempts: 1
reviewers: none
---

This is a scheduler transport smoke test, not a new paper implementation. Brief
is a request tag followed by JSON. Use coil-factory-papers pad, no new pads.
Completion is MCP factory.finish (mcp__factory__finish or factory_finish), not a
shell executable. Discover the deferred MCP tool. Do not run browser commands
inside Codex's macOS sandbox: the native gate runs the complete real browser test.

## Station: select
gate: python3 scripts/check_selection.py

If implementation_tier is free, immediately call finish outcome blocked, summary
"Induced free failure for scheduler smoke test". Do not write files or attempt to
repair this intentionally induced failure. This tests automatic fallback.
If implementation_tier is paid, provision the already accepted implementation by
running the following exact command, then run the selection and preservation
checks and call finish outcome done. Do not alter engine, tests, UI or sources.

COMMAND_PLACEHOLDER

The copied SELECTION.json is the exact original selection for this test; preserve
it. No old judgment is copied. A separate independent judge must review again.

## Station: judge
harness: codex
model: gpt-5.5
attempts: 1
gate: mkdir -p .cache/xdg-config .cache/xdg-cache && XDG_CONFIG_HOME="$PWD/.cache/xdg-config" XDG_CACHE_HOME="$PWD/.cache/xdg-cache" sh scripts/verify.sh && python3 -c 'import json; v=json.load(open("JUDGMENT.json")); assert v["verdict"] == "approved" and v["checks_run"] and v["evidence"]'

Independently review the accepted WOOT implementation and tutorial from the paper
sources, code, tests and lessons. Run non-browser selection, preservation and
mechanism checks; do not change implementation/tests. Write a fresh JUDGMENT.json
with verdict approved/rejected, nonempty checks_run/evidence, corrections, plus
JUDGMENT.md. Do not assume the old trial's approval. Browser verification is the
native finish gate only: do not locally run verify.sh or test:browser inside the
macOS Codex sandbox. Call MCP finish; acceptance requires a successful real native
browser run. No public deployment.
'''.replace("COMMAND_PLACEHOLDER", command)
    Path(config["recipe"]).write_text(recipe)
    atomic_json(root / "config.json", config)
    state_path = Path(config["state_dir"]) / "state.json"
    if not state_path.exists():
        selection = read_json(source / "SELECTION.json")
        day = selection["date"]
        atomic_json(state_path, {"version": 1, "seeds": [], "jobs": {day: {
            "date": day, "status": "running", "selection": selection,
            "created": dt.datetime.now(dt.timezone.utc).timestamp(),
            "attempts": [], "events": []}}})
    deadline = time.monotonic() + 1200
    while time.monotonic() < deadline:
        controller = Controller(config)
        controller.tick()
        job = next(iter(controller.state["jobs"].values()))
        attempt = job["attempts"][-1]
        print(json.dumps({"status": job["status"], "tier": attempt["tier"],
                          "run_id": attempt.get("run_id"), "phase": attempt["status"]}), flush=True)
        if job["status"] != "running":
            if job["status"] != "succeeded" or len(job["attempts"]) != 2:
                raise RuntimeError("live fallback smoke test failed")
            print("Live smoke OK: free failure -> automatic paid fallback -> fresh independent judgment -> real browser gate -> atomic export", flush=True)
            return
        time.sleep(10)
    raise TimeoutError("live smoke test still active; native journals/state retained")


if __name__ == "__main__":
    main()

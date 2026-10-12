"""Install two per-user launchd services and a stable paper-factory runtime."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
from zoneinfo import ZoneInfo

from .controller import atomic_json, export_workspace, metadata, read_json, validate_config

SITE_LABEL = "com.jimmyhmiller.paper-factory-site"
TICK_LABEL = "com.jimmyhmiller.paper-factory-daily"


def run(*args, check=True, **kwargs):
    return subprocess.run(args, check=check, text=True, capture_output=True, **kwargs)


def required_program(name):
    found = shutil.which(name)
    if not found:
        raise RuntimeError(f"Required executable not found: {name}")
    return found


def plist(label, argv, environment, root, **extra):
    return {"Label": label, "ProgramArguments": [str(a) for a in argv],
            "EnvironmentVariables": environment, "WorkingDirectory": str(root),
            "StandardOutPath": str(root / "logs" / (label + ".out.log")),
            "StandardErrorPath": str(root / "logs" / (label + ".err.log")),
            "ProcessType": "Background", **extra}


def seed_trial(config, workspace, run_id):
    """Import the already approved trial instead of rerunning today's paper."""
    from .controller import NativeAPI
    state_path = Path(config["state_dir"]) / "state.json"
    state = read_json(state_path) if state_path.exists() else {"version": 1, "jobs": {}, "seeds": []}
    selection = read_json(workspace / "SELECTION.json")
    date = selection["date"]
    if date in state["jobs"]:
        return
    # Read the original native site's receipt, not merely a copied verdict.
    original_site = workspace.parent.parent.parent
    original_config = read_json(original_site / "site.json")
    api = NativeAPI({"site_home": str(original_site), "port": original_config["port"]})
    run_state = api.request("GET", "/v1/runs/" + run_id)
    if run_state["status"] != "succeeded" or any(s["status"] != "passed" for s in run_state["stations"]):
        raise ValueError("seed trial has not passed all native gates")
    judgment = read_json(workspace / "JUDGMENT.json")
    if judgment["verdict"] != "approved" or not judgment["evidence"]:
        raise ValueError("seed trial is not independently approved")
    job = {"date": date, "status": "succeeded", "selection": selection,
           "created": run_state["started"] / 1000, "finished": run_state["finished"] / 1000,
           "attempts": [{"tier": "paid", "run_id": run_id, "status": "succeeded", "site_home": str(original_site)}],
           "events": [{"message": "Imported accepted WOOT tutorial trial; this day is already complete"}]}
    paper = metadata(workspace, job)
    target = Path(config["output_dir"]) / date
    export_workspace(workspace, target, job, run_id, paper)
    job.update(paper=paper, output=str(target))
    state["jobs"][date] = job
    atomic_json(state_path, state)


def install(args):
    if sys.platform != "darwin":
        raise RuntimeError("This installer uses macOS launchd")
    repository = Path(__file__).resolve().parents[2]
    root = args.root.expanduser().resolve()
    (root / "logs").mkdir(parents=True, exist_ok=True)
    config_path = root / "config.json"
    domain = f"gui/{os.getuid()}"
    runtime = root / "runtime"
    site = root / "site"
    # Updates require idle native workers; never replace a running deployment.
    for label in (TICK_LABEL, SITE_LABEL):
        if run("launchctl", "print", domain + "/" + label, check=False).returncode == 0:
            raise RuntimeError("Services already installed. Unload idle services before updating; state/history are retained.")
    required_program("git")
    programs = {name: required_program(name) for name in ("codex", "opencode", "node", "npm", "python3", "pad")}
    binary = repository / "build/release/factory"
    if not binary.is_file():
        raise RuntimeError("Build the native factory executable before installing")
    runtime.mkdir(exist_ok=True)
    (root / "bin").mkdir(exist_ok=True)
    shutil.copy2(binary, root / "bin/factory")
    shutil.copytree(repository / "scripts/paper_factory", runtime / "paper_factory", dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__"))
    (runtime / "factories").mkdir(exist_ok=True)
    shutil.copy2(repository / "factories/papers.md", runtime / "factories/papers.md")
    site.mkdir(exist_ok=True)
    if config_path.exists():
        config = read_json(config_path)
        if args.start_time:
            config["start_time"] = args.start_time
    else:
        config = {"state_dir": str(root / "state"), "site_home": str(site), "port": args.port,
                  "output_dir": str(args.output.expanduser().resolve()),
                  "recipe": str(runtime / "factories/papers.md"), "enabled": True,
                  "timezone": "America/Indiana/Indianapolis", "start_time": args.start_time or "09:00",
                  "archive_url": "https://jimmyhmiller.com/readings",
                  "free_model": "opencode/big-pickle", "paid_model": "gpt-5.5",
                  "free_idle_seconds": 300, "paid_idle_seconds": 900, "gate_idle_seconds": 1800,
                  "free_max_seconds": 5400, "paid_max_seconds": 10800, "paid_attempts": 2,
                  "pad": programs["pad"]}
    validate_config(config)
    atomic_json(config_path, config)
    site_config = {"name": "daily-papers", "port": config["port"], "max_runs": 1,
                   "codex": programs["codex"], "opencode": programs["opencode"],
                   "codex_model": config["paid_model"], "codex_network": True,
                   "codex_writable": ["~/.pad"], "overseer": {"policy": "observe"}}
    atomic_json(site / "site.json", site_config)
    if args.seed_workspace:
        if not args.seed_run:
            raise ValueError("--seed-run is required with --seed-workspace")
        seed_trial(config, args.seed_workspace.resolve(), args.seed_run)
    path = ":".join(dict.fromkeys([str(Path(p).parent) for p in programs.values()] +
                                 [str(root / "bin"), str(Path.home() / ".local/bin"), str(Path.home() / ".opencode/bin"),
                                  "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin"]))
    environment = {"PATH": path, "HOME": str(Path.home()), "FACTORY_HOME": str(site), "PYTHONPATH": str(runtime),
                   "PYTHONDONTWRITEBYTECODE": "1"}
    agents = Path.home() / "Library/LaunchAgents"
    agents.mkdir(exist_ok=True)
    definitions = {
        SITE_LABEL: plist(SITE_LABEL, [root / "bin/factory", "daemon"], environment, root, KeepAlive=True, RunAtLoad=True, ThrottleInterval=10),
        TICK_LABEL: plist(TICK_LABEL, [programs["python3"], "-m", "paper_factory.controller", "--config", config_path], environment, root,
                         StartInterval=60, RunAtLoad=True, ThrottleInterval=10),
    }
    for label, value in definitions.items():
        p = agents / (label + ".plist")
        with p.open("wb") as stream:
            plistlib.dump(value, stream)
        os.chmod(p, 0o600)
        run("plutil", "-lint", str(p))
    # Service first. The first heartbeat may see startup in progress; it retains
    # state and the next heartbeat retries. No model is started before durable state.
    for label in (SITE_LABEL, TICK_LABEL):
        result = run("launchctl", "bootstrap", domain, str(agents / (label + ".plist")), check=False)
        if result.returncode:
            raise RuntimeError(f"launchd could not load {label}: {result.stderr.strip()}")
    print(json.dumps({"installed": list(definitions), "config": str(config_path), "tutorial_library": config["output_dir"],
                      "schedule": config["start_time"], "timezone": config["timezone"]}, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.home() / ".local/share/paper-factory")
    parser.add_argument("--output", type=Path, default=Path.home() / "Documents/paper-factory")
    parser.add_argument("--start-time", help="HH:MM in America/Indiana/Indianapolis (default 09:00)")
    parser.add_argument("--port", type=int, default=7799)
    parser.add_argument("--seed-workspace", type=Path)
    parser.add_argument("--seed-run")
    install(parser.parse_args())


if __name__ == "__main__":
    main()

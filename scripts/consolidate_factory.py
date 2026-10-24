"""Consolidate idle per-application factory sites into one user service.

Dry-run by default. Run on the Linux host with --apply after reviewing the plan.
Existing journals/checkpoints are never rewritten. Legacy run-directory aliases
preserve saved workspace paths and Git worktree metadata after physical moves.
"""
import argparse
import collections
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import urllib.request
import urllib.error

LEGACY = ("coil-bugs-site.service", "paper-factory-site.service", "paper-models-site.service")
CONTROLLERS = ("coil-bugs.service", "paper-factory-daily.service", "paper-models.service")
TRIGGERS = ("coil-bugs.timer", "coil-bugs-snapshots.path", "paper-factory-daily.timer",
            "paper-factory-requests.path", "paper-models.timer")


def systemctl(*args, check=True):
    return subprocess.run(["systemctl", "--user", *args], check=check,
                          text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def atomic_json(path, value):
    temporary = path.with_name(path.name + ".consolidating")
    with temporary.open("x") as f:
        os.fchmod(f.fileno(), 0o600)
        json.dump(value, f, indent=2)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(temporary, path)


def read(path):
    return json.loads(path.read_text())


def fingerprint(path):
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inventory(target, sources):
    ids = {}
    runs = []
    for home in [target, *sources]:
        directory = home / "runs"
        if directory.is_symlink():
            if directory.resolve() == (target / "runs").resolve():
                continue
            raise RuntimeError(f"Unexpected run-directory symlink: {directory}")
        for run in sorted(directory.iterdir()) if directory.exists() else []:
            if not run.is_dir() or run.is_symlink():
                raise RuntimeError(f"Unexpected run entry: {run}")
            if run.name in ids:
                raise RuntimeError(f"Run ID collision: {run.name}: {ids[run.name]} and {home}")
            ids[run.name] = str(home)
            metadata = {name: fingerprint(run / name) for name in
                        ("run.json", "events.jsonl") if (run / name).exists()}
            runs.append({"id": run.name, "source": str(home), "metadata": metadata})
    return runs


def assert_idle(home):
    settings = read(home / "site.json")
    port = settings.get("port", 7788)
    token = (home / "token").read_text().strip()
    request = urllib.request.Request(f"http://127.0.0.1:{port}/v1/overview?scope=local",
                                     headers={"Authorization": "Bearer " + token})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            overview = json.load(response)
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"Cannot inspect site {home}: HTTP {error.code}") from error
    except urllib.error.URLError as error:
        if not isinstance(error.reason, ConnectionRefusedError):
            raise RuntimeError(f"Cannot inspect site {home}: {error.reason}") from error
        # An offline site is safe only if it has no surviving run workers.
        overview = {"runs": []}
    active = [r["id"] for r in overview.get("runs", []) if
              r.get("alive") or r.get("status") in ("queued", "running", "paused", "approval")]
    for run in (home / "runs").iterdir():
        pidfile = run / "pid"
        if pidfile.exists():
            try:
                pid = int(pidfile.read_text().strip())
                args = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
                if b"_run" in args and run.name.encode() in args:
                    active.append(run.name)
            except (OSError, ValueError):
                pass
    if active:
        raise RuntimeError(f"Site still has active/queued work: {home}: {sorted(set(active))}")


def move_runs(target, sources, runs):
    (target / "runs").mkdir(exist_ok=True)
    for run in runs:
        source = Path(run["source"]) / "runs" / run["id"]
        destination = target / "runs" / run["id"]
        if source != destination:
            source.rename(destination)
        for name, expected in run["metadata"].items():
            if fingerprint(destination / name) != expected:
                raise RuntimeError(f"Run metadata changed during migration: {destination / name}")
    for source in sources:
        directory = source / "runs"
        if directory.is_symlink():
            continue
        directory.rmdir()  # Refuse to hide anything not inventoried.
        directory.symlink_to(target / "runs", target_is_directory=True)


def rewrite_controller_unit(text, target, sources):
    for source in sources:
        text = text.replace("FACTORY_HOME=" + str(source), "FACTORY_HOME=" + str(target))
    for service in LEGACY:
        text = text.replace(service, "factory.service")
    return text


def service_unit(target):
    def quote(value):
        if any(c in str(value) for c in "\n\r\0"):
            raise ValueError("Invalid systemd path")
        return json.dumps(str(value).replace("%", "%%"))
    path = ":".join([str(Path.home() / ".local/bin"), str(Path.home() / ".npm-global/bin"),
                     str(Path.home() / ".cargo/bin"), "/usr/local/bin", "/usr/bin", "/bin"])
    return ("[Unit]\nDescription=Shared factory for all workflows\nAfter=network-online.target\n"
            "Wants=network-online.target\n\n[Service]\nType=simple\n"
            f"WorkingDirectory={str(target).replace('%', '%%')}\nEnvironment={quote('FACTORY_HOME=' + str(target))}\n"
            f"Environment={quote('PATH=' + path)}\nExecStart={quote(target / 'bin/factory')} daemon\n"
            # Workers survive service restarts and are adopted from their PID/journal.
            "KillMode=process\nRestart=on-failure\nRestartSec=2\nUMask=0077\nLimitNOFILE=65536\n"
            "TasksMax=infinity\n\n[Install]\nWantedBy=default.target\n")


def apply(target, sources, binary, runs):
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = target / "migrations" / stamp
    backup.mkdir(parents=True, mode=0o700)
    units = Path.home() / ".config/systemd/user"
    configs = [Path.home() / ".local/share/coil-bugs/config.json",
               Path.home() / ".local/share/paper-factory/config.json"]
    unit_states = {name: {"active": systemctl("is-active", name, check=False).stdout.strip(),
                          "enabled": systemctl("is-enabled", name, check=False).stdout.strip()}
                   for name in (*LEGACY, *CONTROLLERS, *TRIGGERS, "factory.service")}
    files = [*(home / "site.json" for home in [target, *sources]), *configs,
             *(units / unit for unit in (*LEGACY, *CONTROLLERS, "factory.service")), target / "bin/factory"]
    for file in files:
        if file.exists():
            copy = backup / "files" / file.relative_to(Path.home())
            copy.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(file, copy)
    manifest = {"target": str(target), "sources": list(map(str, sources)), "runs": runs,
                "units": unit_states, "phase": "prepared"}
    atomic_json(backup / "manifest.json", manifest)
    print("Backup and rollback manifest:", backup, flush=True)
    # Validate generated units before interrupting any existing service.
    preview = backup / "preview"
    preview.mkdir()
    (preview / "factory.service").write_text(service_unit(target))
    for unit in CONTROLLERS:
        (preview / unit).write_text(rewrite_controller_unit((units / unit).read_text(), target, sources))
    subprocess.run(["systemd-analyze", "--user", "verify", str(preview / "factory.service"),
                    *(str(preview / unit) for unit in CONTROLLERS)], check=True)
    # Stop triggers, allow any in-flight controller heartbeat to finish, then
    # refuse cutover if it started actual work in the meantime.
    for trigger in TRIGGERS:
        systemctl("stop", trigger)
    deadline = time.monotonic() + 310
    while any(systemctl("is-active", unit, check=False).stdout.strip() in
              {"active", "activating", "deactivating", "reloading"} for unit in CONTROLLERS):
        if time.monotonic() > deadline:
            raise RuntimeError("Controller heartbeat did not finish; triggers remain paused")
        time.sleep(0.2)
    for home in [target, *sources]:
        assert_idle(home)
    if inventory(target, sources) != runs:
        raise RuntimeError("Run inventory changed; inspect before retrying")
    for unit in LEGACY:
        systemctl("disable", "--now", unit)
    manifest["phase"] = "legacy-stopped"
    atomic_json(backup / "manifest.json", manifest)
    move_runs(target, sources, runs)
    settings = read(target / "site.json")
    settings.update(name="computer.jimmyhmiller.com", port=7788, max_runs=0,
                    isolation="sandbox", default_harness="codex", codex_model="gpt-6.1-sol",
                    codex=str(Path.home() / ".local/bin/codex"),
                    opencode=str(Path.home() / ".local/bin/opencode"), codex_network=True,
                    overseer={"policy": "observe"})
    atomic_json(target / "site.json", settings)
    (target / "bin").mkdir(exist_ok=True)
    shutil.copy2(binary, target / "bin/factory.new")
    (target / "bin/factory.new").chmod(0o755)
    os.replace(target / "bin/factory.new", target / "bin/factory")
    for file in configs:
        config = read(file)
        config.update(site_home=str(target), port=7788)
        if "models" in config:
            config["models"].update(site_home=str(target), port=7788)
        atomic_json(file, config)
    for unit in CONTROLLERS:
        file = units / unit
        file.write_text(rewrite_controller_unit(file.read_text(), target, sources))
    (units / "factory.service").write_text(service_unit(target))
    retired = backup / "retired-units"
    retired.mkdir()
    for unit in LEGACY:
        file = units / unit
        file.rename(retired / unit)
        systemctl("mask", unit)
    subprocess.run(["systemd-analyze", "--user", "verify", str(units / "factory.service"),
                    *(str(units / unit) for unit in CONTROLLERS)], check=True)
    systemctl("daemon-reload")
    manifest["phase"] = "configured"
    atomic_json(backup / "manifest.json", manifest)
    systemctl("enable", "--now", "factory.service")
    token = (target / "token").read_text().strip()
    deadline = time.monotonic() + 60
    while True:
        try:
            request = urllib.request.Request("http://127.0.0.1:7788/v1/overview?scope=local",
                                             headers={"Authorization": "Bearer " + token})
            with urllib.request.urlopen(request, timeout=5) as response:
                overview = json.load(response)
            actual = {run["id"] for run in overview["runs"]}
            if actual != {run["id"] for run in runs}:
                raise RuntimeError(f"History mismatch: expected {len(runs)}, saw {len(actual)}")
            break
        except OSError:
            if time.monotonic() > deadline:
                raise RuntimeError("Shared factory did not become healthy; triggers remain paused")
            time.sleep(0.5)
    for home in sources:
        for run in runs:
            if run["source"] == str(home):
                assert (home / "runs" / run["id"] / "run.json").exists()
    for trigger, state in unit_states.items():
        if trigger in TRIGGERS and state["active"] == "active":
            systemctl("start", trigger)
    manifest["phase"] = "complete"
    atomic_json(backup / "manifest.json", manifest)
    print(f"Consolidated {len(runs)} historical runs into {target}; concurrency unlimited", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, default=Path.home() / ".factory")
    parser.add_argument("--source-home", type=Path, action="append")
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    target = args.home.resolve()
    sources = args.source_home or [Path.home() / ".local/share/coil-bugs/site",
                                  Path.home() / ".local/share/paper-factory/site",
                                  Path.home() / ".local/share/paper-factory/models-site"]
    runs = inventory(target, sources)
    print(json.dumps({"target": str(target), "historical_runs": len(runs),
                      "by_source": dict(collections.Counter(r["source"] for r in runs)),
                      "port": 7788, "concurrency": "unlimited"}, indent=2), flush=True)
    if args.apply:
        apply(target, sources, args.binary.resolve(), runs)


if __name__ == "__main__":
    main()

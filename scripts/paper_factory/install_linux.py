"""Install the Linux daily site and systemd heartbeat after artifact migration."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys

from .controller import atomic_json, read_json, validate_config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.home() / ".local/share/paper-factory")
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("/srv/paper-factory"))
    parser.add_argument("--signal-executable", type=Path)
    parser.add_argument("--signal-env", type=Path)
    args = parser.parse_args()
    if not sys.platform.startswith("linux"):
        raise RuntimeError("Linux/systemd installer required")
    root, output = args.root.resolve(), args.output.resolve()
    for name in ("codex", "opencode"):
        if not shutil.which(name):
            raise RuntimeError("Missing executable: " + name)
    root.mkdir(parents=True, exist_ok=True)
    (root / "bin").mkdir(exist_ok=True)
    shutil.copy2(args.binary, root / "bin/factory")
    (root / "bin/factory").chmod(0o755)
    site = root / "site"
    site.mkdir(exist_ok=True)
    # Model probes and research run on their own site, so they never hold up
    # a paper run.
    models_site = root / "models-site"
    models_site.mkdir(exist_ok=True)
    config_path = root / "config.json"
    config = read_json(config_path) if config_path.exists() else {}
    config.update(state_dir=str(root / "state"), site_home=str(site), port=7799,
                  output_dir=str(output), recipe=str(root / "runtime/factories/papers.md"),
                  enabled=False, timezone="America/Indiana/Indianapolis", start_time="09:00",
                  archive_url="https://jimmyhmiller.com/readings",
                  free_model="auto", paid_model="gpt-5.5",
                  free_idle_seconds=300, paid_idle_seconds=900, gate_idle_seconds=1800,
                  free_max_seconds=5400, paid_max_seconds=10800, paid_attempts=2,
                  pad=None, publish_readable=True,
                  public_base_url="https://computer.jimmyhmiller.com/papers",
                  request_dir="/var/lib/paper-requests", request_url="/papers/request")
    config["models"] = {
        "site_home": str(models_site), "port": 7798, "opencode_home": str(root / "state/models-opencode"),
        "opencode": shutil.which("opencode"),
        "probe_recipe": str(root / "runtime/factories/model-probe.md"),
        "research_recipe": str(root / "runtime/factories/model-research.md"),
        "research_model": config["paid_model"],
        # Kept across reinstalls: a person's decisions about models.
        "denied": (config.get("models") or {}).get("denied") or {
            "opencode/big-pickle": "Jimmy ruled it out on 2026-10-09: an old stealth model that stalled in both daily runs."},
        "min_context": 128000, "max_turn_seconds": 30,
        "max_probe_seconds": 900, "probe_idle_seconds": 180, "min_probe_score": 5,
        "max_research_seconds": 1800, "research_idle_seconds": 900,
        "probe_every_seconds": 86400, "discover_every_seconds": 3600,
        "research_retry_seconds": 86400, "incident_seconds": 259200, "ranking_max_age_seconds": 7200}
    if args.signal_executable or args.signal_env:
        if not args.signal_executable or not args.signal_env:
            raise ValueError("Both Signal options are required")
        config["signal"] = {"env_file": str(args.signal_env.resolve())}
    validate_config(config)
    atomic_json(config_path, config)
    atomic_json(site / "site.json", {"name": "daily-papers-server", "port":7799,
        "max_runs":1, "isolation":"sandbox", "codex":shutil.which("codex"),
        "opencode":shutil.which("opencode"), "codex_model":config["paid_model"],
        "codex_network":True, "overseer":{"policy":"observe"}})
    atomic_json(models_site / "site.json", {"name": "paper-models", "port": 7798,
        "max_runs":1, "isolation":"sandbox", "codex":shutil.which("codex"),
        "opencode":shutil.which("opencode"), "codex_model":config["paid_model"],
        "codex_network":True, "overseer":{"policy":"observe"}})
    units = Path.home() / ".config/systemd/user"
    units.mkdir(parents=True, exist_ok=True)
    path = ":".join([str(Path.home()/".local/bin"),str(Path.home()/".npm-global/bin"),
                     str(Path.home()/".cargo/bin"),"/usr/local/bin","/usr/bin","/bin"])
    common = 'WorkingDirectory=' + str(root) + '\nEnvironment="PATH=' + path + '"\nEnvironment="PYTHONPATH=' + str(root/"runtime") + '"\nEnvironment="PYTHONDONTWRITEBYTECODE=1"\nEnvironment="FACTORY_HOME=' + str(site) + '"\n'
    definitions = {
        "paper-factory-site.service": "[Unit]\nDescription=Daily paper native factory site\nAfter=network-online.target\nWants=network-online.target\n\n[Service]\nType=simple\n" + common + "ExecStart=" + str(root/"bin/factory") + " daemon\nRestart=on-failure\nRestartSec=5\n\n[Install]\nWantedBy=default.target\n",
        "paper-factory-daily.service": "[Unit]\nDescription=Daily paper controller heartbeat\nAfter=paper-factory-site.service\nWants=paper-factory-site.service\n\n[Service]\nType=oneshot\n" + common + "ExecStart=/usr/bin/python3 -m paper_factory.controller --config " + str(config_path) + "\nTimeoutStartSec=120\n",
        # A heartbeat takes well under a second; every 5s keeps the library's
        # live status current.
        "paper-factory-daily.timer": "[Unit]\nDescription=Check paper schedule and monitor runs every 5 seconds\n\n[Timer]\nOnBootSec=60\nOnUnitActiveSec=5\nAccuracySec=1\nUnit=paper-factory-daily.service\n\n[Install]\nWantedBy=timers.target\n"
    }
    models_common = common.replace('FACTORY_HOME=' + str(site), 'FACTORY_HOME=' + str(models_site))
    definitions["paper-models-site.service"] = "[Unit]\nDescription=Factory site for free-model probes and research\nAfter=network-online.target\nWants=network-online.target\n\n[Service]\nType=simple\n" + models_common + "ExecStart=" + str(root/"bin/factory") + " daemon\nRestart=on-failure\nRestartSec=5\n\n[Install]\nWantedBy=default.target\n"
    definitions["paper-models.service"] = "[Unit]\nDescription=Free-model registry heartbeat\nAfter=paper-models-site.service\nWants=paper-models-site.service\n\n[Service]\nType=oneshot\n" + models_common + "ExecStart=/usr/bin/python3 -m paper_factory.models --config " + str(config_path) + "\nTimeoutStartSec=300\n"
    definitions["paper-models.timer"] = "[Unit]\nDescription=Discover, test and rank free models every minute\n\n[Timer]\nOnBootSec=90\nOnUnitActiveSec=60\nAccuracySec=1\nUnit=paper-models.service\n\n[Install]\nWantedBy=timers.target\n"
    if config.get("request_dir"):
        # Start a heartbeat the moment Gatekeeper commits a request file.
        definitions["paper-factory-requests.path"] = "[Unit]\nDescription=Start the paper controller as soon as a paper is requested\n\n[Path]\nPathChanged=" + config["request_dir"] + "\nUnit=paper-factory-daily.service\n\n[Install]\nWantedBy=default.target\n"
    if args.signal_executable:
        definitions["paper-factory-signal.service"] = "[Unit]\nDescription=Existing Signal account JSON-RPC socket\nAfter=network-online.target\nWants=network-online.target\n\n[Service]\nType=simple\n" + common + "ExecStart=/usr/bin/python3 -m paper_factory.notify --daemon --env-file " + str(args.signal_env.resolve()) + " --executable " + str(args.signal_executable.resolve()) + "\nRestart=on-failure\nRestartSec=15\nUMask=0077\n\n[Install]\nWantedBy=default.target\n"
    for name, text in definitions.items():
        (units / name).write_text(text)
    subprocess.run(["systemd-analyze","--user","verify",*[str(units/name) for name in definitions]],check=True)
    subprocess.run(["systemctl","--user","daemon-reload"],check=True)
    # Scheduler stays disabled until Gatekeeper's route is verified. The daily
    # timer and request path unit are enabled only by the final activation step.
    subprocess.run(["systemctl","--user","enable","--now","paper-factory-site.service","paper-models-site.service"],check=True)
    if args.signal_executable:
        subprocess.run(["systemctl","--user","enable","--now","paper-factory-signal.service"],check=True)
    print("Installed server site and Signal socket; daily timer awaits route verification/activation")


if __name__ == "__main__":
    main()


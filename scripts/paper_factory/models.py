"""The free-model registry: discover, research, test and rank free models.

One bounded heartbeat, like the paper controller, with its own lock, state and
native factory site, so its runs never hold up a paper run.

- Discovery refreshes OpenCode's catalogue and keeps every model offered at zero
  input and output cost with tool calls and a large enough context. Denied
  models (Big Pickle, by decision) are listed but never used.
- The only test is a probe: a sandboxed native run of factories/model-probe.md
  through the same harness paper runs use, on freshly generated tasks
  (probe_tasks.py), graded against a key the model never sees. Whatever a model
  does there is what it would do in a paper run, and is recorded as observed:
  refusing the harness or being retired (the catalogue keeps listing retired
  models), stalling, slow turns, running too long, or scoring too low each put
  it out with the run's own words. Nothing here knows about any one model.
- Research is a native run of factories/model-research.md by the paid model for
  each new model: what it is, parameter counts, a published capability score,
  with sources. It informs the page and breaks ties only; measured results
  decide.
- A model whose free attempt fails in a real paper run is out for a while.

The ranking (state/models.json) is what the paper controller's `free_model:
"auto"` uses (controller.chosen_free_model); models.html in the library shows every model and why it is in or out.
"""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import html
import inspect
import json
import logging
import os
from pathlib import Path
import re
import secrets
import shutil
import statistics
import subprocess
import tempfile
import uuid

from . import probe_tasks
from .controller import TERMINAL, APIError, NativeAPI, atomic_json, read_json

LOG = logging.getLogger("paper-models")
PROGRESS_KINDS = {"agent.turn", "agent.streaming", "agent.text", "agent.thought", "tool.started", "tool.finished",
                  "station.started", "station.finished", "gate.started", "gate.finished"}


def research_errors(research, model):
    """Problems with a RESEARCH.json; shipped verbatim as the run's check_research.py."""
    errors = []
    if not isinstance(research, dict):
        return ["RESEARCH.json must be a JSON object"]
    def text_or_null(key):
        if research.get(key) is not None and not (isinstance(research[key], str) and research[key].strip()):
            errors.append(f"{key} must be a nonempty string or null")
    def url(value):
        return isinstance(value, str) and value.startswith(("https://", "http://")) and " " not in value
    if research.get("model") != model:
        errors.append(f"model must be {model!r}")
    if research.get("identity") not in ("known", "stealth"):
        errors.append('identity must be "known" or "stealth"')
    for key in ("developer", "underlying_model", "parameters_total", "parameters_active", "data_terms"):
        text_or_null(key)
    if research.get("identity") == "known" and not research.get("developer"):
        errors.append("a known model needs its developer")
    if research.get("open_weights") not in (True, False, None):
        errors.append("open_weights must be true, false or null")
    index = research.get("published_index")
    if index is not None:
        if not isinstance(index, dict) or not isinstance(index.get("name"), str) or not index["name"].strip() \
                or type(index.get("value")) not in (int, float) or not url(index.get("url")):
            errors.append("published_index must be null or {name, value (number), url}")
    for key in ("summary", "notes"):
        if not isinstance(research.get(key), str) or not research[key].strip():
            errors.append(f"{key} must be a nonempty string")
    sources = research.get("sources")
    if not isinstance(sources, list) or not sources or not all(url(s) for s in sources):
        errors.append("sources must be a nonempty list of http(s) URLs")
    return errors


CHECK_RESEARCH = (inspect.getsource(research_errors) + '''

if __name__ == "__main__":
    import json, sys
    model = json.load(open("MODEL.json"))["id"]
    try:
        research = json.load(open("RESEARCH.json"))
    except (OSError, ValueError) as exc:
        sys.exit(f"RESEARCH.json is unreadable: {exc}")
    problems = research_errors(research, model)
    for problem in problems:
        print(problem)
    sys.exit(1 if problems else 0)
''')


def parse_catalogue(text):
    """`opencode models --verbose` prints `provider/id` lines each followed by a JSON object."""
    parts = re.split(r"^([\w.\-]+/[\w.:\-]+)\n", text, flags=re.M)
    if len(parts) < 3:
        raise ValueError("OpenCode printed no models")
    catalogue = {}
    for name, body in zip(parts[1::2], parts[2::2]):
        catalogue[name] = json.loads(body)
    return catalogue


def free_entry(entry, min_context):
    cost = entry.get("cost") or {}
    return (cost.get("input") == 0 and cost.get("output") == 0
            and (entry.get("capabilities") or {}).get("toolcall") is True
            and (entry.get("limit") or {}).get("context", 0) >= min_context)


class Registry:
    def __init__(self, config, api=None, now=None, opencode=None, seed=None):
        self.config = config
        self.settings = config["models"]
        self.api = api or NativeAPI({"site_home": self.settings["site_home"], "port": self.settings["port"]})
        self.now = now or dt.datetime.now(dt.timezone.utc)
        self.opencode = opencode or OpenCode(self.settings)
        self.seed = seed or (lambda: secrets.randbits(64))
        self.root = Path(config["state_dir"])
        self.path = self.root / "models.json"
        self.state = read_json(self.path) if self.path.exists() else {"version": 1, "models": {}, "active": None, "events": []}
        if self.state.get("version") != 1:
            raise ValueError("unsupported model registry state version")

    @property
    def timestamp(self):
        return self.now.timestamp()

    def save(self):
        atomic_json(self.path, self.state)

    def note(self, message):
        LOG.info("%s", message)
        self.state["events"] = (self.state["events"] + [{"time": self.now.isoformat(), "message": message}])[-200:]
        self.save()

    def record(self, model):
        return self.state["models"][model]

    def tick(self):
        self.api.request("GET", "/v1/site")
        if self.timestamp - self.state.get("discovered", 0) >= self.settings["discover_every_seconds"]:
            self.discover()
        if self.state["active"]:
            self.monitor()
        if not self.state["active"]:
            self.start_next()
        self.publish()

    # Discovery ---------------------------------------------------

    def discover(self):
        catalogue = self.opencode.catalogue()
        seen = set()
        for model, entry in catalogue.items():
            if not free_entry(entry, self.settings["min_context"]):
                continue
            seen.add(model)
            record = self.state["models"].get(model)
            if record is None:
                record = self.state["models"][model] = {"first_seen": self.timestamp}
                self.note(f"Discovered free model {model}")
            record.update(name=entry.get("name", model), release_date=entry.get("release_date"),
                          context=entry["limit"]["context"], listed=True, last_listed=self.timestamp)
        for model, record in self.state["models"].items():
            if model not in seen and record.get("listed"):
                record["listed"] = False
                self.note(f"{model} is no longer offered free")
        self.state["discovered"] = self.timestamp
        self.save()

    def denied(self, model):
        return self.settings["denied"].get(model)

    # Native runs: probes and research ----------------------------------------

    def start_next(self):
        candidates = [m for m, r in self.state["models"].items() if r.get("listed") and not self.denied(m)]
        never = [m for m in candidates if "probe" not in self.record(m)]
        if never:
            return self.start("probe", min(never, key=lambda m: self.record(m)["first_seen"]))
        # Research only models that actually answered in their test.
        unresearched = [m for m in candidates if "research" not in self.record(m)
                        and self.record(m)["probe"]["turns"] > 0
                        and self.timestamp - self.record(m).get("research_failed", 0) >= self.settings["research_retry_seconds"]]
        if unresearched:
            return self.start("research", min(unresearched, key=lambda m: self.record(m)["first_seen"]))
        stale = [m for m in candidates
                 if self.timestamp - self.record(m)["probe"]["checked"] >= self.settings["probe_every_seconds"]]
        if stale:
            return self.start("probe", min(stale, key=lambda m: self.record(m)["probe"]["checked"]))

    def start(self, kind, model):
        key = f"{kind}-{uuid.uuid4().hex[:12]}"
        tag = f"paper-models/{kind}/{model}/{key}"
        project = self.root / "model-runs" / key
        seed = None
        if kind == "probe":
            seed = self.seed()
            files, answers = probe_tasks.generate(seed)
            recipe, harness, run_model = self.settings["probe_recipe"], "opencode", model
        else:
            entry = self.record(model)
            files = {"MODEL.json": json.dumps({"id": model, "name": entry["name"], "release_date": entry.get("release_date"),
                                               "context": entry.get("context")}, indent=2) + "\n",
                     "check_research.py": CHECK_RESEARCH}
            answers = None
            recipe, harness, run_model = self.settings["research_recipe"], "codex", self.settings["research_model"]
        make_repository(project, files)
        text = Path(recipe).read_text()
        brief = tag + "\n" + json.dumps({"kind": kind, "model": model}, indent=2)
        self.state["active"] = {"kind": kind, "model": model, "tag": tag, "project": str(project), "seed": seed,
                                "key": answers, "status": "submitting", "created": self.timestamp,
                                "request": {"factory_text": text, "brief": brief, "project": str(project),
                                            "model": run_model, "harness": harness, "reviewers": "none"}}
        self.note(f"Prepared {kind} of {model}")
        self.submit()

    def submit(self):
        active = self.state["active"]
        found = self.api.reconcile(active["tag"])
        try:
            run = self.api.request("GET", "/v1/runs/" + found) if found else self.api.request("POST", "/v1/runs", active["request"])
        except APIError as exc:
            if exc.status == 422:
                return self.finish(f"the site rejected the run: {exc}", None)
            raise  # Unknown outcome: the persisted tag reconciles next heartbeat.
        active.update(run_id=run["id"], status="monitoring", started=self.timestamp,
                      last_progress=self.timestamp, journal_offset=0)
        active.pop("request")
        self.note(f'Started {active["kind"]} of {active["model"]} as native run {run["id"]}')

    def journal(self, active):
        """Advance progress from the run journal; return its start and finish times."""
        path = Path(self.settings["site_home"]) / "runs" / active["run_id"] / "events.jsonl"
        if not path.exists():
            return
        with path.open("rb") as stream:
            stream.seek(active["journal_offset"])
            for _ in range(5000):
                line = stream.readline()
                if not line or not line.endswith(b"\n"):
                    break
                event = json.loads(line)
                active["journal_offset"] = stream.tell()
                if event.get("kind") == "run.started":
                    active["run_started"] = event["t"] / 1000
                if event.get("kind") == "run.finished":
                    active["run_finished"] = event["t"] / 1000
                if event.get("kind") == "agent.turn":
                    active.setdefault("turns", []).append(event["t"] / 1000)
                if event.get("kind") in ("run.finished", "station.finished") or "error" in event.get("kind", ""):
                    # Whatever the run said about how it ended, verbatim.
                    detail = {k: v for k, v in event.items() if k not in ("t", "kind", "agent") and isinstance(v, str)}
                    if detail:
                        active.setdefault("said", []).append(event["kind"] + ": " + "; ".join(f"{k}={v}" for k, v in detail.items()))
                if event.get("kind") in PROGRESS_KINDS:
                    active["last_progress"] = max(active["last_progress"], event["t"] / 1000)

    def monitor(self):
        active = self.state["active"]
        if active["status"] == "submitting":
            return self.submit()
        run = self.api.request("GET", "/v1/runs/" + active["run_id"])
        self.journal(active)
        if active["status"] == "stopping":
            if run["status"] in TERMINAL and not run.get("alive", False):
                return self.finish(active["failure"], run)
            if run["status"] not in TERMINAL:
                self.api.request("POST", "/v1/runs/" + run["id"] + "/cancel", {})
            return self.save()
        if run["status"] == "succeeded":
            if run.get("alive", False):
                return self.save()  # The journal can finish before the worker exits.
            return self.finish(None, run)
        if run["status"] in TERMINAL:
            # Blocked (a failed gate, or the agent stopped to ask) still holds a
            # live worker; there is no person to answer a probe, so stop it.
            failure = f'native run {run["status"]}: {run.get("reason", "")}'.rstrip(": ")
            if not run.get("alive", False):
                return self.finish(failure, run)
            active.update(status="stopping", failure=failure)
            self.note(f'{active["kind"]} of {active["model"]}: {failure}; stopping run {run["id"]}')
            self.api.request("POST", "/v1/runs/" + run["id"] + "/cancel", {})
            return
        kind = active["kind"]
        idle = self.settings[kind + "_idle_seconds"]
        limit = self.settings["max_probe_seconds" if kind == "probe" else "max_research_seconds"]
        reason = None
        if self.timestamp - active["last_progress"] > idle:
            reason = f"stalled: no progress for {idle}s"
        elif self.timestamp - active["started"] > limit:
            reason = f"too slow: not finished within {limit}s"
        if reason:
            active.update(status="stopping", failure=reason)
            self.note(f'{kind} of {active["model"]}: {reason}; stopping run {run["id"]}')
            self.api.request("POST", "/v1/runs/" + run["id"] + "/cancel", {})
        else:
            self.save()

    def finish(self, failure, run):
        active = self.state["active"]
        model, record = active["model"], self.record(active["model"])
        workspace = Path(self.settings["site_home"]) / "runs" / active["run_id"] / "workspace" if active.get("run_id") else None
        if active["kind"] == "probe":
            try:
                answers = read_json(workspace / "ANSWERS.json") if workspace else None
            except (OSError, ValueError):
                answers = None
            graded = probe_tasks.grade(answers, active["key"])
            started = active.get("run_started", active.get("started", self.timestamp))
            seconds = round(active.get("run_finished", self.timestamp) - started, 1)
            turns = active.get("turns", [])
            gaps = [b - a for a, b in zip(turns, turns[1:])]
            if failure and active.get("said"):
                failure += " (" + " | ".join(active["said"][-3:])[:600] + ")"
            record["probe"] = {"checked": self.timestamp, "run_id": active.get("run_id"), "seed": active["seed"],
                               "score": sum(graded.values()), "of": len(graded), "correct": graded,
                               "seconds": seconds, "turns": len(turns),
                               "turn_seconds": round(statistics.median(gaps), 1) if gaps else None,
                               "finished": failure is None, "reason": failure}
            self.note(f'Probe of {model}: {sum(graded.values())}/{len(graded)} in {seconds:.0f}s'
                      + (f" ({failure})" if failure else ""))
        else:
            research, problems = None, [failure] if failure else []
            if not problems:
                try:
                    research = read_json(workspace / "RESEARCH.json")
                    problems = research_errors(research, model)
                except (OSError, ValueError) as exc:
                    problems = [f"RESEARCH.json is unreadable: {exc}"]
            if problems:
                record["research_failed"] = self.timestamp
                self.note(f"Research of {model} failed: " + "; ".join(problems))
            else:
                record["research"] = {**research, "researched": self.timestamp, "by": "run " + active["run_id"]}
                record.pop("research_failed", None)
                self.note(f"Researched {model}: {research['summary']}")
        self.state["active"] = None
        self.save()

    def import_research(self, path):
        """Research done outside a run (e.g. by a person), checked like a run's."""
        for research in read_json(path):
            model = research.get("model")
            problems = research_errors(research, model)
            if problems:
                raise ValueError(f"research for {model}: " + "; ".join(problems))
            record = self.state["models"].setdefault(model, {"first_seen": self.timestamp, "name": model})
            record["research"] = {**research, "researched": self.timestamp, "by": f"import of {Path(path).name}"}
        self.save()

    # Ranking -----------------------------------------------------------------

    def incidents(self):
        """Free attempts that failed in real paper runs, by model."""
        path = self.root / "state.json"
        if not path.exists():
            return {}
        found = {}
        for job in read_json(path)["jobs"].values():
            for attempt in job.get("attempts", []):
                failure = attempt.get("failure") or ""
                when = attempt.get("finished") or attempt.get("stop_started") or 0
                if attempt.get("tier") == "free" and attempt.get("model") and failure and \
                        self.timestamp - when < self.settings["incident_seconds"]:
                    found.setdefault(attempt["model"], []).append(f'{job.get("id", job["date"])}: {failure}')
        return found

    def verdict(self, model, incidents):
        """None if the model may be used; otherwise why not."""
        record, s = self.record(model), self.settings
        if self.denied(model):
            return "denied: " + self.denied(model)
        if not record.get("listed"):
            return "no longer offered free"
        probe = record.get("probe")
        if not probe:
            return "not yet tested"
        if self.timestamp - probe["checked"] > 2 * s["probe_every_seconds"]:
            return "test is out of date"
        if not probe["finished"]:
            return ("never answered: " if probe["turns"] == 0 else "test: ") + probe["reason"]
        if probe["turn_seconds"] is not None and probe["turn_seconds"] > s["max_turn_seconds"]:
            return "too slow: median %.0fs per turn (limit %ds)" % (probe["turn_seconds"], s["max_turn_seconds"])
        if probe["seconds"] > s["max_probe_seconds"]:
            return f'too slow: test took {probe["seconds"]:.0f}s'
        if probe["score"] < s["min_probe_score"]:
            return f'test score {probe["score"]}/{probe["of"]} is below {s["min_probe_score"]}'
        if model in incidents:
            return "failed in a paper run: " + incidents[model][-1]
        return None

    def publish(self):
        incidents = self.incidents()
        rows = []
        for model, record in self.state["models"].items():
            reason = self.verdict(model, incidents)
            index = ((record.get("research") or {}).get("published_index") or {}).get("value")
            rows.append({"model": model, "reason": reason, "score": (record.get("probe") or {}).get("score"),
                         "index": index, "seconds": (record.get("probe") or {}).get("seconds")})
        ranked = sorted((r for r in rows if r["reason"] is None),
                        key=lambda r: (-r["score"], -(r["index"] if r["index"] is not None else -1), r["seconds"]))
        self.state["ranking"] = [r["model"] for r in ranked]
        self.state["out"] = {r["model"]: r["reason"] for r in rows if r["reason"] is not None}
        self.state["ranked"] = self.timestamp
        self.save()
        write_page(self.config, self.state)


def make_repository(path, files):
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)
    for name, text in files.items():
        (path / name).write_text(text)
    environment = {**os.environ, "GIT_AUTHOR_NAME": "paper-models", "GIT_AUTHOR_EMAIL": "paper-models@localhost",
                   "GIT_COMMITTER_NAME": "paper-models", "GIT_COMMITTER_EMAIL": "paper-models@localhost"}
    for command in (["git", "init", "-q"], ["git", "add", "-A"], ["git", "commit", "-qm", "Task files"]):
        subprocess.run(command, cwd=path, env=environment, check=True, timeout=60, capture_output=True)


class OpenCode:
    """OpenCode with its own state directory, for the catalogue only; models are
    only ever run through the factory's own harness."""
    def __init__(self, settings):
        self.home = Path(settings["opencode_home"])
        self.program = settings["opencode"]

    def environment(self):
        (self.home / "work").mkdir(parents=True, exist_ok=True)
        return {**os.environ, **{"XDG_" + k.upper() + "_HOME": str(self.home / k) for k in ("config", "data", "cache", "state")}}

    def catalogue(self):
        result = subprocess.run([self.program, "models", "--verbose", "--refresh"], env=self.environment(),
                                cwd=self.home / "work", capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            raise RuntimeError(f"opencode models failed: {result.stderr.strip()[-500:]}")
        return parse_catalogue(result.stdout)

def describe(record):
    research = record.get("research")
    if not research:
        return "Not yet researched"
    parts = [html.escape(research["summary"])]
    if research.get("parameters_total"):
        size = research["parameters_total"] + (f' total, {research["parameters_active"]} active' if research.get("parameters_active") else "")
        parts.append(html.escape(size))
    index = research.get("published_index")
    if index:
        parts.append(f'<a href="{html.escape(index["url"], quote=True)}">{html.escape(index["name"])}: {index["value"]}</a>')
    if research.get("data_terms"):
        parts.append("Data: " + html.escape(research["data_terms"]))
    return "<br>".join(parts)


def write_page(config, state):
    root = Path(config["output_dir"])
    root.mkdir(parents=True, exist_ok=True)
    def when(t):
        return dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC") if t else "never"
    order = {m: i for i, m in enumerate(state.get("ranking", []))}
    rows = []
    for model in sorted(state["models"], key=lambda m: (order.get(m, len(order)), m)):
        record = state["models"][model]
        status = f"#{order[model] + 1}" + (" · in use" if order[model] == 0 else "") if model in order else "out: " + state["out"].get(model, "")
        probe = record.get("probe")
        speed = f'{probe["turn_seconds"]:.1f}s' if probe and probe.get("turn_seconds") is not None else "—"
        test = f'{probe["score"]}/{probe["of"]} in {probe["seconds"]:.0f}s' if probe else "—"
        rows.append(f'<tr><td><b>{html.escape(record.get("name", model))}</b><br><small>{html.escape(model)}</small></td>'
                    f'<td>{html.escape(status)}</td><td>{describe(record)}</td><td>{speed}</td><td>{html.escape(test)}<br><small>{when((probe or {}).get("checked"))}</small></td></tr>')
    active = state.get("active")
    now = f'<p>Testing now: {html.escape(active["kind"])} of {html.escape(active["model"])}.</p>' if active else ""
    page = ('<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Free models</title>'
            '<style>body{font:16px system-ui;max-width:1100px;margin:40px auto;padding:0 16px}td,th{padding:10px;text-align:left;vertical-align:top;border-bottom:1px solid #ddd}table{width:100%}a{color:#087c78}small{color:#666}</style>'
            '<h1>Free models</h1><p><a href="index.html">Daily paper tutorials</a> · Paper runs start on the top-ranked model. '
            'Rank is by the graded test, then a published capability score, then test time. Last ranked ' + when(state.get("ranked")) + '.</p>'
            + now + '<table><tr><th>Model</th><th>Rank</th><th>What it is</th><th>Turn time</th><th>Test</th></tr>' + "".join(rows) + '</table></html>')
    fd, temporary = tempfile.mkstemp(dir=root, prefix=".models-")
    with os.fdopen(fd, "w") as stream:
        stream.write(page)
    if config.get("publish_readable", False):
        os.chmod(temporary, 0o644)
    os.replace(temporary, root / "models.html")


def validate_models_config(models):
    for key in ("site_home", "opencode_home", "probe_recipe", "research_recipe", "opencode"):
        if not Path(models[key]).is_absolute():
            raise ValueError(f"models.{key} must be an absolute path")
    if not isinstance(models["denied"], dict):
        raise ValueError("models.denied maps model ids to reasons")
    for key in ("port", "min_context", "max_turn_seconds", "max_probe_seconds",
                "probe_idle_seconds", "max_research_seconds", "research_idle_seconds", "min_probe_score",
                "probe_every_seconds", "discover_every_seconds", "research_retry_seconds",
                "incident_seconds", "ranking_max_age_seconds"):
        if type(models[key]) is not int or models[key] < 1:
            raise ValueError(f"models.{key} must be a positive integer")
    if models["min_probe_score"] > len(probe_tasks.QUESTIONS):
        raise ValueError("models.min_probe_score exceeds the number of probe questions")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--import-research", type=Path)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config = read_json(args.config)
    validate_models_config(config["models"])
    root = Path(config["state_dir"])
    root.mkdir(parents=True, exist_ok=True)
    with (root / "models.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        try:
            registry = Registry(config)
            if args.import_research:
                registry.import_research(args.import_research)
            else:
                registry.tick()
            (root / "models-last-error.json").unlink(missing_ok=True)
        except Exception as exc:
            atomic_json(root / "models-last-error.json", {"time": dt.datetime.now(dt.timezone.utc).isoformat(), "error": str(exc)})
            LOG.exception("Model registry heartbeat failed; state retained for the next heartbeat")
            raise


if __name__ == "__main__":
    main()

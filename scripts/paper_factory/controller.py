"""One bounded heartbeat; native factory workers do the long-running work.

State is committed before external effects. A persisted request tag reconciles
run creation after interrupted/ambiguous API calls. Only the native factory may
change its run journals; this controller uses its authenticated API.
"""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
import html
import json
import logging
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import tempfile
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from zoneinfo import ZoneInfo

from .notify import SignalNotifier

LOG = logging.getLogger("paper-factory")
TERMINAL = {"succeeded", "failed", "cancelled", "blocked", "lost"}
REQUEST_HEX = set("0123456789abcdef")
STAGES = {"select": "choosing a paper", "build": "implementing and writing", "judge": "independent review"}
# agent.streaming: the model is producing reasoning or text that has not
# finished yet. A tool call's arguments stream as nothing at all (the gateway
# delivers the call whole), which is why free_idle_seconds allows minutes.
PROGRESS_KINDS = {"agent.turn", "agent.streaming", "agent.text", "agent.thought", "tool.started", "tool.finished",
                  "station.started", "station.finished", "gate.started", "gate.finished"}


def read_json(path):
    return json.loads(Path(path).read_text())


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="." + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def title_key(value):
    return "".join(c for c in unicodedata.normalize("NFKC", value).casefold() if c.isalnum())


def url_key(value):
    parsed = urllib.parse.urlsplit(value)
    return (parsed.netloc.casefold().removeprefix("www."), parsed.path.rstrip("/").casefold())


def same_paper(left, right):
    return title_key(left["title"]) == title_key(right["title"]) or url_key(left["url"]) == url_key(right["url"])


def chosen_free_model(config, now):
    """The free model for a paper run: the configured one, or with "auto" the
    model registry's top-ranked model. (None, why) when there is none."""
    if config["free_model"] != "auto":
        return config["free_model"], None
    path = Path(config["state_dir"]) / "models.json"
    if not path.exists():
        return None, "the free-model registry has not run"
    registry = read_json(path)
    if now - registry.get("ranked", 0) > config["models"]["ranking_max_age_seconds"]:
        return None, "the free-model ranking is out of date"
    if not registry.get("ranking"):
        return None, "no free model currently qualifies"
    return registry["ranking"][0], None


def render_recipe(text, tier, config, free_model=None):
    """Set recipe defaults without overriding the independent judge's harness."""
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        raise ValueError("recipe requires front matter")
    end = lines.index("---", 1)
    def replace_field(start, stop, key, value):
        indices = [i for i in range(start, stop) if lines[i].startswith(key + ":")]
        if len(indices) != 1:
            raise ValueError(f"recipe must explicitly declare exactly one {key} in the required section")
        lines[indices[0]] = key + ": " + value
    replace_field(1, end, "harness", "opencode" if tier == "free" else "codex")
    # Free models come and go, so the recipe names none: the attempt's model is
    # the ranking's choice (or the configured paid model), inserted here.
    if any(lines[i].startswith("model:") for i in range(1, end)):
        raise ValueError("recipe front matter must not name a model; the controller chooses it per attempt")
    model = free_model if tier == "free" else config["paid_model"]
    if not model:
        raise ValueError("a free attempt needs a model")
    lines.insert(end, "model: " + model)
    end += 1
    judge = lines.index("## Station: judge")
    stop = next((i for i in range(judge + 1, len(lines)) if lines[i] == ""), len(lines))
    replace_field(judge + 1, stop, "harness", "codex")
    replace_field(judge + 1, stop, "model", config["paid_model"])
    return "\n".join(lines) + "\n"


def announced_choice(workspace):
    """The selector's CHOSEN.json, shown in the library before selection passes.

    Display only: the reserved paper still comes from metadata() once the select
    gate passes. The agent may be mid-write, so an unreadable file means "not
    announced yet" and is read again next heartbeat.
    """
    try:
        chosen = json.loads((workspace / "CHOSEN.json").read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(chosen, dict) or not all(isinstance(chosen.get(k), str) and chosen[k].strip() for k in ("title", "url")):
        return None
    return {"title": chosen["title"].strip(), "url": chosen["url"].strip()}


def metadata(workspace, job):
    paper = read_json(workspace / "PAPER.json")
    if not isinstance(paper, dict):
        raise ValueError("PAPER.json must be an object")
    for key in ("title", "url", "scope", "interestingness", "seed_title", "seed_url", "relationship"):
        if not isinstance(paper.get(key), str) or not paper[key].strip():
            raise ValueError(f"PAPER.json.{key} is missing")
    if urllib.parse.urlsplit(paper["url"]).scheme not in {"http", "https"}:
        raise ValueError("paper URL must be HTTP(S)")
    if type(paper.get("year")) is not int or not 1900 <= paper["year"] <= 2100:
        raise ValueError("paper year is invalid")
    if not isinstance(paper.get("authors"), list) or not paper["authors"] or any(not isinstance(x, str) or not x.strip() for x in paper["authors"]):
        raise ValueError("paper authors are invalid")
    if not isinstance(paper.get("omitted_claims"), list):
        raise ValueError("omitted_claims must be an array")
    if paper.get("mode") != job["selection"]["mode"]:
        raise ValueError("paper changed the persisted selection mode")
    selection = read_json(workspace / "SELECTION.json")
    if not isinstance(selection, dict):
        raise ValueError("SELECTION.json must be an object")
    for key in ("date", "roll", "mode", "archive_url", "prior_papers"):
        if selection.get(key) != job["selection"][key]:
            raise ValueError(f"SELECTION.json changed {key}")
    if job.get("paper") and not same_paper(paper, job["paper"]):
        raise ValueError("attempt changed the already selected paper")
    if any(same_paper(paper, previous) for previous in job["selection"]["prior_papers"]):
        raise ValueError("paper repeats a previous daily selection")
    with (workspace / "source/paper.pdf").open("rb") as stream:
        if stream.read(5) != b"%PDF-":
            raise ValueError("paper source is not a PDF")
    if (workspace / "source/paper.pdf").stat().st_size < 10000 or len((workspace / "source/paper.txt").read_text().strip()) < 1000:
        raise ValueError("paper source is incomplete")
    if not (workspace / "PLAN.md").is_file():
        raise ValueError("missing implementation/tutorial plan")
    return paper


def seed_project(root, job):
    """The repository every run of `job` starts from: SELECTION.json, committed.

    The selection carries the whole paper history, which grows daily. The
    controller owns it, so it writes the file rather than asking a model to
    transcribe it; metadata() then checks the run left it unchanged.
    """
    target = Path(root) / "projects" / job["id"]
    if target.exists():
        shown = subprocess.run(["git", "show", "HEAD:SELECTION.json"], cwd=target,
                               capture_output=True, text=True, check=True)
        if json.loads(shown.stdout) != job["selection"]:
            raise ValueError(f"seed repository {target} does not hold the persisted selection")
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".seed-", dir=target.parent))
    try:
        atomic_json(temporary / "SELECTION.json", job["selection"])
        git = ["git", "-c", "user.name=paper-factory", "-c", "user.email=paper-factory@localhost"]
        for command in (["init", "-q", "-b", "main"], ["add", "SELECTION.json"],
                        ["commit", "-q", "-m", f'paper-factory: selection for {job["id"]}']):
            subprocess.run(git + command, cwd=temporary, capture_output=True, check=True)
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return target


class APIError(RuntimeError):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


class NativeAPI:
    def __init__(self, config):
        self.home = Path(config["site_home"])
        self.base = f'http://127.0.0.1:{config["port"]}'

    def request(self, method, route, body=None):
        # Token is never logged, stored in controller state, or passed on argv.
        token = (self.home / "token").read_text().strip()
        request = urllib.request.Request(self.base + route,
            data=None if body is None else json.dumps(body).encode(), method=method,
            headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            try:
                error = json.load(exc)["error"]
                message = f'{error["code"]}: {error["message"]}'
            except (ValueError, KeyError, TypeError):
                message = f"native API HTTP {exc.code}"
            raise APIError(exc.code, message) from exc

    def reconcile(self, tag):
        # run.json is the durable creation receipt, including queued runs, even
        # when the POST reply never reached us. Read only; never synthesize it.
        matches = []
        for path in (self.home / "runs").glob("*/run.json"):
            spec = read_json(path)
            if spec.get("brief", "").splitlines()[0:1] == [tag]:
                matches.append(spec["id"])
        if len(matches) > 1:
            raise RuntimeError(f"multiple native runs for request {tag}")
        return matches[0] if matches else None


class Controller:
    def __init__(self, config, api=None, now=None, coin=None):
        self.config = config
        self.api = api or NativeAPI(config)
        self.now = now or dt.datetime.now(dt.timezone.utc)
        self.coin = coin or (lambda: secrets.randbelow(2))
        self.root = Path(config["state_dir"])
        self.state_path = self.root / "state.json"
        self.state = read_json(self.state_path) if self.state_path.exists() else {"version": 1, "jobs": {}, "seeds": []}
        if self.state.get("version") != 1:
            raise ValueError("unsupported controller state version")
        # A job's id names its state key, output directory and native tags.
        # Scheduled jobs use the bare date; jobs written before requested
        # papers existed have no id field and were keyed by that date.
        for key, job in self.state["jobs"].items():
            if job.setdefault("id", key) != key:
                raise ValueError(f"job {key} records a different id {job['id']}")

    @property
    def timestamp(self):
        return self.now.timestamp()

    def save(self):
        atomic_json(self.state_path, self.state)

    def note(self, job, message):
        LOG.info("%s: %s", job["id"], message)
        job.setdefault("events", []).append({"time": self.now.isoformat(), "message": message})
        self.save()

    def history(self):
        result = list(self.state["seeds"])
        for job in self.state["jobs"].values():
            if job.get("paper") and not any(same_paper(job["paper"], p) for p in result):
                result.append(job["paper"])
        return result

    def tick(self):
        self.api.request("GET", "/v1/site")  # Do not create state on broken credentials/site.
        active = [j for j in self.state["jobs"].values() if j["status"] == "running"]
        if len(active) > 1:
            raise RuntimeError("overlapping logical daily jobs in state")
        if active:
            self.advance(active[0])
            self.queue_requests()
        else:
            # A failed cancellation can leave a worker alive. Keep monitoring
            # that stop instead of starting tomorrow's work over it.
            stopping = [j for j in self.state["jobs"].values()
                        if j["attempts"] and j["attempts"][-1]["status"] == "stopping"]
            if stopping:
                for job in stopping:
                    attempt = job["attempts"][-1]
                    run = self.api.request("GET", "/v1/runs/" + attempt["run_id"])
                    if run.get("alive", False) or run["status"] not in TERMINAL:
                        if run["status"] not in TERMINAL:
                            self.api.request("POST", "/v1/runs/" + run["id"] + "/cancel", {})
                        self.write_status()
                        return
                    attempt["status"] = "failed"
                    self.save()
            self.queue_requests()
            local = self.now.astimezone(ZoneInfo(self.config["timezone"]))
            day = local.date().isoformat()
            hour, minute = map(int, self.config["start_time"].split(":"))
            if self.config.get("enabled", True):
                if (local.hour, local.minute) >= (hour, minute) and day not in self.state["jobs"]:
                    job = {"id": day, "date": day, "origin": "schedule", "created": self.timestamp,
                           "attempts": [], "events": []}
                    self.state["jobs"][day] = job
                    self.start_job(job)
                else:
                    queued = sorted((j for j in self.state["jobs"].values() if j["status"] == "queued"),
                                    key=lambda j: (j["request"]["requested"], j["id"]))
                    if queued:
                        self.start_job(queued[0])
        self.notify_ready()
        self.write_status()

    def start_job(self, job):
        roll = self.coin()
        job.update(status="running", started=self.timestamp,
                   selection={"date": job["date"], "roll": roll, "mode": "archive" if roll == 0 else "inspired",
                              "archive_url": self.config["archive_url"], "prior_papers": self.history(),
                              "instructions": "SELECTION.json in the workspace is this selection, written by the controller; never edit it, reroll or repeat a prior paper. Deliver a novice-friendly interactive tutorial and tested implementation."})
        self.note(job, f'Persisted roll {roll}: {job["selection"]["mode"]}')
        self.new_attempt(job, "free")
        self.submit(job, job["attempts"][-1])

    def queue_requests(self):
        """Queue one job per new "another paper today" request.

        The Gatekeeper paper-requests function writes each request atomically
        as REQUEST_DIR/<id>.json and never deletes one; this state records which
        were queued. A malformed request is an error, not something to skip.
        """
        if not self.config.get("request_dir"):
            return
        queued = {j["request"]["id"] for j in self.state["jobs"].values() if j.get("request")}
        found = []
        for path in Path(self.config["request_dir"]).iterdir():
            if path.name.startswith(".") and path.name.endswith(".tmp"):
                continue  # Not yet committed by the function's rename.
            if path.suffix != ".json" or len(path.stem) != 32 or not set(path.stem) <= REQUEST_HEX:
                raise ValueError(f"unexpected file in paper request directory: {path}")
            if path.stem in queued:
                continue
            request = read_json(path)
            if (not isinstance(request, dict) or request.get("id") != path.stem
                    or type(request.get("requested")) is not int or request.get("principal") != "browser"):
                raise ValueError(f"malformed paper request: {path}")
            found.append(request)
        for request in sorted(found, key=lambda r: (r["requested"], r["id"])):
            self.queue_request(request)

    def queue_request(self, request):
        zone = ZoneInfo(self.config["timezone"])
        output = Path(self.config["output_dir"])
        day = dt.datetime.fromtimestamp(request["requested"] / 1000, dt.timezone.utc).astimezone(zone).date().isoformat()
        # The scheduled job owns the bare date. Never reuse an id that
        # already has state or an exported directory.
        number = 2
        while f"{day}-{number}" in self.state["jobs"] or (output / f"{day}-{number}").exists():
            number += 1
        job = {"id": f"{day}-{number}", "date": day, "origin": "request", "status": "queued",
               "created": self.timestamp, "request": request, "attempts": [], "events": []}
        self.state["jobs"][job["id"]] = job
        self.note(job, f'Queued paper requested by {request["principal"]} ({request["id"]})')
        return job

    def request_from_operator(self):
        """A paper requested from the server's shell, recorded as such; the
        Gatekeeper button is the only path for a browser request."""
        return self.queue_request({"id": uuid.uuid4().hex, "requested": int(self.timestamp * 1000),
                                   "principal": "operator (server shell)"})

    def notify_ready(self):
        if not self.config.get("signal"):
            return
        for job in self.state["jobs"].values():
            if job["status"] != "succeeded" or not job.get("output"):
                continue
            notice = job.setdefault("notification", {"status": "pending", "attempts": 0})
            if notice["status"] == "sent" or notice.get("next_attempt", 0) > self.timestamp:
                continue
            # Result must exist in the served tree before notifying. The route
            # itself is installed/verified by deployment before enabling ticks.
            output = Path(job["output"])
            if not (output / "index.html").is_file() or not (output / "RUN.json").is_file():
                raise ValueError("accepted tutorial disappeared before notification")
            notice["attempts"] += 1
            self.save()
            url = self.config["public_base_url"].rstrip("/") + "/" + job["id"] + "/index.html"
            message = f'Daily paper tutorial ready ({job["id"]}):\n{job["paper"]["title"]}\n{url}\nImplementation, guided experiments, and independent review passed.'
            try:
                receipt = SignalNotifier(self.config["signal"]).send(message)
            except (OSError, ValueError, RuntimeError) as exc:
                notice.update(status="pending", error=str(exc), next_attempt=self.timestamp + min(3600, 60 * 2 ** min(notice["attempts"], 6)))
                self.note(job, "Signal notification pending; accepted tutorial retained, delivery will retry")
            else:
                notice.update(status="sent", receipt=receipt, sent_at=self.now.isoformat())
                notice.pop("error", None)
                self.note(job, "Signal readiness notification acknowledged")

    def new_attempt(self, job, tier, recovery=None):
        model = None
        if tier == "free":
            model, why = chosen_free_model(self.config, self.timestamp)
            if model is None:
                self.note(job, f"No free attempt: {why}; starting on the paid model")
                tier = "paid"
        number = len(job["attempts"]) + 1
        tag = f'paper-day/{job["id"]}/{tier}-{number}/{uuid.uuid4().hex}'
        attempt = {"tier": tier, "tag": tag, "created": self.timestamp, "status": "submitting",
                   "model": model if tier == "free" else self.config["paid_model"]}
        brief = {"selection": job["selection"], "locked_paper": job.get("paper"),
                 "implementation_tier": tier,
                 "recovery_snapshot": str(recovery) if recovery else None,
                 "previous_findings": job["events"][-6:], "project_pad": "coil-factory-papers"}
        text = render_recipe(Path(self.config["recipe"]).read_text(), tier, self.config, model)
        project = seed_project(self.root, job)
        attempt["request"] = {"factory_text": text, "brief": tag + "\n" + json.dumps(brief, indent=2),
                              "project": str(project), "reviewers": "none"}
        job["attempts"].append(attempt)
        self.note(job, f'Prepared {tier} attempt {number} on {attempt["model"]}')

    def submit(self, job, attempt):
        found = self.api.reconcile(attempt["tag"])
        if found:
            run = self.api.request("GET", "/v1/runs/" + found)
        else:
            try:
                run = self.api.request("POST", "/v1/runs", attempt["request"])
            except APIError as exc:
                if exc.status == 422:
                    attempt["status"] = "failed"
                    self.fail_attempt(job, attempt, f"Provider/run configuration rejected: {exc}")
                    return
                raise  # Unknown outcome: reconcile the persisted tag on next tick.
        attempt.update(run_id=run["id"], status="monitoring", started=self.timestamp,
                       last_progress=self.timestamp, journal_offset=0, station=run.get("current"))
        attempt.pop("request", None)
        self.note(job, f'Started native {attempt["tier"]} run {run["id"]}')

    def progress(self, attempt):
        path = Path(self.config["site_home"]) / "runs" / attempt["run_id"] / "events.jsonl"
        if not path.exists():
            return
        with path.open("rb") as stream:
            stream.seek(attempt["journal_offset"])
            # Bounded page; timestamps reflect actual events, not polling time.
            for _ in range(1000):
                line = stream.readline()
                if not line or not line.endswith(b"\n"):
                    break
                event = json.loads(line)
                attempt["journal_offset"] = stream.tell()
                if event.get("kind") in PROGRESS_KINDS:
                    attempt["last_progress"] = max(attempt["last_progress"], event["t"] / 1000)

    def advance(self, job):
        # Recovery from a crash immediately after persisting the daily roll.
        if not job["attempts"]:
            self.new_attempt(job, "free")
        attempt = job["attempts"][-1]
        if attempt["status"] == "submitting":
            self.submit(job, attempt)
            return
        if attempt["status"] == "failed":
            self.fail_attempt(job, attempt, attempt.get("failure", "attempt failed"))
            return
        run = self.api.request("GET", "/v1/runs/" + attempt["run_id"])
        workspace = Path(self.config["site_home"]) / "runs" / run["id"] / "workspace"
        self.progress(attempt)
        attempt["station"] = run.get("current")
        if not job.get("paper"):
            chosen = announced_choice(workspace)
            if chosen and chosen != job.get("chosen"):
                job["chosen"] = chosen
                self.note(job, f'Selector announced its choice: {chosen["title"]}')
        if attempt["status"] == "stopping":
            if run["status"] in TERMINAL and not run.get("alive", False):
                self.fail_attempt(job, attempt, attempt["failure"])
            elif self.timestamp - attempt["stop_started"] > 180:
                # Never copy a mutating workspace or start more model work.
                job["status"] = "failed"
                self.note(job, "Native cancellation did not stop the worker within 180s; no overlapping fallback started")
            else:
                if run["status"] not in TERMINAL:
                    self.api.request("POST", "/v1/runs/" + run["id"] + "/cancel", {})
                self.save()
            return
        if run["status"] == "cancelled":
            job["status"] = "cancelled"
            attempt["status"] = "stopping" if run.get("alive", False) else "cancelled"
            self.note(job, "Native run cancelled outside controller; respecting operator stop")
            return
        selected = any(s["name"] == "select" and s["status"] == "passed" for s in run["stations"])
        if selected and not job.get("paper"):
            try:
                job["paper"] = metadata(workspace, job)
                self.note(job, f'Reserved selected paper: {job["paper"]["title"]}')
            except (ValueError, OSError, KeyError) as exc:
                self.stop_attempt(job, attempt, run, f"Invalid selection: {exc}")
                return
        if run["status"] == "succeeded" and not run.get("alive", False):
            try:
                self.accept(job, attempt, run, workspace)
            except (ValueError, OSError, KeyError, subprocess.SubprocessError) as exc:
                self.fail_attempt(job, attempt, f"Acceptance/export failed: {exc}")
            return
        if run["status"] == "succeeded":
            self.save()  # Finished journal can precede worker process exit.
            return
        if run["status"] in TERMINAL:
            self.stop_attempt(job, attempt, run, f'Native {run["status"]}: {run.get("reason", "")}')
            return
        if run["status"] == "paused":
            # Respect an operator pause; resume gets a fresh inactivity window.
            attempt["last_progress"] = self.timestamp
            attempt["paused_seconds"] = attempt.get("paused_seconds", 0) + max(0, self.timestamp - attempt.get("last_poll", self.timestamp))
            attempt["last_poll"] = self.timestamp
            self.save()
            return
        tier = attempt["tier"]
        judge = run.get("current") == "judge"
        gate = any(s["status"] == "gate" for s in run["stations"])
        idle_limit = self.config["gate_idle_seconds"] if gate else self.config["paid_idle_seconds" if tier == "paid" or judge else "free_idle_seconds"]
        elapsed = self.timestamp - attempt["started"] - attempt.get("paused_seconds", 0)
        reason = None
        if self.timestamp - attempt["last_progress"] > idle_limit:
            reason = f"No agent/tool/station progress for {idle_limit}s"
        if elapsed > self.config[tier + "_max_seconds"]:
            reason = f"Exceeded {tier} attempt time limit of {self.config[tier + '_max_seconds']}s"
        attempt["last_poll"] = self.timestamp
        if reason:
            self.stop_attempt(job, attempt, run, reason)
        else:
            self.save()

    def stop_attempt(self, job, attempt, run, reason):
        attempt.update(status="stopping", failure=reason, stop_started=self.timestamp)
        self.note(job, reason + "; stopping native attempt before fallback")
        if run["status"] not in {"succeeded", "cancelled"}:
            self.api.request("POST", "/v1/runs/" + run["id"] + "/cancel", {})
        # Always wait until the native process is gone, even for blocked runs.

    def fail_attempt(self, job, attempt, reason):
        attempt.update(status="failed", failure=reason, finished=self.timestamp)
        paid_count = sum(a["tier"] == "paid" for a in job["attempts"])
        self.note(job, reason)
        if paid_count >= self.config["paid_attempts"]:
            job["status"] = "failed"
            self.note(job, "Paid repair budget exhausted; retained evidence and no artifact published")
            return
        snapshot = None
        if attempt.get("run_id"):
            source = Path(self.config["site_home"]) / "runs" / attempt["run_id"] / "workspace"
            snapshot = self.root / "snapshots" / job["id"] / attempt["run_id"]
            snapshot_workspace(source, snapshot)
            # Recover a valid selection completed just before timeout as well.
            if not job.get("paper"):
                try:
                    job["paper"] = metadata(snapshot, job)
                except (OSError, ValueError, KeyError):
                    pass
        self.new_attempt(job, "paid", snapshot)

    def accept(self, job, attempt, run, workspace):
        if not run["stations"] or any(s["status"] != "passed" for s in run["stations"]) or not any(s["name"] == "judge" for s in run["stations"]):
            raise ValueError("not all native stations, including judge, passed")
        judgment = read_json(workspace / "JUDGMENT.json")
        if not isinstance(judgment, dict) or judgment.get("verdict") != "approved" or not isinstance(judgment.get("checks_run"), list) or not judgment["checks_run"] or not isinstance(judgment.get("evidence"), list) or not judgment["evidence"]:
            raise ValueError("independent judgment is missing or rejected")
        paper = metadata(workspace, job)
        if "<html" not in (workspace / "index.html").read_text().lower():
            raise ValueError("missing standalone HTML")
        target = Path(self.config["output_dir"]) / job["id"]
        export_workspace(workspace, target, job, run["id"], paper, readable=self.config.get("publish_readable", False))
        job.update(status="succeeded", paper=paper, output=str(target), finished=self.timestamp)
        attempt.update(status="succeeded", finished=self.timestamp)
        self.note(job, "Native gates and independent judge passed; exported " + str(target))
        self.write_index()

    def write_status(self):
        active = next((j for j in self.state["jobs"].values() if j["status"] == "running"), None)
        status = {"checked": self.now.isoformat(), "enabled": self.config.get("enabled", True),
                  "schedule": self.config["start_time"], "timezone": self.config["timezone"],
                  "active": active, "history_count": len(self.history()),
                  "jobs": [{"id": j["id"], "date": j["date"], "status": j["status"], "paper": j.get("paper", {}).get("title"), "output": j.get("output")} for j in self.state["jobs"].values()]}
        atomic_json(self.root / "status.json", status)
        self.write_index()
        update_pad(self.config, status)

    def write_index(self):
        root = Path(self.config["output_dir"])
        root.mkdir(parents=True, exist_ok=True)
        rows = []
        jobs = sorted(self.state["jobs"].values(), key=lambda x: (x["date"], x["created"]), reverse=True)
        for j in jobs:
            rows.append(index_row(j, root, self.config))
        live = any(j["status"] in ("queued", "running") for j in jobs)
        page = ('<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Daily paper tutorials</title><style>body{font:18px system-ui;max-width:960px;margin:40px auto;padding:0 16px}td,th{padding:12px;text-align:left;border-bottom:1px solid #ddd}table{width:100%}a{color:#087c78}small{color:#666}button{font:inherit;padding:8px 16px;border:1px solid #087c78;border-radius:6px;background:#087c78;color:#fff;cursor:pointer}button:disabled{opacity:.6;cursor:default}#request-status{margin-left:12px}</style><h1>Daily paper tutorials</h1><p>'
                + html.escape(self.config["start_time"] + " daily · " + self.config["timezone"]) + '</p>'
                + free_model_line(self.config, self.timestamp) + request_form(self.config)
                + f'<table id="papers" data-live="{int(live)}"><thead><tr><th>Date</th><th>Status</th><th>Paper</th></tr></thead><tbody>' + "".join(rows) + '</tbody></table>'
                + LIVE_SCRIPT.replace("TIMEZONE", json.dumps(self.config["timezone"])) + '</html>')
        fd, temporary = tempfile.mkstemp(dir=root, prefix=".index-")
        with os.fdopen(fd, "w") as stream:
            stream.write(page)
        if self.config.get("publish_readable", False):
            os.chmod(temporary, 0o644)
        os.replace(temporary, root / "index.html")


def index_row(job, root, config):
    status = job["status"]
    if status == "running" and job["attempts"]:
        attempt = job["attempts"][-1]
        if attempt["status"] == "submitting":
            stage = "starting"
        elif attempt["status"] == "stopping":
            stage = "stopping a stalled attempt"
        else:
            stage = STAGES.get(attempt.get("station"), attempt.get("station") or "starting")
        if attempt["tier"] == "paid":
            stage += " (paid model)"
        status = "running · " + stage
    if job.get("paper"):
        title = html.escape(job["paper"]["title"])
    elif job.get("chosen"):
        title = html.escape(job["chosen"]["title"]) + " <small>chosen, not yet checked</small>"
    elif job["status"] == "queued":
        title = "Waiting to start"
    elif job["status"] == "running":
        title = "Choosing a paper…"
    else:
        title = "No paper selected"
    if job["status"] == "succeeded" and job.get("output"):
        relative = os.path.relpath(Path(job["output"]) / "index.html", root)
        link = urllib.parse.quote(relative)
        if config.get("public_base_url"):
            link = config["public_base_url"].rstrip("/") + "/" + link
        title = f'<a href="{html.escape(link, quote=True)}">{title}</a>'
    day = job["date"] + (" <small>requested</small>" if job.get("origin") == "request" else "")
    request = f' data-request="{html.escape(job["request"]["id"], quote=True)}"' if job.get("request") else ""
    return f'<tr{request}><td>{day}</td><td>{html.escape(status)}</td><td>{title}</td></tr>'


def free_model_line(config, now):
    if config["free_model"] != "auto":
        return '<p>Free model: ' + html.escape(config["free_model"]) + '</p>'
    model, why = chosen_free_model(config, now)
    path = Path(config["state_dir"]) / "models.json"
    name = read_json(path)["models"][model].get("name", model) if model else None
    return ('<p>Free model now: ' + (html.escape(name) if name else "none (" + html.escape(why) + "); runs start on the paid model")
            + ' · <a href="models.html">all free models</a></p>')


def request_form(config):
    """The "another paper today" button, posting to the Gatekeeper function."""
    if not config.get("request_url"):
        return ""
    return ('<p><button id="request" type="button" data-url="' + html.escape(config["request_url"], quote=True)
            + '">Another paper today</button><span id="request-status" role="status"></span></p>')


# Refreshes the table in place from this same page, which the controller
# rewrites every heartbeat: while any paper is queued or running, and after a
# request until the controller has picked it up.
LIVE_SCRIPT = """<script>
const table = document.getElementById("papers");
const button = document.getElementById("request");
const note = document.getElementById("request-status");
let awaiting = null;
let timer = null;

function schedule() {
  clearTimeout(timer);
  if (awaiting || table.dataset.live === "1") timer = setTimeout(refresh, 2000);
}

async function refresh() {
  try {
    const response = await fetch(location.pathname, {cache: "no-store", credentials: "same-origin"});
    if (response.ok) {
      const fresh = new DOMParser().parseFromString(await response.text(), "text/html").getElementById("papers");
      if (fresh) {
        table.replaceChildren(...fresh.childNodes);
        table.dataset.live = fresh.dataset.live;
        if (awaiting && table.querySelector(`tr[data-request="${awaiting}"]`)) {
          awaiting = null;
          note.textContent = "";
        }
      }
    }
  } finally {
    schedule();
  }
}

if (button) button.addEventListener("click", async () => {
  button.disabled = true;
  note.textContent = "Requesting…";
  try {
    const response = await fetch(button.dataset.url, {method: "POST", credentials: "same-origin",
      headers: {"Content-Type": "application/json", "X-Paper-Request": "1"}, body: "{}"});
    if (response.status === 201) {
      const request = await response.json();
      const row = table.tBodies[0].insertRow(0);
      row.dataset.request = request.id;
      const day = new Date(request.requested).toLocaleDateString("en-CA", {timeZone: TIMEZONE});
      row.insertCell().innerHTML = day + " <small>requested</small>";
      row.insertCell().textContent = "starting";
      row.insertCell().textContent = "Waiting to start";
      note.textContent = "Requested.";
      awaiting = request.id;
      schedule();
    } else {
      let message = "HTTP " + response.status;
      try { message = (await response.json()).error || message; } catch (_) {}
      note.textContent = response.status === 401 ? "Your sign-in expired. Reload the page to sign in again." : "Not requested: " + message;
    }
  } catch (error) {
    note.textContent = "Not requested: " + error.message;
  }
  button.disabled = false;
});

schedule();
</script>"""

EXCLUDED = {".git", "node_modules", ".cache", ".npm-cache", ".venv", ".coil", "__pycache__", ".DS_Store"}


def snapshot_workspace(source, target):
    if target.exists():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".snapshot-", dir=target.parent))
    try:
        # Includes unfinished/untracked source; excludes operational state and
        # dependency caches. Reject symlinks, so recovery cannot escape its root.
        for directory, dirs, files in os.walk(source, followlinks=False):
            dirs[:] = [d for d in dirs if d not in EXCLUDED and not (Path(directory) / d).is_symlink()]
            for name in files:
                p = Path(directory) / name
                if name in EXCLUDED or p.is_symlink():
                    continue
                out = temporary / p.relative_to(source)
                out.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(p, out)
        if (temporary / "JUDGMENT.json").exists():
            (temporary / "previous-review").mkdir(exist_ok=True)
            for name in ("JUDGMENT.json", "JUDGMENT.md"):
                p = temporary / name
                if p.exists():
                    p.rename(temporary / "previous-review" / name)
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def export_workspace(source, target, job, run_id, paper, readable=False):
    if target.exists():
        receipt = read_json(target / "RUN.json")
        if receipt["run_id"] != run_id:
            raise ValueError("refusing to overwrite a different accepted run")
        return  # Previous export completed before controller state was saved.
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".export-", dir=target.parent))
    try:
        paths = subprocess.check_output(["git", "ls-files", "-z"], cwd=source, timeout=30).decode().split("\0")
        for name in filter(None, paths):
            relative = Path(name)
            if relative.is_absolute() or ".." in relative.parts or any(part in EXCLUDED for part in relative.parts):
                raise ValueError(f"invalid tracked artifact path: {name}")
            p = source / relative
            if p.is_symlink() or not p.is_file() or not p.resolve().is_relative_to(source.resolve()):
                raise ValueError(f"artifact is not a regular workspace file: {name}")
            out = temporary / relative
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, out)
        for required in ("index.html", "PAPER.json", "JUDGMENT.json", "source/paper.pdf", "source/paper.txt"):
            if not (temporary / required).is_file():
                raise ValueError(f"accepted artifact was not committed: {required}")
        atomic_json(temporary / "RUN.json", {"run_id": run_id, "id": job["id"], "date": job["date"], "paper": paper,
                                           "selection": job["selection"], "attempts": job["attempts"],
                                           "acceptance": "All native station gates and independent judge passed"})
        hashes = {str(p.relative_to(temporary)): hashlib.sha256(p.read_bytes()).hexdigest() for p in temporary.rglob("*") if p.is_file()}
        atomic_json(temporary / "SHA256.json", hashes)
        with zipfile.ZipFile(temporary / "bundle.zip", "w", zipfile.ZIP_DEFLATED) as bundle:
            for p in sorted(temporary.rglob("*")):
                if p.is_file() and p.name != "bundle.zip":
                    bundle.write(p, str(p.relative_to(temporary)))
        if readable:
            for p in temporary.rglob("*"):
                p.chmod(0o755 if p.is_dir() else 0o644)
            temporary.chmod(0o755)
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def update_pad(config, status):
    if not config.get("pad"):
        return
    active = status["active"]
    text = f'## Daily paper factory\n\nEnabled: {status["enabled"]}. Schedule: **{status["schedule"]} {status["timezone"]}**, every day. Last check: {status["checked"]}.\n\n'
    text += f'Paper history: {status["history_count"]}. Tutorial library: `{config["output_dir"]}/index.html`.\n\n'
    if active:
        attempt = active["attempts"][-1]
        text += f'Active {active["id"]}: {attempt["tier"]} attempt, native run `{attempt.get("run_id", "submitting")}`.\n\n'
    text += "\n".join(f'- {j["id"]}: {j["status"]} — {j["paper"] or "selection pending"}' for j in status["jobs"][-7:])
    try:
        subprocess.run([config["pad"], "md", "-n", "coil-factory-papers", "--id", "daily-status"],
                       input=text, text=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=10, check=True)
    except (OSError, subprocess.SubprocessError):
        LOG.warning("Could not update project pad; durable local status is preserved")


def validate_config(config):
    ZoneInfo(config["timezone"])
    time = dt.time.fromisoformat(config["start_time"])
    if time.second or time.tzinfo or len(config["start_time"]) != 5:
        raise ValueError("start_time must be HH:MM local time")
    for key in ("state_dir", "site_home", "output_dir", "recipe"):
        if not Path(config[key]).is_absolute():
            raise ValueError(f"{key} must be an absolute path")
    if not 1 <= config["port"] <= 65535 or not 1 <= config["paid_attempts"] <= 3:
        raise ValueError("invalid port or paid attempt budget")
    for key in ("free_idle_seconds", "paid_idle_seconds", "gate_idle_seconds", "free_max_seconds", "paid_max_seconds"):
        if type(config[key]) is not int or config[key] < 1:
            raise ValueError(f"{key} must be a positive integer")
    if config["free_model"] == "auto" and not config.get("models"):
        raise ValueError('free_model "auto" needs the "models" registry configuration')
    if config.get("models"):
        from .models import validate_models_config  # models imports this module.
        validate_models_config(config["models"])
    if bool(config.get("request_dir")) != bool(config.get("request_url")):
        raise ValueError("request_dir and request_url are configured together")
    if config.get("request_dir") and not Path(config["request_dir"]).is_absolute():
        raise ValueError("request_dir must be an absolute path")
    if config.get("signal") and not config.get("public_base_url", "").startswith("https://"):
        raise ValueError("Signal requires an HTTPS public_base_url")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--request-paper", action="store_true", help="queue one more paper now, as the operator")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config = read_json(args.config)
    validate_config(config)
    root = Path(config["state_dir"])
    root.mkdir(parents=True, exist_ok=True)
    if args.status:
        print(json.dumps(read_json(root / "status.json"), indent=2))
        return
    if args.check:
        site = NativeAPI(config).request("GET", "/v1/site")
        print(json.dumps({"site": site["name"], "schedule": config["start_time"], "timezone": config["timezone"], "recipe_exists": Path(config["recipe"]).is_file()}, indent=2))
        return
    with (root / "controller.lock").open("a") as lock:
        try:
            # A heartbeat skips if another holds the lock; a person's request waits.
            fcntl.flock(lock, fcntl.LOCK_EX if args.request_paper else fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        try:
            controller = Controller(config)
            if args.request_paper:
                print("Queued " + controller.request_from_operator()["id"])
            controller.tick()
            (root / "last-error.json").unlink(missing_ok=True)
        except Exception as exc:
            atomic_json(root / "last-error.json", {"time": dt.datetime.now(dt.timezone.utc).isoformat(), "error": str(exc)})
            LOG.exception("Controller heartbeat failed; persisted state retained for next heartbeat")
            raise


if __name__ == "__main__":
    main()

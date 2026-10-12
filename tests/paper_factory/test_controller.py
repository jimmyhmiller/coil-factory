import datetime as dt
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from paper_factory.controller import (APIError, Controller, atomic_json, export_workspace,
                                      metadata, render_recipe, same_paper, snapshot_workspace, validate_config)


class FakeAPI:
    """Native transport double; fixtures exercise durable controller transitions."""
    def __init__(self, home):
        self.home = home
        self.runs = {}
        self.creates = 0
        self.posts = []
        self.ambiguous_create = False
        self.reject_create = False

    def reconcile(self, tag):
        for run_id, run in self.runs.items():
            if run["tag"] == tag:
                return run_id

    def request(self, method, path, body=None):
        if path == "/v1/site":
            return {"name": "test"}
        if path == "/v1/runs":
            if self.reject_create:
                self.reject_create = False
                raise APIError(422, "test provider rejection")
            self.creates += 1
            run_id = "papers-" + str(self.creates)
            self.posts.append(body)
            run = {"id": run_id, "status": "running", "alive": True,
                   "tag": body["brief"].splitlines()[0], "current": "select",
                   "stations": [{"name": n, "status": "running" if n == "select" else "pending"} for n in ("select", "build", "judge")]}
            self.runs[run_id] = run
            (self.home / "runs" / run_id / "workspace").mkdir(parents=True, exist_ok=True)
            if self.ambiguous_create:
                self.ambiguous_create = False
                raise TimeoutError("response lost after durable native creation")
            return run
        run = self.runs[path.split("/")[3]]
        if path.endswith("/cancel"):
            run.update(status="cancelled", alive=False)
        return run


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        recipe = self.root / "papers.md"
        recipe.write_text("---\nharness: opencode\n---\n## Station: judge\nharness: codex\nmodel: gpt-5.5\n")
        self.config = {"state_dir": str(self.root / "state"), "site_home": str(self.root / "site"),
                       "output_dir": str(self.root / "output"), "recipe": str(recipe),
                       "port": 7799, "timezone": "America/Indiana/Indianapolis", "start_time": "09:00",
                       "archive_url": "https://jimmyhmiller.com/readings", "enabled": True,
                       "free_model": "opencode/fixture-free", "paid_model": "gpt-5.5", "paid_attempts": 2,
                       "free_idle_seconds": 300, "paid_idle_seconds": 900, "gate_idle_seconds": 1800,
                       "free_max_seconds": 5400, "paid_max_seconds": 10800}
        self.api = FakeAPI(Path(self.config["site_home"]))
        self.time = dt.datetime(2026, 10, 8, 13, 0, tzinfo=dt.timezone.utc)  # 09:00 local
        self.coin_calls = 0

    def tearDown(self):
        self.temp.cleanup()

    def controller(self, now=None):
        def coin():
            self.coin_calls += 1
            return 1
        return Controller(self.config, api=self.api, now=now or self.time, coin=coin)

    def start(self):
        self.controller().tick()
        return self.controller().state["jobs"]["2026-10-08"]

    def fixtures(self, job, run_id="papers-1"):
        workspace = Path(self.config["site_home"]) / "runs" / run_id / "workspace"
        paper = {"title": "Example fixture paper", "url": "https://example.org/paper", "year": 2006,
                 "authors": ["Fixture Author"], "scope": "Fixture scope", "mode": "inspired",
                 "seed_title": "Fixture seed", "seed_url": "https://example.org/seed",
                 "relationship": "Test relationship", "interestingness": "Test mechanism", "omitted_claims": ["Test omission"]}
        atomic_json(workspace / "PAPER.json", paper)
        atomic_json(workspace / "SELECTION.json", job["selection"])
        atomic_json(workspace / "JUDGMENT.json", {"verdict": "approved", "checks_run": ["fixture only"], "evidence": ["fixture only"]})
        (workspace / "source").mkdir(exist_ok=True)
        (workspace / "source/paper.pdf").write_bytes(b"%PDF-" + b"fixture" * 2000)
        (workspace / "source/paper.txt").write_text("source fixture " * 200)
        (workspace / "PLAN.md").write_text("Fixture plan")
        (workspace / "index.html").write_text("<!doctype html><html>Fixture only</html>")
        subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
        subprocess.run(["git", "add", "."], cwd=workspace, check=True)
        return workspace, paper

    def finish(self, run_id, status="succeeded", alive=False):
        run = self.api.runs[run_id]
        run.update(status=status, alive=alive)
        for station in run["stations"]:
            station["status"] = "passed"

    def test_schedule_uses_local_time_and_runs_once(self):
        self.controller(self.time - dt.timedelta(seconds=1)).tick()
        self.assertEqual(self.api.creates, 0)
        self.start()
        self.controller().tick()
        self.assertEqual(self.api.creates, 1)
        self.assertEqual(self.coin_calls, 1)
        self.assertEqual(self.api.posts[0]["factory_text"].count("harness: opencode"), 1)

    def test_wake_catchup_does_not_backfill_unbounded_days(self):
        self.controller(self.time + dt.timedelta(days=4, hours=6)).tick()
        self.assertEqual(list(self.controller().state["jobs"]), ["2026-10-12"])

    def test_ambiguous_creation_is_adopted_without_duplicate_or_reroll(self):
        self.api.ambiguous_create = True
        with self.assertRaises(TimeoutError):
            self.start()
        self.controller().tick()
        self.assertEqual(self.api.creates, 1)
        self.assertEqual(self.coin_calls, 1)
        self.assertEqual(self.controller().state["jobs"]["2026-10-08"]["attempts"][0]["run_id"], "papers-1")

    def test_provider_rejection_escalates_to_paid(self):
        self.api.reject_create = True
        job = self.start()
        self.assertEqual(job["attempts"][-1]["tier"], "paid")
        self.controller().tick()
        self.assertNotIn("harness: opencode", self.api.posts[0]["factory_text"])
        self.assertEqual(self.coin_calls, 1)

    def test_idle_timeout_stops_before_paid_recovery(self):
        job = self.start()
        workspace, paper = self.fixtures(job)
        (workspace / "untracked-source.js").write_text("unfinished implementation")
        now = self.time + dt.timedelta(seconds=301)
        self.controller(now).tick()
        self.assertEqual(self.api.creates, 1)
        self.assertEqual(self.controller().state["jobs"][job["date"]]["attempts"][0]["status"], "stopping")
        self.controller(now + dt.timedelta(seconds=60)).tick()
        job = self.controller().state["jobs"][job["date"]]
        self.assertEqual(job["paper"], paper)
        brief = json.loads(job["attempts"][-1]["request"]["brief"].split("\n", 1)[1])
        snapshot = Path(brief["recovery_snapshot"])
        self.assertTrue((snapshot / "untracked-source.js").exists())
        self.assertFalse((snapshot / ".git").exists())
        self.assertFalse((snapshot / "JUDGMENT.json").exists())
        self.assertTrue((snapshot / "previous-review/JUDGMENT.json").exists())
        self.assertEqual(brief["selection"], job["selection"])
        self.controller(now + dt.timedelta(seconds=120)).tick()
        self.assertEqual(self.api.creates, 2)

    def test_no_fallback_while_native_worker_is_alive(self):
        self.start()
        original = self.api.request
        with patch.object(self.api, "request", wraps=self.api.request) as request:
            def slow_cancel(method, path, body=None):
                if path.endswith("/cancel"):
                    return self.api.runs["papers-1"]
                return original(method, path, body)
            request.side_effect = slow_cancel
            self.controller(self.time + dt.timedelta(seconds=301)).tick()
            self.controller(self.time + dt.timedelta(seconds=360)).tick()
            self.assertEqual(self.api.creates, 1)
            self.controller(self.time + dt.timedelta(seconds=600)).tick()
        self.assertEqual(self.controller().state["jobs"]["2026-10-08"]["status"], "failed")

    def test_native_judge_rejection_triggers_paid_repair(self):
        job = self.start()
        workspace, _ = self.fixtures(job)
        atomic_json(workspace / "JUDGMENT.json", {"verdict": "rejected", "corrections": ["teach prerequisites"]})
        self.finish("papers-1", "blocked")
        self.controller().tick()
        self.controller().tick()
        job = self.controller().state["jobs"]["2026-10-08"]
        self.assertEqual(job["attempts"][-1]["tier"], "paid")
        self.assertFalse(Path(self.config["output_dir"], job["date"]).exists())

    def test_success_requires_native_gates_and_judgment_and_committed_artifacts(self):
        job = self.start()
        workspace, _ = self.fixtures(job)
        self.finish("papers-1")
        self.api.runs["papers-1"]["stations"][-1]["status"] = "pending"
        self.controller().tick()
        self.assertNotEqual(self.controller().state["jobs"][job["date"]]["status"], "succeeded")

    def test_rejected_judgment_cannot_publish_even_if_native_status_succeeded(self):
        job = self.start()
        workspace, _ = self.fixtures(job)
        atomic_json(workspace / "JUDGMENT.json", {"verdict": "rejected", "checks_run": ["x"], "evidence": ["x"]})
        self.finish("papers-1")
        self.controller().tick()
        self.assertFalse(Path(self.config["output_dir"], job["date"]).exists())

    def test_accepted_export_and_history_survive_restart(self):
        job = self.start()
        workspace, paper = self.fixtures(job)
        self.finish("papers-1")
        self.controller().tick()
        current = self.controller().state["jobs"][job["date"]]
        self.assertEqual(current["status"], "succeeded")
        output = Path(current["output"])
        self.assertTrue((output / "bundle.zip").exists())
        self.assertIn("index.html", read(output / "SHA256.json"))
        self.controller().tick()
        self.assertEqual(self.api.creates, 1)
        self.controller(self.time + dt.timedelta(days=1)).tick()
        next_job = self.controller().state["jobs"]["2026-10-09"]
        self.assertEqual(next_job["selection"]["prior_papers"], [paper])

    def test_selected_history_includes_failed_days(self):
        job = self.start()
        _, paper = self.fixtures(job)
        self.api.runs["papers-1"]["stations"][0]["status"] = "passed"
        self.controller().tick()
        controller = self.controller()
        controller.state["jobs"][job["date"]]["status"] = "failed"
        controller.save()
        self.controller(self.time + dt.timedelta(days=1)).tick()
        self.assertEqual(self.controller().state["jobs"]["2026-10-09"]["selection"]["prior_papers"], [paper])

    def test_roll_and_prior_history_tampering_are_rejected(self):
        job = self.start()
        workspace, _ = self.fixtures(job)
        selection = read(workspace / "SELECTION.json")
        selection["roll"] = 0
        atomic_json(workspace / "SELECTION.json", selection)
        with self.assertRaisesRegex(ValueError, "changed roll"):
            metadata(workspace, job)

    def test_locked_paper_cannot_change_during_paid_repair(self):
        job = self.start()
        workspace, paper = self.fixtures(job)
        job["paper"] = {**paper, "title": "Different", "url": "https://example.org/other"}
        with self.assertRaisesRegex(ValueError, "already selected"):
            metadata(workspace, job)

    def test_previous_title_and_url_aliases_are_rejected(self):
        self.assertTrue(same_paper({"title": "A PAPER: Theory", "url": "https://x/a"}, {"title": "a paper theory", "url": "https://y/b"}))
        self.assertTrue(same_paper({"title": "x", "url": "http://www.x/a/?tracking=1"}, {"title": "y", "url": "https://x/a"}))

    def test_paid_attempt_budget_is_bounded(self):
        self.start()
        for i in range(1, 4):
            self.finish("papers-" + str(i), "failed")
            self.controller().tick()
            self.controller().tick()
            self.controller().tick()
        job = self.controller().state["jobs"]["2026-10-08"]
        self.assertEqual(job["status"], "failed")
        self.assertEqual(self.api.creates, 3)
        self.assertEqual(self.coin_calls, 1)

    def test_paused_native_run_does_not_escalate(self):
        self.start()
        self.api.runs["papers-1"]["status"] = "paused"
        self.controller(self.time + dt.timedelta(hours=5)).tick()
        self.assertEqual(self.controller().state["jobs"]["2026-10-08"]["status"], "running")
        self.assertEqual(self.api.creates, 1)

    def test_active_run_prevents_next_day_overlap(self):
        self.start()
        self.controller(self.time + dt.timedelta(days=1)).tick()
        self.assertEqual(list(self.controller().state["jobs"]), ["2026-10-08"])

    def test_snapshot_excludes_dependencies_and_external_symlinks(self):
        source = self.root / "source"
        source.mkdir()
        (source / "node_modules").mkdir()
        (source / "node_modules/big").write_text("not source")
        (source / "good.js").write_text("source")
        (source / "escape").symlink_to(self.root)
        snapshot_workspace(source, self.root / "snapshot")
        self.assertEqual([p.name for p in (self.root / "snapshot").iterdir()], ["good.js"])

    def test_export_replay_is_idempotent_and_different_run_cannot_overwrite(self):
        job = self.start()
        workspace, paper = self.fixtures(job)
        target = self.root / "export"
        export_workspace(workspace, target, job, "papers-1", paper)
        export_workspace(workspace, target, job, "papers-1", paper)
        with self.assertRaisesRegex(ValueError, "overwrite"):
            export_workspace(workspace, target, job, "papers-2", paper)

    def test_invalid_config_is_rejected(self):
        validate_config(self.config)
        with self.assertRaises(ValueError):
            validate_config({**self.config, "paid_attempts": 20})

    def test_restart_after_daily_roll_before_attempt_preparation(self):
        job = self.start()
        self.api.runs.clear()
        self.api.creates = 0
        controller = self.controller()
        controller.state["jobs"][job["date"]]["attempts"] = []
        controller.save()
        self.controller().tick()
        self.assertEqual(self.coin_calls, 1)
        self.assertEqual(self.api.creates, 1)

    def test_succeeded_journal_waits_for_process_exit(self):
        job = self.start()
        self.fixtures(job)
        self.finish("papers-1", alive=True)
        self.controller().tick()
        self.assertEqual(self.controller().state["jobs"][job["date"]]["status"], "running")
        self.assertEqual(self.controller().state["jobs"][job["date"]]["attempts"][-1]["status"], "monitoring")
        self.api.runs["papers-1"]["alive"] = False
        self.controller().tick()
        self.assertEqual(self.controller().state["jobs"][job["date"]]["status"], "succeeded")

    def test_operator_cancel_does_not_start_fallback(self):
        self.start()
        self.finish("papers-1", status="cancelled")
        self.controller().tick()
        self.controller().tick()
        self.assertEqual(self.api.creates, 1)
        self.assertEqual(self.controller().state["jobs"]["2026-10-08"]["status"], "cancelled")

    def test_still_alive_failed_cancellation_blocks_next_day(self):
        self.start()
        controller = self.controller()
        job = controller.state["jobs"]["2026-10-08"]
        job["status"] = "failed"
        job["attempts"][0]["status"] = "stopping"
        controller.save()
        original = self.api.request
        def slow_cancel(method, path, body=None):
            if path.endswith("/cancel"):
                return self.api.runs["papers-1"]
            return original(method, path, body)
        with patch.object(self.api, "request", side_effect=slow_cancel):
            self.controller(self.time + dt.timedelta(days=1)).tick()
        self.assertEqual(self.api.creates, 1)
        self.assertEqual(list(self.controller().state["jobs"]), ["2026-10-08"])

    def test_model_configuration_is_applied_without_free_judge(self):
        text = Path(self.config["recipe"]).read_text()
        config = {**self.config, "free_model": "configured-free", "paid_model": "configured-paid"}
        free = render_recipe(text, "free", config, "configured-free")
        self.assertIn("model: configured-free", free)
        self.assertIn("## Station: judge\nharness: codex\nmodel: configured-paid", free)
        paid = render_recipe(text, "paid", config)
        self.assertNotIn("harness: opencode", paid)
        self.assertEqual(paid.count("model: configured-paid"), 2)
        with self.assertRaisesRegex(ValueError, "must not name a model"):
            render_recipe(text.replace("harness: opencode\n", "harness: opencode\nmodel: opencode/some-free\n", 1), "free", config, "x")

    def test_malformed_selection_triggers_repair_instead_of_perpetual_heartbeat_error(self):
        job = self.start()
        workspace, _ = self.fixtures(job)
        atomic_json(workspace / "PAPER.json", [])
        self.api.runs["papers-1"]["stations"][0]["status"] = "passed"
        self.controller().tick()
        self.controller().tick()
        job = self.controller().state["jobs"][job["date"]]
        self.assertEqual(job["attempts"][-1]["tier"], "paid")

    def test_signal_ready_once_after_acceptance(self):
        self.config.update(signal={"env_file": "/fixture/env"}, public_base_url="https://example.org/papers")
        job = self.start()
        self.fixtures(job)
        self.finish("papers-1")
        with patch("paper_factory.controller.SignalNotifier") as notifier:
            notifier.return_value.send.return_value = {"timestamp": 123}
            self.controller().tick()
            self.controller().tick()
            notifier.return_value.send.assert_called_once()
            self.assertIn("https://example.org/papers/2026-10-08/index.html", notifier.return_value.send.call_args.args[0])
        self.assertEqual(self.controller().state["jobs"][job["date"]]["notification"]["status"], "sent")

    def test_signal_failure_preserves_accepted_output_and_retries(self):
        self.config.update(signal={"env_file": "/fixture/env"}, public_base_url="https://example.org/papers")
        job = self.start()
        self.fixtures(job)
        self.finish("papers-1")
        with patch("paper_factory.controller.SignalNotifier") as notifier:
            notifier.return_value.send.side_effect = ConnectionError("offline")
            self.controller().tick()
            self.controller().tick()
            self.assertEqual(notifier.return_value.send.call_count, 1)
            self.assertEqual(self.controller().state["jobs"][job["date"]]["status"], "succeeded")
            notifier.return_value.send.side_effect = None
            notifier.return_value.send.return_value = {"timestamp": 456}
            self.controller(self.time + dt.timedelta(seconds=121)).tick()
            self.assertEqual(notifier.return_value.send.call_count, 2)

    def test_hosted_index_links_resolve_with_or_without_trailing_slash(self):
        import re
        from urllib.parse import urljoin
        job = self.start()
        c = self.controller()
        c.state["jobs"][job["date"]].update(status="succeeded",
            output=str(Path(self.config["output_dir"]) / job["date"]))
        for configured in ("https://example.org/papers", "https://example.org/papers/"):
            c.config["public_base_url"] = configured
            c.write_index()
            page = (Path(self.config["output_dir"]) / "index.html").read_text()
            href = re.search(r'<a href="([^"]+)"', page).group(1)
            expected = "https://example.org/papers/" + job["date"] + "/index.html"
            for address in ("https://example.org/papers", "https://example.org/papers/"):
                self.assertEqual(urljoin(address, href), expected)
        c.config.pop("public_base_url")
        c.write_index()
        page = (Path(self.config["output_dir"]) / "index.html").read_text()
        self.assertIn('href="' + job["date"] + '/index.html"', page)

    def request(self, request_id, requested=None, **fields):
        """A request file exactly as the Gatekeeper paper-requests function writes it."""
        directory = Path(self.config["request_dir"])
        directory.mkdir(parents=True, exist_ok=True)
        body = {"id": request_id, "principal": "browser",
                "requested": int((requested or self.time).timestamp() * 1000), **fields}
        atomic_json(directory / (request_id + ".json"), body)
        return body

    def enable_requests(self):
        self.config.update(request_dir=str(self.root / "requests"), request_url="/papers/request")
        Path(self.config["request_dir"]).mkdir()

    def test_requested_paper_runs_after_the_daily_one_and_keeps_it(self):
        self.enable_requests()
        job = self.start()
        workspace, paper = self.fixtures(job)
        later = self.time + dt.timedelta(seconds=60)
        self.request("a" * 32, later)
        # Queued while today's paper runs; never overlapping it.
        self.controller(later).tick()
        queued = self.controller().state["jobs"]["2026-10-08-2"]
        self.assertEqual((queued["status"], queued["origin"], queued["date"]), ("queued", "request", "2026-10-08"))
        self.assertEqual(self.api.creates, 1)
        self.finish("papers-1")
        self.controller(later).tick()
        self.assertEqual(self.controller().state["jobs"]["2026-10-08"]["status"], "succeeded")
        self.assertEqual(self.api.creates, 1)
        # Next heartbeat starts it, with today's paper in its history.
        self.controller(later + dt.timedelta(minutes=1)).tick()
        state = self.controller().state["jobs"]
        extra = state["2026-10-08-2"]
        self.assertEqual(extra["status"], "running")
        self.assertEqual(extra["selection"]["date"], "2026-10-08")
        self.assertEqual(extra["selection"]["prior_papers"], [paper])
        self.assertEqual(self.api.creates, 2)
        self.assertTrue(self.api.runs["papers-2"]["tag"].startswith("paper-day/2026-10-08-2/free-1/"))
        self.assertEqual(state["2026-10-08"]["output"], str(Path(self.config["output_dir"]) / "2026-10-08"))
        # The request is consumed exactly once, however often it is seen.
        self.controller(later + dt.timedelta(minutes=2)).tick()
        self.assertEqual(len(self.controller().state["jobs"]), 2)

    def test_requested_paper_exports_beside_the_daily_one(self):
        self.enable_requests()
        before_schedule = self.time - dt.timedelta(hours=1)
        self.request("b" * 32, before_schedule)
        self.controller(before_schedule).tick()
        job = self.controller().state["jobs"]["2026-10-08-2"]
        self.assertEqual(job["status"], "running")
        self.fixtures(job)
        self.finish("papers-1")
        self.controller(before_schedule).tick()
        job = self.controller().state["jobs"]["2026-10-08-2"]
        self.assertEqual(job["status"], "succeeded")
        output = Path(self.config["output_dir"])
        self.assertEqual(read(output / "2026-10-08-2/RUN.json")["id"], "2026-10-08-2")
        # The schedule still runs today's own paper afterwards.
        self.controller().tick()
        self.assertEqual(self.controller().state["jobs"]["2026-10-08"]["status"], "running")
        page = (output / "index.html").read_text()
        self.assertIn('href="2026-10-08-2/index.html"', page)
        self.assertIn("2026-10-08 <small>requested</small>", page)

    def test_requests_queue_in_order_and_skip_existing_directories(self):
        self.enable_requests()
        Path(self.config["output_dir"], "2026-10-08-2").mkdir(parents=True)
        self.request("d" * 32, self.time + dt.timedelta(minutes=2))
        self.request("c" * 32, self.time + dt.timedelta(minutes=1))
        self.controller(self.time + dt.timedelta(minutes=3)).tick()
        jobs = self.controller().state["jobs"]
        self.assertEqual(jobs["2026-10-08-3"]["request"]["id"], "c" * 32)
        self.assertEqual(jobs["2026-10-08-4"]["request"]["id"], "d" * 32)
        self.assertEqual(jobs["2026-10-08"]["status"], "running")
        self.assertEqual(jobs["2026-10-08-3"]["status"], "queued")

    def test_requests_wait_while_new_work_is_disabled(self):
        self.enable_requests()
        self.config["enabled"] = False
        self.request("e" * 32)
        self.controller().tick()
        self.assertEqual(self.controller().state["jobs"]["2026-10-08-2"]["status"], "queued")
        self.assertEqual(self.api.creates, 0)
        page = (Path(self.config["output_dir"]) / "index.html").read_text()
        self.assertIn("Waiting to start", page)

    def test_malformed_or_unexpected_requests_are_errors(self):
        self.enable_requests()
        self.request("f" * 32, principal="device")
        with self.assertRaisesRegex(ValueError, "malformed paper request"):
            self.controller().tick()
        (Path(self.config["request_dir"]) / ("f" * 32 + ".json")).unlink()
        (Path(self.config["request_dir"]) / "notes.txt").write_text("x")
        with self.assertRaisesRegex(ValueError, "unexpected file"):
            self.controller().tick()
        (Path(self.config["request_dir"]) / "notes.txt").unlink()
        # An uncommitted temporary file from the function is not a request yet.
        (Path(self.config["request_dir"]) / (".%s.tmp" % ("f" * 32))).write_text("{")
        self.controller().tick()
        self.assertEqual(list(self.controller().state["jobs"]), ["2026-10-08"])

    def test_index_offers_the_request_button_only_when_configured(self):
        self.controller().write_index()
        self.assertNotIn('id="request"', (Path(self.config["output_dir"]) / "index.html").read_text())
        self.enable_requests()
        self.controller().write_index()
        page = (Path(self.config["output_dir"]) / "index.html").read_text()
        self.assertIn('<button id="request" type="button" data-url="/papers/request">', page)
        self.assertIn('"X-Paper-Request": "1"', page)
        with self.assertRaises(ValueError):
            validate_config({**self.config, "request_url": None})
        with self.assertRaises(ValueError):
            validate_config({**self.config, "request_dir": "relative"})

    def page(self):
        return (Path(self.config["output_dir"]) / "index.html").read_text()

    def test_library_shows_the_stage_and_the_announced_choice_while_running(self):
        job = self.start()
        self.assertIn('data-live="1"', self.page())
        self.assertIn("running · choosing a paper</td><td>Choosing a paper…", self.page())
        workspace = Path(self.config["site_home"]) / "runs/papers-1/workspace"
        (workspace / "CHOSEN.json").write_text('{"title": "Partly writ')  # Agent mid-write.
        self.controller().tick()
        self.assertIn("Choosing a paper…", self.page())
        atomic_json(workspace / "CHOSEN.json", {"title": "Early <Choice>", "url": "https://example.org/early"})
        self.controller().tick()
        self.assertIn("Early &lt;Choice&gt; <small>chosen, not yet checked</small>", self.page())
        self.assertNotIn("Early <Choice>", json.dumps(self.controller().history()))
        # Once selection passes, the reserved paper replaces the announcement.
        _, paper = self.fixtures(job)
        self.api.runs["papers-1"].update(current="build")
        self.api.runs["papers-1"]["stations"][0]["status"] = "passed"
        self.controller().tick()
        self.assertIn("running · implementing and writing</td><td>Example fixture paper</td>", self.page())
        self.finish("papers-1")
        self.controller().tick()
        self.assertIn('data-live="0"', self.page())

    def test_library_marks_paid_repairs(self):
        self.api.reject_create = True
        self.start()
        self.assertIn("running · starting (paid model)", self.page())
        self.controller().tick()
        self.assertIn("running · choosing a paper (paid model)", self.page())

    def test_operator_request_is_queued_and_recorded_as_the_operators(self):
        self.enable_requests()
        self.request("a" * 32, self.time - dt.timedelta(hours=1))
        controller = self.controller(self.time - dt.timedelta(minutes=30))
        job = controller.request_from_operator()
        controller.tick()
        jobs = self.controller().state["jobs"]
        self.assertEqual(job["id"], "2026-10-08-2")
        self.assertEqual(jobs["2026-10-08-2"]["request"]["principal"], "operator (server shell)")
        self.assertEqual(jobs["2026-10-08-3"]["request"]["principal"], "browser")
        # Requests start in the order they were made, not the order queued.
        self.assertEqual(jobs["2026-10-08-3"]["status"], "running")
        self.assertEqual(jobs["2026-10-08-2"]["status"], "queued")

    def test_legacy_jobs_take_their_key_as_id(self):
        job = self.start()
        controller = self.controller()
        del controller.state["jobs"][job["date"]]["id"]
        controller.save()
        self.assertEqual(self.controller().state["jobs"][job["date"]]["id"], job["date"])

    def test_server_export_is_readable_by_gatekeeper(self):
        job = self.start()
        workspace, paper = self.fixtures(job)
        target = self.root / "served"
        export_workspace(workspace, target, job, "papers-1", paper, readable=True)
        self.assertEqual(target.stat().st_mode & 0o777, 0o755)
        self.assertEqual((target / "index.html").stat().st_mode & 0o777, 0o644)
        self.assertEqual((target / "RUN.json").stat().st_mode & 0o777, 0o644)
        self.config["publish_readable"] = True
        self.controller().write_index()
        self.assertEqual((Path(self.config["output_dir"]) / "index.html").stat().st_mode & 0o777, 0o644)


def read(path):
    return json.loads(path.read_text())


if __name__ == "__main__":
    unittest.main()

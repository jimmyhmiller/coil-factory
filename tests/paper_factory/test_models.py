import datetime as dt
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from paper_factory import probe_tasks
from paper_factory.controller import Controller, atomic_json, chosen_free_model, validate_config
from paper_factory.models import CHECK_RESEARCH, Registry, parse_catalogue, research_errors
from test_controller import FakeAPI

REPO = Path(__file__).resolve().parents[2]


def entry(name, cost=0, context=1_000_000, tools=True):
    return {"name": name, "release_date": "2026-09-01", "cost": {"input": cost, "output": cost},
            "limit": {"context": context}, "capabilities": {"toolcall": tools}}


class FakeOpenCode:
    def __init__(self):
        self.models = {}

    def catalogue(self):
        return self.models


class ModelsAPI(FakeAPI):
    def request(self, method, path, body=None):
        run = super().request(method, path, body)
        if path == "/v1/runs":
            run["body"] = body
        return run


SETTINGS = {"port": 7798, "opencode": "/usr/bin/false", "research_model": "gpt-5.5",
            "probe_recipe": str(REPO / "factories/model-probe.md"),
            "research_recipe": str(REPO / "factories/model-research.md"),
            "min_context": 128000, "max_turn_seconds": 30, "max_probe_seconds": 900, "probe_idle_seconds": 180,
            "max_research_seconds": 1800, "research_idle_seconds": 900, "min_probe_score": 5,
            "probe_every_seconds": 86400, "discover_every_seconds": 3600, "research_retry_seconds": 86400,
            "incident_seconds": 259200, "ranking_max_age_seconds": 7200}


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = {"state_dir": str(self.root / "state"), "output_dir": str(self.root / "out"),
                       "free_model": "auto", "paid_model": "gpt-5.5",
                       "models": {**SETTINGS, "site_home": str(self.root / "models-site"), "opencode_home": str(self.root / "oc"),
                                  "denied": {"opencode/denied-one": "out by decision"}}}
        self.api = ModelsAPI(Path(self.config["models"]["site_home"]))
        self.opencode = FakeOpenCode()
        self.opencode.models = {"opencode/smart": entry("Smart"), "opencode/quick": entry("Quick"),
                                "opencode/denied-one": entry("Denied"), "opencode/paid": entry("Paid", cost=1),
                                "opencode/tiny": entry("Tiny", context=32000), "opencode/notools": entry("No tools", tools=False)}
        self.time = dt.datetime(2026, 10, 9, 12, 0, tzinfo=dt.timezone.utc)
        self.seeds = iter(range(100, 200))

    def tearDown(self):
        self.temp.cleanup()

    def registry(self, now=None):
        return Registry(self.config, api=self.api, now=now or self.time, opencode=self.opencode, seed=lambda: next(self.seeds))

    def tick(self, seconds=0):
        registry = self.registry(self.time + dt.timedelta(seconds=seconds))
        registry.tick()
        return registry

    def run_for(self, model):
        run_id = max((r for r in self.api.runs if self.api.runs[r]["body"]["brief"].split("\n")[0].split("/")[2:4] == model.split("/")),
                     key=lambda r: int(r.split("-")[1]))
        return run_id, self.api.runs[run_id], Path(self.config["models"]["site_home"]) / "runs" / run_id / "workspace"

    def journal(self, workspace, events):
        start = self.time.timestamp() * 1000
        (workspace.parent / "events.jsonl").write_text(
            "".join(json.dumps({"t": start + seconds * 1000, "kind": kind, **extra}) + "\n" for seconds, kind, extra in events))

    def solve(self, model, correct=7, seconds=300, turn=5):
        """Finish the active probe of model with `correct` right answers."""
        key = self.registry().state["active"]["key"]
        run_id, run, workspace = self.run_for(model)
        answers = {q: key[q] for q in probe_tasks.QUESTIONS[:correct]}
        answers.update({q: "wrong" for q in probe_tasks.QUESTIONS[correct:]})
        atomic_json(workspace / "ANSWERS.json", answers)
        self.journal(workspace, [(0, "run.started", {})] + [(1 + i * turn, "agent.turn", {}) for i in range(6)]
                     + [(seconds, "run.finished", {"status": "succeeded"})])
        run.update(status="succeeded", alive=False)

    def research_file(self, model, **fields):
        return {"model": model, "identity": "known", "developer": "Lab", "underlying_model": "Lab 5",
                "parameters_total": "600B", "parameters_active": "27B", "open_weights": False,
                "published_index": {"name": "Artificial Analysis Intelligence Index", "value": 44, "url": "https://example.org/aa"},
                "data_terms": None, "summary": "Lab's model.", "notes": "None.", "sources": ["https://example.org/card"], **fields}

    def test_discovery_keeps_free_tool_models_and_never_tests_denied_ones(self):
        registry = self.tick()
        self.assertEqual(sorted(registry.state["models"]), ["opencode/denied-one", "opencode/quick", "opencode/smart"])
        for model in ("opencode/smart", "opencode/quick"):
            self.solve(registry.state["active"]["model"])
            registry = self.tick(1)
        self.assertNotIn("opencode/denied-one", [r["body"]["model"] for r in self.api.runs.values()])
        self.assertEqual(registry.state["out"]["opencode/denied-one"], "denied: out by decision")

    def test_probe_runs_sandboxed_on_generated_tasks_and_grades_outside(self):
        self.tick()
        active = self.registry().state["active"]
        self.assertEqual(active["kind"], "probe")
        model = active["model"]
        run_id, run, _ = self.run_for(model)
        self.assertEqual((run["body"]["harness"], run["body"]["model"]), ("opencode", model))
        self.assertIn("## Station: solve", run["body"]["factory_text"])
        project = Path(run["body"]["project"])
        self.assertEqual(sorted(p.name for p in project.iterdir() if p.name != ".git"),
                         ["PROCEDURE.md", "TASKS.md", "archive.md", "loop.py"])
        self.assertNotIn(str(active["key"]["loop"]), (project / "TASKS.md").read_text() + (project / "loop.py").read_text())
        self.solve(model, correct=6, seconds=240, turn=8)
        self.tick(300)
        probe = self.registry().state["models"][model]["probe"]
        self.assertEqual((probe["score"], probe["of"], probe["seconds"], probe["finished"], probe["turns"], probe["turn_seconds"]),
                         (6, 7, 240.0, True, 6, 8.0))

    def test_a_model_that_never_answers_is_out_with_the_runs_own_words(self):
        self.tick()
        model = self.registry().state["active"]["model"]
        _, run, workspace = self.run_for(model)
        self.journal(workspace, [(0, "run.started", {}), (2, "station.finished", {"outcome": "failed"}),
                                 (2, "run.finished", {"status": "failed", "reason": "the free tier rejected the request"})])
        run.update(status="failed", alive=False, reason="the free tier rejected the request")
        state = self.tick(5).state
        self.assertTrue(state["out"][model].startswith("never answered: native run failed: the free tier rejected the request"))
        self.assertIn("run.finished: status=failed; reason=the free tier rejected the request", state["out"][model])
        # It is not researched, and is tested again the next day.
        self.assertNotEqual((state["active"] or {}).get("kind"), "research")

    def test_slow_turns_are_out(self):
        self.tick()
        model = self.registry().state["active"]["model"]
        self.solve(model, seconds=600, turn=45)
        self.assertEqual(self.tick(1).state["out"][model], "too slow: median 45s per turn (limit 30s)")

    def test_stalled_and_slow_probes_are_stopped_and_out(self):
        self.tick()
        model = self.registry().state["active"]["model"]
        run_id, run, _ = self.run_for(model)
        self.tick(181)  # No progress at all.
        self.assertEqual(run["status"], "cancelled")
        state = self.tick(200).state
        self.assertEqual(state["out"][model], "never answered: stalled: no progress for 180s")
        # The next model's probe starts at once. One that keeps working but
        # runs too long is out as well.
        other = state["active"]
        self.assertNotEqual(other["model"], model)
        _, run2, workspace = self.run_for(other["model"])
        self.journal(workspace, [(s, "agent.turn", {}) for s in range(0, 1200, 20)])
        self.tick(1150)
        self.tick(1160)
        self.assertTrue(self.registry().state["out"][other["model"]].startswith("test: too slow: not finished within 900s"))

    def test_blocked_probe_with_a_live_worker_is_cancelled(self):
        self.tick()
        model = self.registry().state["active"]["model"]
        _, run, _ = self.run_for(model)
        run.update(status="blocked", alive=True, reason="gate failed")
        self.tick(10)
        self.assertEqual(run["status"], "cancelled")
        self.tick(20)
        self.assertTrue(self.registry().state["out"][model].startswith("never answered: native run blocked: gate failed"))

    def test_ranking_is_score_then_published_index_then_time(self):
        self.opencode.models["opencode/third"] = entry("Third")
        self.tick()
        for _ in range(3):
            model = self.registry().state["active"]["model"]
            score, seconds = {"opencode/smart": (6, 500), "opencode/quick": (6, 200), "opencode/third": (7, 800)}[model]
            self.solve(model, score, seconds)
            self.tick(1)
        path = self.root / "research.json"
        path.write_text(json.dumps([self.research_file("opencode/smart", published_index={"name": "AA", "value": 50, "url": "https://x.org/a"}),
                                    self.research_file("opencode/quick", published_index=None)]))
        self.registry().import_research(path)
        self.tick(2)
        self.assertEqual(self.registry().state["ranking"], ["opencode/third", "opencode/smart", "opencode/quick"])
        self.assertEqual(chosen_free_model(self.config, self.time.timestamp() + 2), ("opencode/third", None))
        page = (Path(self.config["output_dir"]) / "models.html").read_text()
        self.assertIn("#1 · in use", page)
        self.assertIn("denied: out by decision", page)

    def test_low_scores_and_stale_results_do_not_qualify(self):
        self.tick()
        model = self.registry().state["active"]["model"]
        self.solve(model, correct=4)
        self.assertEqual(self.tick(1).state["out"][model], "test score 4/7 is below 5")
        other = self.registry().state["active"]["model"]
        self.solve(other)
        self.assertIn(other, self.tick(2).state["ranking"])
        self.api.runs.clear()
        state = Registry(self.config, api=self.api, now=self.time + dt.timedelta(days=3), opencode=self.opencode,
                         seed=lambda: 1)
        state.publish()
        self.assertEqual(state.state["out"][other], "test is out of date")

    def test_a_failure_in_a_real_paper_run_puts_the_model_out(self):
        self.tick()
        model = self.registry().state["active"]["model"]
        self.solve(model)
        self.assertIn(model, self.tick(1).state["ranking"])
        atomic_json(Path(self.config["state_dir"]) / "state.json", {"version": 1, "seeds": [], "jobs": {"2026-10-09": {
            "date": "2026-10-09", "attempts": [{"tier": "free", "model": model, "finished": self.time.timestamp(),
                                                 "failure": "No agent/tool/station progress for 300s"}]}}})
        state = self.tick(2).state
        self.assertNotIn(model, state["ranking"])
        self.assertTrue(state["out"][model].startswith("failed in a paper run: 2026-10-09"))
        # Three days later it may compete again.
        self.assertNotIn(model, self.registry(self.time + dt.timedelta(days=4)).incidents())

    def test_a_provider_refusal_pauses_probes_without_blaming_the_model(self):
        self.tick()
        model = self.registry().state["active"]["model"]
        _, run, workspace = self.run_for(model)
        self.journal(workspace, [(0, "run.started", {}),
                                 (1, "agent.retry", {"agent": "solve", "attempt": 1, "status": 0, "delay_ms": 3600000,
                                                     "message": "Free usage exceeded, subscribe to Go", "action": "free_tier_limit"}),
                                 (1, "run.finished", {"status": "failed", "reason": "the provider refused"})])
        run.update(status="failed", alive=False, reason="the provider refused")
        state = self.tick(5).state
        self.assertNotIn("probe", state["models"][model])
        self.assertEqual(state["out"][model], "not yet tested")
        self.assertEqual(state["free_pause"]["until"], self.time.timestamp() + 3601)
        self.assertIsNone(state["active"])
        self.assertIn("Free models are paused until 2026-10-09 13:00 UTC: the provider refused (free_tier_limit): "
                      "Free usage exceeded, subscribe to Go", (Path(self.config["output_dir"]) / "models.html").read_text())
        self.assertIsNone(self.tick(3000).state["active"])
        self.assertEqual(self.tick(3700).state["active"]["kind"], "probe")

    def test_models_that_answered_are_researched_and_bad_research_is_rejected(self):
        self.tick()
        for _ in range(2):
            self.solve(self.registry().state["active"]["model"])
            self.tick(1)
        active = self.registry().state["active"]
        self.assertEqual(active["kind"], "research")
        run_id, run, workspace = self.run_for(active["model"])
        self.assertEqual((run["body"]["harness"], run["body"]["model"]), ("codex", "gpt-5.5"))
        atomic_json(workspace / "RESEARCH.json", self.research_file(active["model"], sources=[]))
        run.update(status="succeeded", alive=False)
        record = self.tick(2).state["models"][active["model"]]
        self.assertNotIn("research", record)
        self.assertIn("research_failed", record)
        # The other model is researched next; the failed one only after the retry interval.
        self.assertNotEqual((self.registry().state["active"] or {}).get("model"), active["model"])

    def test_research_check_script_runs_standalone(self):
        work = self.root / "check"
        work.mkdir()
        (work / "check_research.py").write_text(CHECK_RESEARCH)
        (work / "MODEL.json").write_text(json.dumps({"id": "opencode/smart"}))
        (work / "RESEARCH.json").write_text(json.dumps(self.research_file("opencode/smart")))
        self.assertEqual(subprocess.run([sys.executable, "check_research.py"], cwd=work).returncode, 0)
        (work / "RESEARCH.json").write_text(json.dumps(self.research_file("opencode/smart", identity="maybe")))
        result = subprocess.run([sys.executable, "check_research.py"], cwd=work, capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("identity", result.stdout)
        self.assertEqual(research_errors(self.research_file("opencode/smart", identity="stealth", developer=None), "opencode/smart"), [])

    def test_catalogue_parsing(self):
        text = 'opencode/a\n{"name": "A", "cost": {"input": 0}}\nopencode/b-free\n{\n  "name": "B"\n}\n'
        self.assertEqual(parse_catalogue(text), {"opencode/a": {"name": "A", "cost": {"input": 0}}, "opencode/b-free": {"name": "B"}})


class ControllerUsesRankingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        recipe = self.root / "papers.md"
        recipe.write_text("---\nharness: opencode\n---\n## Station: judge\nharness: codex\nmodel: gpt-5.5\n")
        self.config = {"state_dir": str(self.root / "state"), "site_home": str(self.root / "site"),
                       "output_dir": str(self.root / "output"), "recipe": str(recipe),
                       "port": 7799, "timezone": "America/Indiana/Indianapolis", "start_time": "09:00",
                       "archive_url": "https://jimmyhmiller.com/readings", "enabled": True,
                       "free_model": "auto", "paid_model": "gpt-5.5", "paid_attempts": 2,
                       "free_idle_seconds": 300, "paid_idle_seconds": 900, "gate_idle_seconds": 1800,
                       "free_max_seconds": 5400, "paid_max_seconds": 10800}
        self.config["models"] = {**SETTINGS, "site_home": str(self.root / "ms"), "opencode_home": str(self.root / "oc"),
                                 "denied": {}}
        self.api = FakeAPI(Path(self.config["site_home"]))
        self.time = dt.datetime(2026, 10, 8, 13, 0, tzinfo=dt.timezone.utc)

    def tearDown(self):
        self.temp.cleanup()

    def ranking(self, ranking, age=0):
        atomic_json(Path(self.config["state_dir"]) / "models.json",
                    {"version": 1, "ranked": self.time.timestamp() - age, "ranking": ranking, "out": {}, "active": None,
                     "models": {m: {"name": m.title()} for m in ranking}})

    def tick(self):
        Controller(self.config, api=self.api, now=self.time, coin=lambda: 1).tick()
        return Controller(self.config, api=self.api, now=self.time).state["jobs"]["2026-10-08"]

    def test_free_attempt_uses_the_top_ranked_model(self):
        validate_config(self.config)
        self.ranking(["opencode/step-5", "opencode/mimo"])
        job = self.tick()
        self.assertEqual(job["attempts"][0]["tier"], "free")
        self.assertEqual(job["attempts"][0]["model"], "opencode/step-5")
        self.assertIn("model: opencode/step-5\n", self.api.posts[0]["factory_text"])
        self.assertIn("Free model now: Opencode/Step-5", (Path(self.config["output_dir"]) / "index.html").read_text())

    def test_no_qualifying_free_model_starts_paid_and_says_why(self):
        self.ranking([])
        job = self.tick()
        self.assertEqual((job["attempts"][0]["tier"], job["attempts"][0]["model"]), ("paid", "gpt-5.5"))
        self.assertIn("No free attempt: no free model currently qualifies; starting on the paid model",
                      [e["message"] for e in job["events"]])

    def test_a_refused_free_attempt_pauses_free_models_and_is_not_an_incident(self):
        self.ranking(["opencode/step-5"])
        job = self.tick()
        journal = Path(self.config["site_home"]) / "runs/papers-1/events.jsonl"
        journal.write_text(json.dumps({"t": int(self.time.timestamp() * 1000) + 1000, "kind": "agent.retry", "agent": "select",
                                       "attempt": 1, "status": 0, "delay_ms": 7200000, "action": "free_tier_limit",
                                       "message": "Free usage exceeded, subscribe to Go"}) + "\n")
        self.api.runs["papers-1"].update(status="failed", alive=False, reason="the provider refused")
        job = self.tick()
        job = self.tick()
        self.assertEqual(job["attempts"][-1]["tier"], "paid")
        pause = Controller(self.config, api=self.api, now=self.time).state["free_pause"]
        self.assertEqual(pause["until"], self.time.timestamp() + 7201)
        model, why = chosen_free_model(self.config, self.time.timestamp() + 60)
        self.assertIsNone(model)
        self.assertTrue(why.startswith("Free models are paused until 2026-10-08 15:00 UTC: the provider refused (free_tier_limit)"))
        self.ranking(["opencode/step-5"], age=-7300)  # A fresh ranking once the pause is over.
        self.assertEqual(chosen_free_model(self.config, self.time.timestamp() + 7300), ("opencode/step-5", None))
        registry = Registry(self.config, api=self.api, now=self.time, opencode=None, seed=lambda: 1)
        self.assertEqual(registry.incidents(), {})

    def test_stale_ranking_is_not_trusted(self):
        self.ranking(["opencode/step-5"], age=7201)
        self.assertEqual(self.tick()["attempts"][0]["tier"], "paid")

    def test_auto_requires_the_registry_configuration(self):
        with self.assertRaises(ValueError):
            validate_config({**self.config, "models": None})


class ProbeTaskTests(unittest.TestCase):
    def test_tasks_are_deterministic_per_seed_and_differ_between_seeds(self):
        self.assertEqual(probe_tasks.generate(7), probe_tasks.generate(7))
        self.assertNotEqual(probe_tasks.generate(7)[1], probe_tasks.generate(8)[1])

    def test_archive_is_large_enough_to_test_long_context(self):
        files, _ = probe_tasks.generate(3)
        self.assertGreater(len(files["archive.md"]) / 4, 40000)

    def test_procedure_follows_its_prose_on_a_hand_worked_example(self):
        # K=3, M=101 on [4, 9, 5]: t=1 removes 9 (pos 1) -> divisible by 3, pos 1 of [4, 5] becomes 14;
        # t=2 removes position 0 (4) -> even, nothing more; [14] remains.
        self.assertEqual(probe_tasks.procedure([4, 9, 5], 3, 101), 14)
        # K=4, M=101 on [2, 7]: t=1 removes 7 -> odd, last of [2] becomes 2 + 49 = 51.
        self.assertEqual(probe_tasks.procedure([2, 7], 4, 101), 51)

    def test_grading_accepts_equivalent_forms_only(self):
        key = {q: 0 for q in probe_tasks.QUESTIONS}
        key.update(cited="P-0012", author="Oskar Kowalczyk", queries=23986)
        answers = {**key, "cited": "p-0012", "author": " oskar  kowalczyk ", "queries": "23,986", "loop": 0.0}
        self.assertTrue(all(probe_tasks.grade(answers, key).values()))
        self.assertFalse(probe_tasks.grade({**answers, "queries": True}, key)["queries"])
        self.assertEqual(sum(probe_tasks.grade(None, key).values()), 0)
        self.assertEqual(sum(probe_tasks.grade({}, key).values()), 0)


if __name__ == "__main__":
    unittest.main()

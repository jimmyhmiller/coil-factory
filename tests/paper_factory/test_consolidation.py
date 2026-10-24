import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import consolidate_factory as migration
from paper_factory import install_linux


class ConsolidationTests(unittest.TestCase):
    def test_moves_history_without_rewriting_journals_and_preserves_legacy_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target, old = root / "shared", root / "old"
            for home, run_id in [(target, "one"), (old, "two")]:
                run = home / "runs" / run_id
                run.mkdir(parents=True)
                (run / "run.json").write_text(json.dumps({"id": run_id}))
                (run / "events.jsonl").write_text('{"kind":"run.finished"}\n')
                (run / "workspace").mkdir()
            runs = migration.inventory(target, [old])
            expected = (old / "runs/two/events.jsonl").read_bytes()
            migration.move_runs(target, [old], runs)
            self.assertEqual((target / "runs/two/events.jsonl").read_bytes(), expected)
            self.assertEqual((old / "runs/two/events.jsonl").read_bytes(), expected)
            self.assertEqual({x.name for x in (target / "runs").iterdir()}, {"one", "two"})
            self.assertEqual(len(migration.inventory(target, [old])), 2)

    def test_collision_is_refused_before_mutating_either_home(self):
        with tempfile.TemporaryDirectory() as directory:
            target, old = Path(directory) / "shared", Path(directory) / "old"
            for home in [target, old]:
                (home / "runs/same").mkdir(parents=True)
            with self.assertRaisesRegex(RuntimeError, "collision"):
                migration.inventory(target, [old])
            self.assertTrue((old / "runs/same").is_dir())
            self.assertTrue((target / "runs/same").is_dir())

    def test_service_keeps_workers_alive_on_restart_and_has_no_systemd_task_ceiling(self):
        text = migration.service_unit(Path("/home/example/.factory"))
        self.assertIn("KillMode=process", text)
        self.assertIn("TasksMax=infinity", text)
        self.assertIn("WorkingDirectory=/home/example/.factory\n", text)
        old = Path("/home/example/legacy")
        updated = migration.rewrite_controller_unit(
            f'After=paper-models-site.service\nWants=paper-models-site.service\nEnvironment="FACTORY_HOME={old}"\n',
            Path("/home/example/.factory"), [old])
        self.assertNotIn("paper-models-site", updated)
        self.assertIn("After=factory.service", updated)
        self.assertIn("FACTORY_HOME=/home/example/.factory", updated)

    def test_paper_installer_reuses_shared_service_without_overwriting_its_config(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            site = home / ".factory"
            (site / "bin").mkdir(parents=True)
            binary = site / "bin/factory"
            binary.write_bytes(b"shared factory")
            settings = {"port": 7788, "max_runs": 0, "name": "shared"}
            (site / "site.json").write_text(json.dumps(settings))
            root = home / "paper"
            argv = ["install", "--root", str(root), "--site-home", str(site),
                    "--binary", str(binary), "--output", str(home / "output")]
            with patch.object(sys, "argv", argv), patch.object(sys, "platform", "linux"), \
                 patch.object(Path, "home", return_value=home), \
                 patch.object(install_linux.shutil, "which", return_value="/bin/tool"), \
                 patch.object(install_linux.subprocess, "run"):
                install_linux.main()
            config = json.loads((root / "config.json").read_text())
            self.assertEqual(Path(config["site_home"]), site.resolve())
            self.assertEqual(Path(config["models"]["site_home"]), site.resolve())
            self.assertEqual(config["port"], 7788)
            self.assertEqual(json.loads((site / "site.json").read_text()), settings)
            units = home / ".config/systemd/user"
            self.assertFalse((units / "paper-factory-site.service").exists())
            self.assertFalse((units / "paper-models-site.service").exists())
            self.assertIn("Wants=factory.service", (units / "paper-models.service").read_text())


if __name__ == "__main__":
    unittest.main()

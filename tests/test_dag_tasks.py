"""Member D: pipeline task wrappers and the publication gate. Airflow is optional."""
import importlib.util
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dags"))
import rift_tasks  # noqa: E402

HAS_SKLEARN = importlib.util.find_spec("sklearn") is not None


def synthetic_artifact():
    from src.modeling.train import fit
    rows = [(f"M{i}", i, i % 2, [t, (1 if i % 2 else -1) * (t + 500), 0, 0, 0, 0])
            for i in range(20) for t in (60, 600)]
    return json.loads(json.dumps(fit(rows)))


class IngestModeTests(unittest.TestCase):
    def run_ingest(self, env, **kwargs):
        calls = []

        def fake_cli(argv):
            calls.append(argv)
            return 0, {"status": "success", "metrics": {"accepted": 3}, "database": {}}

        with mock.patch.dict("os.environ", env, clear=False), \
                mock.patch.object(rift_tasks, "_run_ingestion_cli", side_effect=fake_cli), \
                mock.patch("src.config.load_environment"):
            result = rift_tasks.ingest(**kwargs)
        return result, calls

    def test_auto_without_key_rebuilds_offline(self):
        result, calls = self.run_ingest({"RIOT_API_KEY": ""}, mode="auto", seeds="/nonexistent.json")
        self.assertEqual(calls, [["rebuild"]])
        self.assertEqual(result["mode"], "rebuild")

    def test_auto_with_key_and_seeds_collects(self):
        with TemporaryDirectory() as tmp:
            seeds = Path(tmp) / "seeds.json"
            seeds.write_text("[]")
            result, calls = self.run_ingest({"RIOT_API_KEY": "x"}, mode="auto", seeds=str(seeds), target=50)
        self.assertEqual(calls, [["collect", "--seeds", str(seeds), "--target", "50"]])
        self.assertEqual(result["metrics"], {"accepted": 3})

    def test_collect_without_seeds_fails_clearly(self):
        with self.assertRaises(rift_tasks.PipelineError):
            self.run_ingest({"RIOT_API_KEY": "x"}, mode="collect", seeds="/nonexistent.json")

    def test_cli_failure_raises(self):
        with mock.patch("src.ingestion.__main__.main", return_value=1):
            with self.assertRaises(rift_tasks.PipelineError):
                rift_tasks._run_ingestion_cli(["rebuild"])


@unittest.skipUnless(HAS_SKLEARN, "scikit-learn not installed")
class PublishGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.candidate = self.dir / "candidate.json"
        self.candidate.write_text(json.dumps(synthetic_artifact()))
        self.published = self.dir / "week8.json"
        self.published.write_text((ROOT / "models" / "week8.json").read_text())

    def tearDown(self):
        self.tmp.cleanup()

    def test_valid_candidate_is_published_and_previous_archived(self):
        old = self.published.read_text()
        result = rift_tasks.publish_model(self.candidate, published=self.published)
        self.assertEqual(self.published.read_text(), self.candidate.read_text())
        self.assertEqual(Path(result["archived_previous"]).read_text(), old)
        self.assertTrue(0 <= result["sample_p_blue"] <= 1)
        self.assertEqual(sum(result["split_matches"].values()), 20)

    def test_overlapping_splits_block_publication(self):
        bad = json.loads(self.candidate.read_text())
        bad["splits"]["test"].append(bad["splits"]["train"][0])
        self.candidate.write_text(json.dumps(bad))
        before = self.published.read_text()
        with self.assertRaisesRegex(rift_tasks.PipelineError, "in both"):
            rift_tasks.publish_model(self.candidate, published=self.published)
        self.assertEqual(self.published.read_text(), before)

    def test_wrong_feature_order_blocks_publication(self):
        bad = json.loads(self.candidate.read_text())
        bad["features"] = list(reversed(bad["features"]))
        self.candidate.write_text(json.dumps(bad))
        with self.assertRaises(rift_tasks.PipelineError):
            rift_tasks.validate_model(self.candidate)

    def test_worse_than_baseline_blocks_unless_allowed(self):
        bad = json.loads(self.candidate.read_text())
        bad["metrics"]["validation"]["logistic"]["log_loss"] = 9.0
        self.candidate.write_text(json.dumps(bad))
        with self.assertRaisesRegex(rift_tasks.PipelineError, "baseline"):
            rift_tasks.validate_model(self.candidate)
        rift_tasks.validate_model(self.candidate, require_beats_baseline=False)


@unittest.skipUnless(importlib.util.find_spec("airflow"), "Airflow not installed")
class DagStructureTests(unittest.TestCase):
    def test_dag_imports_with_expected_order(self):
        from airflow.models.dagbag import DagBag

        bag = DagBag(dag_folder=str(ROOT / "dags"), include_examples=False)
        self.assertEqual(bag.import_errors, {})
        dag = bag.get_dag("rift_pipeline")
        order = ["init_db", "ingest", "check_training_data", "train_candidate", "publish_model"]
        self.assertEqual(set(dag.task_ids), set(order))
        for upstream, downstream in zip(order, order[1:]):
            self.assertIn(downstream, dag.get_task(upstream).downstream_task_ids)


if __name__ == "__main__":
    unittest.main()

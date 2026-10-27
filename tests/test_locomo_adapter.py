"""只验证公开数据适配和证据计分，不调用服务。"""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("locomo_eval", ROOT / "scripts" / "eval_locomo_retrieval.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class AdapterTests(unittest.TestCase):
    def fixture(self):
        return [{"sample_id": "synthetic", "conversation": {
            "speaker_a": "A", "speaker_b": "B", "session_2_date_time": "no timezone supplied",
            "session_2": [{"speaker": "B", "dia_id": "D2:1", "text": "raw second"}],
            "session_1_date_time": "original date", "session_1": [
                {"speaker": "A", "dia_id": "D1:1", "text": "raw first"}]},
            "qa": [{"question": "q", "answer": "GOLD_DO_NOT_INGEST", "evidence": ["D1:1", "D2:1"], "category": 2},
                   {"question": "no evidence", "evidence": [], "answer": "x"}]}]

    def test_adapter_keeps_source_order_and_excludes_annotations(self):
        base = ROOT / "work"
        base.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base) as folder:
            path = Path(folder) / "synthetic.json"
            path.write_text(json.dumps(self.fixture()), encoding="utf-8")
            data = module.load_conversation(path)
        self.assertEqual([r["source_id"] for r in data["records"]], ["D1:1", "D2:1"])
        self.assertEqual(len(data["questions"]), 1)
        payloads = module.build_payloads(data, "local-user")
        self.assertNotIn("GOLD_DO_NOT_INGEST", json.dumps(payloads))
        self.assertEqual(len(payloads), 2)
        self.assertEqual(payloads[0]["messages"][0]["role"], "user")
        self.assertNotIn("timestamp", payloads[0]["messages"][0])

    def test_source_mapping_ignores_server_ids(self):
        items = [{"id": "123", "content": "[role=user]\n[locomo_id=D1:3; speaker=A]\nraw"}]
        self.assertEqual(module.source_ids(items), ["D1:3"])

    def test_evidence_metrics(self):
        result = module.evidence_metrics(["D1:1", "D2:2"], ["D9:9", "D1:1"])
        self.assertEqual(result["recall"], 0.5)
        self.assertEqual(result["hit"], 1)
        self.assertEqual(result["all_evidence_found"], 0)
        self.assertEqual(result["reciprocal_rank"], 0.5)


if __name__ == "__main__":
    unittest.main(verbosity=2)

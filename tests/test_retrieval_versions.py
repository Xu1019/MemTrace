"""离线行为测试。二维向量只用于算法测试；接口嵌入服务替换为本地假实现。"""
import importlib
import json
import math
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import types
import unittest
from contextlib import closing
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ranking import add_context, bm25_rank, find_neighbors, rank_memories, rrf_fuse
import memory_db


def memory(mid, text, vector=None, user="u", session="s", batch="r", index=0, timestamp=None):
    return {"id": mid, "content": text, "embedding": json.dumps(vector) if vector is not None else None,
            "user_id": user, "session_id": session, "source_request_id": batch,
            "message_index": index, "role": "user", "timestamp": timestamp}


class RankingTests(unittest.TestCase):
    def test_chinese_local_phrase(self):
        rows = [memory(1, "我目前住在杭州。"), memory(2, "我住在成都。")]
        self.assertEqual(bm25_rank(rows, "杭州")[0]["id"], "1")

    def test_rare_identifier(self):
        rows = [memory(1, "passport XK123456"), memory(2, "passport other")]
        self.assertEqual(bm25_rank(rows, "XK123456")[0]["id"], "1")

    def test_dense_preserves_a_order_and_scores(self):
        rows = [memory(1, "apple", [1, 0]), memory(2, "pear", [0.8, 0.2])]
        result = rank_memories(rows, "u", "fruit", [1, 0], mode="dense", top_k=2)
        self.assertEqual([r["id"] for r in result], ["1", "2"])
        self.assertAlmostEqual(result[1]["score"], 0.8 / math.sqrt(0.68))
        self.assertEqual(result[0]["content"], "apple")

    def test_missing_embedding_still_available_in_hybrid(self):
        rows = [memory(1, "unrelated", [1, 0]), memory(2, "ZX-9357", None)]
        result = rank_memories(rows, "u", "ZX-9357", [1, 0], top_k=2, mode="hybrid")
        self.assertEqual({r["id"] for r in result}, {"1", "2"})

    def test_bm25_no_hit_is_empty(self):
        self.assertEqual(bm25_rank([memory(1, "apple")], "passport"), [])

    def test_rrf_uses_identity_and_has_no_duplicates(self):
        a = [{"id": "1", "content": "one", "score": 0.9}]
        b = [{"id": "1", "content": "one", "score": 100},
             {"id": "2", "content": "two", "score": 99}]
        result = rrf_fuse([a, b])
        self.assertEqual(len(result), 2)
        self.assertAlmostEqual(result[0]["score"], 2 / 61)

    def test_user_scope_at_algorithm_entry(self):
        rows = [memory(1, "secret", [1, 0], user="other"), memory(2, "mine", [0, 1])]
        result = rank_memories(rows, "u", "secret", [1, 0], top_k=100, mode="context")
        self.assertEqual([r["id"] for r in result], ["2"])
        self.assertEqual(rank_memories(rows, "missing", "secret", [1, 0]), [])

    def test_neighbor_uses_message_index_not_database_id(self):
        seed = memory(1, "She works at the hospital", index=1)
        before = memory(90, "My sister changed jobs", index=0)
        self.assertEqual(find_neighbors(seed, [seed, before])[0][0]["id"], 90)

    def test_neighbor_never_crosses_user_or_session(self):
        seed = memory(1, "seed", index=1)
        wrong_session = memory(2, "different session", session="other", index=0)
        wrong_user = memory(3, "secret", user="other", index=0)
        self.assertEqual(find_neighbors(seed, [seed, wrong_session, wrong_user]), [])

    def test_ambiguous_old_order_does_not_expand(self):
        seed = memory(1, "seed", batch=None, index=None)
        old = memory(2, "old", batch=None, index=None)
        self.assertEqual(find_neighbors(seed, [seed, old]), [])

    def test_source_time_can_order_different_requests(self):
        seed = memory(1, "later", batch="b", index=0, timestamp=2000)
        before = memory(99, "earlier", batch="a", index=0, timestamp=1000)
        self.assertEqual(find_neighbors(seed, [seed, before])[0][0]["id"], 99)

    def test_c_adds_context_with_budget_and_no_duplicates(self):
        rows = [memory(1, "妹妹换工作", [0, 1], index=0),
                memory(2, "星河医院", [1, 0], index=1),
                memory(3, "coffee", [0.8, 0.2], session="other", batch="b"),
                memory(4, "park", [0.7, 0.3], session="park", batch="c"),
                memory(5, "星河医院电话", None, session="phone", batch="d")]
        b = rank_memories(rows, "u", "星河医院", [1, 0], top_k=3, mode="hybrid")
        c = rank_memories(rows, "u", "星河医院", [1, 0], top_k=3, mode="context")
        self.assertNotIn("1", {r["id"] for r in b})
        self.assertIn("1", {r["id"] for r in c})
        self.assertEqual(len(c), 3)
        self.assertEqual(len({r["id"] for r in c}), 3)
        self.assertEqual([r["score"] for r in c], sorted((r["score"] for r in c), reverse=True))

    def test_top_k_one_keeps_anchor(self):
        rows = [memory(1, "before", [0, 1], index=0), memory(2, "hospital", [1, 0], index=1)]
        result = rank_memories(rows, "u", "hospital", [1, 0], top_k=1, mode="context")
        self.assertEqual([r["id"] for r in result], ["2"])

    def test_timestamp_zero_is_not_missing_and_raw_text_is_preserved(self):
        text = "原文：I live here.\n完整保留。"
        result = rank_memories([memory(1, text, [1, 0], timestamp=0)], "u", "live", [1, 0], mode="context")
        self.assertIn(text, result[0]["content"])
        self.assertTrue(result[0]["created_at"].startswith("1970-01-01"))

    def test_invalid_inputs(self):
        self.assertEqual(rank_memories([], "u", "q", mode="dense"), [])
        self.assertEqual(rank_memories([memory(1, "a")], "u", "  "), [])
        with self.assertRaises(ValueError):
            rank_memories([memory(1, "a", [1, 2])], "u", "q", [1], mode="dense")


class OfflineApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        scratch = Path(os.getenv("AML_TEST_TMP", ROOT / "work"))
        scratch.mkdir(parents=True, exist_ok=True)
        cls.directory = tempfile.TemporaryDirectory(prefix="abc-tests-", dir=scratch)
        cls.db_patch = patch.object(memory_db, "DB_PATH", Path(cls.directory.name) / "test.db")
        cls.db_patch.start()
        cls.environment = patch.dict(os.environ, {"MEMORY_API_KEY": "offline-test-credential", "MEMORY_RETRIEVAL_MODE": "context"})
        cls.environment.start()
        cls.fake_embedding = types.ModuleType("embedding")
        cls.embedding_calls = []

        def embed_texts(texts):
            cls.embedding_calls.append(list(texts))
            return [([1.0, 0.0] if "杭州" in text else [0.0, 1.0]) + [0.0] * 1022 for text in texts]

        cls.fake_embedding.embed_texts = embed_texts
        cls.modules = patch.dict(sys.modules, {"embedding": cls.fake_embedding})
        cls.modules.start()
        cls.api = importlib.import_module("app")
        from fastapi.testclient import TestClient
        cls.client = TestClient(cls.api.app)
        cls.headers = {"Authorization": "Bearer offline-test-credential"}

    @classmethod
    def tearDownClass(cls):
        cls.client.close()
        cls.modules.stop()
        cls.environment.stop()
        cls.db_patch.stop()
        cls.directory.cleanup()

    def payload(self, request_id):
        return {"request_id": request_id, "user_id": "api-u", "session_id": "api-s",
                "messages": [{"role": "user", "content": "我目前住在杭州。", "timestamp": 1000},
                             {"role": "assistant", "content": "已经记下。", "timestamp": 2000}]}

    def test_contract_retry_conflict_restart_and_all_modes(self):
        payload = self.payload("api-contract")
        body = self.client.post("/add", json=payload, headers=self.headers)
        self.assertEqual(body.status_code, 200)
        self.assertEqual(body.json(), {"success": True, "request_id": "api-contract", "user_id": "api-u", "session_id": "api-s"})
        calls = len(self.embedding_calls)
        self.assertEqual(self.client.post("/add", json=payload, headers=self.headers).status_code, 200)
        self.assertEqual(len(self.embedding_calls), calls)
        conflict = dict(payload, messages=[{"role": "user", "content": "changed"}])
        self.assertEqual(self.client.post("/add", json=conflict, headers=self.headers).status_code, 409)
        rows = memory_db.get_memories("api-u")
        self.assertEqual(len(rows), 2)
        self.assertEqual([r["message_index"] for r in rows], [0, 1])
        memory_db.init_db()  # 重新打开数据库并初始化，数据和来源顺序仍存在。
        for mode in ("dense", "hybrid", "context", "bm25"):
            with patch.dict(os.environ, {"MEMORY_RETRIEVAL_MODE": mode}):
                result = self.client.post("/search", json={"user_id": "api-u", "query": "杭州", "top_k": 100}, headers=self.headers)
                self.assertEqual(result.status_code, 200)
                self.assertTrue(result.json()["data"])
                self.assertLessEqual(len(result.json()["data"]), 100)
        self.assertEqual(len(memory_db.get_memories("api-u")), 2)

    def test_auth_validation_and_empty_user(self):
        self.assertEqual(self.client.get("/health").status_code, 200)
        self.assertEqual(self.client.post("/add", json=self.payload("no-key")).status_code, 401)
        query = {"user_id": "missing", "query": "杭州", "top_k": 3}
        self.assertEqual(self.client.post("/search", json=query, headers=self.headers).json(), {"data": []})
        query["top_k"] = 0
        self.assertEqual(self.client.post("/search", json=query, headers=self.headers).status_code, 422)

    def test_bm25_does_not_call_embedding(self):
        rows = [memory(1, "杭州")]
        from retrieval import search_memories
        before = len(self.embedding_calls)
        with patch("retrieval.get_memories", return_value=rows):
            self.assertTrue(search_memories("u", "杭州", mode="bm25"))
        self.assertEqual(len(self.embedding_calls), before)

    def test_failed_embedding_does_not_record_success(self):
        before = len(memory_db.get_memories("api-u"))
        with patch.object(self.fake_embedding, "embed_texts", side_effect=RuntimeError("synthetic provider failure")):
            with self.assertRaises(RuntimeError):
                memory_db.add_message_batch("failed-provider", "api-u", "api-s", [{"role": "user", "content": "text"}])
        self.assertEqual(len(memory_db.get_memories("api-u")), before)
        with closing(sqlite3.connect(memory_db.DB_PATH)) as connection:
            self.assertIsNone(connection.execute("SELECT request_id FROM add_requests WHERE request_id='failed-provider'").fetchone())

    def test_schema_upgrade_keeps_legacy_rows(self):
        legacy = Path(self.directory.name) / "legacy.db"
        with closing(sqlite3.connect(legacy)) as connection:
            connection.executescript("CREATE TABLE memories (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, session_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL, timestamp TEXT); CREATE TABLE add_requests (request_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, session_id TEXT NOT NULL);")
            connection.execute("INSERT INTO memories(user_id,session_id,role,content) VALUES('old','s','user','legacy text')")
            connection.commit()
        with patch.object(memory_db, "DB_PATH", legacy):
            memory_db.init_db()
            rows = memory_db.get_memories("old")
        self.assertEqual(rows[0]["content"], "legacy text")
        self.assertIsNone(rows[0]["message_index"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

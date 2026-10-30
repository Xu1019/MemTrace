"""纯离线长查询回归测试，不读取评测数据、不调用真实模型。"""
import math
import unittest
from unittest.mock import patch

from query_embedding import embed_query, split_text


class LengthError(Exception):
    status_code = 400
    body = {"code": "InvalidParameter", "message": "Range of input length should be [1, 33000]"}


class QueryEmbeddingTests(unittest.TestCase):
    def test_short_query_preserves_exact_request_and_vector(self):
        calls = []
        vector = [0.3, 0.9]
        def embed(parts):
            calls.append(parts)
            return [vector]
        self.assertIs(embed_query("普通查询", embed), vector)
        self.assertEqual(calls, [["普通查询"]])

    def test_accepted_large_query_is_not_changed(self):
        query = "a" * 33000
        calls = []
        def embed(parts):
            calls.extend(parts)
            return [[1, 0]]
        self.assertEqual(embed_query(query, embed), [1, 0])
        self.assertEqual(calls, [query])

    def test_split_preserves_unicode_and_boundaries(self):
        query = ("汉字🙂e\u0301\n " * 2000) + "TAIL_EVIDENCE"
        parts = list(split_text(query))
        self.assertEqual("".join(parts), query)
        self.assertTrue(all(len(part.encode("utf-8")) <= 6000 for part in parts))

    def test_over_gateway_limit_embeds_all_text_in_bounded_batches(self):
        query = "汉🙂" * 18000 + "TAIL_EVIDENCE"
        accepted = []
        def embed(parts):
            self.assertLessEqual(len(parts), 10)
            self.assertTrue(all(len(part.encode('utf-8')) <= 6000 for part in parts))
            accepted.extend(parts)
            return [[1, 0] for _ in parts]
        self.assertEqual(embed_query(query, embed), [1, 0])
        self.assertEqual("".join(accepted), query)

    def test_provider_token_limit_recursively_splits_without_text_loss(self):
        query = "abcdefghijklmnop" * 1000
        accepted = []
        def embed(parts):
            if any(len(part) > 1000 for part in parts):
                raise LengthError()
            accepted.extend(parts)
            return [[1, 0] for _ in parts]
        self.assertEqual(embed_query(query, embed), [1, 0])
        self.assertEqual("".join(accepted), query)

    def test_weighted_merge_uses_every_segment(self):
        query = "a" * 6000 + "b" * 30000
        def embed(parts):
            return [[1, 0] if part[0] == "a" else [0, 1] for part in parts]
        result = embed_query(query, embed)
        self.assertAlmostEqual(result[0], 1 / math.sqrt(26))
        self.assertAlmostEqual(result[1], 5 / math.sqrt(26))

    def test_whitespace_chunks_do_not_cause_empty_model_requests(self):
        calls = []
        def embed(parts):
            self.assertTrue(all(part.strip() for part in parts))
            calls.extend(parts)
            return [[1, 0] for _ in parts]
        self.assertEqual(embed_query(" " * 36000 + "meaningful tail", embed), [1, 0])
        self.assertTrue(calls[-1].endswith("meaningful tail"))

    def test_unrelated_provider_errors_propagate(self):
        for status, message in ((429, "rate limited"), (401, "bad key"), (400, "bad dimensions")):
            error = RuntimeError(message)
            error.status_code = status
            with self.subTest(status=status):
                def embed(parts):
                    raise error
                with self.assertRaises(RuntimeError) as raised:
                    embed_query("query", embed)
                self.assertIs(raised.exception, error)

    def test_unsplittable_length_error_is_not_hidden(self):
        def embed(parts):
            raise LengthError()
        with self.assertRaises(LengthError):
            embed_query("x", embed)

    def test_inconsistent_response_fails_explicitly(self):
        with self.assertRaises(ValueError):
            embed_query("query", lambda parts: [])
        with self.assertRaises(ValueError):
            embed_query("x" * 36000, lambda parts: [[1], [1, 0]] + [[1]] * (len(parts) - 2))

    def test_original_query_is_retained_for_ranking(self):
        import sys
        import types
        import retrieval
        query = "synthetic text " * 3000
        fake = types.ModuleType("embedding")
        fake.embed_texts = lambda parts: [[1, 0] for _ in parts]
        with patch.dict(sys.modules, {"embedding": fake}), \
             patch.object(retrieval, "get_memories", return_value=[{"embedding": [1, 0]}]), \
             patch.object(retrieval, "rank_memories", return_value=[]) as rank:
            retrieval.search_memories("u", query, mode="context")
        self.assertEqual(rank.call_args.args[2], query)


if __name__ == "__main__":
    unittest.main()

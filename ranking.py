"""不调用 API 的检索算法：便于先用人工向量学习和测试。

这些函数只接受当前用户的候选数据；入口仍再次按 user_id 筛选。
BM25 与 RRF 根据标准公式自行实现，没有复制模板的分词器或项目源码。
"""
from collections import Counter
from datetime import datetime, timezone
import json
import math
import re
import unicodedata

from retrieval_config import (
    CANDIDATE_MULTIPLIER, CONTEXT_RATIO, CONTEXT_SEEDS, CONTEXT_WINDOW,
    MIN_CANDIDATES, RRF_K,
)

STOPWORDS = set("a an the is are was were be been to of in on at for and or i me my you your what which who where do does did".split())
CJK = r"[\u3400-\u4dbf\u4e00-\u9fff]+"
PARTS = re.compile(CJK + r"|[^\W_\u3400-\u4dbf\u4e00-\u9fff]+", re.UNICODE)


def tokenize(text):
    """英文按词；中文用单字和连续双字，不需要下载分词模型。"""
    text = unicodedata.normalize("NFKC", text).casefold()
    tokens = []
    for part in PARTS.findall(text):
        if re.fullmatch(CJK, part):
            tokens.extend(part)
            tokens.extend(part[i:i + 2] for i in range(len(part) - 1))
        elif part not in STOPWORDS:
            tokens.append(part)
    return tokens


def cosine_similarity(vector_a, vector_b):
    if len(vector_a) != len(vector_b):
        raise ValueError("向量维度不一致，请检查模型配置")
    if not all(math.isfinite(float(x)) for x in (*vector_a, *vector_b)):
        raise ValueError("向量包含非有限数值")
    dot = sum(a * b for a, b in zip(vector_a, vector_b))
    length_a = math.sqrt(sum(a * a for a in vector_a))
    length_b = math.sqrt(sum(b * b for b in vector_b))
    return dot / (length_a * length_b) if length_a and length_b else 0.0


def dense_rank(memories, query_vector):
    results = []
    for memory in memories:
        embedding = memory.get("embedding")
        if embedding is None:
            continue
        if query_vector is None:
            raise ValueError("向量检索需要 query_vector")
        vector = json.loads(embedding) if isinstance(embedding, str) else embedding
        results.append({"id": str(memory["id"]), "content": memory["content"],
                        "score": cosine_similarity(query_vector, vector)})
    # Python 的稳定排序保持同分记录原来的顺序，与 A 版一致。
    return sorted(results, key=lambda item: item["score"], reverse=True)


def bm25_rank(memories, query, k1=1.2, b=0.75):
    """只统计当前用户的词频，BM25 无命中时不返回零分记录。"""
    query_terms = sorted(set(tokenize(query)))
    if not memories or not query_terms:
        return []
    documents = [tokenize(memory["content"]) for memory in memories]
    frequencies = [Counter(document) for document in documents]
    lengths = [len(document) for document in documents]
    average = sum(lengths) / len(lengths)
    if not average:
        return []
    document_frequency = Counter()
    for frequency in frequencies:
        document_frequency.update(frequency.keys())

    results = []
    for memory, frequency, length in zip(memories, frequencies, lengths):
        score = 0.0
        for term in query_terms:
            tf = frequency.get(term, 0)
            if not tf:
                continue
            df = document_frequency[term]
            idf = math.log(1 + (len(memories) - df + 0.5) / (df + 0.5))
            score += idf * tf * (k1 + 1) / (tf + k1 * (1 - b + b * length / average))
        if score > 0:
            results.append({"id": str(memory["id"]), "content": memory["content"], "score": score})
    return sorted(results, key=lambda item: item["score"], reverse=True)


def rrf_fuse(rankings, k=RRF_K):
    """按同一 memory_id 合并名次；不直接相加两路原始分数。"""
    if k <= 0:
        raise ValueError("RRF 的 k 必须为正数")
    merged = {}
    for ranking in rankings:
        seen_in_channel = set()
        for rank, item in enumerate(ranking, start=1):
            memory_id = str(item["id"])
            if memory_id in seen_in_channel:
                continue
            seen_in_channel.add(memory_id)
            if memory_id not in merged:
                merged[memory_id] = {"id": memory_id, "content": item["content"], "score": 0.0}
            merged[memory_id]["score"] += 1 / (k + rank)
    return sorted(merged.values(), key=lambda item: item["score"], reverse=True)


def timestamp_ms(memory):
    value = memory.get("timestamp")
    if value is None or isinstance(value, bool):
        return None
    try:
        # 只认可收到的整数毫秒值，不把旧演示字符串或服务器时间当来源时间。
        return int(str(value))
    except (TypeError, ValueError):
        return None


def source_time(memory):
    value = timestamp_ms(memory)
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc).isoformat()
    except (ValueError, OverflowError, OSError):
        return None


def find_neighbors(seed, memories, window=CONTEXT_WINDOW):
    """优先使用同一 Add 的消息序号；跨 Add 仅使用完整且无歧义的来源时间。

    旧数据没有批次/序号且时间不明确时，不猜测全局自增 ID 是对话顺序。
    """
    session = [m for m in memories if m["user_id"] == seed["user_id"]
               and m["session_id"] == seed["session_id"]]
    batch_id = seed.get("source_request_id")
    batch_position = seed.get("message_index")
    batch = [m for m in session if batch_id and m.get("source_request_id") == batch_id]
    positions = [m.get("message_index") for m in batch]
    batch_usable = (isinstance(batch_position, int) and positions
                    and all(isinstance(p, int) for p in positions)
                    and len(set(positions)) == len(positions))
    by_position = {m["message_index"]: m for m in batch} if batch_usable else {}

    times = [timestamp_ms(m) for m in session]
    time_usable = all(t is not None for t in times) and len(set(times)) == len(times)
    ordered = sorted(session, key=timestamp_ms) if time_usable else []
    time_index = next((i for i, m in enumerate(ordered) if str(m["id"]) == str(seed["id"])), None)
    neighbors, seen = [], {str(seed["id"])}
    for distance in range(1, window + 1):
        for direction in (-1, 1):
            neighbor = by_position.get(batch_position + direction * distance) if batch_usable else None
            # 有批次时只在批次边缘才尝试时间顺序，避免跳过本批次内缺失序号。
            outside_batch = batch_usable and (batch_position + direction * distance < min(positions)
                                               or batch_position + direction * distance > max(positions))
            if neighbor is None and time_index is not None and (not batch_usable or outside_batch):
                index = time_index + direction * distance
                if 0 <= index < len(ordered):
                    neighbor = ordered[index]
            if neighbor is not None and str(neighbor["id"]) not in seen:
                seen.add(str(neighbor["id"]))
                neighbors.append((neighbor, distance))
    return neighbors


def add_context(ranking, memories, top_k, ratio=CONTEXT_RATIO, seeds=CONTEXT_SEEDS):
    """保留主要命中，把有限名额用于上下文；剩余名额仍按原排名填充。"""
    if not 0 <= ratio < 1:
        raise ValueError("上下文比例应在 [0, 1) 之间")
    if not ranking or top_k < 1:
        return []
    by_id = {str(memory["id"]): memory for memory in memories}
    limit = min(top_k, len(by_id))
    budget = min(limit - 1, math.ceil(limit * ratio)) if ratio else 0
    anchor_count = min(len(ranking), max(1, limit - budget))
    selected = {item["id"]: dict(item) for item in ranking[:anchor_count]}
    ranked_scores = {item["id"]: item["score"] for item in ranking}
    context_count = 0
    for seed_item in ranking[:min(anchor_count, seeds)]:
        for neighbor, distance in find_neighbors(by_id[seed_item["id"]], memories):
            memory_id = str(neighbor["id"])
            if memory_id in selected or context_count >= budget:
                continue
            selected[memory_id] = {"id": memory_id, "content": neighbor["content"],
                                  "score": max(ranked_scores.get(memory_id, 0), seed_item["score"] * 0.5 ** distance)}
            context_count += 1
    for item in ranking:
        if len(selected) >= limit:
            break
        selected.setdefault(item["id"], dict(item))
    results = sorted(selected.values(), key=lambda item: item["score"], reverse=True)
    for item in results:
        memory = by_id[item["id"]]
        timestamp = source_time(memory)
        role = memory.get("role", "unknown")
        header = f"[role={role}; source_time={timestamp or 'unknown'}]"
        item["content"] = header + "\n" + memory["content"]
        if timestamp:
            item["created_at"] = timestamp
    return results[:top_k]


def rank_memories(memories, user_id, query, query_vector=None, top_k=3, mode="context"):
    """教学与实服务共同调用的唯一算法入口；人工向量只用于离线演示。"""
    if mode not in {"dense", "bm25", "hybrid", "context"}:
        raise ValueError("未知检索模式")
    if not query.strip() or top_k < 1:
        return []
    memories = [m for m in memories if m.get("user_id") == user_id]
    if not memories:
        return []
    if mode == "bm25":
        return bm25_rank(memories, query)[:top_k]
    dense = dense_rank(memories, query_vector)
    if mode == "dense":
        return dense[:top_k]
    lexical = bm25_rank(memories, query)
    candidate_count = max(MIN_CANDIDATES, CANDIDATE_MULTIPLIER * top_k)
    fused = rrf_fuse([dense[:candidate_count], lexical[:candidate_count]])
    if mode == "hybrid":
        return fused[:top_k]
    return add_context(fused, memories, top_k)

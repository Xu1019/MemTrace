"""A=dense，B=hybrid，C=context。环境变量优先于默认值。"""
import os

DEFAULT_MODE = "context"
RRF_K = 60
CANDIDATE_MULTIPLIER = 3
MIN_CANDIDATES = 50
CONTEXT_RATIO = 0.25
CONTEXT_WINDOW = 1
CONTEXT_SEEDS = 10


def get_mode():
    mode = os.getenv("MEMORY_RETRIEVAL_MODE", DEFAULT_MODE).strip().lower()
    if mode not in {"dense", "bm25", "hybrid", "context"}:
        raise ValueError("MEMORY_RETRIEVAL_MODE 应为 dense / bm25 / hybrid / context")
    return mode

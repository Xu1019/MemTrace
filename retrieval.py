from memory_db import get_memories
from ranking import cosine_similarity, rank_memories
from retrieval_config import get_mode


def search_memories(user_id, query, top_k=3, mode=None):
    """服务入口：读真实数据库；需要向量时才调用现有嵌入 API。"""
    mode = mode or get_mode()
    if not query.strip() or top_k < 1:
        return []

    memories = get_memories(user_id)
    if not memories:
        return []
    query_vector = None
    if mode != "bm25" and any(m.get("embedding") is not None for m in memories):
        from embedding import embed_texts
        query_vector = embed_texts([query])[0]
    return rank_memories(memories, user_id, query, query_vector, top_k, mode)


def semantic_search(user_id, query, top_k=3):
    """保留旧名称与 A 版语义，已有学习脚本仍可调用。"""
    return search_memories(user_id, query, top_k, mode="dense")

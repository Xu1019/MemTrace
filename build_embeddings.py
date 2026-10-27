from embedding import embed_texts

from memory_db import (
    init_db,
    get_pending_memories,
    save_embeddings
)


def main():
    init_db()

    processed = 0

    while True:
        # 每次只读取还没有向量的记录
        memories = get_pending_memories(limit=10)

        if not memories:
            break

        # 取出本批记忆的原文
        texts = [
            memory["content"]
            for memory in memories
        ]

        # 调用模型，生成向量
        vectors = embed_texts(texts)

        # 将向量保存到对应记录
        save_embeddings(memories, vectors)

        processed += len(memories)

        print(
            f"本批保存 {len(memories)} 条，"
            f"本次累计完成 {processed} 条"
        )

    print(f"处理结束，本次共生成并保存 {processed} 条向量。")


if __name__ == "__main__":
    main()
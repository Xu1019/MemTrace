from memory_db import init_db
from retrieval import semantic_search


if __name__ == "__main__":
    init_db()

    results = semantic_search(
        user_id="eval_user_001",
        query="我平时有什么爱好？",
        top_k=3
    )

    for memory in results:
        print(
            round(memory["score"], 4),
            memory["content"]
        )
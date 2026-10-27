import math

from embedding import embed_texts


def cosine_similarity(vector_a, vector_b):
    dot_product = sum(
        a * b for a, b in zip(vector_a, vector_b)
    )

    length_a = math.sqrt(sum(a * a for a in vector_a))
    length_b = math.sqrt(sum(b * b for b in vector_b))

    if length_a == 0 or length_b == 0:
        return 0

    return dot_product / (length_a * length_b)


def main():
    query = "我现在居住在哪座城市？"

    memories = [
        "我住在北京市。",
        "我平时喜欢游泳。",
        "周末我常和朋友打篮球。"
    ]

    # 调用 embedding.py 中的函数，获取问题和记忆的向量
    vectors = embed_texts([query, *memories])

    query_vector = vectors[0]
    memory_vectors = vectors[1:]

    scored_memories = []

    for text, vector in zip(memories, memory_vectors):
        score = cosine_similarity(query_vector, vector)
        scored_memories.append((score, text))

    scored_memories.sort(reverse=True)

    for score, text in scored_memories:
        print(round(score, 4), text)


if __name__ == "__main__":
    main()
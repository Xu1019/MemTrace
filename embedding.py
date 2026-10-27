import os

from openai import OpenAI


api_key = os.getenv("DASHSCOPE_API_KEY")

if not api_key:
    raise RuntimeError("请设置 DASHSCOPE_API_KEY")


client = OpenAI(
    api_key=api_key,
    base_url=(
        "https://ws-yf00g2y1b0g4kj5n."
        "cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
    )
)


def embed_texts(texts):
    if not texts:
        return []

    response = client.embeddings.create(
        model="text-embedding-v4",
        input=texts,
        dimensions=1024,
        encoding_format="float"
    )

    items = sorted(
        response.data,
        key=lambda item: item.index
    )

    return [item.embedding for item in items]
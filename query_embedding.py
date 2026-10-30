"""模型输入长度兼容：完整分段，不修改传给词法检索的原查询。"""
import math


MAX_DIRECT_CHARS = 33000  # 提前分段的字符阈值；服务端 Token 限制仍另行检查。
CHUNK_UTF8_BYTES = 6000  # 保守的初始分段预算，不等同于精确 Token 数。
MAX_BATCH = 10


def split_text(text, byte_budget=CHUNK_UTF8_BYTES):
    """按 Unicode 字符边界分段，拼接结果与输入逐字一致。"""
    if byte_budget < 4:
        raise ValueError("分段预算至少为 4 字节")
    start = size = 0
    for index, character in enumerate(text):
        width = len(character.encode("utf-8"))
        if size + width > byte_budget:
            yield text[start:index]
            start, size = index, 0
        size += width
    if start < len(text):
        yield text[start:]


def is_length_error(error):
    """仅识别已知的输入长度 400；鉴权、限流等错误保持原样抛出。"""
    if getattr(error, "status_code", None) != 400:
        return False
    body = getattr(error, "body", None)
    message = str(body) if isinstance(body, dict) else str(error)
    message = message.lower()
    return ("range of input length" in message
            or "maximum context length" in message
            or ("token" in message and ("exceed" in message or "too long" in message)))


def _embed_parts(parts, embed_texts):
    try:
        vectors = embed_texts(parts)
    except Exception as error:
        if not is_length_error(error):
            raise
        if len(parts) > 1:
            middle = len(parts) // 2
            return (_embed_parts(parts[:middle], embed_texts)
                    + _embed_parts(parts[middle:], embed_texts))
        text = parts[0]
        if len(text) <= 1:
            raise
        middle = len(text) // 2
        smaller = [part for part in (text[:middle], text[middle:]) if part.strip()]
        return _embed_parts(smaller, embed_texts)
    if len(vectors) != len(parts):
        raise ValueError("查询分段与向量数量不一致")
    return list(zip(vectors, map(len, parts)))


def embed_query(query, embed_texts):
    if not query.strip():
        raise ValueError("查询不能为空")
    # 已经可被服务接受的普通查询完全沿用原路径、原向量。
    if len(query) <= MAX_DIRECT_CHARS:
        try:
            vectors = embed_texts([query])
        except Exception as error:
            if not is_length_error(error):
                raise
        else:
            if len(vectors) != 1:
                raise ValueError("查询向量数量应为 1")
            return vectors[0]
    # 纯空白段不单独请求模型；其余字符均参与向量化，不截断正文。
    parts = [part for part in split_text(query) if part.strip()]
    weighted = []
    for start in range(0, len(parts), MAX_BATCH):
        weighted.extend(_embed_parts(parts[start:start + MAX_BATCH], embed_texts))
    dimensions = len(weighted[0][0])
    if not dimensions or any(len(vector) != dimensions for vector, _ in weighted):
        raise ValueError("查询分段向量维度不一致")
    total = sum(weight for _, weight in weighted)
    merged = [math.fsum(vector[i] * weight / total for vector, weight in weighted)
              for i in range(dimensions)]
    if not all(math.isfinite(value) for value in merged):
        raise ValueError("查询分段向量包含非有限值")
    norm = math.sqrt(math.fsum(value * value for value in merged))
    return [value / norm for value in merged] if norm else merged

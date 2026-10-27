import sqlite3
from pathlib import Path
import json
import hashlib
import os

DB_PATH = Path(os.getenv("MEMORY_DB_PATH", str(Path(__file__).resolve().parent / "memories.db")))


class RequestConflictError(ValueError):
    """同一个 request_id 携带了不同的请求内容。"""


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)

    try:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                timestamp TEXT
            )
        """)


        #增加“已处理请求”表
        connection.execute("""
            CREATE TABLE IF NOT EXISTS add_requests (
                request_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                session_id TEXT NOT NULL
            )
        """)                            


        # 读取当前表中的列名
        columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(memories)"
            ).fetchall()
        }

        # 旧数据库没有 request_id 时，补上这一列
        if "request_id" not in columns:
            connection.execute(
                "ALTER TABLE memories ADD COLUMN request_id TEXT"
            )
# 同一个 request_id 最多只能出现一次，限制 request_id 不能重复


        if "embedding" not in columns:
            connection.execute(
                "ALTER TABLE memories ADD COLUMN embedding TEXT"
            )
        # C 版只对有来源顺序依据的消息补上下文；旧数据不伪造序号。
        for name, declaration in (("source_request_id", "TEXT"), ("message_index", "INTEGER")):
            if name not in columns:
                connection.execute(f"ALTER TABLE memories ADD COLUMN {name} {declaration}")
        request_columns = {row[1] for row in connection.execute("PRAGMA table_info(add_requests)")}
        if "payload_hash" not in request_columns:
            connection.execute("ALTER TABLE add_requests ADD COLUMN payload_hash TEXT")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_memories_user_session ON memories(user_id, session_id)")
#这会给已有表增加字段。旧记录会暂时没有向量，后面我们再给它们补上


        connection.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS
            idx_memories_request_id
            ON memories(request_id)
        """)

        connection.commit()
    finally:
        connection.close()


def add_memory(request_id, user_id, session_id, content, timestamp):
    # 请求编号或记忆内容为空，就不写入
    if not request_id.strip() or not content.strip():
        return None

    connection = sqlite3.connect(DB_PATH)

    try:
        cursor = connection.execute(
            """
            INSERT INTO memories
                (request_id, user_id, session_id, role, content, timestamp)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(request_id) DO NOTHING              
            """,                                #如果插入时发现 request_id 已经存在，就跳过这次插入，不报重复编号错误
            (
                request_id,
                user_id,
                session_id,
                "user",
                content,
                timestamp
            )
        )

        connection.commit()

        # rowcount == 1 表示这次确实新增了一条
        if cursor.rowcount == 1:
            return cursor.lastrowid

        # 没新增，说明这个 request_id 已经存在，返回原记录的 id
        row = connection.execute(
            "SELECT id FROM memories WHERE request_id = ?",
            (request_id,)
        ).fetchone()

        return row[0] if row else None
    finally:
        connection.close()


#加入批量写入函数
def add_message_batch(request_id, user_id, session_id, messages):
    # 1. 检查输入
    if not request_id.strip():
        raise ValueError("request_id 不能为空")

    if not user_id.strip() or not session_id.strip():
        raise ValueError("user_id 和 session_id 不能为空")

    if not messages:
        raise ValueError("messages 不能为空")

    for message in messages:
        if message["role"] not in ["user", "assistant"]:
            raise ValueError("role 只能是 user 或 assistant")

        if not message["content"].strip():
            raise ValueError("消息内容不能为空")

    canonical_messages = [{"role": m["role"], "content": m["content"], "timestamp": m.get("timestamp")}
                          for m in messages]
    payload_hash = hashlib.sha256(json.dumps(
        {"user_id": user_id, "session_id": session_id, "messages": canonical_messages},
        ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")).hexdigest()

    # 2. 新账本校验负载；旧账本没有哈希时保留以前的重试行为。
    connection = sqlite3.connect(DB_PATH)

    try:
        existing = connection.execute(
            "SELECT payload_hash, user_id, session_id FROM add_requests WHERE request_id = ?",
            (request_id,)
        ).fetchone()
    finally:
        connection.close()

    if existing:
        if existing[1:] != (user_id, session_id) or (existing[0] is not None and existing[0] != payload_hash):
            raise RequestConflictError("request_id 与已有请求内容冲突")
        print("重复请求，跳过写入：", request_id)
        return False

    # 3. 生成向量，此时还没有开始数据库写入事务
    from embedding import embed_texts

    vectors = []

    for start in range(0, len(messages), 10):
        batch = messages[start:start + 10]

        texts = [
            message["content"]
            for message in batch
        ]

        batch_vectors = embed_texts(texts)

        if len(batch_vectors) != len(batch):
            raise ValueError("返回的向量数量与消息数量不一致")

        vectors.extend(batch_vectors)

    if any(len(vector) != 1024 for vector in vectors):
        raise ValueError("向量维度应当为 1024")

    # 4. 原文、向量和请求编号一起保存
    connection = sqlite3.connect(DB_PATH)

    try:
        connection.execute("BEGIN IMMEDIATE")

        # 防止生成向量期间，同一个请求已被另一次调用处理
        existing = connection.execute(
            "SELECT payload_hash, user_id, session_id FROM add_requests WHERE request_id = ?",
            (request_id,)
        ).fetchone()

        if existing:
            if existing[1:] != (user_id, session_id) or (existing[0] is not None and existing[0] != payload_hash):
                raise RequestConflictError("request_id 与已有请求内容冲突")
            connection.rollback()
            return False

        connection.execute(
            """
            INSERT INTO add_requests
                (request_id, user_id, session_id, payload_hash)
            VALUES (?, ?, ?, ?)
            """,
            (request_id, user_id, session_id, payload_hash)
        )

        for message_index, (message, vector) in enumerate(zip(messages, vectors)):
            connection.execute(
                """
                INSERT INTO memories
                    (
                        user_id,
                        session_id,
                        role,
                        content,
                        timestamp,
                        embedding,
                        source_request_id,
                        message_index
                    )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    session_id,
                    message["role"],
                    message["content"],
                    message.get("timestamp"),
                    json.dumps(vector),
                    request_id,
                    message_index
                )
            )

        connection.commit()
        print("已保存原文和向量：", len(messages), "条")

        return True

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()


def get_memories(user_id):
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row

    try:
        cursor = connection.execute(
            """
            SELECT *
            FROM memories
            WHERE user_id = ?
            ORDER BY id
            """,
            (user_id,)
        )

        rows = cursor.fetchall()

        return [dict(row) for row in rows]              #表示把每条数据库记录转换成字典，再组成列表
    finally:
        connection.close()



def search_memory(user_id, keyword):
    if not keyword.strip():
        return []

    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row

    try:
        cursor = connection.execute(
            """
            SELECT *
            FROM memories
            WHERE user_id = ?
              AND content LIKE ?
            ORDER BY id
            """,
            (user_id, f"%{keyword}%")
        )

        rows = cursor.fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


def get_pending_memories(limit=10):
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row

    try:
        cursor = connection.execute(
            """
            SELECT id, content
            FROM memories
            WHERE embedding IS NULL
            ORDER BY id
            LIMIT ?
            """,
            (limit,)
        )
#WHERE embedding IS NULL只找还没有向量的记录    LIMIT ?限制一次取出的数量。这里每批最多取 10 条，因为 text-embedding-v4 一次最多接收 10 条文本
        return [dict(row) for row in cursor.fetchall()]
    finally:
        connection.close()


def save_embeddings(memories, vectors):
    if len(memories) != len(vectors):
        raise ValueError("记忆数量与向量数量不一致")

    if any(len(vector) != 1024 for vector in vectors):
        raise ValueError("向量维度应当为 1024")

    connection = sqlite3.connect(DB_PATH)

    try:
        for memory, vector in zip(memories, vectors):
            connection.execute(
                """
                UPDATE memories
                SET embedding = ?
                WHERE id = ?
                  AND embedding IS NULL
                """,
                (
                    json.dumps(vector),
                    memory["id"]
                )
            )

        connection.commit()

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()



if __name__ == "__main__":
    init_db()

    request_id = "demo-request-001"

    # 第一次写入
    first_id = add_memory(
        request_id,
        "user_001",
        "session_demo",
        "这条记忆用于测试重复写入。",
        "2026-10-09"
    )

    # 用完全相同的 request_id 再试一次
    second_id = add_memory(
        request_id,
        "user_001",
        "session_demo",
        "这条记忆用于测试重复写入。",
        "2026-10-09"
    )

    print("第一次返回的 id：", first_id)
    print("第二次返回的 id：", second_id)

    results = search_memory(
        "user_001",
        "测试重复写入"
    )

    print("数据库中找到的记录数：", len(results))

    add_memory(
        "demo-request-002",
        "user_002",
        "session_demo",
        "这条记忆用于测试重复写入。",
        "2026-10-09"
    )

    user_001_results = search_memory("user_001", "测试重复写入")
    user_002_results = search_memory("user_002", "测试重复写入")

    print("user_001 找到：", len(user_001_results))
    print("user_002 找到：", len(user_002_results))

    for memory in results:
        print(memory)

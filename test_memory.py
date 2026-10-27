from memory_demo import memories, add_memory, search_memory

def test_memory():
    memories.clear()                #清空列表，每次测试从相同状态开始

    add_memory(
        "user_001",
        "session_001",
        "我住在北京。",
        "2026-09-01"
    )

    add_memory(
        "user_001",
        "session_002",
        "我搬到了上海。",
        "2026-10-01"
    )

    add_memory(
        "user_002",
        "session_003",
        "我在北京学习。",
        "2026-10-02"
    )

    # 1. 正确找回原文
    results = search_memory("user_001","北京")
    assert len(results) == 1
    assert results[0]["content"] == "我住在北京。"

    # 2. 旧记录没有被新记录覆盖
    results = search_memory("user_001", "上海")
    assert len(results) == 1
    assert results[0]["content"] == "我搬到了上海。"
    assert len(memories) == 3

    # 3. 同一个关键词，不同用户只能查到自己的记录
    results = search_memory("user_002", "北京")
    assert len(results) == 1
    assert results[0]["user_id"] == "user_002"
    assert results[0]["content"] == "我在北京学习。"

    # 4. 不存在的用户，不能查到其他人的记忆
    assert search_memory("user_003", "北京") == []

    # 5. 没有匹配内容，返回空列表
    assert search_memory("user_001", "广州") == []

    # 6. 空白查询不应返回所有记忆
    assert search_memory("user_001", "   ") == []

    # 7. 空白内容不应写入
    count_before = len(memories)

    result = add_memory(                        #add函数里有strip功能，所以results is None
        "user_001",
        "session_004",
        "   ",
        "2026-10-08"
    )

    assert result is None
    assert len(memories) == count_before

    print("全部测试通过！")


if __name__ == "__main__":
        test_memory()
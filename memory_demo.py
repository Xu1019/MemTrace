memories = []

def add_memory(user_id, session_id, content, timestamp):
    if not content.strip():
        return None

    memory = {
        "id" : len(memories) + 1,           #演示编号
        "user_id": user_id,
        "session_id": session_id,
        "role": "user",
        "content": content,
        "timestamp": timestamp
    }

    memories.append(memory)

    return memory

def search_memory(user_id, keyword):
    if not keyword.strip():
        return[]

    results = []

    for memory in memories:                 #是否是这个用户的记忆
        if memory["user_id"] != user_id:
            continue

        if keyword in memory["content"]:    #检查原文内容是否包含关键字
            results.append(memory)

    return results

#模拟
if __name__ == "__main__":                  #其他文件import此文件不会运行测试
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

    results = search_memory("user_001","北京")

    print("找到的记忆量：",len(results))

    for memory in results:
        print(memory)
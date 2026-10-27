from uuid import uuid4

import httpx

import os


def evidence_text(item):
    """C 版在原文前显示来源标签；内容断言仍检查完整原文。"""
    content = item["content"]
    return content.split("\n", 1)[1] if content.startswith("[role=") and "\n" in content else content
def post_json(client, path, payload):
    response = client.post(path, json=payload)

    assert response.status_code == 200, (
        f"{path} 请求失败："
        f"{response.status_code}，{response.text}"
    )

    return response.json()


def main():
    # 每次生成新的测试编号，避免混入以前的测试数据
    tag = uuid4().hex

    user_a = f"api_test_a_{tag}"
    user_b = f"api_test_b_{tag}"


    memory_api_key = os.getenv("MEMORY_API_KEY")

    if not memory_api_key:
        raise RuntimeError("请设置 MEMORY_API_KEY")


    with httpx.Client(
        base_url=os.getenv("MEMORY_API_BASE", "http://127.0.0.1:8000"),
        timeout=120,
        trust_env=False,
        headers={
            "Authorization": f"Bearer {memory_api_key}"
        }
    ) as client:

        # 1. 检查服务是否正常
        response = client.get("/health")
        assert response.status_code == 200, (
            f"健康检查失败："
            f"{response.status_code}，{response.text}"
        )
        print("通过：服务正常")

        # 2. 用户 A 写入两条记忆
        payload_a = {
            "request_id": f"request_a_{tag}",
            "user_id": user_a,
            "session_id": f"session_a_{tag}",
            "messages": [
                {"role": "user", "content": "我目前住在杭州。"},
                {"role": "user", "content": "我平时喜欢打篮球。"}
            ]
        }

        result = post_json(client, "/add", payload_a)

        assert result == {
            "success": True,
            "request_id": payload_a["request_id"],
            "user_id": user_a,
            "session_id": payload_a["session_id"]
        }, "Add 返回格式或编号不正确"

        print("通过：Add 返回成功且编号一致")

        # 完全相同的请求再提交一次
        post_json(client, "/add", payload_a)

        # 用户 B 写入另一条记忆
        post_json(client, "/add", {
            "request_id": f"request_b_{tag}",
            "user_id": user_b,
            "session_id": f"session_b_{tag}",
            "messages": [
                {"role": "user", "content": "我目前住在成都。"}
            ]
        })

        # 3. 用户 A 应只有两条，重复请求没有重复写入
        result = post_json(client, "/search", {
            "user_id": user_a,
            "query": "我住在哪座城市？",
            "top_k": 100
        })

        memories = result["data"]

        assert len(memories) == 2, "重复写入或返回数量异常"

        assert {evidence_text(item) for item in memories} == {
            "我目前住在杭州。",
            "我平时喜欢打篮球。"
        }, "用户 A 的记忆内容不正确"

        assert evidence_text(memories[0]) == "我目前住在杭州。", (
            "居住信息没有排在第一"
        )

        for item in memories:
            assert isinstance(item["id"], str), "id 应为字符串"
            assert isinstance(item["score"], (int, float)), (
                "score 应为数字"
            )

        print("通过：重复请求、语义排序和结果格式")

        # 4. 用户 B 只能找到自己的记忆
        result = post_json(client, "/search", {
            "user_id": user_b,
            "query": "我住在哪座城市？",
            "top_k": 100
        })

        assert len(result["data"]) == 1, "用户 B 记录数量异常"
        assert evidence_text(result["data"][0]) == "我目前住在成都。", (
            "发生用户记忆混用"
        )

        print("通过：用户隔离")

        # 5. top_k=1 时只能返回一条
        result = post_json(client, "/search", {
            "user_id": user_a,
            "query": "我喜欢什么运动？",
            "top_k": 1
        })

        assert len(result["data"]) == 1, "top_k 限制失效"
        assert evidence_text(result["data"][0]) == "我平时喜欢打篮球。", (
            "运动信息检索错误"
        )

        print("通过：返回数量限制和运动信息检索")

        # 6. 不存在的用户返回空列表
        result = post_json(client, "/search", {
            "user_id": f"unknown_{tag}",
            "query": "我住在哪里？",
            "top_k": 3
        })

        assert result == {"data": []}, "不存在的用户应返回空结果"
        print("通过：不存在的用户")

        # 7. 非法 top_k 应被拒绝
        response = client.post("/search", json={
            "user_id": user_a,
            "query": "我住在哪里？",
            "top_k": 0
        })

        assert response.status_code == 422, "非法参数没有被拒绝"
        print("通过：参数校验")

    print("全部接口检查通过！")


if __name__ == "__main__":
    main()

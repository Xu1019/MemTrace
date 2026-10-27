"""人工小例子：不联网、不读真实数据库、不需要 API Key。"""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ranking import rank_memories


def sample():
    def record(mid, content, vector, session="story", position=0, batch="demo"):
        return {"id": mid, "user_id": "demo-user", "session_id": session,
                "role": "user", "content": content, "timestamp": None,
                "embedding": json.dumps(vector) if vector is not None else None,
                "source_request_id": batch, "message_index": position}
    return [
        record(1, "我妹妹最近换工作了。", [0.1, 1.0], position=0),
        record(2, "她去了杭州的星河医院。", [1.0, 0.0], position=1),
        record(3, "医院附近有一家咖啡店。", [0.8, 0.2], session="other", batch="other"),
        record(4, "我周末常去公园散步。", [0.7, 0.3], session="weekend", batch="weekend"),
        record(5, "星河医院的预约电话是 555-0101。", None, session="phone", batch="phone"),
    ]


def main():
    memories = sample()
    query = "星河医院"
    print("注意：这是人工设置的二维向量，用于理解算法，不是真实模型或质量评测。")
    print("问题：", query)
    rows = {}
    for mode, label in (("dense", "A：纯向量"), ("hybrid", "B：向量 + BM25 + RRF"),
                        ("context", "C：B + 有预算的相邻消息补全")):
        results = rank_memories(memories, "demo-user", query, [1.0, 0.0], top_k=3, mode=mode)
        rows[mode] = results
        print("\n" + label)
        for item in results:
            print(f"  ID={item['id']} score={item['score']:.6f} {item['content']}")
    print("\n观察：A 缺少‘妹妹’这句上下文；B 能使用没有向量的电话记录；")
    print("C 把同一批对话里说明人物的前一句补回来。RRF 分数不是概率。")
    output = Path(__file__).resolve().parents[1] / "experiments" / "synthetic-ABC-demo.json"
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps({"synthetic": True, "query": query, "results": rows},
                                 ensure_ascii=False, indent=2), encoding="utf-8")
    print("人工示例结果保存于：", output)


if __name__ == "__main__":
    main()

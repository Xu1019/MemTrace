"""LoCoMo 原版的小规模 HTTP 检索评测。

plan 不联网、不写库；ingest/search 仅在用户显式运行时调用服务。
只摄入原始对话，不摄入 QA、答案、证据标注或作者生成的摘要。
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
ADAPTER_VERSION = "locomo-raw-source-v1"
EVIDENCE_ID = re.compile(r"D\d+:\d+")
SOURCE_MARKER = re.compile(r"\[locomo_id=(D\d+:\d+);")


def load_conversation(path, conversation_index=0):
    raw = path.read_bytes()
    data = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(data, list) or not 0 <= conversation_index < len(data):
        raise ValueError("conversation-index 超出范围")
    sample = data[conversation_index]
    conversation = sample["conversation"]
    sessions = sorted((key for key, value in conversation.items()
                       if re.fullmatch(r"session_\d+", key) and isinstance(value, list)),
                      key=lambda key: int(key.split("_")[1]))
    records, known_ids = [], set()
    for session in sessions:
        date_text = str(conversation.get(session + "_date_time", "unknown"))
        for order, turn in enumerate(conversation[session]):
            source_id = str(turn["dia_id"])
            if not EVIDENCE_ID.fullmatch(source_id) or source_id in known_ids:
                raise ValueError("来源消息 ID 无效或重复")
            text = turn["text"]
            if not isinstance(text, str) or not text.strip():
                raise ValueError("来源消息正文为空")
            known_ids.add(source_id)
            speaker = str(turn.get("speaker", "unknown"))
            # 两位说话人均为对话来源，不把第二位人类虚构成助手。
            # 日期原文无时区，因此保留原字符串，不伪造 UTC 毫秒时间。
            content = f"[locomo_id={source_id}; speaker={speaker}; session_date={date_text}]\n{text}"
            records.append({"source_id": source_id, "session": session, "order": order,
                            "message": {"role": "user", "content": content}})

    questions = []
    for index, qa in enumerate(sample["qa"]):
        evidence = qa.get("evidence") or []
        if isinstance(evidence, str):
            evidence = [evidence]
        gold_ids = {source_id for value in evidence
                    for source_id in EVIDENCE_ID.findall(str(value))}
        # 无证据题和来源不完整的题不计入本次 evidence-recall 小实验。
        if gold_ids and gold_ids <= known_ids:
            questions.append({"index": index, "question": qa["question"],
                              "category": qa.get("category"), "gold_ids": sorted(gold_ids)})
    return {"dataset_sha256": hashlib.sha256(raw).hexdigest(),
            "sample_id": str(sample.get("sample_id", conversation_index)),
            "conversation_index": conversation_index,
            "sessions": sessions, "records": records, "questions": questions,
            "total_questions": len(sample["qa"])}


def build_payloads(dataset, user_id, max_messages=20, max_words=2000):
    """按来源 session 分批，保持原消息顺序；这是本地适配器，不冒充官方冻结适配器。"""
    payloads = []
    for session in dataset["sessions"]:
        messages = [r["message"] for r in dataset["records"] if r["session"] == session]
        chunk, words, ordinal = [], 0, 0

        def emit():
            nonlocal chunk, words, ordinal
            if chunk:
                payloads.append({"request_id": f"{user_id}:{session}:chunk-{ordinal}",
                                 "user_id": user_id, "session_id": f"{user_id}:{session}",
                                 "messages": chunk})
                ordinal += 1
                chunk, words = [], 0

        for message in messages:
            count = len(message["content"].split())
            if count > max_words:
                raise ValueError("单条消息超过本地分批词数上限，请先检查数据")
            if chunk and (len(chunk) >= max_messages or words + count > max_words):
                emit()
            chunk.append(message)
            words += count
        emit()
    return payloads


def save_json(path, obj):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def source_ids(items):
    """将服务器 ID 映射回公开数据中的 dia_id，避免混用两种 ID。"""
    ordered, seen = [], set()
    for item in items:
        match = SOURCE_MARKER.search(item.get("content", ""))
        if match and match.group(1) not in seen:
            seen.add(match.group(1))
            ordered.append(match.group(1))
    return ordered


def evidence_metrics(gold_ids, retrieved_ids):
    gold = set(gold_ids)
    if not gold:
        raise ValueError("不能给无标注证据的问题计算召回率")
    matches = gold.intersection(retrieved_ids)
    rank = next((i for i, source_id in enumerate(retrieved_ids, 1) if source_id in gold), None)
    return {"recall": len(matches) / len(gold), "hit": int(bool(matches)),
            "all_evidence_found": int(matches == gold), "reciprocal_rank": 1 / rank if rank else 0.0}


def plan(dataset, count):
    payloads = build_payloads(dataset, "preview")
    embedding_batches = sum((len(p["messages"]) + 9) // 10 for p in payloads)
    print("数据：LoCoMo 原版公开数据；不是官方正式成绩复现包。")
    print("样本：", dataset["sample_id"])
    print("会话数：", len(dataset["sessions"]))
    print("将写入的原始消息数：", len(dataset["records"]))
    print("Add 请求数：", len(payloads))
    print("问题总数：", dataset["total_questions"])
    print("可用于证据召回评测的问题数：", len(dataset["questions"]))
    print("本次选取问题数：", min(count, len(dataset["questions"])))
    print("预计文档嵌入批次数（按现有每批最多 10 条，不含重试）：", embedding_batches)
    print("搜索阶段另外每题生成一次问题向量。plan 阶段没有调用任何 API。")
    print("数据 SHA256：", dataset["dataset_sha256"])


def load_run(args, dataset):
    if not args.run_id or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", args.run_id):
        raise ValueError("ingest/search 需要 --run-id，例如 locomo-c-01；仅使用字母数字下划线连字符")
    folder = ROOT / "experiments" / "locomo" / args.run_id
    folder.mkdir(parents=True, exist_ok=True)
    manifest_path = folder / "manifest.json"
    identity = {"adapter_version": ADAPTER_VERSION, "dataset_sha256": dataset["dataset_sha256"],
                "sample_id": dataset["sample_id"], "conversation_index": args.conversation_index,
                "base_url": args.base_url.rstrip("/")}
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if any(manifest.get(k) != v for k, v in identity.items()):
            raise ValueError("数据、样本、接口或适配器与已保存的 run 不一致，请保留旧记录并使用新的 run-id")
    else:
        if args.stage == "search":
            raise ValueError("找不到本次写入记录，请先用同一个 run-id 完成 ingest")
        manifest = {**identity, "created_at": datetime.now(timezone.utc).isoformat(),
                    "user_id": f"local-locomo:{args.run_id}:{dataset['sample_id']}",
                    "completed_adds": [], "source_message_count": len(dataset["records"]),
                    "annotations_ingested": False}
        save_json(manifest_path, manifest)
    return folder, manifest_path, manifest


def checked_json(response):
    if response.status_code != 200:
        raise RuntimeError(f"接口返回 HTTP {response.status_code}；请查看服务终端，不要更换 run-id 盲目重试")
    return response.json()


def run_http(args, dataset):
    import os
    import httpx
    key = os.getenv("MEMORY_API_KEY")
    if not key:
        raise ValueError("当前终端缺少 MEMORY_API_KEY，请从用户环境变量读取，不要粘贴到代码")
    folder, manifest_path, manifest = load_run(args, dataset)
    payloads = build_payloads(dataset, manifest["user_id"])
    with httpx.Client(base_url=args.base_url.rstrip("/"),
                      headers={"Authorization": f"Bearer {key}"},
                      timeout=httpx.Timeout(240, connect=10), trust_env=False) as client:
        health = checked_json(client.get("/health"))
        if health.get("retrieval_mode") != args.expected_mode:
            raise ValueError(f"服务当前模式不是 {args.expected_mode}，请先检查 /health")
        if args.stage == "ingest":
            done = set(manifest["completed_adds"])
            manifest["ingest_mode"] = args.expected_mode
            for index, payload in enumerate(payloads, start=1):
                request_id = payload["request_id"]
                if request_id in done:
                    print(f"[{index}/{len(payloads)}] 已完成，跳过")
                    continue
                start = time.perf_counter()
                body = checked_json(client.post("/add", json=payload))
                if body.get("success") is not True or any(body.get(k) != payload[k] for k in ("request_id", "user_id", "session_id")):
                    raise ValueError("Add 成功响应与请求 ID 不一致")
                done.add(request_id)
                manifest["completed_adds"] = sorted(done)
                save_json(manifest_path, manifest)
                print(f"[{index}/{len(payloads)}] 写入 {len(payload['messages'])} 条，耗时 {time.perf_counter()-start:.1f}s", flush=True)
            print("写入完成。原始消息数：", len(dataset["records"]))
            print("run-id：", args.run_id)
            print("下一步用同一个 run-id 运行 --stage search。")
            return

        expected_ids = {p["request_id"] for p in payloads}
        if not expected_ids <= set(manifest["completed_adds"]):
            raise ValueError("对话还未完整写入，请先用同一 run-id 继续 ingest")
        questions = dataset["questions"][:args.questions]
        cache_path = folder / f"retrieval-{args.expected_mode}-k{args.top_k}.jsonl"
        cached = {}
        if cache_path.exists():
            for line in cache_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    result = json.loads(line)
                    if result.get("dataset_sha256") != dataset["dataset_sha256"]:
                        raise ValueError("结果缓存的数据哈希不匹配")
                    cached[result["question_index"]] = result
        results = []
        for number, question in enumerate(questions, 1):
            index = question["index"]
            if index in cached:
                result = cached[index]
            else:
                start = time.perf_counter()
                # 注意这里只发送问题，不发送 answer、evidence 或 category。
                body = checked_json(client.post("/search", json={"user_id": manifest["user_id"],
                                    "query": question["question"], "top_k": args.top_k}))
                items = body.get("data")
                if not isinstance(items, list) or len(items) > args.top_k:
                    raise ValueError("Search 返回数量或 data 结构不正确")
                if any(not isinstance(i.get("id"), str) or not isinstance(i.get("content"), str) for i in items):
                    raise ValueError("Search 返回记录的 id/content 类型不正确")
                if len({i["id"] for i in items}) != len(items):
                    raise ValueError("Search 返回了重复的服务器记忆 ID")
                retrieved = source_ids(items)
                if len(retrieved) != len(items):
                    raise ValueError("返回证据缺少来源编号或发生重复映射，不能计算可信召回率")
                result = {"dataset_sha256": dataset["dataset_sha256"], "question_index": index,
                          "question": question["question"], "category": question["category"],
                          "gold_source_ids": question["gold_ids"], "retrieved_source_ids": retrieved,
                          "elapsed_seconds": time.perf_counter()-start,
                          "returned_chars": sum(len(i["content"]) for i in items),
                          "metrics": evidence_metrics(question["gold_ids"], retrieved), "items": items}
                with cache_path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(result, ensure_ascii=False) + "\n")
            results.append(result)
            print(f"[{number}/{len(questions)}] evidence recall={result['metrics']['recall']:.3f}，返回 {len(result['items'])} 条", flush=True)
        if not results:
            raise ValueError("没有可评测的问题")
        summary = {"official_score": False, "dataset": "original LoCoMo", "sample_id": dataset["sample_id"],
                   "run_id": args.run_id, "mode": args.expected_mode, "top_k": args.top_k, "questions": len(results),
                   "mean_evidence_recall": sum(r["metrics"]["recall"] for r in results)/len(results),
                   "hit_rate": sum(r["metrics"]["hit"] for r in results)/len(results),
                   "all_evidence_rate": sum(r["metrics"]["all_evidence_found"] for r in results)/len(results),
                   "mrr": sum(r["metrics"]["reciprocal_rank"] for r in results)/len(results),
                   "mean_search_seconds": sum(r["elapsed_seconds"] for r in results)/len(results),
                   "scope": "First N questions with complete annotated source IDs; no answer generation or answer grading"}
        stem = f"summary-{args.expected_mode}-k{args.top_k}-n{len(results)}"
        save_json(folder / (stem + ".json"), summary)
        lines = ["# LoCoMo 本地检索测试", "", "本报告只统计标注证据召回，不是答案正确率或 AML 官方成绩。", "",
                 f"样本：{dataset['sample_id']}；模式：{args.expected_mode}；Top K：{args.top_k}；题数：{len(results)}", "",
                 f"平均证据召回率：{summary['mean_evidence_recall']:.3f}",
                 f"至少命中一条证据的题目比例：{summary['hit_rate']:.3f}",
                 f"标注证据全部找回的题目比例：{summary['all_evidence_rate']:.3f}",
                 f"MRR：{summary['mrr']:.3f}", ""]
        for result in results:
            lines += [f"## 问题 {result['question_index']}", "", result["question"], "",
                      "标注证据：" + ", ".join(result["gold_source_ids"]),
                      "找回的标注证据：" + ", ".join(sorted(set(result["gold_source_ids"]) & set(result["retrieved_source_ids"]))),
                      f"证据召回率：{result['metrics']['recall']:.3f}", ""]
        report_path = folder / (stem + ".md")
        report_path.write_text("\n".join(lines), encoding="utf-8")
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        print("报告：", report_path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=("plan", "ingest", "search"), default="plan")
    parser.add_argument("--data", type=Path, default=ROOT / "datasets" / "locomo" / "locomo10.json")
    parser.add_argument("--conversation-index", type=int, default=0)
    parser.add_argument("--questions", type=int, default=20)
    parser.add_argument("--top-k", type=int, default=100)
    parser.add_argument("--run-id")
    parser.add_argument("--base-url", default="http://127.0.0.1:8001")
    parser.add_argument("--expected-mode", choices=("dense", "hybrid", "context", "bm25"), default="context")
    args = parser.parse_args()
    if args.questions < 1 or not 1 <= args.top_k <= 100:
        parser.error("questions 必须为正数；top-k 必须在 1..100")
    dataset = load_conversation(args.data, args.conversation_index)
    if args.stage == "plan":
        plan(dataset, args.questions)
    else:
        run_http(args, dataset)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n已中断。下次使用同一 run-id 和相同参数继续，勿更换数据或清空库。", file=sys.stderr)
        raise SystemExit(130)
    except Exception as error:
        # 不打印请求头、客户端对象或 key；模型供应商报错请查看服务端。
        import httpx
        if isinstance(error, httpx.TransportError):
            message = "网络连接失败或请求超时。先检查服务终端；完成后用同一 run-id 继续。"
        else:
            message = str(error)
        print("错误：" + message, file=sys.stderr)
        raise SystemExit(1)

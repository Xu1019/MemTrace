"""保存源码和模式配置，不保存密钥、数据库、数据集和虚拟环境。"""
import argparse
from datetime import datetime, timezone, timedelta
import hashlib
import importlib.metadata
import json
from pathlib import Path
import re
import zipfile

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("version", choices=["A", "B", "C"])
parser.add_argument("--revision", default="v1")
args = parser.parse_args()
if not re.fullmatch(r"[A-Za-z0-9_-]+", args.revision):
    raise SystemExit("revision 只能包含字母、数字、下划线和连字符")
mode = {"A": "dense", "B": "hybrid", "C": "context"}[args.version]
destination = ROOT / "versions" / f"{args.version}-{mode}-{args.revision}.zip"
destination.parent.mkdir(exist_ok=True)
if destination.exists():
    raise SystemExit("已有同名快照，不覆盖。请用 --revision v2 等新编号。")

files = list(ROOT.glob("*.py"))
for directory in ("tests", "scripts", "docs"):
    base = ROOT / directory
    if base.exists():
        files.extend(p for p in base.rglob("*") if p.is_file() and p.suffix in {".py", ".md"})
for name in ("requirements.txt", "README.md", ".gitignore"):
    if (ROOT / name).is_file():
        files.append(ROOT / name)

contents = {}
for path in sorted(set(files)):
    relative = path.relative_to(ROOT).as_posix()
    data = path.read_bytes()
    if path.name == "retrieval_config.py":
        data = re.sub(r'DEFAULT_MODE = "[a-z]+"', f'DEFAULT_MODE = "{mode}"', data.decode("utf-8")).encode("utf-8")
    if re.search(rb"\bsk-[A-Za-z0-9_-]{16,}", data) or re.search(rb"(?m)^\s*#\s*[A-Za-z0-9_-]{32,}\s*$", data):
        raise SystemExit(f"发现疑似密钥，请先检查文件：{relative}")
    contents[relative] = data

manifest = {"version": args.version, "mode": mode, "revision": args.revision,
            "saved_at_utc8": datetime.now(timezone(timedelta(hours=8))).isoformat(),
            "meaning": "Code/config checkpoint; not an official evaluation result",
            "sha256": {name: hashlib.sha256(data).hexdigest() for name, data in contents.items()},
            "runtime_packages": {name: importlib.metadata.version(name) for name in ("fastapi", "uvicorn", "httpx", "openai")}}
with zipfile.ZipFile(destination, "x", compression=zipfile.ZIP_DEFLATED) as archive:
    for name, data in contents.items():
        archive.writestr(name, data)
    archive.writestr("VERSION_MANIFEST.json", json.dumps(manifest, ensure_ascii=False, indent=2))
print("已保存：", destination)
print("外部 MEMORY_RETRIEVAL_MODE 环境变量仍优先；恢复快照时同时核对模式。")

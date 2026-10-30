# MemTrace

**迹忆 · 融合语义与关键词检索的可追溯对话记忆系统。**

[GitHub 仓库](https://github.com/Xu1019/MemTrace)

面向对话场景的记忆存储与检索服务。MemTrace 将原始消息保存到 SQLite，通过向量检索与 BM25 混合召回，并补充同一会话中的相邻消息，为上层应用提供有来源的记忆片段。

服务基于 FastAPI，提供写入、检索和健康检查接口。检索流程使用嵌入模型，不包含生成式大模型调用；回答生成可由调用方完成。

## 功能

- **混合检索**：结合语义相似度与关键词匹配，通过 RRF 融合排序。
- **上下文补全**：根据消息批次顺序或有效的来源时间，补充同一用户、同一会话中的相邻原文。
- **来源展示**：保留消息角色和来源时间；缺失时间标记为 `unknown`。
- **用户隔离**：按 `user_id` 存储和检索，避免跨用户召回。
- **幂等写入**：相同请求可安全重试；相同 `request_id` 携带不同内容时返回 HTTP 409。
- **持久化与鉴权**：使用 SQLite 保存原文和向量，写入及检索接口采用 Bearer Token 鉴权。

## 工作原理

1. 写入消息时，调用 `text-embedding-v4` 生成 1024 维向量，保存原文和来源信息。
2. 检索时，分别计算查询与记忆的向量余弦相似度，以及 BM25 词法得分。
3. 使用 RRF 融合两路候选排名。
4. 在返回数量预算内补充相邻上下文，按记忆 ID 去重并返回结果。

默认模式为 `context`。参数集中在 [`retrieval_config.py`](retrieval_config.py)：RRF 常数为 60，相邻窗口为前后各 1 条，上下文补全名额按返回上限的 25% 向上取整，并至少保留一个直接命中。没有可靠来源顺序时，不推测相邻关系。

查询长度兼容逻辑位于 `query_embedding.py`：服务可接受的普通查询沿用一次嵌入；超过 33000 字符的查询提前分段，其他查询遇到明确的输入长度 400 错误时也转为分段。初始分段按 6000 UTF-8 字节划分，每批最多 10 段；若仍触及模型长度限制，继续细分。各段向量按字符数加权平均后归一化，BM25 始终使用完整原始查询。纯空白段不单独请求模型，其余文本不截断。长查询因此可能产生多次嵌入调用；该聚合向量不等同于模型直接编码整段文本的结果。鉴权、限流等其他错误不由此逻辑吞掉。Add 和已有数据库向量保持原实现。

## 参考来源与方法改动

MemTrace 以现有的 FastAPI / SQLite 向量检索实现为基础，结合标准 BM25 与 RRF 方法进行扩展。主要设计参考如下：

- **[ReFind](https://github.com/imlrz/ReFind)**（Ruizhe Li 等）：参考其基于原始对话的 BM25 检索与相邻上下文扩展思路。原始工作见论文 [When Your Agent Opens the Chat App: Agent-Controlled Search over Raw Chat Logs Rivals Structured Memory](https://arxiv.org/abs/2608.12888)。
- **[FlowGrid AML Retriever](https://github.com/dlxeva/flowgrid-aml-retriever)**（维护者账号：dlxeva）：参考其保留原始消息、关联来源 ID、结合邻接上下文与融合排序的设计，方法说明见该项目 README。
- **[AML 官方接入规范](https://agentmemoryleaderboard.ai/api-guide)**：用于对齐 Add / Search 请求响应、用户隔离和同步写入行为。

相对原有的纯向量检索，MemTrace 增加了 BM25 词法召回、RRF 排名融合，以及有数量预算的同会话上下文补全；同时保留角色和来源时间，并完善请求幂等与内容冲突检查。词法处理包含中文单字与相邻双字匹配。

上述开源项目作为方法设计参考，未直接移植其检索源码；`ranking.py` 中的 BM25、RRF 和上下文逻辑按标准公式及本项目需求单独实现。MemTrace 未复现 ReFind 的 LLM 多轮检索规划，也未实现 FlowGrid 的完整多视图索引与时间更新排序，当前流程不包含生成式 LLM 调用。

## Linux 服务器部署

Linux 部署步骤、后台服务和 Nginx 配置见 [服务器部署说明](docs/DEPLOYMENT.md)。

已验证环境为 Ubuntu 22.04 / Python 3.10，使用 systemd 管理后台服务，通过 Nginx 提供 HTTP 入口。

## Windows 本地运行

以下命令使用 Windows PowerShell，假定项目位于 `E:\MemTrace`。已验证的运行环境为 Python 3.13.2。

### 安装依赖

已有可用的 `.venv` 时可跳过此步骤。

```powershell
cd E:\MemTrace
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### 配置密钥

```powershell
$env:DASHSCOPE_API_KEY = '<嵌入服务的 API Key>'
$env:MEMORY_API_KEY = '<自行设置的服务访问密钥>'
```

`DASHSCOPE_API_KEY` 用于访问嵌入模型，`MEMORY_API_KEY` 用于调用 MemTrace。嵌入服务地址和模型参数目前在 [`embedding.py`](embedding.py) 中配置；接入其他环境时，需确认服务地址与密钥匹配。

启动脚本优先读取当前进程的密钥，缺失时读取 Windows 用户环境变量。已配置好密钥时无需重复设置；项目不会自动读取 `.env` 文件。

### 启动服务

```powershell
powershell -ExecutionPolicy Bypass -File E:\MemTrace\start.ps1
```

| 配置项 | 启动脚本使用的值 |
|---|---|
| 监听地址 | `127.0.0.1` |
| 端口 | `8001` |
| 检索模式 | `context` |
| 数据库 | 项目目录下的 `work\learning-c.db` |

保持启动终端运行，按 `Ctrl+C` 停止服务。停止服务不会删除已保存的数据。需要更换端口时：

```powershell
powershell -ExecutionPolicy Bypass -File E:\MemTrace\start.ps1 -Port 8002
```

### 检查服务

另开一个 PowerShell：

```powershell
Invoke-RestMethod http://127.0.0.1:8001/health
```

预期响应：

```json
{"status": "ok", "retrieval_mode": "context"}
```

交互式接口文档：<http://127.0.0.1:8001/docs>。根路径 `/` 未设置页面，访问时返回 404。

## API

| 方法 | 路径 | 用途 | 鉴权 |
|---|---|---|---|
| GET | `/health` | 健康检查与当前检索模式 | 无 |
| POST | `/add` | 批量写入对话消息 | Bearer Token |
| POST | `/search` | 检索用户记忆 | Bearer Token |

写入和检索请求需携带请求头：

```http
Authorization: Bearer <MEMORY_API_KEY>
Content-Type: application/json
```

### 写入记忆

`POST /add`

```json
{
  "request_id": "req-demo-001",
  "user_id": "user-demo",
  "session_id": "session-demo",
  "messages": [
    {"role": "user", "content": "我喜欢周末去徒步。"},
    {"role": "assistant", "content": "你通常去哪里徒步？"},
    {"role": "user", "content": "我经常去西山。"}
  ]
}
```

每条消息的 `role` 为 `user` 或 `assistant`，`content` 必须非空；可选字段 `timestamp` 为 Unix 毫秒时间戳。`request_id` 应全局唯一，仅在重试同一个请求时复用。

成功响应：

```json
{
  "success": true,
  "request_id": "req-demo-001",
  "user_id": "user-demo",
  "session_id": "session-demo"
}
```

### 检索记忆

`POST /search`

```json
{
  "user_id": "user-demo",
  "query": "我一般去哪里徒步？",
  "top_k": 5
}
```

`top_k` 为 1–100，默认值为 3。响应为 `{"data": [...]}`，每项包含 `id`、`content`、`score`，有有效来源时间时还包含 `created_at`。默认模式下，`content` 以 `[role=...; source_time=...]` 开头，后面保留完整原文。

结果按得分降序排列，数量不超过 `top_k`；没有可返回的记忆时为 `{"data": []}`。返回分数用于排序，不代表答案正确率。

常见错误：鉴权失败返回 401，请求内容冲突返回 409，字段校验失败返回 422。

## 配置

| 环境变量 | 说明 |
|---|---|
| `DASHSCOPE_API_KEY` | 嵌入服务密钥 |
| `MEMORY_API_KEY` | MemTrace 接口访问密钥 |
| `MEMORY_DB_PATH` | SQLite 数据库路径 |
| `MEMORY_RETRIEVAL_MODE` | 检索模式：`dense`、`bm25`、`hybrid`、`context` |
| `MEMORY_API_BASE` | `test_api.py` 等客户端脚本使用的服务地址 |

`start.ps1` 会将模式固定为 `context`，数据库固定为项目下的 `work\learning-c.db`。需要自定义模式、数据库或监听地址时，可在配置密钥后直接启动：

```powershell
cd E:\MemTrace
$env:MEMORY_RETRIEVAL_MODE = 'context'
$env:MEMORY_DB_PATH = 'E:\MemTrace\work\learning-c.db'
.\.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8001
```

直接启动且未设置 `MEMORY_DB_PATH` 时，程序使用项目根目录的 `memories.db`，它与启动脚本选用的数据库相互独立。

## 项目结构

```text
MemTrace/
├── app.py                       # HTTP 接口、请求校验和鉴权
├── memory_db.py                 # SQLite 存储、请求幂等和来源信息
├── embedding.py                 # 嵌入模型客户端
├── retrieval.py                 # 检索服务入口
├── ranking.py                   # 向量、BM25、RRF 和上下文算法
├── retrieval_config.py          # 检索配置
├── start.ps1                  # Windows 本地启动脚本
├── requirements.txt            # 依赖版本
├── test_api.py                  # 真实 HTTP 接口检查
├── tests/                       # 离线测试
├── scripts/                     # 数据评测和存档工具
├── work/                        # 运行数据库及工作文件
├── datasets/                    # 本地测试数据
├── experiments/                 # 本地实验结果
├── docs/                        # 补充文档
└── versions/                    # 版本存档
```

## 验证

离线测试使用模拟嵌入和临时数据库，不调用真实模型：

```powershell
cd E:\MemTrace
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_retrieval_versions.py -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_locomo_adapter.py -v
```

服务运行后，可在另一个终端执行真实接口检查。先确保客户端环境中设置了与服务相同的 `MEMORY_API_KEY`：

```powershell
cd E:\MemTrace
$env:MEMORY_API_BASE = 'http://127.0.0.1:8001'
.\.venv\Scripts\python.exe test_api.py
```

真实接口检查会写入测试消息并调用嵌入服务，产生相应 API 用量。`scripts/eval_locomo_retrieval.py` 可用于公开 LoCoMo 数据的检索评测；本地结果不等同于比赛官方成绩。

## 运行边界

- 当前检索会加载指定用户的全部记忆，逐条计算向量相似度并重建 BM25 统计；尚未使用向量索引或持久化倒排索引。
- 上下文补全依据已保存的来源信息。缺少可靠顺序的记录不会强行补全，也不会自动生成摘要或消解事实冲突。
- 幂等冲突检查依赖请求指纹；缺少指纹的历史记录仅能检查用户和会话是否一致。
- 启动脚本仅监听本机地址；公网访问需另行配置服务器、网络和服务进程管理。
- 大规模数据性能与公网稳定性需在目标部署环境中验证。

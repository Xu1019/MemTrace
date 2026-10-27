# MemTrace 参赛填写说明

本文记录当前部署信息，供申请 AML 评测权限时使用。联系人、团队和实际服务器规格由参赛者核对填写；接口凭据通过官方表单的专用凭据字段提供。

## 基本信息

| 字段 | 填写内容 |
|---|---|
| 系统名称 | MemTrace |
| 中文名称 | 迹忆 |
| 版本名称建议 | v1.0.0 |
| 参评类型 | Textual Memory / 文本记忆 |
| 参赛组别 | Open-source Methods / 开源方法榜 |
| 申请方式 | 申请新的 Eval Key / Leaderboard Key |
| 代码仓库 | https://github.com/Xu1019/MemTrace |
| 固定 commit | 在服务器完成版本同步后，填写 `git rev-parse HEAD` 输出的完整编号 |
| 联系人、邮箱、团队 | 由参赛者在申请表中填写 |

版本名称用于标识本次参评配置；实际代码以提交的完整 commit 编号为准。

## 在线接口

| 用途 | 方法 | 地址 |
|---|---|---|
| 写入 | POST | http://123.57.184.133/add |
| 检索 | POST | http://123.57.184.133/search |
| 健康检查 | GET | http://123.57.184.133/health |

Add / Search 使用 Bearer 鉴权，请求头为 `Authorization: Bearer <MEMORY_API_KEY>`；Health 不需要鉴权。服务访问密钥保存在服务器 `/etc/memtrace.env` 中，不写入本文或 GitHub。DashScope 模型密钥仅供服务器调用嵌入接口，不提供给评测平台。

上述地址当前使用 HTTP，尚未配置 HTTPS。若后续变更协议、域名或端口，应同步更新本说明及申请材料，并重新检查公网访问。

## 方法介绍（可复制）

MemTrace 是面向对话场景的记忆存储与检索系统。系统保留原始消息，使用 text-embedding-v4 生成 1024 维向量，将语义检索与 BM25 词法检索的候选通过 RRF 融合，并在数量预算内补充同一用户、同一会话中具有可靠来源顺序的相邻消息。系统返回带角色及来源时间的原文证据，支持用户隔离、同步写入及请求幂等，不生成最终答案，也不调用生成式 LLM。

参考来源和方法改动见 [README](../README.md#参考来源与方法改动)，部署步骤见 [DEPLOYMENT.md](DEPLOYMENT.md)。

## 运行参数与已验证范围

- 系统环境：Ubuntu 22.04 LTS、Python 3.10；依赖版本以 `requirements.txt` 为准。
- 服务：systemd 管理单个 Uvicorn worker，Nginx 将公网 80 端口转发到 `127.0.0.1:8001`。
- 持久化：SQLite，路径 `/opt/MemTrace/work/memtrace.db`；后台服务使用 `memtrace` 用户。
- 检索配置：`MEMORY_RETRIEVAL_MODE=context`，`top_k` 支持 1–100。
- 建议初始申请并发：Add 为 1，Search 为 1；未进行完整评测规模的峰值负载测试。
- Nginx 配置：请求体上限 `20m`，后端连接超时 10 秒，后端读写超时 1900 秒；平台自身的超时规则另行适用。
- 当前未配置应用层 QPS 限流，实际处理能力受模型接口配额、服务器资源及 SQLite 写入并发影响。
- 已通过：公网 Health，以及经本机 Nginx 转发的真实 Add / Search 检查，包括返回字段、幂等重试、用户隔离、排序、数量限制和参数校验。
- 尚未完成：官方准入审核、平台 Smoke、Full 及公榜审核。已有接口检查不代表正式成绩或完整规模容量保证。

## 固定参评版本

服务器运行代码应与提交的 commit 一致。申请前在服务器检查：

```bash
cd /opt/MemTrace
git status --short
git rev-parse HEAD
nproc
free -h
df -h /opt/MemTrace
```

记录实际 CPU、内存、存储及完整 commit。正式评测期间保持申报版本和配置稳定。

官方申请流程和最新要求以 [AML 官方接入指南](https://agentmemoryleaderboard.ai/api-guide) 为准。

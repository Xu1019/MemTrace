# MemTrace Linux 服务器部署

已验证环境：Ubuntu 22.04 LTS、Python 3.10、约 4 GiB 内存、40 GiB 系统盘。以下安装命令由 root 执行，程序由专用用户 `memtrace` 运行。

## 安装项目

```bash
apt update
apt install -y git python3-venv python3-pip curl ca-certificates nginx
git clone https://github.com/Xu1019/MemTrace.git /opt/MemTrace
cd /opt/MemTrace
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip check
```

每一步成功后再继续。若项目目录已经存在，使用已有目录，不重复克隆。嵌入服务地址在 `embedding.py` 中配置，须与使用的 DashScope 密钥匹配。

## 保存服务配置

单独执行下面一行，在提示后输入 DashScope API Key 并按回车；输入不回显：

```bash
read -r -s -p "DashScope API Key: " DASHSCOPE_API_KEY
```

输入完成后再执行：

```bash
printf '\n'
export DASHSCOPE_API_KEY
cd /opt/MemTrace
.venv/bin/python - <<'PY'
import json
import os
import secrets
from pathlib import Path

key = os.environ.get("DASHSCOPE_API_KEY", "").strip()
if not key or any(c.isspace() for c in key):
    raise SystemExit("密钥为空或包含空白，请重新输入。")
path = Path("/etc/memtrace.env")
if path.exists():
    raise SystemExit("配置文件已存在，保持原配置。")
values = {
    "DASHSCOPE_API_KEY": key,
    "MEMORY_API_KEY": secrets.token_hex(32),
    "MEMORY_RETRIEVAL_MODE": "context",
    "MEMORY_DB_PATH": "/opt/MemTrace/work/memtrace.db",
}
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w") as f:
    for name, value in values.items():
        f.write(name + "=" + json.dumps(value) + "\n")
print("配置已保存，密钥未显示。")
PY
```

配置文件由 root 持有，权限为 `0600`。已有配置应保留；后续服务从该文件加载环境变量。

## 后台运行与开机自启

```bash
if ! id memtrace >/dev/null 2>&1; then
    useradd --system --user-group --no-create-home --shell /usr/sbin/nologin memtrace
fi
mkdir -p /opt/MemTrace/work
chown -R memtrace:memtrace /opt/MemTrace/work

cat > /etc/systemd/system/memtrace.service <<'UNIT'
[Unit]
Description=MemTrace memory retrieval service
Wants=network-online.target
After=network-online.target

[Service]
Type=exec
User=memtrace
Group=memtrace
WorkingDirectory=/opt/MemTrace
EnvironmentFile=/etc/memtrace.env
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart=/opt/MemTrace/.venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 8001 --workers 1
Restart=on-failure
RestartSec=5
UMask=0077
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable --now memtrace
systemctl status memtrace --no-pager -l
```

服务启动后检查：

```bash
curl --max-time 10 -sS http://127.0.0.1:8001/health
```

预期返回 `{"status":"ok","retrieval_mode":"context"}`。

## Nginx 公网入口

下例的 `123.57.184.133` 是当前部署地址；部署到其他服务器时，将其换成目标服务器的公网 IP 或域名。

```bash
cat > /etc/nginx/sites-available/memtrace <<'NGINX'
server {
    listen 80;
    server_name 123.57.184.133 127.0.0.1;
    client_max_body_size 20m;
    access_log off;

    location / {
        proxy_pass http://127.0.0.1:8001;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_connect_timeout 10s;
        proxy_read_timeout 1900s;
        proxy_send_timeout 1900s;
    }
}
NGINX

ln -sfn /etc/nginx/sites-available/memtrace /etc/nginx/sites-enabled/memtrace
nginx -t
```

配置检查通过后：

```bash
systemctl enable --now nginx
systemctl reload nginx
curl --max-time 10 -sS http://127.0.0.1/health
```

云安全组入方向放行 TCP 80，公网访问来源为 `0.0.0.0/0`。若服务器防火墙已启用，也需允许该入口。从另一台电脑验证公网 `/health`。当前配置使用 HTTP；部署 HTTPS 时另行配置域名或证书和对应入口。

## 真实接口检查

在服务器执行以下命令，会经 Nginx 调用服务，写入少量测试消息并产生嵌入 API 用量。脚本读取接口密钥但不输出其内容。

```bash
cd /opt/MemTrace
.venv/bin/python - <<'PY'
import json
import os
from pathlib import Path

for line in Path("/etc/memtrace.env").read_text().splitlines():
    name, _, value = line.partition("=")
    if name == "MEMORY_API_KEY":
        os.environ[name] = json.loads(value)
os.environ["MEMORY_API_BASE"] = "http://127.0.0.1"
from test_api import main
main()
PY
```

## 日常管理

```bash
systemctl status memtrace --no-pager -l
journalctl -u memtrace -n 50 --no-pager
systemctl restart memtrace
systemctl stop memtrace
```

以上分别用于查看状态、查看近期日志、重启和停止，应按需单独执行。代码更新使用 `git pull --ff-only`；程序或配置变更后再重启服务，正式评测期间保持申报版本稳定。数据库位于 `work`，密钥位于 `/etc/memtrace.env`，均不纳入 Git。

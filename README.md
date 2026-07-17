# grok-auto-register

本仓库用于自动化注册 Grok / xAI 账号，支持 GUI 与 CLI。

仓库地址：<https://github.com/Xeltra233/grok-auto-register>

## 功能概览

- GUI / CLI 双入口
- 邮箱服务：DuckMail / YYDS / Cloudflare / Freemail / iCloud HME
- 多 worker 并发注册
- SSO 登录后导出 CPA xAI 凭证
- 成功门禁：测活通过后才本地保存 / 推送
- 特性分支提供 Web 面板 + 本地 GoProxy

> 本项目仅供学习与自用。请遵守目标站点服务条款与当地法律法规。

## 分支说明

| 分支 | 说明 |
| --- | --- |
| `main` | 基础 GUI / CLI 注册能力 |
| `feature/web-panel-goproxy` | Web 面板 + 本地 GoProxy + 账号池补货 + 日志清理等 |

推荐使用特性分支：

```bash
git clone https://github.com/Xeltra233/grok-auto-register.git
cd grok-auto-register
git checkout feature/web-panel-goproxy
```

## 环境要求

- Python 3.9+
- Google Chrome / Chromium
- 对应邮箱服务商的 API 配置

## 安装

```bash
pip install -r requirements.txt
mkdir -p config
cp config/config.example.json config/config.json
# 兼容旧路径：也可 cp config.example.json config.json
```

按需编辑 `config/config.json`（也兼容根目录 `config.json`）。

## 运行

### 启动 GUI

```bash
python grok_register_ttk.py
```

### 启动 CLI

```bash
python grok_register_ttk.py cli --count 1
```

### 特性分支统一入口

```bash
# 启动 Web 面板（默认 127.0.0.1:8787）
python run_branch.py panel

# GUI
python run_branch.py gui

# CLI
python run_branch.py cli --start --count 1

# 查看状态
python run_branch.py status
```

面板默认地址：`http://127.0.0.1:8787/`

如需设置面板访问密码：

```bash
# Windows PowerShell
$env:GROK_PANEL_PASSWORD="your-password"
python run_branch.py panel
```

## Docker 部署

本分支支持 Docker 运行 Web 面板（容器内不跑 GUI）。

镜像在构建阶段预编译 Linux 版 GoProxy（`third_party/goproxy/bin/proxygo`），运行时不需要安装 Go。

### 构建与启动

```bash
# 准备配置目录（推荐挂载整个文件夹）
mkdir -p config cpa_auths logs data
cp config/config.example.json config/config.json
# 按需编辑 config/config.json（邮箱 API、代理、密码等）

docker compose up -d --build
```

面板地址：`http://服务器IP:8787/`

### 仅构建镜像

```bash
docker build -t grok-auto-register:panel .
docker run -d --name grok-auto-register \
  -p 8787:8787 \
  -e PANEL_HOST=0.0.0.0 \
  -e PORT=8787 \
  -e GOPROXY_ENABLED=0 \
  -v "$PWD/config:/app/config" \
  -v "$PWD/cpa_auths:/app/cpa_auths" \
  -v "$PWD/logs:/app/logs" \
  -v "$PWD/data:/app/data" \
  grok-auto-register:panel
```

### 容器内注册

```bash
docker exec -it grok-auto-register python run_branch.py cli --start --count 1
```

### 说明

- 入口文件：`main.py`（启动 Web 面板）
- 默认监听：`0.0.0.0:8787`
- 配置目录：挂载 `./config` → `/app/config`，主配置为 `config/config.json`
- 建议挂载卷：`config/`、`cpa_auths/`、`logs/`、`data/`
- 镜像已预置 Linux GoProxy 二进制：`third_party/goproxy/bin/proxygo`（另含 `sing-box`）

### 环境变量

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `PANEL_HOST` / `HOST` | `0.0.0.0` | 面板监听地址 |
| `PORT` / `PANEL_PORT` | `8787` | 面板端口 |
| `GOPROXY_ENABLED` | `0` | `1/true/on` 启动镜像内置 GoProxy；`0` 关闭 |
| `GROK_PANEL_PASSWORD` | 空 | 面板访问密码；也可用配置项 `panel_token` |
| `GROK_CONFIG_DIR` | 空 | 配置目录；默认使用 `/app/config` |
| `GROK_CONFIG_FILE` | 空 | 直接指定配置文件绝对/相对路径 |
| `CPA_HEADLESS` | 空 | `1/true/on` 强制 CPA 浏览器无头模式 |

说明：

- `GOPROXY_ENABLED=0` 只表示“容器启动时不自动拉起 GoProxy”，不是删掉二进制。
- 需要代理池时设 `GOPROXY_ENABLED=1` 即可，无需在容器内再装 Go。
- 配置优先读 `config/config.json`；兼容旧路径根目录 `config.json`。

## 特性分支能力

| 能力 | 说明 |
| --- | --- |
| Web 面板 | 配置与状态 UI，SSE 实时刷新 |
| 本地 GoProxy | 本地 HTTP/2 + SOCKS5 代理入口 |
| 代理绑定 | 注册流 / CPA 流可分别绑定 GoProxy |
| 账号池补货 | 池内数量低于阈值时自动触发注册 |
| 测活门禁 | 成功账号先测活再落盘 / 推送 |
| 日志清理 | 按天数与体积清理历史日志 |
| 远程测活 | 面板侧可定时对凭证做远程测活 |
| 浏览器收尾 | 关闭时抓取 Chromium 进程树并强杀残留 |

## 常用配置

| 配置项 | 说明 |
| --- | --- |
| `email_provider` | `duckmail` / `yyds` / `cloudflare` / `freemail` / `icloud_hme` |
| `register_count` | 目标注册数量 |
| `concurrent_count` | 并发线程数（GUI/CLI 均生效） |
| `proxy` / `cpa_proxy` | 直连代理；也可改绑 GoProxy |
| `live_inspect_enabled` | 是否开启测活 |
| `success_require_live` | 是否要求测活通过才算成功 |
| `cpa_export_enabled` | 是否导出 CPA 凭证 |
| `panel_host` / `panel_port` | 面板监听地址 |
| `goproxy_enabled` | 是否启用本地 GoProxy |
| `goproxy_endpoint` | `http_random` / `http_stable` / `socks5_random` / `socks5_stable` |
| `goproxy_bind_register_proxy` | 注册流是否绑定 GoProxy |
| `goproxy_bind_cpa_proxy` | CPA 流是否绑定 GoProxy |
| `pool_autoreg_enabled` | 是否开启账号池自动补货 |
| `pool_autoreg_min_count` | 触发补货的最低库存 |
| `pool_autoreg_target_count` | 补货目标库存 |
| `log_cleanup_enabled` | 是否开启日志清理 |

示例：

```json
{
  "panel_enabled": true,
  "panel_host": "127.0.0.1",
  "panel_port": 8787,
  "goproxy_enabled": true,
  "goproxy_auto_start": true,
  "goproxy_pool_mode": "mixed_equal",
  "goproxy_endpoint": "http_random",
  "goproxy_bind_register_proxy": false,
  "goproxy_bind_cpa_proxy": false,
  "live_inspect_enabled": true,
  "success_require_live": true,
  "log_cleanup_enabled": true,
  "pool_autoreg_enabled": false,
  "pool_autoreg_min_count": 5,
  "pool_autoreg_target_count": 5,
  "pool_autoreg_interval_sec": 300
}
```

## 产出文件

- `accounts_YYYYMMDD_HHMMSS.txt`：成功账号
- `tokens.txt`：token 汇总
- `mail_credentials.txt`：邮箱凭证
- `cpa_auths/`：CPA 凭证（pending / uploaded）
- `logs/`：运行日志

## 测试

```bash
python -m unittest discover -s tests -p "test_*.py"
```

常用子集：

```bash
python -m unittest tests.test_run_branch tests.test_panel_server tests.test_goproxy_manager tests.test_pool_autoreg tests.test_browser_lifecycle tests.test_browser_monitor_and_logs -v
```

## 目录结构

```text
grok-auto-register/
  grok_register_ttk.py      # GUI / CLI 主程序
  run_branch.py             # 特性分支统一入口
  config/
    config.example.json
  config.example.json
  panel/                    # Web 面板与相关服务
  cpa_xai/                  # CPA mint / 导出
  third_party/goproxy/      # 本地 GoProxy 源码
  tests/
```

## 说明

日常使用建议优先切到 `feature/web-panel-goproxy`。
维护仓库：`Xeltra233/grok-auto-register`。

## License

[MIT](LICENSE)

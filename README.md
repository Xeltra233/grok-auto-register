<div align="center">

[![Grok Register — GUI and CLI registration automation toolkit](assets/banner.png)](https://github.com/AaronL725/grok-register)

Grok Register 是一个面向自动化流程研究、测试环境验证和个人学习的 Python 自动化注册工具 — 支持 GUI / CLI、临时邮箱、浏览器流程控制、账号输出和 grok2api token 池写入。

<p>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-blue.svg" alt="License: MIT"></a>
  <img src="https://img.shields.io/badge/Python-3.9%2B-3776AB.svg" alt="Python 3.9+">
  <img src="https://img.shields.io/badge/Interface-GUI%20%2B%20CLI-success.svg" alt="GUI + CLI">
  <img src="https://img.shields.io/badge/Browser-Chromium%2FChrome-4285F4.svg" alt="Chromium/Chrome">
  <a href="http://makeapullrequest.com"><img src="https://img.shields.io/badge/PRs-welcome-brightgreen.svg" alt="PRs Welcome"></a>
  <a href="https://linux.do"><img src="https://img.shields.io/badge/Join-linux.do-orange" alt="linux.do"></a>
</p>

<p align="center">
 <a href="https://www.star-history.com/aaronl725/grok-register">
  <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/badge?repo=AaronL725/grok-register&theme=dark" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/badge?repo=AaronL725/grok-register" />
   <img alt="Star History Rank" src="https://api.star-history.com/badge?repo=AaronL725/grok-register" />
  </picture>
 </a>
</p>

</div>

---

> 本项目仅用于自动化流程研究、测试环境验证和个人学习。请遵守目标网站服务条款、当地法律法规和第三方服务限制。

## Contents

- [功能](#功能)
- [环境要求](#环境要求)
- [安装](#安装)
- [配置](#配置)
- [运行](#运行)
- [输出文件](#输出文件)
- [稳定性机制](#稳定性机制)
- [常见问题](#常见问题)
- [目录结构](#目录结构)
- [分支版：Web 面板 + 内嵌 GoProxy](#分支版web-面板--内嵌-goproxy)
- [License](#license)
- [Acknowledgments](#acknowledgments)
- [Star History](#star-history)

## 功能

- 支持 GUI 图形界面运行。
- 支持 CLI 终端运行，不启动 Tk GUI。
- 注册流程使用 Chromium/Chrome 浏览器页面完成。
- 支持多 worker 并发注册（`concurrent_count`），每个 worker 独立浏览器与隔离 profile。
- 支持 DuckMail、YYDS、Cloudflare、Freemail、iCloud Hide My Email 邮箱接口。
- 支持验证码邮件轮询和解析。
- 支持成功账号实时写入 `accounts_*.txt`。
- 支持将 SSO token 写入 grok2api 本地或远端池。
- 支持注册后尝试开启 NSFW。
- 支持 CPA xAI 凭证异步导出（默认独立 mint 浏览器，不占用注册页）。
- 支持日志级别（`quiet` / `info` / `debug`）与每分钟创建速度统计。
- 支持页面卡住检测、当前账号重试、每账号浏览器重启和内存清理。
- 每个账号使用全新 Chromium 实例；账号结束后先关闭旧实例，下个账号开始时再创建，避免残留窗口和重复开窗。

## 环境要求

- Python 3.9+
- Google Chrome 或 Chromium
- 可访问注册页面和临时邮箱 API 的网络环境

## 安装

下载项目到电脑：

```bash
git clone https://github.com/AaronL725/grok-register.git
cd grok-register
```

安装依赖：

```bash
pip install -r requirements.txt
```

复制配置文件：

```bash
cp config.example.json config.json
```

然后按需编辑 `config.json`。

## 配置

常用配置项：

| 配置项 | 说明 |
| --- | --- |
| `email_provider` | 邮箱服务商：`duckmail`、`yyds`、`cloudflare`、`freemail`、`icloud_hme` |
| `register_count` | 本次目标注册数量 |
| `proxy` | 代理地址，可留空 |
| `enable_nsfw` | 注册后是否尝试开启 NSFW |
| `cloudflare_api_base` | Cloudflare 临时邮箱 API 地址 |
| `cloudflare_api_key` | Cloudflare 临时邮箱接口密钥；默认匿名模式留空，admin 模式填 `ADMIN_PASSWORD` |
| `cloudflare_auth_mode` | Cloudflare API 鉴权模式；默认 `none`，可选 `bearer`、`x-api-key`、`x-admin-auth`、`query-key` |
| `cloudflare_path_domains` | Cloudflare 域名列表路径；默认 `/api/domains` |
| `cloudflare_path_accounts` | Cloudflare 创建邮箱路径；默认匿名模式用 `/api/new_address`，admin 模式用 `/admin/new_address` |
| `cloudflare_path_token` | Cloudflare token 路径；默认 `/api/token` |
| `cloudflare_path_messages` | Cloudflare 收件列表路径；默认 `/api/mails` |
| `defaultDomains` | Cloudflare 临时邮箱默认域名 |
| `freemail_api_base` | [idinging/freemail](https://github.com/idinging/freemail) Worker 地址 |
| `freemail_jwt_token` | Freemail 部署时配置的 `JWT_TOKEN`，通过 Bearer Header 调用 API |
| `freemail_domain` | 创建邮箱时使用的域名，支持逗号分隔多域名轮换；留空则轮换 Freemail `/api/domains` 全部域名 |
| `icloud_hme_api_base` | [xiaozhou26/icloud-hme](https://github.com/xiaozhou26/icloud-hme) 服务地址，可填根地址或以 `/api` 结尾的地址 |
| `icloud_hme_account_id` | icloud-hme `accounts.json` 中要使用的账号 ID，例如 `acc_1` |
| `icloud_hme_label` | 创建 Hide My Email 别名时写入的标签 |
| `grok2api_auto_add_local` | 是否写入本地 grok2api token 池 |
| `grok2api_local_token_file` | 本地 grok2api token 文件路径 |
| `grok2api_auto_add_remote` | 是否写入远端 grok2api |
| `grok2api_remote_base` | 远端 grok2api 地址，可填站点根地址或 `/admin/api` 管理 API 地址 |
| `grok2api_remote_app_key` | 远端 grok2api app key |
| `concurrent_count` | 并发 worker 数；`1` 为单浏览器顺序注册，`>1` 为多浏览器并发 |
| `browser_restart_every` | 额外周期重启提示间隔（账号数）；**每个账号结束后仍会完整重启浏览器**，避免会话残留 |
| `cpa_export_enabled` | 是否在注册成功后导出 CPA xAI 凭证 |
| `cpa_mint_async` | 是否异步 mint CPA（默认 `true`：独立浏览器 + 后台线程，不阻塞下一号注册） |
| `cpa_probe_after_write` | 写出 CPA 文件后是否探测接口可用性 |
| `cpa_remote_enabled` | 是否通过 CLIProxyAPI 管理 API 自动上传 CPA JSON |
| `cpa_remote_base` | CLIProxyAPI 服务地址，例如 `https://cpa.example.com` |
| `cpa_remote_management_key` | CLIProxyAPI 的 `remote-management.secret-key`，不是普通业务 API Key |
| `cpa_remote_timeout_sec` | 远端上传和确认超时秒数，默认 `30` |
| `cpa_remote_pending_dir` | 未上传或待重试目录，默认 `cpa_auths/pending` |
| `cpa_remote_uploaded_dir` | 已被远端确认的目录，默认 `cpa_auths/uploaded` |
| `log_level` | 日志级别：`quiet` / `info`（默认）/ `debug`；`info` 会隐藏高频 `[Debug]` |
| `speed_log_interval_sec` | 创建速度统计间隔秒数，默认 `60`；输出类似 `成功 9/min` |
| `browser_use_custom_ua` | 是否强制使用配置中的自定义 UA（默认 `false`，更贴近本机 Chrome） |
| `token_only_file` | 仅写入 SSO token 的附加文件路径，可留空 |

### Cloudflare 临时邮箱匿名模式（默认）

默认情况下，Cloudflare 邮箱使用 `dreamhunter2333/cloudflare_temp_email` 的匿名接口创建邮箱并读取邮件：

- 创建邮箱：`POST /api/new_address`
- 读取邮件：`GET /api/mails`
- 鉴权模式：`none`
- `cloudflare_api_key`：留空

这是项目的默认路线。没有特殊需求时，保持下面配置即可：

```json
{
  "email_provider": "cloudflare",
  "cloudflare_api_base": "https://你的-worker-api-域名",
  "cloudflare_api_key": "",
  "cloudflare_auth_mode": "none",
  "cloudflare_path_domains": "/api/domains",
  "cloudflare_path_accounts": "/api/new_address",
  "cloudflare_path_token": "/api/token",
  "cloudflare_path_messages": "/api/mails",
  "defaultDomains": "你的收信域名.com"
}
```

### Cloudflare 临时邮箱 admin 模式（可选）

如果使用 `dreamhunter2333/cloudflare_temp_email` 且匿名 `/api/new_address` 开启了 Turnstile，可以改用 admin 创建邮箱接口：

```json
{
  "email_provider": "cloudflare",
  "cloudflare_api_base": "https://你的-worker-api-域名",
  "cloudflare_api_key": "你的 ADMIN_PASSWORD",
  "cloudflare_auth_mode": "x-admin-auth",
  "cloudflare_path_accounts": "/admin/new_address",
  "cloudflare_path_messages": "/api/mails",
  "defaultDomains": "你的收信域名.com"
}
```

创建邮箱会使用 `x-admin-auth` 调用 `/admin/new_address`，后续收件仍使用接口返回的地址 JWT 调用 `/api/mails`。也就是说，admin 密码只用于创建邮箱，不用于读取邮箱邮件。

可先用调试脚本验证 admin 创建接口：

```bash
python cf_mail_debug.py --api-base "https://你的-worker-api-域名" --auth-mode x-admin-auth --api-key "你的 ADMIN_PASSWORD" --create-path /admin/new_address --domain "你的收信域名.com"
```

### Freemail 模式

本项目支持 [idinging/freemail](https://github.com/idinging/freemail) 的邮箱创建和收件接口。配置 Worker URL、部署时的 `JWT_TOKEN` 和注册域名（支持多域名）：

```json
{
  "email_provider": "freemail",
  "freemail_api_base": "https://你的-freemail-worker-域名",
  "freemail_jwt_token": "你的 JWT_TOKEN",
  "freemail_domain": "mail1.example.com,mail2.example.com"
}
```

`freemail_domain` 支持逗号/分号分隔的多域名；每次创建邮箱会按配置顺序轮换。域名需存在于 Freemail 服务端 `MAIL_DOMAIN` / `GET /api/domains` 返回列表中。若留空，则自动轮换服务端返回的全部域名。

程序会调用 `GET /api/domains` 解析域名索引，再调用 `GET /api/generate?domainIndex=...` 创建邮箱；验证码轮询使用 `GET /api/emails?mailbox=...` 和 `GET /api/email/:id`。JWT Token 仅从配置读取，不会作为单邮箱凭证写入 `mail_credentials.txt`。

### iCloud Hide My Email 模式

本项目支持 [xiaozhou26/icloud-hme](https://github.com/xiaozhou26/icloud-hme) 的本地 HTTP API。先按该项目文档配置 iCloud 账号、Cookie，以及用于稳定收信的 App Password，然后启动服务：

```json
{
  "email_provider": "icloud_hme",
  "icloud_hme_api_base": "http://127.0.0.1:8081",
  "icloud_hme_account_id": "acc_1",
  "icloud_hme_label": "Grok auto-register"
}
```

程序会调用 `POST /api/create` 创建新的 Hide My Email 别名，再使用 `GET /api/inbox?account_id=...&alias=...` 轮询该别名收到的验证码邮件。`icloud_hme_api_base` 同时兼容 `http://127.0.0.1:8081` 和 `http://127.0.0.1:8081/api` 两种写法。目标服务当前没有额外的 API Token 鉴权，建议只监听本机或放在受信任网络内。

### CPA 远端自动上传

注册成功并生成 `cpa_auths/xai-*.json` 后，可自动上传到 CLIProxyAPI：

```json
{
  "cpa_remote_enabled": true,
  "cpa_remote_base": "https://你的-cliproxyapi-域名",
  "cpa_remote_management_key": "remote-management.secret-key"
}
```

新生成文件先写入 `cpa_auths/pending/`。上传使用官方 `POST /v0/management/auth-files` 接口，随后通过 `GET /v0/management/auth-files` 确认文件名确实存在；确认成功后移动到 `cpa_auths/uploaded/`。失败文件继续留在 `pending/`，下一次 CPA 导出开始前优先重试。关闭自动传输时文件同样保留在 `pending/`。状态保存在 `cpa_auths/.cpa_remote_upload_state.json`。

### grok2api 远端入池配置

如果开启 `grok2api_auto_add_remote`，`grok2api_remote_base` 可以填写站点根地址，也可以直接填写管理 API 地址：

```json
{
  "grok2api_auto_add_remote": true,
  "grok2api_remote_base": "https://你的-grok2api-域名",
  "grok2api_remote_app_key": "你的 app_key"
}
```

或：

```json
{
  "grok2api_auto_add_remote": true,
  "grok2api_remote_base": "https://你的-grok2api-域名/admin/api",
  "grok2api_remote_app_key": "你的 app_key"
}
```

程序会优先尝试 `/tokens/add`，并兼容 `/admin/api/tokens/add`；旧版全量保存接口也会兼容 `/tokens` 和 `/admin/api/tokens`。

`config.json` 包含个人配置和密钥，不要提交到 Git。

## 运行

### CLI 模式

CLI 模式不会启动 Tk GUI，但注册流程仍会打开 Chromium/Chrome 浏览器页面。

```bash
python grok_register_ttk.py cli
```

看到提示后输入：

```text
start
```

停止任务：

```text
Ctrl+C
```

CLI 模式适合长时间批量运行。每个账号结束后会完整重启浏览器；另外每成功注册 5 个账号会做一次运行时内存清理。

并发示例（在 `config.json` 中设置）：

```json
{
  "register_count": 20,
  "concurrent_count": 3,
  "log_level": "info",
  "speed_log_interval_sec": 60
}
```

### GUI 模式

```bash
python grok_register_ttk.py
```

GUI 模式会打开 Tkinter 窗口，适合手动调整配置和观察日志。日志同样受 `log_level` 过滤，并会打印全局创建速度。

## 输出文件

运行过程中会生成：

- `accounts_*.txt`：成功账号、密码和 SSO token。
- `mail_credentials.txt`：临时邮箱凭证。
- `cpa_auths/`：CPA xAI 凭证 JSON（开启 `cpa_export_enabled` 时）。
- `.browser_profiles/`：并发 worker 临时浏览器 profile（运行中生成，已 gitignore）。
- `*.log`：可选日志文件。

这些文件包含敏感信息，已被 `.gitignore` 忽略。

## 稳定性机制

- **每个账号结束后完整重启浏览器**（`restart_browser`），避免复用上号 SSO / 落到 `tos-gate` 等错误页。
- 并发 worker 使用独立 Chromium 与隔离 user-data 目录。
- 默认 CPA 异步 mint 使用独立浏览器（`page=None`），不占用注册 tab。
- Cloudflare 拦截页检测与打开注册页重试。
- 每成功 5 个账号执行一次内存清理。
- CLI 支持 `Ctrl+C`：第一次请求停止并收尾，连按两次强制退出。
- 最终页长时间无变化时自动重试当前账号。
- 验证码未收到时自动更换邮箱重试。
- 全局每分钟输出创建速度（成功数 / min）。

## 常见问题

### CLI 模式为什么还会打开浏览器？

CLI 模式只是不启动 Tk GUI。注册页、Turnstile、验证码提交和 SSO cookie 获取仍依赖真实浏览器环境。

### 并发时前几个成功、后面提示找不到「使用邮箱注册」？

常见原因是账号间会话残留（例如页面落到 `grok.com/tos-gate`）。当前版本在每个账号结束后都会完整重启浏览器；请确认使用最新代码，且不要改回「仅轻量清 cookie、不重启」。

### NSFW 开启失败怎么办？

如果日志显示 `Cloudflare 防护拦截，HTTP 403`，说明请求被目标站点防护拦截。程序会继续保存账号和写入 grok2api。

### 日志太多 / 想看 Debug？

在 `config.json` 设置：

- `"log_level": "quiet"`：只看成功/失败/关键警告与速度
- `"log_level": "info"`：默认，隐藏 `[Debug]`
- `"log_level": "debug"`：全量诊断

### GUI 显示的数量和配置不同？

GUI 数量控件可能有上限。CLI 模式直接读取 `config.json` 中的 `register_count`。

## 目录结构

```text
.
├── grok_register_ttk.py   # 主程序（GUI/CLI 注册）
├── cpa_export.py          # CPA xAI 导出入口
├── cpa_xai/               # CPA mint / OAuth / schema
├── cf_mail_debug.py       # Cloudflare 邮箱调试工具
├── config.example.json    # 配置示例
├── requirements.txt       # Python 依赖
└── README.md
```



## 分支版：Web 面板 + 内嵌 GoProxy

本仓库 `feature/web-panel-goproxy` 分支在主流程基础上合并了本地 GoProxy、玻璃质感网页面板、浏览器僵尸清理、日志自动清理、测活门槛与账号池阈值自动注册。

### 统一启动入口

```bash
# 启动玻璃面板（默认尝试自动启动 GoProxy）
python run_branch.py panel

# 仅面板，不自动起 GoProxy
python run_branch.py panel --no-goproxy

# 查看当前配置/账号池/浏览器摘要
python run_branch.py status

# GUI / CLI 注册
python run_branch.py gui
python run_branch.py cli --start --count 3
```

也可直接：

```bash
python -m panel.server
python grok_register_ttk.py
python grok_register_ttk.py cli
```

默认面板地址：`http://127.0.0.1:8787/`

### 关键能力

| 能力 | 说明 |
| --- | --- |
| 浏览器监控 | 观察注册/mint 浏览器实例，清理 `.browser_profiles` 僵尸进程 |
| 内嵌 GoProxy | HTTP×2 + SOCKS×2 本地端口，5 种池模式 |
| 代理绑定 | `goproxy_bind_register_proxy` / `goproxy_bind_cpa_proxy` 把注册与 CPA 指到本地端口 |
| 测活门槛 | 参考 grok-inspection，测活通过后才本地保存/推送 |
| 凭证库 | uploaded 多选/全选下载、一键删除已推送 |
| 日志清理 | 按保留天数 + 总量配额后台巡检清理 |
| 账号池补货 | `pool_autoreg_*`：池内账号低于阈值时自动触发注册 |

### 分支配置示例

```json
{
  "panel_enabled": true,
  "panel_host": "127.0.0.1",
  "panel_port": 8787,
  "goproxy_enabled": true,
  "goproxy_auto_start": true,
  "goproxy_pool_mode": "mixed_equal",
  "goproxy_endpoint": "http_random",
  "goproxy_bind_register_proxy": true,
  "goproxy_bind_cpa_proxy": true,
  "live_inspect_enabled": true,
  "success_require_live": true,
  "log_cleanup_enabled": true,
  "pool_autoreg_enabled": false,
  "pool_autoreg_min_count": 5,
  "pool_autoreg_batch": 3,
  "pool_autoreg_interval_sec": 300
}
```

### 面板实时同步

前端通过 `/api/stream` SSE 推送 overview；断开时回退 3 秒轮询。操作（清理浏览器/日志、代理切换、凭证删除）后会立即刷新后端状态。

### 基础测试

```bash
python -m unittest discover -s tests -p "test_*.py"
```

重点分支测试：

```bash
python -m unittest tests.test_run_branch tests.test_panel_server tests.test_goproxy_manager tests.test_pool_autoreg tests.test_browser_lifecycle tests.test_browser_monitor_and_logs -v
```

## License

[MIT](LICENSE).

## Acknowledgments

Thanks to [linux.do](https://linux.do) — a vibrant tech community where this project is shared and discussed.

## Star History

<a href="https://www.star-history.com/?repos=AaronL725%2Fgrok-register&type=date&legend=top-left">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/chart?repos=AaronL725/grok-register&type=date&theme=dark&legend=top-left&sealed_token=uCM--S2xEp0n8rFUZHUg6wUJOgYcfO4XEVCIF9UZAT04YjL9YsMEOVOGAOlQfqwsoS7cQef0Rwc1cYCY4lAmTuMmcg-hKzNnx1A7KNekuCXQotFd4YifLIkvJWOEy5vxiREJX80Mwxbr8F-3GfCv0utIsQz_iq19nS57svUqwv0mSosV8OTxqXTLjmsI" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/chart?repos=AaronL725/grok-register&type=date&legend=top-left&sealed_token=uCM--S2xEp0n8rFUZHUg6wUJOgYcfO4XEVCIF9UZAT04YjL9YsMEOVOGAOlQfqwsoS7cQef0Rwc1cYCY4lAmTuMmcg-hKzNnx1A7KNekuCXQotFd4YifLIkvJWOEy5vxiREJX80Mwxbr8F-3GfCv0utIsQz_iq19nS57svUqwv0mSosV8OTxqXTLjmsI" />
   <img alt="Star History Chart" src="https://api.star-history.com/chart?repos=AaronL725/grok-register&type=date&legend=top-left&sealed_token=uCM--S2xEp0n8rFUZHUg6wUJOgYcfO4XEVCIF9UZAT04YjL9YsMEOVOGAOlQfqwsoS7cQef0Rwc1cYCY4lAmTuMmcg-hKzNnx1A7KNekuCXQotFd4YifLIkvJWOEy5vxiREJX80Mwxbr8F-3GfCv0utIsQz_iq19nS57svUqwv0mSosV8OTxqXTLjmsI" />
 </picture>
</a>

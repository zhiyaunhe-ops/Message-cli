# Message-cli — 本地邮件 Web 阅读器 + AI 可读 API

把 IMAP 邮箱收件箱搬到本地一份可搜索的 SQLite 索引，同时提供：
- **WebUI**：5 套美学主题（`/?theme=ink|swiss|neon|terminal|glass`），三栏布局，搜索 / 标记已读 / 附件下载
- **会话视图**：列表头可切换「邮件 / 会话」——把往来的多封邮件聚成一条 IM 风格的聊天（收件箱与已发送自动合并、引用历史折叠、自己发的靠右）
- **无聊邮件分类**：本地规则引擎自动打标（📅 会议/日程、🤖 系统自动、📢 营销推广），列表徽章显示、可一键「只看人工」
- **写邮件 / 回复 / 删除**：顶栏「✏ 写邮件」（SMTP，支持多附件）；阅读器与会话气泡可「↩ 回复」（自动带引用）和「🗑 删除」（COPY 到服务器回收站）
- **微信统计页签**：顶栏「💬 微信」切换到本地微信数据看板（消息总量 / 每日趋势 / 24 小时分布 / 类型分布 / 活跃会话排行 / 最近会话），点会话看最近消息
- **REST API**：完整的 JSON 接口；自动生成 OpenAPI 文档（`/docs`）
- **AI 友好端点**：结构化 JSON、自动截断正文、含 `attachments` 直链；详见 `/llms.txt` 与 `/api/ai/schema`

## 启动

```bash
# 同步邮件到本地索引（首次约 30s）
python run.py sync --since 2026-07-01 --all-folders

# 启动 WebUI + API（默认 127.0.0.1:8765）
python run.py serve --host 127.0.0.1 --port 8765
```

副命令：`sync` / `verify` / `ai` / `wechat` —— 见 `python run.py -h`。

## 微信统计（可选）

WebUI 的「💬 微信」页签读的是本机微信（Weixin.exe）的加密数据库，靠 `wechat-cli` 提取的密钥解密，
**只读、不写微信数据**。启用步骤：

```bash
# 1. 微信保持登录，提取数据库密钥（扫描进程内存，约 3 秒）
wechat-cli init

# 2. 运行服务的 Python 环境需要有 pycryptodome / zstandard
pip install pycryptodome zstandard

# 3. 命令行验证（不开服务也能看）
python run.py wechat --days 7
```

`app/wechat.py` 复用 `source/wechat_cli` 的解密与查询内核（若环境里已装 `wechat-cli` 包则优先用包）。
密钥/配置在 `~/.wechat-cli/`，不在本仓库内。微信数据不可用时接口返回 `503`，邮件功能不受影响。

## 主题

| id | 主题 | 关键风格 |
| --- | --- | --- |
| ink | 宣纸水墨 | 宋体 × 朱砂 × 宣纸纹理 |
| swiss | 瑞士极简 | 黑白网格 × 国际主义排版 |
| neon | 暗夜霓虹 | 深空底 × 紫青辉光 |
| terminal | 复古终端 | CRT 绿字 × 扫描线 |
| glass | 琉璃柔彩 | 毛玻璃 × 粉紫渐变 |

主题选择持久化在 localStorage，也可通过 URL `?theme=xxx` 强制指定。

## 关键 API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET  | `/api/ai/inbox` | AI 首选：结构化邮件列表，正文按 `body_chars` 截断 |
| GET  | `/api/ai/digest` | 按 sender/date/subject/folder 聚合统计 |
| GET  | `/api/ai/verify` | 校验时间窗内各目录数量与日期范围 |
| GET  | `/api/wechat/overview` | 微信全局统计（`days=7`，`days=0` 全部时间，`refresh=1` 强制重扫） |
| GET  | `/api/wechat/sessions` | 微信最近会话（含未读数） |
| GET  | `/api/wechat/history` | 某微信会话的最近消息（`chat` + `days` + `limit`） |
| GET  | `/api/wechat/stats` | 某微信会话统计（类型 / 发言排行 / 24 小时） |
| GET  | `/api/wechat/status` | 微信数据可用性自检 |
| GET  | `/api/messages` | 信封级列表（含 q/since/unread_only/has_attachment 过滤） |
| GET  | `/api/messages/{uid}` | 单封全文（headers / body_text / body_html / attachments） |
| GET  | `/api/messages/{uid}/attachment/{idx}` | 下载附件 |
| POST | `/api/messages/{uid}/flags` | 标记已读 / 未读 / 星标 |
| POST | `/api/sync` | 增量同步 |
| GET  | `/llms.txt` | 给 AI 看的接口说明 |
| GET  | `/api/ai/schema` | JSON 版接口自述 |

## 验证（2026-07-01 以来）

```
Drafts        1 封    正文 1   日期 2026-09-08
INBOX       133 封   正文 133  附件 352  日期 2026-06-30 → 2026-09-09
Sent Items   17 封   正文 17   附件 29   日期 2026-07-08 → 2026-09-09
合计        151 封   正文 151
```

与 IMAP `SINCE 01-Jul-2026` 实测计数（133 / 17 / 1）完全吻合。

## 目录结构

```
Message-cli/
├─ config/himalaya/{config.toml, secret}   # IMAP 配置（备份解压后整理）
├─ app/
│  ├─ settings.py      # 配置加载，密码读取（环境变量/secret/命令）
│  ├─ imap_client.py   # IMAP 连接与抓取封装
│  ├─ mailparse.py     # RFC822 → 结构化 dict（含 GBK 回退）
│  ├─ store.py         # SQLite 缓存（信封 / 正文 / 附件）
│  ├─ sync.py          # 增量同步
│  └─ main.py          # FastAPI 路由
├─ web/
│  ├─ index.html       # SPA 容器
│  ├─ style.css        # 5 套主题
│  ├─ app.js           # 列表 / 搜索 / 阅读 / 同步
│  └─ llms.txt         # 给 AI 看的接口说明
├─ data/
│  ├─ mail.db          # 本地索引
│  ├─ attachments/     # 附件文件
│  └─ shots/           # 5 主题截图
├─ run.py              # CLI: serve / sync / verify / ai
└─ README.md
```
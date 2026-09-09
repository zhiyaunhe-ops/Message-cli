"""Mail WebUI + API 服务。

给人类：5 套美学主题的收件箱 WebUI（/）
给 AI  ：结构化 JSON 接口（/api/...），说明见 /llms.txt 与 /api/ai/schema
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import sync as syncmod
from .classify import CATEGORY_EMOJI, CATEGORY_LABELS, classify, label as cat_label
from .imap_client import IMAPClient
from .settings import Account, ROOT, load_account
from .store import Store
from .threads import rebuild as rebuild_threads

WEB_DIR = ROOT / "web"
DEFAULT_SINCE = "2026-07-01"

app = FastAPI(
    title="Message WebUI / Mail API",
    version="1.0.0",
    description="IMAP 邮箱的本地 Web 阅读器与 AI 可读接口。所有 /api/* 返回 JSON。",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

_state: dict = {"account": None, "store": None, "syncing": False, "last_sync": None, "log": []}


def account() -> Account:
    if _state["account"] is None:
        _state["account"] = load_account()
    return _state["account"]


def store() -> Store:
    if _state["store"] is None:
        _state["store"] = Store()
    return _state["store"]


def client() -> IMAPClient:
    return IMAPClient(account())


def own_emails() -> set[str]:
    """自己的地址（用于判断一封邮件是「我发的」还是「别人发的」）。"""
    return {account().email.strip().lower()}


def is_mine(msg: dict) -> bool:
    addr = ((msg.get("from") or {}).get("email") or "").strip().lower()
    return bool(addr and addr in own_emails()) or msg.get("folder") in ("Sent Items", "Drafts", "Sent", "已发送")


def _enrich_categories(msg: dict) -> dict:
    cat = msg.get("category") or "personal"
    msg["category"] = cat
    msg["category_label"] = cat_label(cat)
    msg["category_emoji"] = CATEGORY_EMOJI.get(cat, "✉️")
    msg["is_boring"] = bool(msg.get("is_boring"))
    msg["mine"] = is_mine(msg)
    return msg


def reindex(classify_all: bool = False, rebuild: bool = True) -> dict:
    """重建本地派生数据：分类 + 会话归组。"""
    s = store()
    out: dict = {}
    if classify_all:
        out["classified"] = s.classify_all(force=True)
    else:
        pending = s.conn.execute(
            "SELECT COUNT(*) AS c FROM messages WHERE category='' OR category IS NULL"
        ).fetchone()["c"]
        out["classified"] = s.classify_all() if pending else {"scanned": 0}
    if rebuild:
        out["threads"] = rebuild_threads(s, own_emails=own_emails())
    out["categories"] = s.category_stats()
    return out


@app.on_event("startup")
def _startup():
    """首次启动（或新增列后）自动补全分类与会话归组。"""
    try:
        reindex(classify_all=False, rebuild=True)
    except Exception:
        pass


# ---------------------------------------------------------------- 页面 / 静态
app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(WEB_DIR / "index.html")


@app.get("/llms.txt", include_in_schema=False)
def llms_txt():
    return PlainTextResponse((WEB_DIR / "llms.txt").read_text(encoding="utf-8"), media_type="text/plain; charset=utf-8")


@app.get("/api/ai/schema", summary="AI 接口自述：可直接照此调用")
def ai_schema():
    base = "/api"
    return {
        "service": "mail-api",
        "account": {"email": account().email, "display_name": account().display_name},
        "conventions": {
            "time": "ISO8601，默认 UTC 偏移已含在字符串里；since/until 接受 'YYYY-MM-DD' 或 ISO datetime",
            "folder": "服务端原名，如 INBOX / Sent Items / Drafts；folder 参数可重复传入",
            "uid": "IMAP UID，仅在所属 folder 内唯一；全局 id 形如 'INBOX:1234'",
            "pagination": "limit + offset，响应含 total",
            "category": "personal=人工邮件；meeting=会议/日程；automated=系统自动；promotion=营销推广。"
                        "后三类合称「无聊邮件」(is_boring=true)，可用 ?boring=true/false 或 ?category= 过滤",
            "thread_id": "会话 ID，同一来回复用一封；thread_id 形如 't'+14 位十六进制",
            "mine": "true 表示这封是本人发出的（发件人是自己，或在已发送/草稿目录里）",
        },
        "endpoints": [
            {"method": "GET", "path": f"{base}/ai/inbox", "desc": "AI 首选：一次拿到结构化邮件列表（含清洗后的正文）",
             "params": ["since", "until", "folder", "q", "unread_only", "has_attachment", "limit", "offset", "body_chars", "include_html"]},
            {"method": "GET", "path": f"{base}/ai/threads", "desc": "会话视图：同一来回复用一封，按时间顺序给出聊天式内容",
             "params": ["since", "until", "folder", "q", "unread_only", "boring", "category", "only_grouped", "limit", "offset", "body_chars", "max_messages"]},
            {"method": "GET", "path": f"{base}/threads", "desc": "会话列表（不含正文，轻量）",
             "params": ["folder", "since", "until", "q", "unread_only", "boring", "category", "only_grouped", "limit", "offset", "preview"]},
            {"method": "GET", "path": f"{base}/threads/{{thread_id}}", "desc": "单条会话全文，聊天视图直接消费",
             "params": ["body_chars", "body_format"]},
            {"method": "GET", "path": f"{base}/categories", "desc": "分类统计：无聊邮件占比、各类数量、会话数"},
            {"method": "POST", "path": f"{base}/reindex", "desc": "重建派生索引（分类 + 会话归组）",
             "body": {"classify_all": False, "rebuild": True}},
            {"method": "GET", "path": f"{base}/ai/digest", "desc": "聚合视图：按发件人/日期/主题分组统计，适合快速概览",
             "params": ["since", "until", "folder", "q", "group_by", "top"]},
            {"method": "GET", "path": f"{base}/messages", "desc": "信封级列表（不含正文，轻量）",
             "params": ["folder", "since", "until", "q", "unread_only", "has_attachment", "limit", "offset", "order"]},
            {"method": "GET", "path": f"{base}/messages/{{uid}}", "desc": "单封全文（正文 + 附件清单）",
             "params": ["folder", "include_body", "body_format"]},
            {"method": "GET", "path": f"{base}/messages/{{uid}}/attachment/{{index}}", "desc": "下载附件"},
            {"method": "POST", "path": f"{base}/messages/{{uid}}/flags", "desc": "标记已读/未读、加星标",
             "body": {"folder": "INBOX", "add": ["\\Seen"], "remove": []}},
            {"method": "GET", "path": f"{base}/folders", "desc": "所有目录及计数 / 同步状态"},
            {"method": "POST", "path": f"{base}/sync", "desc": "从 IMAP 增量同步到本地索引",
             "body": {"folder": "INBOX", "since": DEFAULT_SINCE, "limit": 500, "force": False, "with_body": True}},
            {"method": "GET", "path": f"{base}/status", "desc": "服务与索引健康状态"},
        ],
        "tips": [
            "想读 7 月以来的全部邮件：GET /api/ai/inbox?since=2026-07-01&limit=200",
            "只想看人写的邮件（滤掉会议通知/系统自动/营销）：GET /api/ai/inbox?since=2026-07-01&boring=false",
            "想按会话读（推荐，省 token）：GET /api/ai/threads?since=2026-07-01&only_grouped=true",
            "正文默认截断到 body_chars（默认 4000）字符，需要全文再调 /api/messages/{uid}",
            "搜索 q 会同时匹配主题、发件人、收件人和正文",
        ],
    }


# ---------------------------------------------------------------- 内嵌资源
_CID_RE = re.compile(r"""(?i)(?:cid:|["']cid:)([^\s"'<>)\]]+)""")


def att_url(folder: str, uid: int, index: int, disposition: str = "inline") -> str:
    return f"/api/messages/{uid}/attachment/{index}?folder={quote(folder, safe='')}&disposition={disposition}"


def resolve_cids(html: str, folder: str, uid: int, atts: list[dict]) -> str:
    """把正文里的 cid: 引用替换成指向本地附件的 URL，让内嵌图片直接显示在正文中。"""
    if not html or "cid:" not in html.lower():
        return html
    by_cid: dict[str, dict] = {}
    for a in atts:
        cid = (a.get("content_id") or "").strip().strip("<>")
        if cid:
            by_cid[cid.lower()] = a
            by_cid[cid.split("@")[0].lower()] = a
        if a.get("filename"):
            by_cid[a["filename"].lower()] = a

    def repl(m: re.Match) -> str:
        cid = m.group(1).strip()
        a = by_cid.get(cid.lower()) or by_cid.get(cid.split("@")[0].lower())
        if not a:
            return m.group(0)
        return att_url(folder, uid, a["index"], "inline")

    return _CID_RE.sub(repl, html)


def split_attachments(atts: list[dict], folder: str, uid: int) -> tuple[list[dict], list[dict]]:
    """拆成「真实附件」与「内嵌资源（签名 logo / 插图等，已渲染进正文）」。"""
    real, inline = [], []
    for a in atts:
        item = {
            "filename": a["filename"],
            "content_type": a["content_type"],
            "size": a["size"],
            "is_inline": bool(a["is_inline"]),
            "content_id": a.get("content_id") or "",
            "url": att_url(folder, uid, a["index"], "attachment"),
            "inline_url": att_url(folder, uid, a["index"], "inline"),
        }
        (inline if a["is_inline"] or a.get("content_id") else real).append(item)
    return real, inline


# ---------------------------------------------------------------- 状态
@app.get("/api/status", summary="服务与索引状态")
def status():
    s = store()
    acc = account()
    folders = []
    for f in s.list_folders():
        c = s.folder_counts(f["name"])
        folders.append(
            {
                "name": f["name"],
                "total": c["total"],
                "unread": c["unread"],
                "uidvalidity": f["uidvalidity"],
                "last_synced": f["last_synced"],
                "last_error": f["last_error"],
            }
        )
    return {
        "ok": True,
        "account": {"name": acc.name, "email": acc.email, "display_name": acc.display_name},
        "imap": {"host": acc.imap_host, "port": acc.imap_port, "ssl": acc.imap_ssl},
        "stats": s.stats(),
        "categories": s.category_stats(),
        "threads": s.thread_stats(),
        "folders": folders,
        "syncing": _state["syncing"],
        "last_sync": _state["last_sync"],
    }


@app.get("/api/folders", summary="目录列表与计数")
def folders():
    s = store()
    known = {f["name"]: f for f in s.list_folders()}
    try:
        remote = {f["name"]: f for f in client().list_folders()}
    except Exception as e:
        remote = {}
        _state["log"].append(f"list_folders failed: {e}")
    out = []
    for name in sorted(set(known) | set(remote)):
        c = s.folder_counts(name)
        synced = name in known
        out.append(
            {
                "name": name,
                "total": c["total"],
                "unread": c["unread"],
                "synced": synced,
                "last_synced": known[name]["last_synced"] if synced else 0,
                "flags": remote.get(name, {}).get("flags", []),
            }
        )
    return {"folders": out, "count": len(out)}


# ---------------------------------------------------------------- 同步
class SyncRequest(BaseModel):
    folder: str | list[str] | None = None
    since: str | None = DEFAULT_SINCE
    limit: int = 500
    body_limit: int | None = None
    force: bool = False
    with_body: bool = True
    all_folders: bool = False


@app.post("/api/sync", summary="增量同步 IMAP -> 本地索引")
def do_sync(req: SyncRequest):
    if _state["syncing"]:
        raise HTTPException(429, "同步正在进行中")
    _state["syncing"] = True
    t0 = time.time()
    try:
        c = client()
        s = store()
        if req.all_folders or req.folder is None:
            targets = [f["name"] for f in c.list_folders()]
        elif isinstance(req.folder, str):
            targets = [req.folder]
        else:
            targets = list(req.folder)

        def progress(folder: str, msg: str):
            _state["log"].append(f"[{folder}] {msg}")

        results = [
            syncmod.sync_folder(
                c,
                s,
                f,
                since=req.since,
                envelope_limit=req.limit,
                body_limit=req.body_limit,
                force=req.force,
                with_body=req.with_body,
                progress=progress,
            )
            for f in targets
        ]
        _state["last_sync"] = {"at": time.time(), "results": results}
        return {
            "ok": True,
            "elapsed": round(time.time() - t0, 2),
            "folders": results,
            "new": sum(r["new"] for r in results),
            "bodies": sum(r["bodies"] for r in results),
            "attachments": sum(r["attachments"] for r in results),
        }
    except Exception as e:
        raise HTTPException(500, f"同步失败: {e}")
    finally:
        _state["syncing"] = False


# ---------------------------------------------------------------- 邮件列表 / 详情
@app.get("/api/messages", summary="信封级邮件列表")
def list_messages(
    folder: list[str] | None = Query(default=None, description="可重复；默认 INBOX"),
    since: str | None = Query(default=None, examples=["2026-07-01"]),
    until: str | None = None,
    q: str | None = None,
    unread_only: bool = False,
    has_attachment: bool | None = None,
    category: str | None = Query(default=None, pattern="^(personal|meeting|automated|promotion)$"),
    boring: bool | None = Query(default=None, description="true=只看无聊邮件，false=只看人工邮件"),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    order: str = "desc",
):
    s = store()
    folders = folder or ["INBOX"]
    total, items = s.list_messages(
        folders=folders,
        since=since,
        until=until,
        q=q,
        unread_only=unread_only,
        has_attachment=has_attachment,
        category=category,
        boring=boring,
        limit=limit,
        offset=offset,
        order=order,
    )
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "count": len(items),
        "items": [_enrich_categories(m) for m in items],
    }


@app.get("/api/messages/{uid}", summary="单封邮件全文")
def get_message(
    uid: int,
    folder: str = "INBOX",
    include_body: bool = True,
    body_format: str = Query(default="both", pattern="^(text|html|both)$"),
):
    s = store()
    msg = s.get_message(folder, uid)
    if not msg:
        raise HTTPException(404, f"未找到邮件 {folder}:{uid}（可能需要先同步）")
    out = dict(msg)
    atts = s.get_attachments(folder, uid)
    real, inline = split_attachments(atts, folder, uid)
    if include_body:
        body = s.get_body(folder, uid)
        if body_format in ("text", "both"):
            out["body_text"] = body["body_text"]
        if body_format in ("html", "both"):
            # cid: 已替换为可直接引用的 URL，前端可直接把这段 HTML 渲染进正文
            out["body_html"] = resolve_cids(body["body_html"], folder, uid, atts)
        out["headers"] = body["headers"]
    out["attachments"] = real          # 真实附件（下载用）
    out["inline_images"] = inline      # 内嵌资源（已渲染进正文，不单独列出）
    out["attachment_count"] = len(real)
    out["inline_count"] = len(inline)
    _enrich_categories(out)
    return out


@app.get("/api/messages/{uid}/attachment/{index}", summary="下载附件")
def get_attachment(uid: int, index: int, folder: str = "INBOX", disposition: str = "attachment"):
    s = store()
    atts = [a for a in s.get_attachments(folder, uid) if a["index"] == index]
    if not atts:
        raise HTTPException(404, "附件不存在")
    a = atts[0]
    p = Path(a["path"]) if a["path"] else None
    if not p or not p.exists():
        raise HTTPException(404, "附件文件尚未落盘，请重新同步")
    # disposition=inline 用于正文里的 <img src>，不带 filename 浏览器才会直接显示
    fname = None if disposition == "inline" else a["filename"]
    return FileResponse(p, filename=fname, media_type=a["content_type"] or "application/octet-stream")


class FlagsRequest(BaseModel):
    folder: str = "INBOX"
    add: list[str] = []
    remove: list[str] = []


@app.post("/api/messages/{uid}/flags", summary="修改标志（已读/未读/星标）")
def set_flags(uid: int, req: FlagsRequest):
    s = store()
    msg = s.get_message(req.folder, uid)
    if not msg:
        raise HTTPException(404, "邮件不存在")
    try:
        flags = client().set_flags(uid, add=req.add, remove=req.remove, folder=req.folder)
        s.set_flags(req.folder, uid, flags)
    except Exception as e:
        raise HTTPException(500, f"设置标志失败: {e}")
    return {"ok": True, "uid": uid, "folder": req.folder, "flags": flags, "unread": "\\Seen" not in flags}


@app.get("/api/search", summary="全文搜索")
def search(
    q: str = Query(..., min_length=1),
    folder: list[str] | None = Query(default=None),
    since: str | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = 0,
):
    total, items = store().list_messages(folders=folder, since=since, q=q, limit=limit, offset=offset)
    return {"query": q, "total": total, "items": items}


# ---------------------------------------------------------------- AI 专用
# ---------------------------------------------------------------- 会话（聊天视图）
@app.get("/api/threads", summary="会话列表（同一来回复用一封，类似 IM 会话）")
def list_threads(
    folder: list[str] | None = Query(default=None, description="可重复；不传=全部目录"),
    since: str | None = Query(default=None, examples=["2026-07-01"]),
    until: str | None = None,
    q: str | None = None,
    unread_only: bool = False,
    category: str | None = Query(default=None, pattern="^(personal|meeting|automated|promotion)$"),
    boring: bool | None = Query(default=None, description="true=只看无聊邮件，false=只看人工邮件"),
    only_grouped: bool = Query(default=False, description="true=只看多于一封的会话"),
    limit: int = Query(default=50, ge=1, le=300),
    offset: int = Query(default=0, ge=0),
    preview: int = Query(default=3, ge=0, le=20, description="每条会话预览几封邮件"),
):
    s = store()
    # 先全量取回再过滤分页，保证 only_grouped 下的 total 准确
    _total, threads = s.list_threads(
        folders=list(folder) if folder else None,
        since=since,
        until=until,
        q=q,
        unread_only=unread_only,
        boring=boring,
        category=category,
        own_emails=own_emails(),
        limit=1000000,
        offset=0,
    )
    items = []
    for t in threads:
        if only_grouped and t["message_count"] < 2:
            continue
        msgs = t["messages"]
        items.append(
            {
                "thread_id": t["thread_id"],
                "subject": t["subject"],
                "message_count": t["message_count"],
                "participants": t["participants"],
                "participant_count": t["participant_count"],
                "unread": t["unread"],
                "first_date": t["first_date"],
                "last_date": t["last_date"],
                "folders": t["folders"],
                "has_attachment": t["has_attachment"],
                "attachment_count": t["attachment_count"],
                "snippet": t["snippet"],
                "last_from": t["last_from"],
                "category": t["category"],
                "category_label": cat_label(t["category"]),
                "category_emoji": CATEGORY_EMOJI.get(t["category"], "✉️"),
                "is_boring": t["is_boring"],
                "preview": [
                    {
                        "id": m["id"],
                        "folder": m["folder"],
                        "uid": m["uid"],
                        "date": m["date"],
                        "from": m["from"],
                        "mine": is_mine(m),
                        "snippet": m["snippet"],
                    }
                    for m in msgs[-preview:]
                ],
                "url": f"/api/threads/{t['thread_id']}",
                "web_url": f"/?thread={t['thread_id']}",
            }
        )
    items = items[offset : offset + limit]
    return {"total": _total if not only_grouped else len(items), "limit": limit, "offset": offset,
            "count": len(items), "items": items}


@app.get("/api/threads/{thread_id}", summary="单条会话全文（聊天视图数据）")
def get_thread(
    thread_id: str,
    body_chars: int = Query(default=20000, ge=0, le=200000),
    body_format: str = Query(default="both", pattern="^(text|html|both)$"),
):
    s = store()
    t = s.get_thread(thread_id, own_emails=own_emails())
    if not t:
        raise HTTPException(404, f"未找到会话 {thread_id}（可能索引已重建）")
    msgs = t.pop("messages")
    out_msgs = []
    for m in msgs:
        body = s.get_body(m["folder"], m["uid"])
        atts = s.get_attachments(m["folder"], m["uid"])
        real, inline = split_attachments(atts, m["folder"], m["uid"])
        item = dict(m)
        if body_format in ("text", "both"):
            item["body_text"] = _truncate(body["body_text"], body_chars)
        if body_format in ("html", "both"):
            item["body_html"] = resolve_cids(body["body_html"], m["folder"], m["uid"], atts)
        item["attachments"] = real
        item["inline_images"] = inline
        item["attachment_count"] = len(real)
        item["inline_count"] = len(inline)
        item["web_url"] = f"/?folder={m['folder']}&uid={m['uid']}"
        item["url"] = f"/api/messages/{m['uid']}?folder={m['folder']}"
        out_msgs.append(_enrich_categories(item))
    t["messages"] = out_msgs
    t["category_label"] = cat_label(t["category"])
    t["category_emoji"] = CATEGORY_EMOJI.get(t["category"], "✉️")
    t["participants"] = t["participants"][:8]
    return t


@app.post("/api/reindex", summary="重建本地派生索引（分类 + 会话归组）")
def do_reindex(classify_all: bool = False, rebuild: bool = True):
    try:
        return {"ok": True, **reindex(classify_all=classify_all, rebuild=rebuild)}
    except Exception as e:
        raise HTTPException(500, f"重建索引失败: {e}")


@app.get("/api/categories", summary="分类统计：无聊邮件占比")
def categories():
    s = store()
    stats = s.category_stats()
    stats["labels"] = CATEGORY_LABELS
    stats["threads"] = s.thread_stats()
    return stats


def _truncate(text: str, n: int) -> str:
    if n <= 0 or not text or len(text) <= n:
        return text or ""
    return text[:n] + f"\n…（已截断，共 {len(text)} 字符，取全文请调用 /api/messages/{{uid}}）"


@app.get("/api/ai/inbox", summary="AI 入口：结构化邮件列表（含清洗正文）")
def ai_inbox(
    since: str | None = Query(default=DEFAULT_SINCE, examples=["2026-07-01"]),
    until: str | None = None,
    folder: list[str] | None = Query(default=None, description="默认 INBOX；传 all 表示全部目录"),
    q: str | None = None,
    unread_only: bool = False,
    has_attachment: bool | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = 0,
    body_chars: int = Query(default=4000, ge=0, le=200000),
    include_html: bool = False,
    include_inline: bool = Query(default=False, description="内嵌图片（签名 logo 等）是否列入 attachments"),
    category: str | None = Query(default=None, pattern="^(personal|meeting|automated|promotion)$"),
    boring: bool | None = Query(default=None, description="true=只要无聊邮件，false=排除无聊邮件"),
    order: str = "desc",
):
    s = store()
    if folder is None:
        folders = ["INBOX"]
    elif any(f.lower() == "all" for f in folder):
        folders = None
    else:
        folders = list(folder)

    total, items = s.list_messages(
        folders=folders,
        since=since,
        until=until,
        q=q,
        unread_only=unread_only,
        has_attachment=has_attachment,
        category=category,
        boring=boring,
        limit=limit,
        offset=offset,
        order=order,
    )
    out = []
    for m in items:
        body = s.get_body(m["folder"], m["uid"])
        atts = s.get_attachments(m["folder"], m["uid"])
        real, inline = split_attachments(atts, m["folder"], m["uid"])
        listed = real + inline if include_inline else real
        item = {
            "id": m["id"],
            "folder": m["folder"],
            "uid": m["uid"],
            "date": m["date"],
            "from": m["from"],
            "to": m["to"],
            "cc": m["cc"],
            "subject": m["subject"],
            "unread": m["unread"],
            "flags": m["flags"],
            "snippet": m["snippet"],
            # 分类：personal=人工邮件，其余为「无聊邮件」
            "category": m.get("category") or "personal",
            "category_label": cat_label(m.get("category") or "personal"),
            "is_boring": bool(m.get("is_boring")),
            "mine": is_mine(m),
            "thread_id": m.get("thread_id") or "",
            "body_text": _truncate(body["body_text"], body_chars),
            # 默认只列真实附件；内嵌图片已在正文里渲染，对 AI 也是噪声
            "attachments": [
                {
                    "filename": a["filename"],
                    "content_type": a["content_type"],
                    "size": a["size"],
                    "is_inline": a["is_inline"],
                    "url": a["url"],
                }
                for a in listed
            ],
            "inline_count": len(inline),
            "url": f"/api/messages/{m['uid']}?folder={m['folder']}",
            "web_url": f"/?folder={m['folder']}&uid={m['uid']}",
        }
        if include_html:
            item["body_html"] = resolve_cids(body["body_html"], m["folder"], m["uid"], atts)
        out.append(item)

    return {
        "account": account().email,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "query": {
            "since": since,
            "until": until,
            "folder": folders or "all",
            "q": q,
            "unread_only": unread_only,
            "limit": limit,
            "offset": offset,
            "body_chars": body_chars,
        },
        "total": total,
        "count": len(out),
        "next_offset": offset + len(out) if offset + len(out) < total else None,
        "messages": out,
    }


@app.get("/api/ai/threads", summary="AI 入口：会话视图（同一来回聚合，聊天式输出）")
def ai_threads(
    since: str | None = Query(default=DEFAULT_SINCE, examples=["2026-07-01"]),
    until: str | None = None,
    folder: list[str] | None = Query(default=None, description="默认全部目录；传 all 也是全部"),
    q: str | None = None,
    unread_only: bool = False,
    category: str | None = Query(default=None, pattern="^(personal|meeting|automated|promotion)$"),
    boring: bool | None = Query(default=None),
    only_grouped: bool = Query(default=False, description="true=只返回多于一封的会话"),
    limit: int = Query(default=20, ge=1, le=200),
    offset: int = 0,
    body_chars: int = Query(default=1500, ge=0, le=200000),
    max_messages: int = Query(default=30, ge=1, le=200, description="单条会话最多返回多少封"),
):
    s = store()
    folders = None if not folder or any(f.lower() == "all" for f in folder) else list(folder)
    _total, threads = s.list_threads(
        folders=folders,
        since=since,
        until=until,
        q=q,
        unread_only=unread_only,
        boring=boring,
        category=category,
        own_emails=own_emails(),
        limit=1000000,
        offset=0,
    )
    out = []
    for t in threads:
        if only_grouped and t["message_count"] < 2:
            continue
        msgs = t["messages"][-max_messages:]
        out.append(
            {
                "thread_id": t["thread_id"],
                "subject": t["subject"],
                "participants": [p["name"] or p["email"] for p in t["participants"]],
                "message_count": t["message_count"],
                "unread": t["unread"],
                "first_date": t["first_date"],
                "last_date": t["last_date"],
                "folders": t["folders"],
                "category": t["category"],
                "category_label": cat_label(t["category"]),
                "is_boring": t["is_boring"],
                "attachment_count": t["attachment_count"],
                "messages": [
                    {
                        "id": m["id"],
                        "folder": m["folder"],
                        "uid": m["uid"],
                        "date": m["date"],
                        "from": m["from"],
                        "to": m["to"],
                        "mine": is_mine(m),
                        "unread": m["unread"],
                        "body_text": _truncate(s.get_body(m["folder"], m["uid"])["body_text"], body_chars),
                        "attachments": [
                            {
                                "filename": a["filename"],
                                "size": a["size"],
                                "url": att_url(m["folder"], m["uid"], a["index"], "attachment"),
                            }
                            for a in s.get_attachments(m["folder"], m["uid"])
                            if not a["is_inline"]
                        ],
                        "url": f"/api/messages/{m['uid']}?folder={m['folder']}",
                        "web_url": f"/?folder={m['folder']}&uid={m['uid']}",
                    }
                    for m in msgs
                ],
                "url": f"/api/threads/{t['thread_id']}",
                "web_url": f"/?thread={t['thread_id']}",
            }
        )
    out = out[offset : offset + limit]
    return {
        "account": account().email,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "query": {"since": since, "until": until, "folder": folders or "all", "q": q,
                  "boring": boring, "category": category, "only_grouped": only_grouped},
        "total": _total if not only_grouped else len(out),
        "count": len(out),
        "threads": out,
    }


@app.get("/api/ai/digest", summary="AI 入口：按维度聚合统计")
def ai_digest(
    since: str | None = Query(default=DEFAULT_SINCE),
    until: str | None = None,
    folder: list[str] | None = Query(default=None),
    q: str | None = None,
    group_by: str = Query(default="sender", pattern="^(sender|date|subject|folder)$"),
    top: int = Query(default=20, ge=1, le=200),
):
    s = store()
    folders = None if folder and any(f.lower() == "all" for f in folder) else (list(folder) if folder else ["INBOX"])
    _, items = s.list_messages(folders=folders, since=since, until=until, q=q, limit=5000, order="desc")

    def key(m: dict) -> str:
        if group_by == "sender":
            return f'{m["from"]["name"]} <{m["from"]["email"]}>'
        if group_by == "date":
            return (m["date"] or "")[:10]
        if group_by == "folder":
            return m["folder"]
        import re as _re

        return _re.sub(r"^(re|fw|fwd)\s*[:：]\s*", "", (m["subject"] or ""), flags=_re.I).strip().lower()

    buckets: dict[str, dict] = {}
    for m in items:
        k = key(m)
        b = buckets.setdefault(k, {"key": k, "count": 0, "unread": 0, "with_attachment": 0, "latest": "", "samples": []})
        b["count"] += 1
        b["unread"] += 1 if m["unread"] else 0
        b["with_attachment"] += 1 if m["has_attachment"] else 0
        if (m["date"] or "") > b["latest"]:
            b["latest"] = m["date"] or ""
        if len(b["samples"]) < 3:
            b["samples"].append({"id": m["id"], "date": m["date"], "subject": m["subject"], "snippet": m["snippet"][:120]})

    groups = sorted(buckets.values(), key=lambda x: x["count"], reverse=True)[:top]
    return {
        "account": account().email,
        "query": {"since": since, "until": until, "folder": folders or "all", "q": q, "group_by": group_by},
        "total_messages": len(items),
        "group_count": len(groups),
        "groups": groups,
    }


@app.get("/api/ai/verify", summary="校验：指定时间窗内各目录的邮件数量与日期范围")
def ai_verify(since: str = Query(default=DEFAULT_SINCE), folder: list[str] | None = Query(default=None)):
    s = store()
    folders = None if folder and any(f.lower() == "all" for f in folder) else (list(folder) if folder else None)
    if folders is None:
        folders = [f["name"] for f in s.list_folders()]
    out = []
    total = 0
    with_body = 0
    for f in folders:
        total_, items = s.list_messages(folders=[f], since=since, limit=100000)
        bodies = s.conn.execute(
            "SELECT COUNT(*) AS c FROM messages m JOIN bodies b ON b.folder=m.folder AND b.uid=m.uid "
            "WHERE m.folder=? AND m.date_ts>=?",
            (f, s._ts(since)),
        ).fetchone()["c"]
        dates = [i["date"] for i in items if i["date"]]
        out.append(
            {
                "folder": f,
                "count": total_,
                "with_body": bodies,
                "oldest": min(dates) if dates else None,
                "newest": max(dates) if dates else None,
                "attachments": s.conn.execute(
                    "SELECT COUNT(*) AS c FROM attachments a JOIN messages m ON m.folder=a.folder AND m.uid=a.uid "
                    "WHERE m.folder=? AND m.date_ts>=?",
                    (f, s._ts(since)),
                ).fetchone()["c"],
            }
        )
        total += total_
        with_body += bodies
    return {"since": since, "total": total, "with_body": with_body, "folders": out}


@app.on_event("shutdown")
def _shutdown():
    if _state["store"] is not None:
        try:
            _state["store"].conn.close()
        except Exception:
            pass

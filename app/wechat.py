"""微信数据接入 —— 复用 wechat-cli 的解密内核，产出可读的统计与摘要。

wechat-cli 负责三件事：从微信进程内存提取数据库密钥（init）、把 SQLCipher 库解密到临时目录、
按 `Msg_<md5(username)>` 分表查询消息。这里只复用它的解密与查询能力，聚合逻辑自己写，
因为我们想要的是「跨全部会话的时间窗统计」，而 CLI 只有单会话 stats。

依赖：pycryptodome / zstandard / click + wechat_cli 包（优先用已安装的，退回 source/ 源码树）。
"""
from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import sys
import threading
import time
from collections import Counter
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path

from . import wechat_classify as wc
from .context import ctx
from .context import CACHE_TTL as CACHE_TTL_DEFAULT

ROOT = Path(__file__).resolve().parent.parent
SOURCE_DIR = ROOT / "source"

TYPE_LABELS = {
    1: "文本", 3: "图片", 34: "语音", 42: "名片",
    43: "视频", 47: "表情", 48: "位置", 49: "链接/文件",
    50: "通话", 10000: "系统", 10002: "撤回",
}

_TABLE_RE = re.compile(r"Msg_[0-9a-f]{32}")

_lock = threading.Lock()
_mods = None           # 导入的 wechat_cli 子模块集合
CACHE_TTL = CACHE_TTL_DEFAULT   # 统计结果缓存 5 分钟（统一上下文里的默认值）

# 缓存统一走 app/context.py，加模块前缀便于按模块失效
_CACHE_PREFIX = "wx:"


class WechatError(RuntimeError):
    """微信数据不可用（未初始化 / 微信未运行 / 依赖缺失）。"""


# ---------------------------------------------------------------- 基础设施

def _load_mods():
    """导入 wechat_cli 及其核心函数。优先已安装包，退回 source/ 源码树。"""
    global _mods
    if _mods is not None:
        return _mods
    try:
        import wechat_cli  # noqa: F401
    except ImportError:
        # 开发者可能把 wechat-cli 源码树放在项目根的 source/ 下（非必须）
        if SOURCE_DIR.exists():
            sys.path.insert(0, str(SOURCE_DIR))
        try:
            import wechat_cli  # noqa: F401
        except ImportError as exc:
            raise WechatError(
                "未找到 wechat_cli 包。请先安装外部依赖 wechat-cli："
                'pip install "wechat-cli @ git+https://github.com/huohuoer/wechat-cli"'
            ) from exc

    from wechat_cli.core.config import STATE_DIR, load_config
    from wechat_cli.core.contacts import get_contact_names, get_self_username
    from wechat_cli.core.context import AppContext
    from wechat_cli.core.messages import (
        _format_message_text,
        _load_name2id_maps,
        _resolve_sender_label,
        decompress_content,
        find_msg_db_keys,
        format_msg_type,
        resolve_chat_context,
    )

    _mods = {
        "STATE_DIR": STATE_DIR,
        "load_config": load_config,
        "get_contact_names": get_contact_names,
        "get_self_username": get_self_username,
        "AppContext": AppContext,
        "_format_message_text": _format_message_text,
        "_load_name2id_maps": _load_name2id_maps,
        "_resolve_sender_label": _resolve_sender_label,
        "decompress_content": decompress_content,
        "find_msg_db_keys": find_msg_db_keys,
        "format_msg_type": format_msg_type,
        "resolve_chat_context": resolve_chat_context,
    }
    return _mods


def _app_ctx():
    """惰性创建微信 AppContext（解密缓存跨请求复用），实例挂在统一上下文里。"""
    if ctx.wx_app is not None:
        return ctx.wx_app
    m = _load_mods()
    cfg_path = os.environ.get("WECHAT_CLI_CONFIG") or None
    try:
        app = m["AppContext"](cfg_path)
    except FileNotFoundError as e:
        raise WechatError(
            f"{e}\n先运行（微信需保持登录）: wechat-cli init"
        )
    except Exception as e:
        raise WechatError(f"微信数据初始化失败: {e}")
    ctx.wx_app = app
    return app


def _cached(key, ttl=CACHE_TTL):
    return ctx.cache.get(_CACHE_PREFIX + str(key))


def _put(key, value, ttl=CACHE_TTL):
    return ctx.cache.put(_CACHE_PREFIX + str(key), value, ttl)


def invalidate() -> int:
    """只清微信的缓存（邮件侧缓存不受影响）。"""
    return ctx.cache.invalidate(_CACHE_PREFIX)


# ---------------------------------------------------------------- 状态

def status() -> dict:
    """微信数据可用性自检。"""
    m = _load_mods()
    state_dir = m["STATE_DIR"]
    cfg_path = os.environ.get("WECHAT_CLI_CONFIG") or os.path.join(state_dir, "config.json")
    keys_path = os.path.join(state_dir, "all_keys.json")
    info = {
        "ok": False,
        "state_dir": state_dir,
        "config_exists": os.path.exists(cfg_path),
        "keys_exists": os.path.exists(keys_path),
        "db_dir": None,
        "msg_dbs": 0,
        "error": None,
    }
    try:
        app = _app_ctx()
    except WechatError as e:
        info["error"] = str(e)
        return info
    info["db_dir"] = app.db_dir
    info["msg_dbs"] = len(app.msg_db_keys)
    info["ok"] = bool(app.msg_db_keys)
    if not info["ok"]:
        info["error"] = "未找到消息数据库，请确认微信已登录并重新 wechat-cli init"
    return info


# ---------------------------------------------------------------- 时间窗

def window(days: int | None) -> tuple[int | None, int | None]:
    """最近 N 个自然日（含今天）的 [start_ts, end_ts]。days=None → 全部时间。"""
    if not days:
        return None, None
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    return int((today - timedelta(days=days - 1)).timestamp()), None


def _range_label(start_ts, end_ts) -> str:
    f = "%Y-%m-%d"
    a = datetime.fromtimestamp(start_ts).strftime(f) if start_ts else "最早"
    b = datetime.fromtimestamp(end_ts).strftime(f) if end_ts else "今天"
    return f"{a} → {b}"


# ---------------------------------------------------------------- 全局统计

def _scan(start_ts: int | None, end_ts: int | None, exclude_official: bool = False) -> dict:
    """扫全部消息库，按时间窗聚合。单趟扫描，聚合在 Python 里做。

    exclude_official=True 时，公众号 / 系统通知这类非人对话整表跳过（连表都不查）。
    """
    app = _app_ctx()
    m = _load_mods()
    names = m["get_contact_names"](app.cache, app.decrypted_dir)
    self_user = m["get_self_username"](app.db_dir, app.cache, app.decrypted_dir)
    disp = app.display_name_fn

    def _dn(u: str) -> str:
        try:
            return disp(u, names) or u
        except Exception:
            return names.get(u, u)

    total = 0
    mine = 0
    by_day: Counter = Counter()
    by_hour: Counter = Counter()
    by_type: Counter = Counter()
    by_chat: Counter = Counter()      # username -> count
    by_sender: Counter = Counter()    # username -> count
    by_cat: Counter = Counter()       # category -> 消息数
    cat_chats: Counter = Counter()    # category -> 会话数
    by_project: Counter = Counter()   # 项目名 -> 消息数
    chat_group: dict = {}
    chat_meta: dict = {}              # username -> classify() 结果
    skipped_official = 0
    first_ts = None
    last_ts = None
    dbs = 0
    tables = 0

    for rel in app.msg_db_keys:
        path = app.cache.get(rel)
        if not path:
            continue
        dbs += 1
        try:
            with closing(sqlite3.connect(path)) as conn:
                id2u = m["_load_name2id_maps"](conn)
                hash2u = {hashlib.md5(u.encode()).hexdigest(): u for u in set(id2u.values())}
                tbl_names = [
                    r[0] for r in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Msg_%'"
                    )
                ]
                where, params = [], []
                if start_ts is not None:
                    where.append("create_time >= ?")
                    params.append(start_ts)
                if end_ts is not None:
                    where.append("create_time <= ?")
                    params.append(end_ts)
                wsql = f"WHERE {' AND '.join(where)}" if where else ""

                for t in tbl_names:
                    if not _TABLE_RE.fullmatch(t):
                        continue
                    username = hash2u.get(t[4:], "")
                    is_group = "@chatroom" in username
                    meta = wc.classify(username, _dn(username) if username else "", is_group)
                    if exclude_official and meta["category"] in wc.NON_HUMAN:
                        skipped_official += 1
                        continue
                    tables += 1
                    if username:
                        chat_group[username] = is_group
                        chat_meta[username] = meta
                    try:
                        rows = conn.execute(
                            f"SELECT create_time, local_type, real_sender_id FROM [{t}] {wsql}",
                            params,
                        ).fetchall()
                    except sqlite3.Error:
                        continue
                    for ts, lt, sid in rows:
                        total += 1
                        try:
                            dt = datetime.fromtimestamp(ts)
                        except (OSError, ValueError, OverflowError):
                            continue
                        by_day[dt.strftime("%Y-%m-%d")] += 1
                        by_hour[dt.hour] += 1
                        by_type[lt & 0xFFFFFFFF] += 1
                        by_cat[meta["category"]] += 1
                        if meta["project"]:
                            by_project[meta["project"]] += 1
                        if username:
                            by_chat[username] += 1
                        su = id2u.get(sid) if sid else None
                        if su:
                            by_sender[su] += 1
                            if su == self_user:
                                mine += 1
                        elif username and not is_group:
                            # 单聊里自己发的消息 real_sender_id 通常为 0
                            mine += 1
                        if first_ts is None or ts < first_ts:
                            first_ts = ts
                        if last_ts is None or ts > last_ts:
                            last_ts = ts
        except Exception:
            continue

    for u in by_chat:
        cat_chats[chat_meta.get(u, {}).get("category", "private")] += 1

    top_chats = []
    for u, c in by_chat.most_common(80):
        meta = chat_meta.get(u) or wc.classify(u, _dn(u), chat_group.get(u))
        top_chats.append({
            "username": u,
            "chat": _dn(u),
            "is_group": bool(chat_group.get(u)),
            "count": c,
            "pct": round(c / total * 100, 1) if total else 0.0,
            "category": meta["category"],
            "cat_label": meta["label"],
            "tag": meta["tag"],
            "project": meta["project"],
            "org": meta["org"],
        })
    top_senders = [
        {"username": u, "name": _dn(u), "count": c}
        for u, c in by_sender.most_common(15)
    ]
    group_cnt = sum(c for u, c in by_chat.items() if chat_group.get(u))
    private_cnt = total - group_cnt

    return {
        "total": total,
        "by_category": wc.category_summary(by_cat, cat_chats),
        "by_project": [
            {"project": p, "count": c,
             "pct": round(c / total * 100, 1) if total else 0.0}
            for p, c in by_project.most_common(20)
        ],
        "exclude_official": bool(exclude_official),
        "skipped_official_chats": skipped_official,
        "mine": mine,
        "others": total - mine,
        "chats": len(by_chat),
        "group_chats": sum(1 for u in by_chat if chat_group.get(u)),
        "private_chats": sum(1 for u in by_chat if not chat_group.get(u)),
        "group_messages": group_cnt,
        "private_messages": private_cnt,
        "by_day": dict(sorted(by_day.items())),
        "by_hour": {str(h): by_hour.get(h, 0) for h in range(24)},
        "by_type": [
            {"type": TYPE_LABELS.get(t, f"type={t}"), "count": c,
             "pct": round(c / total * 100, 1) if total else 0.0}
            for t, c in by_type.most_common()
        ],
        "top_chats": top_chats,
        "top_senders": top_senders,
        "scanned": {"databases": dbs, "tables": tables},
        "first_ts": first_ts,
        "last_ts": last_ts,
    }


def overview(days: int | None = 7, refresh: bool = False,
             exclude_official: bool = False) -> dict:
    """最近 N 天的全局统计（含未读数）。"""
    start_ts, end_ts = window(days)
    key = ("overview", days, int(bool(exclude_official)))
    if not refresh:
        hit = _cached(key)
        if hit:
            return hit

    with _lock:
        hit = _cached(key)
        if hit and not refresh:
            return hit
        data = _scan(start_ts, end_ts, exclude_official=exclude_official)
        data.update(
            {
                "days": days,
                "range": _range_label(start_ts, end_ts),
                "start_ts": start_ts,
                "end_ts": end_ts,
                "unread": unread_total(),
                "generated_at": int(time.time()),
            }
        )
        return _put(key, data)


def unread_total() -> int:
    """session.db 里所有会话的未读总数。"""
    app = _app_ctx()
    path = app.cache.get(os.path.join("session", "session.db"))
    if not path:
        return 0
    try:
        with closing(sqlite3.connect(path)) as conn:
            row = conn.execute("SELECT COALESCE(SUM(unread_count), 0) FROM SessionTable").fetchone()
            return int(row[0] or 0)
    except sqlite3.Error:
        return 0


# ---------------------------------------------------------------- 会话

def sessions(limit: int = 30, exclude_official: bool = False) -> list[dict]:
    """最近会话列表（与 wechat-cli sessions 一致），带分类标签。"""
    app = _app_ctx()
    m = _load_mods()
    names = m["get_contact_names"](app.cache, app.decrypted_dir)
    path = app.cache.get(os.path.join("session", "session.db"))
    if not path:
        return []
    out = []
    with closing(sqlite3.connect(path)) as conn:
        rows = conn.execute(
            "SELECT username, unread_count, summary, last_timestamp, last_msg_type,"
            "       last_msg_sender, last_sender_display_name"
            "  FROM SessionTable WHERE last_timestamp > 0"
            " ORDER BY last_timestamp DESC LIMIT ?",
            # 过滤公众号会砍掉一部分行，先多取一些再截断
            (limit * 4 if exclude_official else limit,),
        ).fetchall()
    for username, unread, summary, ts, msg_type, sender, sender_name in rows:
        if len(out) >= limit:
            break
        is_group = "@chatroom" in username
        meta = wc.classify(username, names.get(username, username), is_group)
        if exclude_official and meta["category"] in wc.NON_HUMAN:
            continue
        if isinstance(summary, bytes):
            summary = m["decompress_content"](summary, 4) or ""
        if isinstance(summary, str) and ":\n" in summary:
            summary = summary.split(":\n", 1)[1]
        sender_disp = ""
        if is_group and sender:
            sender_disp = names.get(sender) or sender_name or sender
        out.append(
            {
                "chat": names.get(username, username),
                "username": username,
                "is_group": is_group,
                "category": meta["category"],
                "cat_label": meta["label"],
                "tag": meta["tag"],
                "project": meta["project"],
                "unread": unread or 0,
                "last_message": str(summary or ""),
                "msg_type": m["format_msg_type"](msg_type),
                "sender": sender_disp,
                "timestamp": ts,
                "time": datetime.fromtimestamp(ts).strftime("%m-%d %H:%M"),
            }
        )
    return out


def chat_history(chat: str, days: int | None = 7, limit: int = 50) -> dict:
    """某个会话在时间窗内的最近消息（结构化）。"""
    app = _app_ctx()
    m = _load_mods()
    ctx = m["resolve_chat_context"](chat, app.msg_db_keys, app.cache, app.decrypted_dir)
    if not ctx:
        raise WechatError(f"找不到聊天对象: {chat}")
    if not ctx.get("db_path"):
        return {"chat": ctx["display_name"], "username": ctx["username"],
                "is_group": ctx["is_group"], "items": [], "total": 0}

    names = m["get_contact_names"](app.cache, app.decrypted_dir)
    disp = app.display_name_fn
    start_ts, end_ts = window(days)
    rows: list[tuple] = []
    tables = ctx.get("message_tables") or [{"db_path": ctx["db_path"], "table_name": ctx["table_name"]}]
    for t in tables:
        tbl = t["table_name"]
        if not _TABLE_RE.fullmatch(tbl):
            continue
        where, params = [], []
        if start_ts is not None:
            where.append("create_time >= ?")
            params.append(start_ts)
        if end_ts is not None:
            where.append("create_time <= ?")
            params.append(end_ts)
        wsql = f"WHERE {' AND '.join(where)}" if where else ""
        try:
            with closing(sqlite3.connect(t["db_path"])) as conn:
                id2u = m["_load_name2id_maps"](conn)
                for r in conn.execute(
                    f"SELECT local_id, local_type, create_time, real_sender_id, message_content,"
                    f"       WCDB_CT_message_content FROM [{tbl}] {wsql}"
                    f" ORDER BY create_time DESC LIMIT ?",
                    (*params, max(limit * 3, 100)),
                ).fetchall():
                    rows.append((r, id2u))
        except sqlite3.Error:
            continue

    rows.sort(key=lambda x: x[0][2], reverse=True)
    rows = rows[:limit]
    items = []
    for (local_id, local_type, ts, sid, content, ct), id2u in rows:
        text = m["decompress_content"](content, ct)
        if text is None:
            text = "(无法解压)"
        _, body = m["_format_message_text"](
            local_id, local_type, text, ctx["is_group"], ctx["username"],
            ctx["display_name"], names, disp,
        )
        try:
            who = m["_resolve_sender_label"](
                sid, "", ctx["is_group"], ctx["username"], ctx["display_name"],
                names, id2u, disp,
            ) or ""
        except Exception:
            who = ""
        items.append(
            {
                "id": local_id,
                "time": datetime.fromtimestamp(ts).strftime("%m-%d %H:%M"),
                "timestamp": ts,
                "type": m["format_msg_type"](local_type),
                "sender": who,
                "text": (body or "")[:400],
            }
        )
    return {
        "chat": ctx["display_name"],
        "username": ctx["username"],
        "is_group": ctx["is_group"],
        "days": days,
        "range": _range_label(start_ts, end_ts),
        "total": len(items),
        "items": items,
    }


def recent(limit: int = 20, days: int | None = 7, exclude_official: bool = False,
           category: str | None = None) -> dict:
    """跨全部会话的最新消息流（时间窗内按时间倒序），供统计页「最新消息」面板。

    exclude_official 过滤公众号 / 系统通知；category 只看某一类（project / colleague / …）。
    """
    key = ("recent", days, limit, int(bool(exclude_official)), category or "")
    hit = _cached(key)
    if hit:
        return hit

    app = _app_ctx()
    m = _load_mods()
    names = m["get_contact_names"](app.cache, app.decrypted_dir)
    disp = app.display_name_fn
    self_user = m["get_self_username"](app.db_dir, app.cache, app.decrypted_dir)
    start_ts, end_ts = window(days)
    today_label = datetime.now().strftime("%m-%d")

    def _dn(u: str) -> str:
        try:
            return disp(u, names) or u
        except Exception:
            return names.get(u, u)

    rows: list[tuple] = []
    with _lock:
        hit = _cached(key)
        if hit:
            return hit
        for rel in app.msg_db_keys:
            path = app.cache.get(rel)
            if not path:
                continue
            try:
                with closing(sqlite3.connect(path)) as conn:
                    id2u = m["_load_name2id_maps"](conn)
                    hash2u = {hashlib.md5(u.encode()).hexdigest(): u
                              for u in set(id2u.values())}
                    tbl_names = [
                        r[0] for r in conn.execute(
                            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Msg_%'"
                        )
                    ]
                    where, params = [], []
                    if start_ts is not None:
                        where.append("create_time >= ?")
                        params.append(start_ts)
                    if end_ts is not None:
                        where.append("create_time <= ?")
                        params.append(end_ts)
                    wsql = f"WHERE {' AND '.join(where)}" if where else ""
                    for t in tbl_names:
                        if not _TABLE_RE.fullmatch(t):
                            continue
                        username = hash2u.get(t[4:], "")
                        if not username:
                            continue
                        is_group = "@chatroom" in username
                        meta = wc.classify(username, _dn(username), is_group)
                        if exclude_official and meta["category"] in wc.NON_HUMAN:
                            continue
                        if category and meta["category"] != category:
                            continue
                        try:
                            for r in conn.execute(
                                f"SELECT local_id, local_type, create_time, real_sender_id,"
                                f"       message_content, WCDB_CT_message_content FROM [{t}] {wsql}"
                                f" ORDER BY create_time DESC LIMIT ?",
                                # 单表最多取 60 条候选：够前端「每会话留 3 条 + 折叠」用，
                                # 又不至于在 days=0（全部时间）时把几百张表的 blob 全拉进内存
                                (*params, min(max(limit, 10), 60)),
                            ).fetchall():
                                rows.append((r, username, is_group, id2u, meta))
                        except sqlite3.Error:
                            continue
            except Exception:
                continue

        rows.sort(key=lambda x: x[0][2], reverse=True)
        rows = rows[:limit]
        items = []
        for (local_id, local_type, ts, sid, content, ct), username, is_group, id2u, meta in rows:
            text = m["decompress_content"](content, ct)
            if text is None:
                text = "(无法解压)"
            chat_name = _dn(username)
            _, body = m["_format_message_text"](
                local_id, local_type, text, is_group, username, chat_name, names, disp,
            )
            try:
                who = m["_resolve_sender_label"](
                    sid, "", is_group, username, chat_name, names, id2u, disp,
                ) or ""
            except Exception:
                who = ""
            su = id2u.get(sid) if sid else None
            mine = su == self_user or (not is_group and not sid)
            who_disp = "" if mine else who
            if who_disp == chat_name:  # 单聊里 sender 就是会话名，名字列已展示
                who_disp = ""
            dt = datetime.fromtimestamp(ts)
            time_label = dt.strftime("%m-%d %H:%M")
            if time_label.startswith(today_label):
                time_label = "今天 " + time_label[6:]
            items.append(
                {
                    "chat": chat_name,
                    "username": username,
                    "is_group": is_group,
                    "category": meta["category"],
                    "cat_label": meta["label"],
                    "tag": meta["tag"],
                    "project": meta["project"],
                    "sender": who_disp,
                    "mine": mine,
                    "time": time_label,
                    "timestamp": ts,
                    "type": m["format_msg_type"](local_type),
                    "text": (body or "")[:120],
                }
            )
        out = {
            "days": days,
            "range": _range_label(start_ts, end_ts),
            "exclude_official": bool(exclude_official),
            "category": category or "",
            "total": len(items),
            "items": items,
        }
        return _put(key, out)


# ---------------------------------------------------------------- 关键词词云

# 词云抽样策略：每个会话取「最近 N 条文本」，N 随时间窗变小。
# 这样每个会话贡献相同额度（分层抽样），不会因为扫库顺序而被前几个库的话痨群带偏；
# 总量天然有界（会话数 × N），KW_TOTAL_CAP 只是最后的安全阀。
def _kw_per_table(days: int | None) -> int:
    if not days:
        return 40          # 全部时间：661 个会话 × 40 ≈ 1.5 万条，几秒内出结果
    if days <= 7:
        return 800
    if days <= 31:
        return 400
    return 150


KW_TOTAL_CAP = 40000


def keywords(days: int | None = 7, limit: int = 80, exclude_official: bool = True,
             category: str | None = None, mine: str = "all") -> dict:
    """时间窗内的关键词词云。只吃纯文本消息（local_type=1），同一条消息里同词只算一次。

    mine: all | others（别人说的）| me（我说的）
    """
    key = ("kw", days, limit, int(bool(exclude_official)), category or "", mine)
    hit = _cached(key)
    if hit:
        return hit

    app = _app_ctx()
    m = _load_mods()
    names = m["get_contact_names"](app.cache, app.decrypted_dir)
    self_user = m["get_self_username"](app.db_dir, app.cache, app.decrypted_dir)
    disp = app.display_name_fn
    start_ts, end_ts = window(days)

    def _dn(u: str) -> str:
        try:
            return disp(u, names) or u
        except Exception:
            return names.get(u, u)

    texts: list[str] = []
    scanned = 0
    chats_used = 0
    truncated = False
    per_table = _kw_per_table(days)

    with _lock:
        hit = _cached(key)
        if hit:
            return hit
        for rel in app.msg_db_keys:
            if scanned >= KW_TOTAL_CAP:
                truncated = True
                break
            path = app.cache.get(rel)
            if not path:
                continue
            try:
                with closing(sqlite3.connect(path)) as conn:
                    id2u = m["_load_name2id_maps"](conn)
                    hash2u = {hashlib.md5(u.encode()).hexdigest(): u
                              for u in set(id2u.values())}
                    tbl_names = [
                        r[0] for r in conn.execute(
                            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Msg_%'"
                        )
                    ]
                    where = ["local_type = 1"]
                    params: list = []
                    if start_ts is not None:
                        where.append("create_time >= ?")
                        params.append(start_ts)
                    if end_ts is not None:
                        where.append("create_time <= ?")
                        params.append(end_ts)
                    wsql = "WHERE " + " AND ".join(where)

                    for t in tbl_names:
                        if not _TABLE_RE.fullmatch(t):
                            continue
                        if scanned >= KW_TOTAL_CAP:
                            truncated = True
                            break
                        username = hash2u.get(t[4:], "")
                        if not username:
                            continue
                        is_group = "@chatroom" in username
                        meta = wc.classify(username, _dn(username), is_group)
                        if exclude_official and meta["category"] in wc.NON_HUMAN:
                            continue
                        if category and meta["category"] != category:
                            continue
                        try:
                            rows = conn.execute(
                                f"SELECT real_sender_id, message_content,"
                                f"       WCDB_CT_message_content FROM [{t}] {wsql}"
                                f" ORDER BY create_time DESC LIMIT ?",
                                (*params, per_table),
                            ).fetchall()
                        except sqlite3.Error:
                            continue
                        if rows:
                            chats_used += 1
                        for sid, content, ct in rows:
                            if mine != "all":
                                su = id2u.get(sid) if sid else None
                                is_mine = su == self_user or (not is_group and not sid)
                                if (mine == "me") != is_mine:
                                    continue
                            body = m["decompress_content"](content, ct)
                            if not body:
                                continue
                            texts.append(body)
                            scanned += 1
            except Exception:
                continue

        words = wc.keyword_cloud(texts, limit=limit)
        out = {
            "days": days,
            "range": _range_label(start_ts, end_ts),
            "exclude_official": bool(exclude_official),
            "category": category or "",
            "mine": mine,
            "messages": scanned,
            "chats": chats_used,
            "per_chat_cap": per_table,
            "truncated": truncated,
            "engine": "jieba" if wc._load_jieba() is not None else "bigram",
            "total": len(words),
            "words": words,
        }
        return _put(key, out)


def chat_stats(chat: str, days: int | None = 7) -> dict:
    """单会话统计：类型分布 / 发言排行 / 24 小时分布（复用 CLI 的聚合实现）。"""
    from wechat_cli.core.messages import collect_chat_stats, _iter_table_contexts

    app = _app_ctx()
    m = _load_mods()
    ctx = m["resolve_chat_context"](chat, app.msg_db_keys, app.cache, app.decrypted_dir)
    if not ctx:
        raise WechatError(f"找不到聊天对象: {chat}")
    if not ctx.get("db_path"):
        return {"chat": ctx["display_name"], "username": ctx["username"],
                "is_group": ctx["is_group"], "total": 0}
    names = m["get_contact_names"](app.cache, app.decrypted_dir)
    start_ts, end_ts = window(days)
    result = collect_chat_stats(ctx, names, app.display_name_fn, start_ts=start_ts, end_ts=end_ts)
    result.update(
        {
            "chat": ctx["display_name"],
            "username": ctx["username"],
            "is_group": ctx["is_group"],
            "days": days,
            "range": _range_label(start_ts, end_ts),
            "hourly": {str(h): result.get("hourly", {}).get(h, 0) for h in range(24)},
        }
    )
    return result

"""本地 SQLite 缓存：邮件信封、正文、附件落盘，供 WebUI 与 AI 快速读取。"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

from .settings import DB_FILE, ensure_dirs

SCHEMA = """
CREATE TABLE IF NOT EXISTS folders (
    name TEXT PRIMARY KEY,
    uidvalidity INTEGER DEFAULT 0,
    uidnext INTEGER DEFAULT 0,
    last_synced REAL DEFAULT 0,
    last_error TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS messages (
    folder TEXT NOT NULL,
    uid INTEGER NOT NULL,
    message_id TEXT DEFAULT '',
    subject TEXT DEFAULT '',
    from_name TEXT DEFAULT '',
    from_addr TEXT DEFAULT '',
    to_json TEXT DEFAULT '[]',
    cc_json TEXT DEFAULT '[]',
    date_iso TEXT DEFAULT '',
    date_ts REAL DEFAULT 0,
    flags TEXT DEFAULT '',
    size INTEGER DEFAULT 0,
    has_attach INTEGER DEFAULT 0,
    attach_count INTEGER DEFAULT 0,
    snippet TEXT DEFAULT '',
    in_reply_to TEXT DEFAULT '',
    has_body INTEGER DEFAULT 0,
    PRIMARY KEY (folder, uid)
);
CREATE INDEX IF NOT EXISTS idx_msg_date ON messages(date_ts DESC);
CREATE INDEX IF NOT EXISTS idx_msg_folder_date ON messages(folder, date_ts DESC);
CREATE INDEX IF NOT EXISTS idx_msg_from ON messages(from_addr);
CREATE TABLE IF NOT EXISTS bodies (
    folder TEXT NOT NULL,
    uid INTEGER NOT NULL,
    body_text TEXT DEFAULT '',
    body_html TEXT DEFAULT '',
    headers_json TEXT DEFAULT '{}',
    PRIMARY KEY (folder, uid)
);
CREATE TABLE IF NOT EXISTS attachments (
    folder TEXT NOT NULL,
    uid INTEGER NOT NULL,
    idx INTEGER NOT NULL,
    filename TEXT DEFAULT '',
    content_type TEXT DEFAULT '',
    size INTEGER DEFAULT 0,
    path TEXT DEFAULT '',
    is_inline INTEGER DEFAULT 0,
    content_id TEXT DEFAULT '',
    PRIMARY KEY (folder, uid, idx)
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT DEFAULT ''
);
"""


class Store:
    def __init__(self, db_path: Path | str = DB_FILE):
        ensure_dirs()
        self.db_path = str(db_path)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.executescript(SCHEMA)
        self.conn.commit()
        self._migrate()

    # ---------- 基础 ----------
    def _migrate(self) -> None:
        """给早期建好的表补列（CREATE TABLE IF NOT EXISTS 不会加列）。"""
        cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(attachments)")}
        if "content_id" not in cols:
            with self._lock:
                self.conn.execute("ALTER TABLE attachments ADD COLUMN content_id TEXT DEFAULT ''")
                self.conn.commit()

        mcols = {r["name"] for r in self.conn.execute("PRAGMA table_info(messages)")}
        added = False
        with self._lock:
            if "category" not in mcols:
                self.conn.execute("ALTER TABLE messages ADD COLUMN category TEXT DEFAULT ''")
                added = True
            if "is_boring" not in mcols:
                self.conn.execute("ALTER TABLE messages ADD COLUMN is_boring INTEGER DEFAULT 0")
                added = True
            if "thread_id" not in mcols:
                self.conn.execute("ALTER TABLE messages ADD COLUMN thread_id TEXT DEFAULT ''")
                added = True
                self.conn.execute("CREATE INDEX IF NOT EXISTS idx_msg_thread ON messages(thread_id)")
            if added:
                self.conn.commit()

    def _exec(self, sql: str, params=()):
        with self._lock:
            cur = self.conn.execute(sql, params)
            self.conn.commit()
            return cur

    def set_meta(self, key: str, value: str) -> None:
        self._exec("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

    def get_meta(self, key: str, default: str = "") -> str:
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    # ---------- 目录 ----------
    def upsert_folder(self, name: str, uidvalidity: int = 0, uidnext: int = 0) -> None:
        self._exec(
            "INSERT INTO folders(name,uidvalidity,uidnext,last_synced) VALUES(?,?,?,?) "
            "ON CONFLICT(name) DO UPDATE SET uidvalidity=excluded.uidvalidity, uidnext=excluded.uidnext, last_synced=excluded.last_synced",
            (name, uidvalidity, uidnext, time.time()),
        )

    def get_folder(self, name: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM folders WHERE name=?", (name,)).fetchone()
        return dict(row) if row else None

    def list_folders(self) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM folders ORDER BY name").fetchall()
        return [dict(r) for r in rows]

    def set_folder_error(self, name: str, err: str) -> None:
        self._exec("UPDATE folders SET last_error=?, last_synced=? WHERE name=?", (err, time.time(), name))

    def invalidate_folder(self, name: str) -> None:
        """UIDVALIDITY 变化时，旧 UID 全部失效。"""
        with self._lock:
            self.conn.execute("DELETE FROM messages WHERE folder=?", (name,))
            self.conn.execute("DELETE FROM bodies WHERE folder=?", (name,))
            self.conn.execute("DELETE FROM attachments WHERE folder=?", (name,))
            self.conn.commit()

    def folder_counts(self, name: str) -> dict:
        row = self.conn.execute(
            "SELECT COUNT(*) AS total, SUM(CASE WHEN flags NOT LIKE '%\\Seen%' THEN 1 ELSE 0 END) AS unread "
            "FROM messages WHERE folder=?",
            (name,),
        ).fetchone()
        return {"total": row["total"] or 0, "unread": row["unread"] or 0}

    # ---------- 写入 ----------
    def upsert_message(self, rec: dict) -> None:
        self._exec(
            """INSERT INTO messages(folder,uid,message_id,subject,from_name,from_addr,to_json,cc_json,
                   date_iso,date_ts,flags,size,has_attach,attach_count,snippet,in_reply_to,has_body)
               VALUES(:folder,:uid,:message_id,:subject,:from_name,:from_addr,:to_json,:cc_json,
                   :date_iso,:date_ts,:flags,:size,:has_attach,:attach_count,:snippet,:in_reply_to,:has_body)
               ON CONFLICT(folder,uid) DO UPDATE SET
                   message_id=excluded.message_id, subject=excluded.subject, from_name=excluded.from_name,
                   from_addr=excluded.from_addr, to_json=excluded.to_json, cc_json=excluded.cc_json,
                   date_iso=excluded.date_iso, date_ts=excluded.date_ts, flags=excluded.flags,
                   size=excluded.size, has_attach=excluded.has_attach, attach_count=excluded.attach_count,
                   snippet=excluded.snippet, in_reply_to=excluded.in_reply_to, has_body=excluded.has_body""",
            rec,
        )

    def upsert_body(self, folder: str, uid: int, text: str, html: str, headers: dict) -> None:
        self._exec(
            "INSERT INTO bodies(folder,uid,body_text,body_html,headers_json) VALUES(?,?,?,?,?) "
            "ON CONFLICT(folder,uid) DO UPDATE SET body_text=excluded.body_text, body_html=excluded.body_html, "
            "headers_json=excluded.headers_json",
            (folder, uid, text, html, json.dumps(headers, ensure_ascii=False)),
        )

    def clear_attachments(self, folder: str, uid: int) -> None:
        self._exec("DELETE FROM attachments WHERE folder=? AND uid=?", (folder, uid))

    def add_attachment(self, folder: str, uid: int, att: dict) -> None:
        self._exec(
            "INSERT INTO attachments(folder,uid,idx,filename,content_type,size,path,is_inline,content_id) "
            "VALUES(?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(folder,uid,idx) DO UPDATE SET filename=excluded.filename, content_type=excluded.content_type, "
            "size=excluded.size, path=excluded.path, is_inline=excluded.is_inline, content_id=excluded.content_id",
            (
                folder,
                uid,
                att["index"],
                att["filename"],
                att["content_type"],
                att["size"],
                att.get("path", ""),
                1 if att.get("is_inline") else 0,
                att.get("content_id") or "",
            ),
        )

    def max_uid(self, folder: str) -> int:
        row = self.conn.execute("SELECT MAX(uid) AS m FROM messages WHERE folder=?", (folder,)).fetchone()
        return int(row["m"] or 0)

    def known_uids(self, folder: str) -> set[int]:
        rows = self.conn.execute("SELECT uid FROM messages WHERE folder=?", (folder,)).fetchall()
        return {int(r["uid"]) for r in rows}

    def uids_without_body(self, folder: str, limit: int = 1000) -> list[int]:
        rows = self.conn.execute(
            "SELECT uid FROM messages WHERE folder=? AND has_body=0 ORDER BY uid DESC LIMIT ?", (folder, limit)
        ).fetchall()
        return [int(r["uid"]) for r in rows]

    # ---------- 查询条件 ----------
    def _msg_where(
        self,
        folders=None,
        since=None,
        until=None,
        q=None,
        unread_only=False,
        has_attachment=None,
        boring=None,
        category=None,
    ) -> tuple[str, list]:
        where, params = [], []
        if folders:
            where.append("m.folder IN (%s)" % ",".join("?" * len(folders)))
            params.extend(folders)
        if since:
            where.append("m.date_ts >= ?")
            params.append(self._ts(since))
        if until:
            where.append("m.date_ts < ?")
            params.append(self._ts(until))
        if unread_only:
            where.append(r"m.flags NOT LIKE '%\Seen%'")
        if has_attachment is True:
            where.append("m.has_attach = 1")
        elif has_attachment is False:
            where.append("m.has_attach = 0")
        if category:
            where.append("m.category = ?")
            params.append(category)
        if boring is True:
            where.append("m.is_boring = 1")
        elif boring is False:
            where.append("m.is_boring = 0")
        if q:
            like = f"%{q}%"
            where.append(
                "(m.subject LIKE ? OR m.from_name LIKE ? OR m.from_addr LIKE ? OR m.to_json LIKE ? "
                "OR m.snippet LIKE ? OR COALESCE(b.body_text,'') LIKE ?)"
            )
            params.extend([like] * 6)
        return ("WHERE " + " AND ".join(where)) if where else "", params

    # ---------- 读取 ----------
    def list_messages(
        self,
        folders: list[str] | None = None,
        since: str | None = None,
        until: str | None = None,
        q: str | None = None,
        unread_only: bool = False,
        has_attachment: bool | None = None,
        category: str | None = None,
        boring: bool | None = None,
        limit: int = 50,
        offset: int = 0,
        order: str = "desc",
    ) -> tuple[int, list[dict]]:
        wsql, params = self._msg_where(folders, since, until, q, unread_only, has_attachment, boring, category)
        ordsql = "DESC" if order.lower() != "asc" else "ASC"

        total = self.conn.execute(
            f"SELECT COUNT(*) AS c FROM messages m LEFT JOIN bodies b ON b.folder=m.folder AND b.uid=m.uid {wsql}",
            params,
        ).fetchone()["c"]

        rows = self.conn.execute(
            f"SELECT m.* FROM messages m LEFT JOIN bodies b ON b.folder=m.folder AND b.uid=m.uid {wsql} "
            f"ORDER BY m.date_ts {ordsql} LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
        return total, [self._row_to_msg(r) for r in rows]

    def get_message(self, folder: str, uid: int) -> dict | None:
        row = self.conn.execute("SELECT * FROM messages WHERE folder=? AND uid=?", (folder, uid)).fetchone()
        if not row:
            return None
        return self._row_to_msg(row)

    def get_body(self, folder: str, uid: int) -> dict:
        row = self.conn.execute("SELECT * FROM bodies WHERE folder=? AND uid=?", (folder, uid)).fetchone()
        if not row:
            return {"body_text": "", "body_html": "", "headers": {}}
        return {
            "body_text": row["body_text"] or "",
            "body_html": row["body_html"] or "",
            "headers": json.loads(row["headers_json"] or "{}"),
        }

    def get_attachments(self, folder: str, uid: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM attachments WHERE folder=? AND uid=? ORDER BY idx", (folder, uid)
        ).fetchall()
        return [
            {
                "index": r["idx"],
                "filename": r["filename"],
                "content_type": r["content_type"],
                "size": r["size"],
                "path": r["path"],
                "is_inline": bool(r["is_inline"]),
                "content_id": r["content_id"] or "",
            }
            for r in rows
        ]

    def set_flags(self, folder: str, uid: int, flags: list[str]) -> None:
        self._exec("UPDATE messages SET flags=? WHERE folder=? AND uid=?", (" ".join(flags), folder, uid))

    def stats(self) -> dict:
        row = self.conn.execute(
            "SELECT COUNT(*) AS total, MIN(date_iso) AS oldest, MAX(date_iso) AS newest FROM messages WHERE date_ts>0"
        ).fetchone()
        return {"total": row["total"] or 0, "oldest": row["oldest"], "newest": row["newest"]}

    def date_range(self, folder: str | None = None) -> dict:
        sql = "SELECT MIN(date_iso) AS oldest, MAX(date_iso) AS newest, COUNT(*) AS n FROM messages WHERE date_ts>0"
        params: list = []
        if folder:
            sql += " AND folder=?"
            params.append(folder)
        row = self.conn.execute(sql, params).fetchone()
        return {"count": row["n"] or 0, "oldest": row["oldest"], "newest": row["newest"]}

    # ---------- 分类 ----------
    def classify_all(self, force: bool = False) -> dict:
        """给邮件打上 category / is_boring（默认只处理还没分类的）。"""
        from . import classify as classify_mod

        sql = "SELECT folder,uid,subject,from_name,from_addr,snippet FROM messages"
        if not force:
            sql += " WHERE category='' OR category IS NULL"
        rows = self.conn.execute(sql).fetchall()
        counts: dict[str, int] = {}
        for r in rows:
            brow = self.conn.execute(
                "SELECT headers_json FROM bodies WHERE folder=? AND uid=?", (r["folder"], r["uid"])
            ).fetchone()
            headers = json.loads(brow["headers_json"] or "{}") if brow else {}
            rec = {
                "subject": r["subject"],
                "from_name": r["from_name"],
                "from_addr": r["from_addr"],
                "snippet": r["snippet"],
                "headers": headers,
                "attachments": self.get_attachments(r["folder"], r["uid"]),
            }
            cat, boring, _score, _reasons = classify_mod.classify(rec)
            counts[cat] = counts.get(cat, 0) + 1
            self._exec(
                "UPDATE messages SET category=?, is_boring=? WHERE folder=? AND uid=?",
                (cat, 1 if boring else 0, r["folder"], r["uid"]),
            )
        return {"scanned": len(rows), "counts": counts}

    def category_stats(self) -> dict:
        rows = self.conn.execute(
            "SELECT COALESCE(NULLIF(category,''),'personal') AS c, COUNT(*) AS n FROM messages GROUP BY c"
        ).fetchall()
        by = {r["c"]: r["n"] for r in rows}
        total = sum(by.values())
        boring = sum(v for k, v in by.items() if k != "personal")
        return {
            "total": total,
            "boring": boring,
            "personal": by.get("personal", 0),
            "boring_ratio": round(boring / total, 3) if total else 0.0,
            "by_category": by,
        }

    def thread_stats(self) -> dict:
        row = self.conn.execute(
            "SELECT COUNT(DISTINCT thread_id) AS t, COUNT(*) AS m FROM messages WHERE thread_id<>''"
        ).fetchone()
        grouped = self.conn.execute(
            "SELECT COUNT(*) AS c FROM (SELECT thread_id FROM messages WHERE thread_id<>'' "
            "GROUP BY thread_id HAVING COUNT(*)>1)"
        ).fetchone()["c"]
        return {"threads": row["t"] or 0, "messages": row["m"] or 0, "grouped": grouped or 0}

    # ---------- 会话 ----------
    def _thread_summary(self, tid: str, rows: list, own: set[str]) -> dict:
        from . import threads as threads_mod

        msgs = [self._row_to_msg(r) for r in rows]
        msgs.sort(key=lambda m: (m["date_ts"] or 0, m["uid"]))
        last = msgs[-1]
        first = msgs[0]

        seen: set[str] = set()
        participants: list[dict] = []
        for m in msgs:
            people = [m["from"]] + (m["to"] or [])
            for p in people:
                e = (p.get("email") or "").strip().lower()
                if not e or e in own or e in seen:
                    continue
                seen.add(e)
                participants.append({"name": p.get("name") or e.split("@")[0], "email": e})

        cats = [m["category"] for m in msgs if m["is_boring"]]
        category = max(set(cats), key=cats.count) if cats else "personal"

        return {
            "thread_id": tid,
            "subject": threads_mod.subject_title(last["subject"]),
            "message_count": len(msgs),
            "participants": participants[:6],
            "participant_count": len(participants),
            "unread": sum(1 for m in msgs if m["unread"]),
            "first_date": first["date"],
            "last_date": last["date"],
            "last_ts": last["date_ts"],
            "folders": sorted({m["folder"] for m in msgs}),
            "has_attachment": any(m["has_attachment"] for m in msgs),
            "attachment_count": sum(m["attachment_count"] or 0 for m in msgs),
            "category": category,
            "is_boring": bool(cats),
            "snippet": last["snippet"],
            "last_from": last["from"],
            "last_uid": last["uid"],
            "last_folder": last["folder"],
            "messages": msgs,
        }

    def list_threads(
        self,
        folders: list[str] | None = None,
        since: str | None = None,
        until: str | None = None,
        q: str | None = None,
        unread_only: bool = False,
        boring: bool | None = None,
        category: str | None = None,
        own_emails: set[str] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[int, list[dict]]:
        own = {a.strip().lower() for a in (own_emails or set()) if a}
        wsql, params = self._msg_where(folders, since, until, q, unread_only, None, boring, category)
        wsql = (wsql + " AND" if wsql else "WHERE") + " m.thread_id<>''"
        tids = {
            r[0]
            for r in self.conn.execute(
                "SELECT DISTINCT m.thread_id FROM messages m "
                "LEFT JOIN bodies b ON b.folder=m.folder AND b.uid=m.uid " + wsql,
                params,
            )
        }
        if not tids:
            return 0, []
        rows = self.conn.execute(
            "SELECT * FROM messages WHERE thread_id<>'' ORDER BY date_ts ASC, uid ASC"
        ).fetchall()
        groups: dict[str, list] = {}
        for r in rows:
            if r["thread_id"] in tids:
                groups.setdefault(r["thread_id"], []).append(r)
        summaries = [self._thread_summary(tid, msgs, own) for tid, msgs in groups.items()]
        summaries.sort(key=lambda t: t["last_ts"] or 0, reverse=True)
        return len(summaries), summaries[offset : offset + limit]

    def get_thread(self, thread_id: str, own_emails: set[str] | None = None) -> dict | None:
        own = {a.strip().lower() for a in (own_emails or set()) if a}
        rows = self.conn.execute(
            "SELECT * FROM messages WHERE thread_id=? ORDER BY date_ts ASC, uid ASC", (thread_id,)
        ).fetchall()
        if not rows:
            return None
        return self._thread_summary(thread_id, rows, own)

    # ---------- 工具 ----------
    @staticmethod
    def _ts(date_str: str) -> float:
        """接受 '2026-07-01' / ISO datetime / 时间戳字符串。"""
        import datetime as _dt

        s = date_str.strip()
        try:
            return float(s)
        except ValueError:
            pass
        try:
            if "T" in s:
                return _dt.datetime.fromisoformat(s).timestamp()
            return _dt.datetime.strptime(s, "%Y-%m-%d").timestamp()
        except ValueError:
            return 0.0

    @staticmethod
    def _row_to_msg(row: sqlite3.Row) -> dict:
        flags = (row["flags"] or "").split()
        return {
            "folder": row["folder"],
            "uid": row["uid"],
            "id": f"{row['folder']}:{row['uid']}",
            "message_id": row["message_id"],
            "subject": row["subject"],
            "from": {"name": row["from_name"], "email": row["from_addr"]},
            "to": json.loads(row["to_json"] or "[]"),
            "cc": json.loads(row["cc_json"] or "[]"),
            "date": row["date_iso"],
            "date_ts": row["date_ts"],
            "flags": flags,
            "unread": "\\Seen" not in flags,
            "size": row["size"],
            "has_attachment": bool(row["has_attach"]),
            "attachment_count": row["attach_count"],
            "snippet": row["snippet"],
            "has_body": bool(row["has_body"]),
            "category": row["category"] or "",
            "is_boring": bool(row["is_boring"]),
            "thread_id": row["thread_id"] or "",
        }

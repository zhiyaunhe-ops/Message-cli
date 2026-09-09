"""IMAP 客户端封装：连接、列目录、按 UID 抓取信封与全文。"""
from __future__ import annotations

import imaplib
import re
import ssl
from dataclasses import dataclass

from . import settings

# 大邮件（含附件）会超出 imaplib 默认 10KB 行缓冲
imaplib._MAXLINE = 20_000_000

MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()

_UID_RE = re.compile(rb"UID\s+(\d+)")
_FLAGS_RE = re.compile(rb"FLAGS\s+\(([^)]*)\)")
_DATE_RE = re.compile(rb'INTERNALDATE\s+"([^"]*)"')
_SIZE_RE = re.compile(rb"RFC822\.SIZE\s+(\d+)")


def quote_mailbox(name: str) -> str:
    """IMAP 目录名里有空格/特殊字符时需要加引号。"""
    if name.startswith('"') and name.endswith('"'):
        return name
    if re.search(r"[\s\"\\(){%*]", name):
        return '"%s"' % name.replace("\\", "\\\\").replace('"', '\\"')
    return name


def imap_since(date_str: str) -> str:
    """'2026-07-01' -> '01-Jul-2026'"""
    y, m, d = date_str.split("-")[:3]
    return f"{int(d):02d}-{MONTHS[int(m) - 1]}-{y}"


@dataclass
class Envelope:
    uid: int
    flags: list[str]
    internaldate: str
    size: int
    headers: bytes


class IMAPClient:
    def __init__(self, account: "settings.Account", timeout: int = 60):
        self.account = account
        self.timeout = timeout
        self._conn: imaplib.IMAP4 | None = None

    # ---------- 生命周期 ----------
    def connect(self) -> imaplib.IMAP4:
        if self._conn is not None:
            try:
                self._conn.noop()
                return self._conn
            except Exception:
                self._conn = None
        ctx = ssl.create_default_context()
        conn = (
            imaplib.IMAP4_SSL(self.account.imap_host, self.account.imap_port, ssl_context=ctx, timeout=self.timeout)
            if self.account.imap_ssl
            else imaplib.IMAP4(self.account.imap_host, self.account.imap_port, timeout=self.timeout)
        )
        # 该服务端要求分步 LOGIN（sasl-ir = false），imaplib.login 本身即分步/单步兼容
        conn.login(self.account.username, self.account.password)
        self._conn = conn
        return conn

    def close(self) -> None:
        try:
            if self._conn is not None:
                self._conn.logout()
        except Exception:
            pass
        self._conn = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *exc):
        self.close()

    # ---------- 目录 ----------
    def list_folders(self) -> list[dict]:
        conn = self.connect()
        typ, data = conn.list()
        folders = []
        for line in data or []:
            if line is None:
                continue
            raw = line.decode("utf-8", "replace") if isinstance(line, bytes) else str(line)
            m = re.match(r'^\((?P<flags>[^)]*)\)\s+"(?P<delim>[^"]*)"\s+(?P<name>.+)$', raw)
            if not m:
                continue
            name = m.group("name").strip()
            if name.startswith('"') and name.endswith('"'):
                name = name[1:-1]
            folders.append({"name": name, "flags": m.group("flags").split(), "delim": m.group("delim")})
        return folders

    def status(self, folder: str) -> dict:
        conn = self.connect()
        typ, data = conn.status(quote_mailbox(folder), "(MESSAGES UNSEEN UIDNEXT UIDVALIDITY)")
        out = {"messages": 0, "unseen": 0, "uidnext": 0, "uidvalidity": 0}
        if not data or not data[0]:
            return out
        raw = data[0].decode() if isinstance(data[0], bytes) else str(data[0])
        for key, cast in (("MESSAGES", int), ("UNSEEN", int), ("UIDNEXT", int), ("UIDVALIDITY", int)):
            m = re.search(key + r"\s+(\d+)", raw)
            if m:
                out[key.lower()] = cast(m.group(1))
        return out

    # ---------- 抓取 ----------
    def select(self, folder: str, readonly: bool = True) -> dict:
        conn = self.connect()
        typ, data = conn.select(quote_mailbox(folder), readonly=readonly)
        if typ != "OK":
            raise RuntimeError(f"无法打开邮箱 {folder}: {data}")
        raw = data[0].decode() if isinstance(data[0], bytes) else str(data[0])
        m = re.search(r"UIDVALIDITY\s+(\d+)", raw)
        return {"exists": int(raw.split()[0]), "uidvalidity": int(m.group(1)) if m else 0}

    def search_uids(self, criteria: list[str] | None = None) -> list[int]:
        conn = self.connect()
        typ, data = conn.uid("SEARCH", *(str(c) for c in (criteria or ["ALL"])))
        if typ != "OK" or not data or not data[0]:
            return []
        return [int(x) for x in data[0].split()]

    def fetch_envelopes(self, uids: list[int], chunk: int = 100) -> list[Envelope]:
        conn = self.connect()
        fields = (
            "(UID FLAGS INTERNALDATE RFC822.SIZE "
            "BODY.PEEK[HEADER.FIELDS (FROM TO CC SUBJECT DATE MESSAGE-ID IN-REPLY-TO REFERENCES)])"
        )
        out: list[Envelope] = []
        for i in range(0, len(uids), chunk):
            batch = uids[i : i + chunk]
            typ, data = conn.uid("FETCH", ",".join(str(u) for u in batch), fields)
            if typ != "OK":
                continue
            out.extend(self._parse_envelopes(data))
        return out

    def fetch_raw(self, uid: int) -> bytes | None:
        conn = self.connect()
        typ, data = conn.uid("FETCH", str(uid), "(RFC822)")
        if typ != "OK" or not data:
            return None
        for item in data:
            if isinstance(item, tuple) and len(item) == 2:
                return item[1]
        return None

    def fetch_raw_batch(self, uids: list[int]) -> dict[int, bytes]:
        """批量抓全文；单封失败时自动降级为逐封抓取。"""
        conn = self.connect()
        out: dict[int, bytes] = {}
        for i in range(0, len(uids), 20):
            batch = uids[i : i + 20]
            typ, data = conn.uid("FETCH", ",".join(str(u) for u in batch), "(UID RFC822)")
            if typ != "OK":
                for u in batch:
                    raw = self.fetch_raw(u)
                    if raw:
                        out[u] = raw
                continue
            for item in data:
                if not isinstance(item, tuple) or len(item) != 2:
                    continue
                meta, payload = item
                m = _UID_RE.search(meta)
                if m:
                    out[int(m.group(1))] = payload
        return out

    def set_flags(self, uid: int, add: list[str] | None = None, remove: list[str] | None = None, folder: str | None = None) -> list[str]:
        conn = self.connect()
        if folder:
            self.select(folder, readonly=False)
        if add:
            conn.uid("STORE", str(uid), "+FLAGS", "(%s)" % " ".join(add))
        if remove:
            conn.uid("STORE", str(uid), "-FLAGS", "(%s)" % " ".join(remove))
        typ, data = conn.uid("FETCH", str(uid), "(FLAGS)")
        if typ != "OK" or not data or not data[0]:
            return []
        raw = data[0].decode() if isinstance(data[0], bytes) else str(data[0])
        m = _FLAGS_RE.search(raw.encode())
        return m.group(1).decode().split() if m else []

    # ---------- 内部 ----------
    @staticmethod
    def _parse_envelopes(data) -> list[Envelope]:
        out: list[Envelope] = []
        for item in data:
            if not isinstance(item, tuple) or len(item) != 2:
                continue
            meta, payload = item
            if not isinstance(meta, bytes):
                continue
            m_uid = _UID_RE.search(meta)
            if not m_uid:
                continue
            m_flags = _FLAGS_RE.search(meta)
            m_date = _DATE_RE.search(meta)
            m_size = _SIZE_RE.search(meta)
            out.append(
                Envelope(
                    uid=int(m_uid.group(1)),
                    flags=m_flags.group(1).decode().split() if m_flags else [],
                    internaldate=m_date.group(1).decode() if m_date else "",
                    size=int(m_size.group(1)) if m_size else 0,
                    headers=payload or b"",
                )
            )
        return out

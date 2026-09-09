"""增量同步：信封优先，正文按时间窗按需抓取，附件落盘。"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from . import mailparse, settings
from .imap_client import IMAPClient, imap_since
from .store import Store

_UNSAFE = re.compile(r'[^\w\u4e00-\u9fff.\-()\[\]]+')


def safe_name(name: str, limit: int = 120) -> str:
    cleaned = _UNSAFE.sub("_", (name or "").strip()) or "unnamed"
    return cleaned[:limit]


def _folder_dir(folder: str) -> Path:
    return settings.ATTACH_DIR / safe_name(folder, 60)


def save_attachments(store: Store, folder: str, uid: int, attachments: list[dict]) -> None:
    if not attachments:
        return
    d = _folder_dir(folder) / str(uid)
    d.mkdir(parents=True, exist_ok=True)
    store.clear_attachments(folder, uid)
    for att in attachments:
        data = att.pop("_data", b"")
        fname = safe_name(att["filename"])
        path = d / f"{att['index']:02d}_{fname}"
        try:
            path.write_bytes(data)
            att["path"] = str(path)
        except Exception:
            att["path"] = ""
        store.add_attachment(folder, uid, att)


def sync_folder(
    client: IMAPClient,
    store: Store,
    folder: str,
    since: str | None = None,
    envelope_limit: int = 500,
    body_limit: int | None = None,
    force: bool = False,
    with_body: bool = True,
    progress=None,
) -> dict:
    """同步单个目录。

    since: 'YYYY-MM-DD'，只对该日期之后的邮件抓取完整正文与附件
    envelope_limit: 单目录单轮最多处理多少封（按 UID 倒序取最新的）
    force: 忽略本地已有记录，重新抓取
    """
    t0 = time.time()
    result = {"folder": folder, "new": 0, "updated": 0, "bodies": 0, "attachments": 0, "errors": [], "elapsed": 0.0}

    def tick(msg: str):
        if progress:
            try:
                progress(folder, msg)
            except Exception:
                pass

    try:
        st = client.status(folder)
    except Exception as e:
        result["errors"].append(f"status: {e}")
        return result

    existing = store.get_folder(folder)
    if existing and existing["uidvalidity"] and existing["uidvalidity"] != st["uidvalidity"]:
        store.invalidate_folder(folder)
        result["errors"].append("uidvalidity 变化，已重建本地索引")
    store.upsert_folder(folder, st["uidvalidity"], st["uidnext"])

    try:
        client.select(folder, readonly=True)
    except Exception as e:
        result["errors"].append(f"select: {e}")
        store.set_folder_error(folder, str(e))
        return result

    # 1) 收集候选 UID
    known = set() if force else store.known_uids(folder)
    max_uid = 0 if force else store.max_uid(folder)
    candidates: set[int] = set()
    if max_uid:
        candidates |= set(client.search_uids(["UID", f"{max_uid + 1}:*"]))
    if since:
        candidates |= set(client.search_uids(["SINCE", imap_since(since)]))
    if not candidates:
        candidates = set(client.search_uids(["ALL"]))

    if envelope_limit and len(candidates) > envelope_limit:
        candidates = set(sorted(candidates, reverse=True)[:envelope_limit])
    todo = sorted(candidates - known) if not force else sorted(candidates)
    result["errors"].append(f"候选 {len(candidates)} 封，待处理 {len(todo)} 封")

    if todo:
        tick(f"抓取 {len(todo)} 封信封")
        envelopes = client.fetch_envelopes(todo)
        for env in envelopes:
            parsed = mailparse.parse_raw(env.headers)
            rec = {
                "folder": folder,
                "uid": env.uid,
                "message_id": parsed["message_id"],
                "subject": parsed["subject"],
                "from_name": parsed["from_name"],
                "from_addr": parsed["from_addr"],
                "to_json": json.dumps(parsed["to"], ensure_ascii=False),
                "cc_json": json.dumps(parsed["cc"], ensure_ascii=False),
                "date_iso": parsed["date_iso"] or "",
                "date_ts": parsed["date_ts"] or 0,
                "flags": " ".join(env.flags),
                "size": env.size,
                "has_attach": 0,
                "attach_count": 0,
                "snippet": "",
                "in_reply_to": parsed["in_reply_to"] or "",
                "has_body": 0,
            }
            store.upsert_message(rec)
            result["new" if env.uid not in known else "updated"] += 1

    # 2) 正文/附件：限定时间窗内、且本地还没有正文的邮件
    if with_body:
        need = store.uids_without_body(folder, limit=body_limit or envelope_limit)
        if since:
            ts = Store._ts(since)
            rows = store.conn.execute(
                "SELECT uid FROM messages WHERE folder=? AND has_body=0 AND date_ts>=? ORDER BY uid DESC LIMIT ?",
                (folder, ts, body_limit or envelope_limit),
            ).fetchall()
            need = [int(r["uid"]) for r in rows]
        if need:
            tick(f"抓取 {len(need)} 封正文")
            raws = client.fetch_raw_batch(need)
            for uid, raw in raws.items():
                try:
                    parsed = mailparse.parse_raw(raw)
                except Exception as e:
                    result["errors"].append(f"parse uid={uid}: {e}")
                    continue
                msg = store.get_message(folder, uid) or {}
                store.upsert_message(
                    {
                        "folder": folder,
                        "uid": uid,
                        "message_id": parsed["message_id"],
                        "subject": parsed["subject"],
                        "from_name": parsed["from_name"],
                        "from_addr": parsed["from_addr"],
                        "to_json": json.dumps(parsed["to"], ensure_ascii=False),
                        "cc_json": json.dumps(parsed["cc"], ensure_ascii=False),
                        "date_iso": parsed["date_iso"] or msg.get("date") or "",
                        "date_ts": parsed["date_ts"] or msg.get("date_ts") or 0,
                        "flags": " ".join(msg.get("flags", [])),
                        "size": len(raw),
                        "has_attach": 1 if parsed["attachments"] else 0,
                        "attach_count": len(parsed["attachments"]),
                        "snippet": parsed["snippet"],
                        "in_reply_to": parsed["in_reply_to"] or "",
                        "has_body": 1,
                    }
                )
                store.upsert_body(
                    folder,
                    uid,
                    parsed["body_text"],
                    parsed["body_html"],
                    {
                        "subject": parsed["subject"],
                        "from": f'{parsed["from_name"]} <{parsed["from_addr"]}>',
                        "to": ", ".join(f'{t["name"]} <{t["email"]}>' for t in parsed["to"]),
                        "cc": ", ".join(f'{t["name"]} <{t["email"]}>' for t in parsed["cc"]),
                        "date": parsed["date_iso"] or "",
                        "message_id": parsed["message_id"],
                        "reply_to": parsed["reply_to"],
                    },
                )
                if parsed["attachments"]:
                    save_attachments(store, folder, uid, parsed["attachments"])
                    result["attachments"] += len(parsed["attachments"])
                result["bodies"] += 1
        else:
            tick("正文已是最新")

    store.upsert_folder(folder, st["uidvalidity"], st["uidnext"])
    result["elapsed"] = round(time.time() - t0, 2)
    return result


def sync_all(
    client: IMAPClient,
    store: Store,
    folders: list[str] | None = None,
    since: str | None = None,
    envelope_limit: int = 500,
    body_limit: int | None = None,
    force: bool = False,
    with_body: bool = True,
    progress=None,
) -> list[dict]:
    if not folders:
        folders = [f["name"] for f in client.list_folders()]
    out = []
    for f in folders:
        out.append(
            sync_folder(
                client,
                store,
                f,
                since=since,
                envelope_limit=envelope_limit,
                body_limit=body_limit,
                force=force,
                with_body=with_body,
                progress=progress,
            )
        )
    return out

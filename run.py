#!/usr/bin/env python
"""命令行入口。

    python run.py                          # 启动 WebUI + API（默认 127.0.0.1:8765）
    python run.py sync --since 2026-07-01  # 仅同步到索引
    python run.py verify --since 2026-07-01  # 校验时间窗内的邮件
    python run.py ai --since 2026-07-01 --limit 20   # 打印 AI 接口 JSON
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import sync as syncmod  # noqa: E402
from app.imap_client import IMAPClient  # noqa: E402
from app.settings import load_account  # noqa: E402
from app.store import Store  # noqa: E402

DEFAULT_SINCE = "2026-07-01"


def cmd_serve(args):
    import uvicorn

    uvicorn.run("app.main:app", host=args.host, port=args.port, reload=args.reload, log_level=args.log_level)


def cmd_sync(args):
    acc = load_account()
    client = IMAPClient(acc)
    store = Store()
    if args.all_folders or not args.folder:
        targets = [f["name"] for f in client.list_folders()]
    else:
        targets = args.folder

    def progress(folder, msg):
        print(f"  [{folder}] {msg}", flush=True)

    results = []
    for f in targets:
        print(f"同步 {f} …", flush=True)
        r = syncmod.sync_folder(
            client,
            store,
            f,
            since=args.since,
            envelope_limit=args.limit,
            body_limit=args.body_limit,
            force=args.force,
            with_body=not args.no_body,
            progress=progress,
        )
        results.append(r)
        print(
            f"  -> 新增 {r['new']} / 更新 {r['updated']} / 正文 {r['bodies']} / 附件 {r['attachments']} "
            f"（{r['elapsed']}s）",
            flush=True,
        )
        for e in r["errors"]:
            print(f"     · {e}", flush=True)
    client.close()
    print(json.dumps({"new": sum(r["new"] for r in results), "bodies": sum(r["bodies"] for r in results)}, ensure_ascii=False))


def cmd_verify(args):
    store = Store()
    folders = [f["name"] for f in store.list_folders()]
    rows = []
    total = 0
    with_body = 0
    for f in folders:
        _, items = store.list_messages(folders=[f], since=args.since, limit=100000)
        bodies = store.conn.execute(
            "SELECT COUNT(*) AS c FROM messages m JOIN bodies b ON b.folder=m.folder AND b.uid=m.uid "
            "WHERE m.folder=? AND m.date_ts>=?",
            (f, store._ts(args.since)),
        ).fetchone()["c"]
        atts = store.conn.execute(
            "SELECT COUNT(*) AS c FROM attachments a JOIN messages m ON m.folder=a.folder AND m.uid=a.uid "
            "WHERE m.folder=? AND m.date_ts>=?",
            (f, store._ts(args.since)),
        ).fetchone()["c"]
        dates = sorted(i["date"] for i in items if i["date"])
        if not items:
            continue
        rows.append(
            {
                "folder": f,
                "count": len(items),
                "with_body": bodies,
                "attachments": atts,
                "oldest": dates[0] if dates else None,
                "newest": dates[-1] if dates else None,
            }
        )
        total += len(items)
        with_body += bodies

    print(f"\n自 {args.since} 以来的邮件：共 {total} 封（含完整正文 {with_body} 封）\n")
    print(f"{'目录':<22}{'数量':>6}{'有正文':>8}{'附件':>6}   日期范围")
    print("-" * 86)
    for r in rows:
        span = f"{r['oldest'][:10]} → {r['newest'][:10]}" if r["oldest"] else "-"
        print(f"{r['folder']:<22}{r['count']:>6}{r['with_body']:>8}{r['attachments']:>6}   {span}")
    print("-" * 86)
    print(f"{'合计':<22}{total:>6}{with_body:>8}")
    if args.json:
        print()
        print(json.dumps(rows, ensure_ascii=False, indent=2))


def cmd_ai(args):
    """离线打印 AI 接口等价输出（不启动服务，直接调服务层）。"""
    from app.services import build_ai_inbox  # 延迟导入，避免 uvicorn 依赖

    data = build_ai_inbox(
        since=args.since,
        until=None,
        folder=["all"] if args.all_folders else (args.folder or None),
        q=args.q,
        unread_only=False,
        has_attachment=None,
        limit=args.limit,
        offset=0,
        body_chars=args.body_chars,
        include_html=False,
        include_inline=False,
        category=args.category,
        boring=args.boring,
        order="desc",
    )
    print(json.dumps(data, ensure_ascii=False, indent=2))


def cmd_wechat(args):
    """离线打印微信统计（不启动服务）。"""
    from app import wechat as wx

    st = wx.status()
    if not st["ok"]:
        print(f"微信数据不可用: {st.get('error')}")
        print("提示：微信需保持登录，然后运行 wechat-cli init")
        return 1

    if args.chat:
        data = wx.chat_history(args.chat, days=args.days or None, limit=args.limit)
    else:
        data = wx.overview(days=args.days or None)

    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0

    if args.chat:
        print(f"\n{data['chat']}（{data['range']}）共 {data['total']} 条\n")
        for it in data["items"]:
            who = f"{it['sender']}: " if it["sender"] else ""
            print(f"  [{it['time']}] {who}{it['text']}")
        return 0

    print(f"\n微信统计 · 最近 {args.days} 天（{data['range']}）")
    print(f"  消息总数   {data['total']}   （我发 {data['mine']} / 收到 {data['others']}）")
    print(f"  活跃会话   {data['chats']} 个（群 {data['group_chats']} / 单聊 {data['private_chats']}）")
    print(f"  群聊消息   {data['group_messages']}   单聊消息 {data['private_messages']}")
    print(f"  当前未读   {data['unread']}")
    print(f"  扫描范围   {data['scanned']['databases']} 个库 / {data['scanned']['tables']} 张消息表")

    print("\n  每日消息量:")
    peak = max(data["by_day"].values()) if data["by_day"] else 0
    for d, c in data["by_day"].items():
        print(f"    {d} |{'█' * int(c / peak * 34) if peak else ''} {c}")

    print("\n  消息类型:")
    for t in data["by_type"][:8]:
        print(f"    {t['type']:<10} {t['count']:>6}  {t['pct']}%")

    print("\n  活跃会话 Top 15:")
    for c in data["top_chats"][:15]:
        tag = "群" if c["is_group"] else "私"
        print(f"    [{tag}] {c['chat'][:34]:<34} {c['count']:>6}  {c['pct']}%")

    print("\n  24 小时分布:")
    peak_h = max(data["by_hour"].values()) if data["by_hour"] else 0
    for h in range(24):
        c = data["by_hour"].get(str(h), 0)
        print(f"    {h:02d}时 |{'█' * int(c / peak_h * 30) if peak_h else ''} {c}")
    return 0


def main():
    p = argparse.ArgumentParser(description="邮件 WebUI / API")
    sub = p.add_subparsers(dest="cmd")

    s = sub.add_parser("serve", help="启动服务")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--reload", action="store_true")
    s.add_argument("--log-level", default="info")
    s.set_defaults(func=cmd_serve)

    y = sub.add_parser("sync", help="同步邮件到本地索引")
    y.add_argument("--folder", action="append", help="可重复；不指定则同步全部目录")
    y.add_argument("--all-folders", action="store_true")
    y.add_argument("--since", default=DEFAULT_SINCE)
    y.add_argument("--limit", type=int, default=500)
    y.add_argument("--body-limit", type=int, default=None)
    y.add_argument("--force", action="store_true")
    y.add_argument("--no-body", action="store_true", help="只抓信封不抓正文")
    y.set_defaults(func=cmd_sync)

    v = sub.add_parser("verify", help="校验时间窗内的邮件")
    v.add_argument("--since", default=DEFAULT_SINCE)
    v.add_argument("--json", action="store_true")
    v.set_defaults(func=cmd_verify)

    a = sub.add_parser("ai", help="以 AI 接口格式输出邮件")
    a.add_argument("--since", default=DEFAULT_SINCE)
    a.add_argument("--folder", action="append")
    a.add_argument("--all-folders", action="store_true")
    a.add_argument("--q", default=None)
    a.add_argument("--limit", type=int, default=20)
    a.add_argument("--body-chars", type=int, default=4000)
    a.add_argument("--category", default=None,
                   choices=["personal", "meeting", "automated", "promotion"], help="只看某一类")
    a.add_argument("--boring", default=None, action="store_true", help="只看无聊邮件（会议/系统/推广）")
    a.set_defaults(func=cmd_ai)

    w = sub.add_parser("wechat", help="微信消息统计（复用 wechat-cli 解密内核）")
    w.add_argument("--days", type=int, default=7, help="最近 N 个自然日；0 表示全部时间")
    w.add_argument("--chat", default=None, help="指定会话名，输出该会话最近消息")
    w.add_argument("--limit", type=int, default=50)
    w.add_argument("--json", action="store_true")
    w.set_defaults(func=cmd_wechat)

    args = p.parse_args()
    if not getattr(args, "func", None):
        args = p.parse_args(["serve"])
    args.func(args)


if __name__ == "__main__":
    main()

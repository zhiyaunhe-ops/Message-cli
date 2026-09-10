"""统一运行上下文：一个服务，一套全局状态。

这个进程里跑着两个模块：
  · 邮件（IMAP/SMTP + 本地 SQLite 索引）
  · 微信（本机数据库统计，只读）

它们共享这里的资源，而不是各管各的模块级变量：
  account / store / imap 客户端   —— 惰性单例，线程安全
  syncing / last_sync / log       —— 同步进度与运行日志
  TTL 缓存                        —— 邮件派生数据与微信统计共用一套
  wx_app                          —— 微信解密上下文（由 wechat 模块填充）

用法：
    from .context import ctx, account, store, client, own_emails, is_mine
"""
from __future__ import annotations

import threading
import time
from typing import Any

from .imap_client import IMAPClient
from .settings import Account, load_account
from .store import Store

LOG_LIMIT = 300     # 运行日志最多保留多少条
CACHE_TTL = 300     # 默认缓存 5 分钟


class AppContext:
    """进程内共享状态（单例由模块级 ctx 提供）。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._account: Account | None = None
        self._store: Store | None = None

        # 同步进度
        self.syncing: bool = False
        self.last_sync: dict | None = None
        self.log: list[str] = []

        # TTL 缓存：key -> (过期时间戳, 值)
        self._cache: dict[str, tuple[float, Any]] = {}

        # 微信解密上下文（惰性创建，由 app/wechat.py 填充）
        self.wx_app: Any = None

    # ---------------------------------------------------------- 账号 / 索引
    @property
    def lock(self) -> threading.RLock:
        return self._lock

    def account(self) -> Account:
        with self._lock:
            if self._account is None:
                self._account = load_account()
            return self._account

    def store(self) -> Store:
        with self._lock:
            if self._store is None:
                self._store = Store()
            return self._store

    def client(self) -> IMAPClient:
        return IMAPClient(self.account())

    @property
    def own_emails(self) -> set[str]:
        """自己的地址（判断一封邮件是「我发的」还是「别人发的」）。"""
        email = (self.account().email or "").strip().lower()
        return {email} if email else set()

    def is_mine(self, msg: dict) -> bool:
        addr = ((msg.get("from") or {}).get("email") or "").strip().lower()
        if addr and addr in self.own_emails:
            return True
        return msg.get("folder") in ("Sent Items", "Drafts", "Sent", "已发送")

    # ---------------------------------------------------------- 运行日志
    def log_add(self, message: str) -> None:
        with self._lock:
            self.log.append(message)
            if len(self.log) > LOG_LIMIT:
                del self.log[: len(self.log) - LOG_LIMIT]

    def recent_log(self, n: int = 50) -> list[str]:
        with self._lock:
            return self.log[-n:]

    # ---------------------------------------------------------- TTL 缓存
    def cache_get(self, key: str) -> Any | None:
        item = self._cache.get(key)
        if item and item[0] > time.time():
            return item[1]
        return None

    def cache_put(self, key: str, value: Any, ttl: float = CACHE_TTL) -> Any:
        self._cache[key] = (time.time() + ttl, value)
        return value

    def cache_invalidate(self, prefix: str | None = None) -> int:
        """清缓存；给前缀则只清匹配的 key。返回清掉的条数。"""
        with self._lock:
            if prefix is None:
                n = len(self._cache)
                self._cache.clear()
                return n
            keys = [k for k in self._cache if k.startswith(prefix)]
            for k in keys:
                self._cache.pop(k, None)
            return len(keys)

    def cache_keys(self) -> list[str]:
        return sorted(self._cache)

    # ---------------------------------------------------------- 生命周期
    def shutdown(self) -> None:
        with self._lock:
            if self._store is not None:
                try:
                    self._store.conn.close()
                except Exception:
                    pass
                self._store = None
            self.wx_app = None
            self._cache.clear()


ctx = AppContext()

# ---- 便捷函数（模块级，保持调用点简洁） ----
def account() -> Account:
    return ctx.account()


def store() -> Store:
    return ctx.store()


def client() -> IMAPClient:
    return ctx.client()


def own_emails() -> set[str]:
    return ctx.own_emails


def is_mine(msg: dict) -> bool:
    return ctx.is_mine(msg)


def log_add(message: str) -> None:
    ctx.log_add(message)

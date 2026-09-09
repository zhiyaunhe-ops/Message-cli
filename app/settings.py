"""加载 himalaya 风格配置，解析出账号 / IMAP / 密码。

配置来源优先级：
    1. 环境变量 MAIL_PASSWORD
    2. config.toml 中 imap.sasl.login.password.command 指向的脚本（可执行时）
    3. 与 config.toml 同目录的 secret 文件
"""
from __future__ import annotations

import os
import subprocess
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config" / "himalaya"
CONFIG_FILE = CONFIG_DIR / "config.toml"
SECRET_FILE = CONFIG_DIR / "secret"
DATA_DIR = ROOT / "data"
ATTACH_DIR = DATA_DIR / "attachments"
DB_FILE = DATA_DIR / "mail.db"


def _dig(d: dict, *keys, default=None):
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k)
        if cur is None:
            return default
    return cur


@dataclass
class Account:
    name: str
    email: str
    display_name: str
    imap_host: str
    imap_port: int
    imap_ssl: bool
    username: str
    password: str
    smtp_host: str | None = None
    smtp_port: int | None = None
    smtp_ssl: bool = True
    smtp_starttls: bool = False
    aliases: dict[str, str] = field(default_factory=dict)

    @property
    def folders(self) -> list[str]:
        """需要同步的本地化文件夹列表（服务端原名）。"""
        order = ["inbox", "sent", "drafts", "archive", "junk", "trash"]
        out: list[str] = []
        for key in order:
            val = self.aliases.get(key)
            if val and val not in out:
                out.append(val)
        return out or ["INBOX"]


def _read_secret_from_command(cmd: list[str] | str) -> str | None:
    if not cmd:
        return None
    parts = cmd if isinstance(cmd, list) else [cmd]
    script = Path(parts[0])
    # 备份里是 Linux 路径（/root/.local/bin/...），Windows 上不可执行 -> 交给后面的 secret 兜底
    if not script.exists():
        return None
    try:
        if os.name == "nt" and script.suffix in {".sh", ""}:
            sh = _find_sh()
            if not sh:
                return None
            out = subprocess.run([sh, str(script)], capture_output=True, timeout=20)
        else:
            out = subprocess.run([str(script), *parts[1:]], capture_output=True, timeout=20)
        if out.returncode == 0:
            return out.stdout.decode("utf-8", "replace").strip()
    except Exception:
        return None
    return None


def _find_sh() -> str | None:
    for cand in (
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
        r"C:\Program Files (x86)\Git\bin\bash.exe",
    ):
        if Path(cand).exists():
            return cand
    return None


def _read_secret_file() -> str | None:
    for p in (SECRET_FILE, CONFIG_DIR / "secret.txt"):
        if p.exists():
            return p.read_text(encoding="utf-8", errors="replace").strip()
    return None


def load_account(name: str | None = None) -> Account:
    if not CONFIG_FILE.exists():
        raise FileNotFoundError(f"未找到配置文件: {CONFIG_FILE}")
    with CONFIG_FILE.open("rb") as fh:
        raw = tomllib.load(fh)

    accounts: dict = raw.get("accounts", {})
    if not accounts:
        raise ValueError("config.toml 中没有 [accounts.*] 配置")

    if name is None:
        for k, v in accounts.items():
            if isinstance(v, dict) and v.get("default"):
                name = k
                break
    if name is None:
        name = next(iter(accounts))
    acc_cfg = accounts[name]

    hostport = _dig(acc_cfg, "imap", "server", default="")
    host, _, port = hostport.partition(":")
    port_i = int(port or 993)
    ssl_on = bool(_dig(acc_cfg, "imap", "ssl", default=True))

    smtp_hostport = _dig(acc_cfg, "smtp", "server") or ""
    smtp_host = smtp_port = None
    if smtp_hostport:
        smtp_host, _, sp = smtp_hostport.partition(":")
        smtp_port = int(sp or 465)

    username = _dig(acc_cfg, "imap", "sasl", "login", "username") or acc_cfg.get("email", "")
    pw_cmd = _dig(acc_cfg, "imap", "sasl", "login", "password", "command")
    password = (
        os.environ.get("MAIL_PASSWORD")
        or _read_secret_from_command(pw_cmd)
        or _read_secret_file()
        or ""
    )
    if not password:
        raise RuntimeError("无法获取邮箱密码：请设置环境变量 MAIL_PASSWORD 或放置 config/himalaya/secret")

    aliases_raw = _dig(acc_cfg, "mailbox", "alias", default={}) or {}
    aliases = {str(k): str(v) for k, v in aliases_raw.items()}

    return Account(
        name=name,
        email=str(acc_cfg.get("email", "")),
        display_name=str(acc_cfg.get("display-name", "") or acc_cfg.get("email", "")),
        imap_host=host,
        imap_port=port_i,
        imap_ssl=ssl_on,
        username=str(username),
        password=password,
        smtp_host=smtp_host or None,
        smtp_port=smtp_port,
        smtp_ssl=bool(_dig(acc_cfg, "smtp", "ssl", default=True)),
        smtp_starttls=bool(_dig(acc_cfg, "smtp", "starttls", default=False)),
        aliases=aliases,
    )


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ATTACH_DIR.mkdir(parents=True, exist_ok=True)


if __name__ == "__main__":
    a = load_account()
    print(f"account={a.name} email={a.email} imap={a.imap_host}:{a.imap_port} ssl={a.imap_ssl}")
    print(f"folders={a.folders} password={'*' * len(a.password)}")

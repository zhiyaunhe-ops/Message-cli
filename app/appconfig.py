"""应用级共同配置（config/app.toml）—— 与邮箱账号无关、各模块共享的设置。

和 config/himalaya/config.toml（himalaya 风格的账号配置）分开存放：
那份是「连哪个邮箱」，这份是「这个服务怎么跑」，目前主要装 AI 相关设置。

读：tomllib（只读标准库） -> dict；写：一个只处理标量与子表的小序列化器，
够用且不引入 toml 依赖。带 key 的配置（api_key 等）返回时会打码。

环境变量优先级最高（CI / 临时调试用）：
    AI_BASE_URL / AI_API_KEY / AI_MODEL / AI_ENABLED / AI_TIMEOUT
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
APP_CONFIG_FILE = CONFIG_DIR / "app.toml"
APP_CONFIG_EXAMPLE = CONFIG_DIR / "app.example.toml"

DEFAULTS: dict = {
    "ai": {
        "enabled": True,
        "base_url": "https://api.openai.com/v1",
        "api_key": "",
        "model": "gpt-4o-mini",
        "temperature": 0.7,
        "timeout": 60,
        "recent_count": 10,     # 生成时参考每位收件人的最近几封邮件
        "context_chars": 1200,  # 单封邮件摘要截断长度
    }
}

SECRET_KEYS = {"api_key", "key", "token", "secret", "password"}


# ------------------------------------------------------------------ 读
def load_raw() -> dict:
    """读配置文件，缺字段用默认值补齐；文件不存在时给一份纯默认。"""
    data: dict = {}
    if APP_CONFIG_FILE.exists():
        try:
            with APP_CONFIG_FILE.open("rb") as fh:
                data = tomllib.load(fh)
        except Exception:
            data = {}
    return _merge(DEFAULTS, data)


def _merge(base: dict, over: dict) -> dict:
    """深一层拷贝地合并 —— 必须拷贝内层 dict，否则调用方改到的是 DEFAULTS 本身。"""
    out = {k: (dict(v) if isinstance(v, dict) else v) for k, v in base.items()}
    for k, v in (over or {}).items():
        if isinstance(v, dict):
            out[k] = _merge(out[k], v) if isinstance(out.get(k), dict) else dict(v)
        else:
            out[k] = v
    return out


@dataclass
class AIConfig:
    enabled: bool = True
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    temperature: float = 0.7
    timeout: int = 60
    recent_count: int = 10
    context_chars: int = 1200
    source: str = "default"  # default | file | env —— 诊断用

    @property
    def usable(self) -> bool:
        return bool(self.enabled and self.base_url and self.api_key and self.model)

    def public(self) -> dict:
        """给前端：不吐明文密钥，只说配没配。"""
        return {
            "enabled": self.enabled,
            "base_url": self.base_url,
            "model": self.model,
            "temperature": self.temperature,
            "timeout": self.timeout,
            "recent_count": self.recent_count,
            "context_chars": self.context_chars,
            "has_key": bool(self.api_key),
            "key_hint": _mask(self.api_key),
            "usable": self.usable,
            "source": self.source,
        }


def _mask(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 8:
        return key[:2] + "*" * max(0, len(key) - 2)
    return f"{key[:4]}…{key[-4:]}"


def ai_config() -> AIConfig:
    raw = load_raw().get("ai", {})
    cfg = AIConfig(
        enabled=bool(raw.get("enabled", True)),
        base_url=str(raw.get("base_url", "") or "").strip(),
        api_key=str(raw.get("api_key", "") or "").strip(),
        model=str(raw.get("model", "") or "").strip(),
        temperature=float(raw.get("temperature", 0.7)),
        timeout=int(raw.get("timeout", 60) or 60),
        recent_count=int(raw.get("recent_count", 10) or 10),
        context_chars=int(raw.get("context_chars", 1200) or 1200),
        source="file" if APP_CONFIG_FILE.exists() else "default",
    )
    # 环境变量兜底/覆盖
    if os.environ.get("AI_BASE_URL"):
        cfg.base_url = os.environ["AI_BASE_URL"].strip()
        cfg.source = "env"
    if os.environ.get("AI_API_KEY"):
        cfg.api_key = os.environ["AI_API_KEY"].strip()
        cfg.source = "env"
    if os.environ.get("AI_MODEL"):
        cfg.model = os.environ["AI_MODEL"].strip()
        cfg.source = "env"
    if os.environ.get("AI_ENABLED"):
        cfg.enabled = os.environ["AI_ENABLED"].strip().lower() in ("1", "true", "yes", "on")
    if os.environ.get("AI_TIMEOUT"):
        try:
            cfg.timeout = int(os.environ["AI_TIMEOUT"])
        except ValueError:
            pass
    return cfg


# ------------------------------------------------------------------ 写
def save_ai(patch: dict) -> AIConfig:
    """局部更新 [ai] 段并落盘；api_key 传空串表示「不改」。"""
    data = load_raw()
    ai = dict(data.get("ai", {}))
    allowed = {
        "enabled", "base_url", "api_key", "model",
        "temperature", "timeout", "recent_count", "context_chars",
    }
    for k, v in (patch or {}).items():
        if k not in allowed or v is None:
            continue
        if k == "api_key" and isinstance(v, str) and not v.strip():
            continue  # 空串 = 保留原值（前端不回填明文密钥）
        if k in ("enabled",):
            ai[k] = bool(v)
        elif k in ("temperature",):
            ai[k] = float(v)
        elif k in ("timeout", "recent_count", "context_chars"):
            ai[k] = int(v)
        else:
            ai[k] = str(v).strip()
    data["ai"] = ai
    write_raw(data)
    return ai_config()


def write_raw(data: dict) -> None:
    """把配置写回 config/app.toml（覆盖式，但内容来自 load_raw，未登记的段会保留）。"""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = APP_CONFIG_FILE.with_suffix(".toml.tmp")
    tmp.write_text(_dump_toml(data), encoding="utf-8")
    tmp.replace(APP_CONFIG_FILE)


def _fmt_val(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_fmt_val(x) for x in v) + "]"
    s = str(v).replace("\\", "\\\\").replace('"', '\\"')
    s = s.replace("\r", "").replace("\n", "\\n")
    return f'"{s}"'


def _dump_toml(data: dict) -> str:
    """极简 TOML 序列化：只支持标量 / 标量数组 / 子表，够写这份配置。"""
    lines: list[str] = []

    def walk(d: dict, prefix: str) -> None:
        for k, v in d.items():
            if isinstance(v, dict):
                continue
            lines.append(f"{k} = {_fmt_val(v)}")
        for k, v in d.items():
            if not isinstance(v, dict):
                continue
            name = f"{prefix}.{k}" if prefix else k
            lines.append("")
            lines.append(f"[{name}]")
            walk(v, name)

    walk(data, "")
    return "\n".join(lines).rstrip() + "\n"


def ensure_example() -> None:
    """首次使用生成一份示例配置（不含密钥），方便照着填。"""
    if APP_CONFIG_EXAMPLE.exists():
        return
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    example = _merge(DEFAULTS, {})
    example["ai"]["api_key"] = "sk-xxxxxx"
    APP_CONFIG_EXAMPLE.write_text(
        "# 应用级共同配置（复制为 app.toml 后生效；app.toml 已 gitignore，不会入库）\n"
        "# 也可以在 WebUI 右上角 ⚙ 设置里直接改，改完立即生效。\n\n"
        + _dump_toml(example),
        encoding="utf-8",
    )

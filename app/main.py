"""Message WebUI 服务（单进程双模块：邮件 + 微信）。

给人类：5 套美学主题的 WebUI（/），顶栏切换「✉ 信箱」与「💬 微信」
给 AI  ：结构化 JSON 接口（/api/...），说明见 /llms.txt 与 /api/ai/schema

本文件只做装配：
    app 实例 / CORS / 静态资源 / 首页 / llms.txt
    include_router(api_mail)      —— 邮件、会话、分类、AI 端点
    include_router(api_wechat)    —— 微信统计
    生命周期（启动补分类与会话归组、退出关闭连接）
共享状态统一在 app/context.py，两个模块不再各持全局变量。
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from .api_mail import router as mail_router
from .api_wechat import router as wechat_router
from .context import ctx
from .services import reindex
from .settings import ROOT

WEB_DIR = ROOT / "web"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动：把统一上下文挂到 app.state（供 Depends 注入），补分类与会话归组；
    退出：关闭 SQLite 连接、清缓存。"""
    app.state.ctx = ctx
    try:
        reindex(classify_all=False, rebuild=True)
    except Exception:
        pass
    yield
    ctx.close()


app = FastAPI(
    title="Message WebUI / Mail + WeChat API",
    version="1.1.0",
    description="一个本地服务：IMAP 邮箱的 Web 阅读器（含会话/分类/发信）与本机微信统计；所有 /api/* 返回 JSON。",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------- 路由装配
app.include_router(mail_router)
app.include_router(wechat_router)

# ---------------------------------------------------------------- 页面 / 静态
class NoCacheStaticFiles(StaticFiles):
    """静态资源要求浏览器每次用 ETag 协商（未变更返回 304），
    避免 Chromium 启发式缓存把旧版 app.js/index.html 多缓存几个小时不更新。"""

    def file_response(self, *args, **kwargs):
        resp = super().file_response(*args, **kwargs)
        resp.headers["Cache-Control"] = "no-cache"
        return resp


app.mount("/static", NoCacheStaticFiles(directory=str(WEB_DIR)), name="static")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(WEB_DIR / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/llms.txt", include_in_schema=False)
def llms_txt():
    return PlainTextResponse(
        (WEB_DIR / "llms.txt").read_text(encoding="utf-8"),
        media_type="text/plain; charset=utf-8",
    )

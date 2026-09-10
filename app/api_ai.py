"""AI 相关路由：配置读写 / 连通自检 / 写信正文生成。

配置落在 config/app.toml（应用级共同配置），也可以在 WebUI 右上角 ⚙ 里改。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from . import ai as aimod
from .appconfig import ai_config, ensure_example, save_ai

router = APIRouter(tags=["ai"])


class AIConfigPatch(BaseModel):
    enabled: bool | None = None
    base_url: str | None = None
    api_key: str | None = Field(default=None, description="留空表示不修改已有密钥")
    model: str | None = None
    temperature: float | None = None
    timeout: int | None = None
    recent_count: int | None = None
    context_chars: int | None = None


class ComposeRequest(BaseModel):
    to: str = ""
    cc: str = ""
    subject: str = ""
    body: str = ""
    recent: int | None = Field(default=None, ge=1, le=30, description="参考最近几封往来邮件，默认取配置值")


@router.get("/api/config/ai", summary="读取 AI 配置（密钥打码）")
def get_ai_config():
    ensure_example()
    return {"ok": True, "config": ai_config().public(), "file": "config/app.toml"}


@router.put("/api/config/ai", summary="写入 AI 配置（局部更新）")
def put_ai_config(patch: AIConfigPatch):
    try:
        cfg = save_ai(patch.model_dump(exclude_none=True))
    except Exception as e:
        raise HTTPException(500, f"保存配置失败：{e}")
    return {"ok": True, "config": cfg.public(), "file": "config/app.toml"}


@router.post("/api/config/ai/test", summary="测试 AI 接口连通性")
def test_ai_config():
    try:
        return aimod.ping()
    except aimod.AIError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"测试失败：{e}")


@router.post("/api/compose/ai", summary="AI 生成邮件正文：3 个场景方案供选择")
def compose_ai(req: ComposeRequest):
    if not (req.to.strip() or req.cc.strip() or req.subject.strip() or req.body.strip()):
        raise HTTPException(400, "请先填写收件人或写点内容，AI 才有东西可参考")
    try:
        return aimod.draft_suggestions(
            to=req.to, cc=req.cc, subject=req.subject, body=req.body, recent=req.recent,
        )
    except aimod.AIError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"生成失败：{e}")

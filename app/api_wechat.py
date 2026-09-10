"""微信模块路由：只读统计本机微信数据库，与邮件共用同一个 FastAPI 服务。

依赖缺失时（未装 wechat-cli / 未 init / 微信没开）返回 503，不影响邮件功能。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

router = APIRouter(tags=["wechat"])

# ------------------------------------------------------------------ 微信统计

def _wx():
    """导入微信模块（依赖缺失时给友好提示，不影响邮件功能）。"""
    try:
        from . import wechat as wx
    except Exception as e:  # 依赖缺失 / 源码树不在
        raise HTTPException(status_code=503, detail=f"微信模块不可用: {e}")
    return wx


@router.get("/api/wechat/status", summary="微信数据可用性自检")
def wechat_status():
    wx = _wx()
    try:
        return wx.status()
    except wx.WechatError as e:
        return JSONResponse(status_code=503, content={"ok": False, "error": str(e)})


@router.get("/api/wechat/overview", summary="微信全局统计：最近 N 天消息量 / 类型 / 活跃会话")
def wechat_overview(
    days: int | None = Query(default=7, ge=0, le=3650, description="最近 N 个自然日；0 表示全部时间"),
    refresh: bool = Query(default=False, description="忽略缓存重新扫描"),
):
    wx = _wx()
    try:
        return wx.overview(days=days or None, refresh=refresh)
    except wx.WechatError as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.get("/api/wechat/sessions", summary="微信最近会话列表")
def wechat_sessions(limit: int = Query(default=30, ge=1, le=200)):
    wx = _wx()
    try:
        return {"limit": limit, "items": wx.sessions(limit=limit)}
    except wx.WechatError as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.get("/api/wechat/history", summary="微信单个会话的最近消息")
def wechat_history(
    chat: str = Query(..., description="会话名 / 备注 / 微信号"),
    days: int | None = Query(default=7, ge=0, le=3650),
    limit: int = Query(default=50, ge=1, le=300),
):
    wx = _wx()
    try:
        return wx.chat_history(chat, days=days or None, limit=limit)
    except wx.WechatError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/api/wechat/stats", summary="微信单个会话统计（类型 / 发言排行 / 24 小时）")
def wechat_chat_stats(
    chat: str = Query(..., description="会话名 / 备注 / 微信号"),
    days: int | None = Query(default=7, ge=0, le=3650),
):
    wx = _wx()
    try:
        return wx.chat_stats(chat, days=days or None)
    except wx.WechatError as e:
        raise HTTPException(status_code=404, detail=str(e))

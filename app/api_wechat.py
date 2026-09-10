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


def _norm_cat(category: str | None) -> str | None:
    """校验分类参数：空 / all 视为不过滤，非法值直接 400。"""
    from . import wechat_classify as wc

    c = (category or "").strip()
    if not c or c == "all":
        return None
    if c not in wc.CATEGORY_LABELS:
        raise HTTPException(status_code=400, detail=f"未知分类: {c}")
    return c


@router.get("/api/wechat/status", summary="微信数据可用性自检")
def wechat_status():
    wx = _wx()
    try:
        return wx.status()
    except wx.WechatError as e:
        return JSONResponse(status_code=503, content={"ok": False, "error": str(e)})


@router.get("/api/wechat/overview", summary="微信全局统计：最近 N 天消息量 / 类型 / 分类 / 活跃会话")
def wechat_overview(
    days: int | None = Query(default=7, ge=0, le=3650, description="最近 N 个自然日；0 表示全部时间"),
    refresh: bool = Query(default=False, description="忽略缓存重新扫描"),
    exclude_official: bool = Query(default=False, description="过滤公众号 / 系统通知"),
):
    wx = _wx()
    try:
        return wx.overview(days=days or None, refresh=refresh,
                           exclude_official=exclude_official)
    except wx.WechatError as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.get("/api/wechat/sessions", summary="微信最近会话列表（带分类标签）")
def wechat_sessions(
    limit: int = Query(default=30, ge=1, le=200),
    exclude_official: bool = Query(default=False, description="过滤公众号 / 系统通知"),
):
    wx = _wx()
    try:
        return {"limit": limit,
                "items": wx.sessions(limit=limit, exclude_official=exclude_official)}
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


@router.get("/api/wechat/recent", summary="微信跨会话最新消息流")
def wechat_recent(
    days: int | None = Query(default=7, ge=0, le=3650),
    limit: int = Query(default=20, ge=1, le=200),
    exclude_official: bool = Query(default=False, description="过滤公众号 / 系统通知"),
    category: str | None = Query(default=None, description="只看某一类：project/colleague/group/wecom/private/official/system"),
):
    wx = _wx()
    try:
        return wx.recent(limit=limit, days=days or None,
                         exclude_official=exclude_official,
                         category=_norm_cat(category))
    except wx.WechatError as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.get("/api/wechat/categories", summary="会话分类字典（前端画筛选器用）")
def wechat_categories():
    from . import wechat_classify as wc

    return {
        "items": [
            {"category": c, "label": wc.CATEGORY_LABELS[c], "tag": wc.CATEGORY_TAGS[c],
             "non_human": c in wc.NON_HUMAN}
            for c in wc.CATEGORY_ORDER
        ],
        "rules": {
            "project": "群名以 # 开头 → 项目群，# 后面到「项目」为项目名",
            "colleague": "备注以 YY- / YYHK- 开头 → 同事（YYHK 记为用友香港）",
            "official": "username 以 gh_ 开头或 @app 结尾 → 公众号",
            "system": "微信内置账号（微信团队 / 文件传输助手 / 订阅号入口 …）",
            "wecom": "username 含 @openim → 企业微信联系人",
        },
    }


@router.get("/api/wechat/keywords", summary="微信关键词词云（时间窗内文本消息）")
def wechat_keywords(
    days: int | None = Query(default=7, ge=0, le=3650),
    limit: int = Query(default=80, ge=10, le=300),
    exclude_official: bool = Query(default=True, description="默认过滤公众号 / 系统通知"),
    category: str | None = Query(default=None, description="只看某一类会话"),
    mine: str = Query(default="all", pattern="^(all|me|others)$", description="all / me（我说的）/ others（别人说的）"),
):
    wx = _wx()
    try:
        return wx.keywords(days=days or None, limit=limit,
                           exclude_official=exclude_official,
                           category=_norm_cat(category), mine=mine)
    except wx.WechatError as e:
        raise HTTPException(status_code=503, detail=str(e))


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

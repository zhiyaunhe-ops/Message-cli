"""微信会话分类 + 关键词抽取的纯函数层（不碰数据库，方便单测）。

分类规则来自用户自己的命名习惯：
  - 项目群：群名以 `#` 开头，如 `#某项目 香港团队` → project=某项目
  - 同事：备注以 `YY-` / `YYHK-` 开头（大小写随意），如 `YY-中文名` / `YYHK- 英文名`
  - 公众号：username 以 `gh_` 开头，或 `@app` 结尾
  - 系统通知：微信自带账号（微信团队 / 文件传输助手 / 订阅号合并入口 …）
  - 企业微信：username 含 `@openim`
  - 其他群 / 个人好友：兜底

关键词抽取优先用 jieba（有就用），没装则退回「CJK 二元组 + ASCII 单词」的轻量方案。
"""
from __future__ import annotations

import re
from collections import Counter

# ---------------------------------------------------------------- 分类

CATEGORY_ORDER = ["project", "colleague", "group", "wecom", "private", "official", "system"]

CATEGORY_LABELS = {
    "project": "项目群",
    "colleague": "同事",
    "group": "其他群",
    "wecom": "企业微信",
    "private": "个人好友",
    "official": "公众号",
    "system": "系统通知",
}

# 行内短标签（列表里占一个字的位置）
CATEGORY_TAGS = {
    "project": "项",
    "colleague": "同",
    "group": "群",
    "wecom": "企",
    "private": "私",
    "official": "号",
    "system": "统",
}

# 非人类对话：公众号 + 系统通知（「隐藏公众号」开关过滤的就是这两类）
NON_HUMAN = ("official", "system")

# 微信内置账号（username 全小写比较）+ 下面的 SYSTEM_MARKS 做子串兜底
SYSTEM_MARKS = (
    "sessionholder",            # brandsessionholder / brandservicesessionholder（订阅号/服务号入口）
    "@placeholder_foldgroup",   # 「折叠的群聊」聚合入口
    "notification_messages",
)

SYSTEM_USERS = {
    "weixin", "filehelper", "fmessage", "medianote", "floatbottle", "qmessage",
    "tmessage", "qqmail", "qqsync", "newsapp", "notification_messages",
    "notifymessage", "brandsessionholder", "weixinreminder", "officialaccounts",
    "mphelper", "voip", "voicevoipnotify", "blogapp", "facebookapp",
    "masssendapp", "meishiapp", "feedsapp", "readerapp", "shakeapp", "lbsapp",
    "marionette", "weibo", "exmail_tool", "linkedinplugin", "pc_share",
}

# 同事前缀：YY / YYHK，中间可带地区（YY某地 - 某项目），分隔符可带空格
# 真实数据里出现过：YY-中文名 / Yy-中文名 / YYHK- 英文名 / YY - 中文名 / YY某地 - 某项目
_COLLEAGUE_RE = re.compile(
    r"^\s*yy\s*(hk|[\u4e00-\u9fff]{1,4})?\s*[-_\u2010-\u2015\uff0d]\s*",
    re.IGNORECASE,
)
ORG_LABELS = {"YY": "用友", "YYHK": "用友香港"}


def org_label(org: str) -> str:
    """YY → 用友、YYHK → 用友香港、YY某地 → 用友某地。"""
    if not org:
        return ""
    if org in ORG_LABELS:
        return ORG_LABELS[org]
    return "用友" + org[2:] if org.upper().startswith("YY") else org

_HASH_PREFIX = ("#", "＃")
_PROJECT_RE = re.compile(r"^(.{1,14}?项目)")
_PROJECT_SPLIT_RE = re.compile(r"[\s\-\u2010-\u2015_&+/|｜·、,，:：(（\[【]")


def project_of(name: str) -> str:
    """从 `#xxx…` 群名里抽项目名：优先切到「…项目」，否则取第一段。"""
    t = (name or "").lstrip("#＃").strip()
    if not t:
        return ""
    m = _PROJECT_RE.match(t)
    if m:
        return m.group(1)
    return (_PROJECT_SPLIT_RE.split(t, 1)[0] or t)[:16]


def classify(username: str, display_name: str = "", is_group: bool | None = None) -> dict:
    """返回 {category, label, tag, project, org, person}。纯字符串判断，无副作用。"""
    u = (username or "").strip()
    d = (display_name or "").strip()
    ul = u.lower()
    if is_group is None:
        is_group = "@chatroom" in ul

    cat, project, org, person = "private", "", "", d

    if ul in SYSTEM_USERS or any(k in ul for k in SYSTEM_MARKS):
        cat = "system"
    elif ul.startswith("gh_") or ul.endswith("@app"):
        cat = "official"
    elif is_group:
        if d.startswith(_HASH_PREFIX):
            cat, project = "project", project_of(d)
        else:
            cat = "group"
    else:
        m = _COLLEAGUE_RE.match(d)
        if m:
            cat = "colleague"
            region = (m.group(1) or "").strip()
            org = "YYHK" if region.lower() == "hk" else ("YY" + region if region else "YY")
            person = d[m.end():].strip() or d
        elif "@openim" in ul:
            cat = "wecom"

    return {
        "category": cat,
        "label": CATEGORY_LABELS[cat],
        "tag": CATEGORY_TAGS[cat],
        "project": project,
        "org": org,
        "org_label": org_label(org),
        "person": person,
    }


def is_non_human(username: str, display_name: str = "", is_group: bool | None = None) -> bool:
    return classify(username, display_name, is_group)["category"] in NON_HUMAN


def category_summary(counter, chat_counter=None) -> list[dict]:
    """把 {category: 消息数} 整理成固定顺序的列表（顺带带上会话数）。"""
    total = sum(counter.values()) or 0
    out = []
    for c in CATEGORY_ORDER:
        n = counter.get(c, 0)
        if not n and not (chat_counter or {}).get(c):
            continue
        out.append({
            "category": c,
            "label": CATEGORY_LABELS[c],
            "tag": CATEGORY_TAGS[c],
            "count": n,
            "chats": (chat_counter or {}).get(c, 0),
            "pct": round(n / total * 100, 1) if total else 0.0,
        })
    return out


# ---------------------------------------------------------------- 关键词

_URL_RE = re.compile(r"https?://\S+|www\.\S+")
_BRACKET_RE = re.compile(r"\[[^\]\[]{1,12}\]")          # [图片] [微笑] 之类
_XML_RE = re.compile(r"<[^>]{1,200}>")
_MENTION_RE = re.compile(r"@[^\s@]{1,20}")
_SENDER_PREFIX_RE = re.compile(r"^[^\n]{1,64}:\n")       # 群消息的 "wxid_xxx:\n"
_CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff]+")
_ASCII_RUN_RE = re.compile(r"[A-Za-z][A-Za-z0-9+#.]{1,19}")
_NUM_RE = re.compile(r"^[\d.:%,+\-]+$")
_DIGITY_RE = re.compile(r"\d{3,}")   # pm94164170 这类单号/编号，不是关键词

# 中文停用词（口语 / 虚词 / 聊天套话）
_CN_STOP = set("""
的 了 是 在 我 你 他 她 它 们 我们 你们 他们 咱们 自己 这 那 这个 那个 这些 那些 这样 那样
就 都 也 还 又 很 太 更 最 挺 比较 非常 真的 确实 其实 好像 应该 可能 大概 也许 差不多
有 没有 没 不 不是 不用 不要 别 可以 能 会 要 想 需要 一定 必须 应 得 让 使 把 被 给 对 从
和 与 跟 或 或者 但是 但 不过 然后 所以 因为 如果 虽然 而且 并且 以及 之类 等等 什么 怎么
怎样 为什么 哪里 哪个 多少 几个 一下 一点 一些 一个 一样 现在 今天 明天 昨天 上午 下午 晚上
时候 时间 已经 正在 马上 稍等 刚刚 刚才 之后 之前 目前 后面 前面 里面 上面 下面 左右 大家
老师 先生 女士 你好 您好 谢谢 感谢 麻烦 辛苦 收到 好的 可以的 没问题 请问 请 帮忙 看看
知道 觉得 感觉 认为 说 讲 问 答 回复 回 发 发送 收 到 去 来 走 做 干 弄 搞 用 出 上 下
啊 吧 呀 呢 吗 哦 嗯 哈 哈哈 呵呵 嘿 唉 哎 噢 咦 额 emmm
以上 以下 关于 针对 通过 根据 按照 由于 目前 另外 此外 同时 另 各 每 该 本 此 其 之 者 所
还是 就是 这么 那么 还有 只是 不能 不了 不对 不好 不会 不知 有点 有些 一起 一直 一定 一般
这边 那边 这里 那里 这次 那次 上次 下次 之类 什么样 怎么办 差不多 好不好 是不是 对不对
哈哈哈 哈哈哈哈 我说 你说 他说 我看 你看 咱们 各位 大概 顺便 反正 干嘛 而已 之类 多少
才能 能不能 要不要 有没有 是否 这个是 那个是 现在是 好呀 好呢 行吧 那就 就先 先看 看下
情况 问题 内容 方面 部分 相关 具体 直接 主要 一般 基本 整体 全部 所有 其他 别的 任何
图片 表情 语音 视频 位置 文件 链接 引用 拍了拍 撤回 消息 微信 转发 分享 名片 红包 转账
邀请 加入 群聊 通过 好友 请求 添加 已 未 正常 完成 处理 大佬 各位 同学 亲 呗 咯 嘛 喔
""".split())

_EN_STOP = set("""
the and for you your are was were with that this have has had not but can could would should
will just from all any our out about into over than then there their they them this these those
what when where which who how why get got let its it's don't doesn't didn't yes yeah okay ok
please thanks thank hi hello hey yep nope sure very more most some such only also here now
one two three four five six seven eight nine ten via etc app com http https www jpg png pdf
""".split())

_STOP = _CN_STOP | _EN_STOP

_jieba = None
_jieba_tried = False


def _load_jieba():
    global _jieba, _jieba_tried
    if _jieba_tried:
        return _jieba
    _jieba_tried = True
    try:
        import jieba  # type: ignore
        jieba.setLogLevel(60)
        for w in ("用友", "上线", "测试", "需求", "凭证", "总账", "应收", "应付", "费用报销",
                  "供应链", "主数据", "存货", "科目", "接口", "报表", "实施", "物流", "运维",
                  "顾问", "财务", "报表", "生产环境", "开发环境", "工单", "补丁", "验收"):
            try:
                jieba.add_word(w)
            except Exception:
                break
        _jieba = jieba
    except Exception:
        _jieba = None
    return _jieba


def clean_text(text: str) -> str:
    """去掉发送者前缀 / URL / xml / [表情] / @提及，只留能分词的自然语言。"""
    if not text:
        return ""
    t = _SENDER_PREFIX_RE.sub("", text, count=1)
    if t.lstrip().startswith("<"):
        t = _XML_RE.sub(" ", t)
    t = _URL_RE.sub(" ", t)
    t = _BRACKET_RE.sub(" ", t)
    t = _MENTION_RE.sub(" ", t)
    return t


def tokenize(text: str) -> list[str]:
    """分词 → 归一化 → 去停用词。jieba 可用时走 jieba，否则 CJK 二元组兜底。"""
    t = clean_text(text)
    if not t.strip():
        return []
    out: list[str] = []
    jb = _load_jieba()
    if jb is not None:
        for w in jb.cut(t):
            w = w.strip().lower()
            if not w or w in _STOP or _NUM_RE.match(w):
                continue
            if _CJK_RUN_RE.fullmatch(w):
                if len(w) < 2:
                    continue
            elif len(w) < 3 or not w[0].isalpha() or _DIGITY_RE.search(w):
                continue
            out.append(w)
        return out

    # 无 jieba：中文取二元组（相邻两字都不是停用字），英文取整词
    for run in _CJK_RUN_RE.findall(t):
        for i in range(len(run) - 1):
            bg = run[i:i + 2]
            if bg in _STOP or run[i] in _CN_STOP or run[i + 1] in _CN_STOP:
                continue
            out.append(bg)
    for w in _ASCII_RUN_RE.findall(t):
        w = w.lower()
        if len(w) >= 3 and w not in _STOP and not _DIGITY_RE.search(w):
            out.append(w)
    return out


def keyword_cloud(texts, limit: int = 80) -> list[dict]:
    """一批消息文本 → 词云条目。每条消息里同一个词只计一次，避免复读机刷榜。"""
    df: Counter = Counter()
    for t in texts:
        toks = set(tokenize(t))
        if toks:
            df.update(toks)
    if not df:
        return []
    common = df.most_common(limit)
    top = common[0][1] or 1
    floor = common[-1][1]
    span = max(1, top - floor)
    return [
        {
            "word": w,
            "count": c,
            # 1..10，用于前端字号；对数缩放让长尾不至于全是最小号
            "weight": 1 + round(9 * ((c - floor) / span) ** 0.6),
        }
        for w, c in common
    ]

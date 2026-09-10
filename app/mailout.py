"""SMTP 发信：构建 MIME -> 发送 -> 副本 APPEND 回 Sent Items。

纯标准库（smtplib / email.message），无第三方依赖。
密码复用 IMAP 的 Account.password；SMTP 地址来自 config.toml 的
smtp.server（未配置时按 settings 的兜底逻辑用 IMAP 同主机:465）。
"""
from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate, getaddresses, make_msgid

from .settings import Account


class MailOutError(Exception):
    pass


def parse_addresses(raw: str | list) -> list[tuple[str, str]]:
    """'a@x.com, Name <b@y.com>' 或 list -> [(name, addr)]。"""
    if not raw:
        return []
    if isinstance(raw, str):
        raw = [raw]
    return [(n or a, a) for n, a in getaddresses([str(x) for x in raw]) if a]


def _format_addrs(pairs: list[tuple[str, str]]) -> list[str]:
    out = []
    for name, addr in pairs:
        out.append(formataddr_safe(name, addr))
    return out


def formataddr_safe(name: str, addr: str) -> str:
    from email.header import Header
    from email.utils import formataddr

    try:
        name.encode("ascii")
        return formataddr((name, addr))
    except UnicodeEncodeError:
        return formataddr((str(Header(name, "utf-8")), addr))


def smtp_connect(acc: Account, timeout: int = 30) -> smtplib.SMTP:
    if not acc.smtp_host:
        raise MailOutError("未配置 SMTP 服务器（config.toml: smtp.server）")
    try:
        if acc.smtp_ssl:
            s = smtplib.SMTP_SSL(acc.smtp_host, acc.smtp_port or 465, timeout=timeout,
                                 context=ssl.create_default_context())
        else:
            s = smtplib.SMTP(acc.smtp_host, acc.smtp_port or 587, timeout=timeout)
            if acc.smtp_starttls:
                s.starttls(context=ssl.create_default_context())
        s.login(acc.username, acc.password)
        return s
    except smtplib.SMTPAuthenticationError as e:
        raise MailOutError(f"SMTP 登录被拒（{e.smtp_code} {e.smtp_error!r}）") from e
    except Exception as e:
        raise MailOutError(f"SMTP 连接失败 {acc.smtp_host}:{acc.smtp_port} -> {e}") from e


Attachment = tuple[str, str, bytes]  # (filename, content_type, data)


def build_message(
    acc: Account,
    to: str | list,
    subject: str,
    body_text: str,
    cc: str | list = "",
    body_html: str = "",
    attachments: list[Attachment] | None = None,
    in_reply_to: str = "",
    references: str = "",
    allow_empty_recipients: bool = False,
) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = formataddr_safe(acc.display_name or acc.email, acc.email)
    to_pairs = parse_addresses(to)
    cc_pairs = parse_addresses(cc)
    if not to_pairs and not allow_empty_recipients:
        raise MailOutError("收件人为空")
    msg["To"] = ", ".join(_format_addrs(to_pairs))
    if cc_pairs:
        msg["Cc"] = ", ".join(_format_addrs(cc_pairs))
    msg["Subject"] = subject or "(无主题)"
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=acc.email.split("@")[-1])
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    if references:
        msg["References"] = references
    msg.set_content(body_text or "")
    if body_html:
        msg.add_alternative(body_html, subtype="html")

    for filename, content_type, data in attachments or []:
        maintype, _, subtype = (content_type or "application/octet-stream").partition("/")
        msg.add_attachment(
            data,
            maintype=maintype or "application",
            subtype=subtype or "octet-stream",
            filename=filename or "attachment.bin",
        )
    return msg


def send_message(acc: Account, msg: EmailMessage, timeout: int = 30) -> dict:
    """发送并返回 {message_id, accepted}。"""
    all_addrs = getaddresses(msg.get_all("To", []) + msg.get_all("Cc", []))
    rcpts = [a for _, a in all_addrs if a]
    if not rcpts:
        raise MailOutError("没有有效收件人")
    s = smtp_connect(acc, timeout=timeout)
    try:
        refused = s.sendmail(acc.email, rcpts, msg.as_bytes())
    finally:
        try:
            s.quit()
        except Exception:
            pass
    mid = (msg.get("Message-ID") or "").strip()
    if refused:
        raise MailOutError(f"部分收件人被拒: {refused}")
    return {"message_id": mid, "accepted": rcpts}

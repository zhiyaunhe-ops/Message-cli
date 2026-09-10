/* Mail WebUI — 5 主题 + 三栏阅读器 */
(function () {
  "use strict";

  const THEMES = [
    { id: "ink", name: "宣纸水墨", desc: "宋体 × 朱砂 × 宣纸质地", sw: ["#f2ede1", "#a3302a", "#1f1c17"] },
    { id: "swiss", name: "瑞士极简", desc: "黑白网格 × 国际主义排版", sw: ["#ffffff", "#0b57ff", "#111111"] },
    { id: "neon", name: "暗夜霓虹", desc: "深空底 × 紫青辉光", sw: ["#111528", "#8b5cf6", "#22d3ee"] },
    { id: "terminal", name: "复古终端", desc: "CRT 绿字 × 扫描线", sw: ["#080c08", "#b6ff3a", "#4dff88"] },
    { id: "glass", name: "琉璃柔彩", desc: "毛玻璃 × 粉紫渐变", sw: ["#fdeef6", "#ff5f9e", "#7c6cf5"] },
  ];

  const state = {
    folder: "INBOX",
    q: "",
    since: "2026-07-01",
    unreadOnly: false,
    limit: 60,
    offset: 0,
    total: 0,
    items: [],
    current: null,
    folders: [],
    bodyMode: "rich",       // rich | text | source
    showExternal: false,    // 是否显示外链图片（防追踪，默认屏蔽）
    viewMode: "message",    // message | thread
    category: "all",        // all | personal | boring | meeting | automated | promotion
  };

  const $ = (sel) => document.querySelector(sel);
  const el = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  };

  /* ------------------------------ 工具 ------------------------------ */
  function fmtBytes(n) {
    if (!n) return "0 B";
    const u = ["B", "KB", "MB", "GB"];
    let i = 0, v = n;
    while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
    return (i === 0 ? v : v.toFixed(1)) + " " + u[i];
  }

  function parseDate(s) {
    if (!s) return null;
    const d = new Date(s);
    return isNaN(d.getTime()) ? null : d;
  }

  function fmtTime(s) {
    const d = parseDate(s);
    if (!d) return "";
    const now = new Date();
    const sameDay = d.toDateString() === now.toDateString();
    if (sameDay) return String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0");
    if (d.getFullYear() === now.getFullYear())
      return String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0");
    return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0");
  }

  function fmtFull(s) {
    const d = parseDate(s);
    if (!d) return s || "";
    const wd = "日一二三四五六"[d.getDay()];
    return `${d.getFullYear()} 年 ${d.getMonth() + 1} 月 ${d.getDate()} 日（周${wd}） ` +
      `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  }

  function groupLabel(s) {
    const d = parseDate(s);
    if (!d) return "未知日期";
    const now = new Date();
    if (d.toDateString() === now.toDateString()) return "今天";
    const y = new Date(now.getTime() - 86400000);
    if (d.toDateString() === y.toDateString()) return "昨天";
    if (d.getFullYear() === now.getFullYear()) return `${d.getMonth() + 1} 月 ${d.getDate()} 日`;
    return `${d.getFullYear()} 年 ${d.getMonth() + 1} 月`;
  }

  function esc(s) { return String(s == null ? "" : s); }

  const CAT_META = {
    meeting: { label: "会议", emoji: "📅", cls: "cat-meeting" },
    automated: { label: "系统", emoji: "🤖", cls: "cat-automated" },
    promotion: { label: "推广", emoji: "📢", cls: "cat-promotion" },
  };
  function catBadge(m) {
    const meta = CAT_META[m.category];
    if (!meta) return null;
    return el("span", "cat-badge " + meta.cls, `${meta.emoji} ${meta.label}`);
  }
  function initials(name, email) {
    const n = (name || email || "?").trim();
    return (n[0] || "?").toUpperCase();
  }

  let toastTimer = null;
  function toast(msg, ms) {
    const t = $("#toast");
    t.textContent = msg;
    t.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => t.classList.remove("show"), ms || 2600);
  }

  async function api(path, opts) {
    const res = await fetch(path, opts);
    if (!res.ok) {
      const txt = await res.text().catch(() => "");
      throw new Error(`HTTP ${res.status} ${txt.slice(0, 160)}`);
    }
    return res.json();
  }

  /* ------------------------------ 主题 ------------------------------ */
  function applyTheme(id) {
    const t = THEMES.find((x) => x.id === id) || THEMES[0];
    document.documentElement.setAttribute("data-theme", t.id);
    $("#themeName").textContent = t.name;
    try { localStorage.setItem("mail.theme", t.id); } catch (e) {}
    document.querySelectorAll(".theme-opt").forEach((n) =>
      n.classList.toggle("is-active", n.dataset.theme === t.id)
    );
  }

  function buildThemeMenu() {
    const menu = $("#themeMenu");
    menu.innerHTML = "";
    THEMES.forEach((t) => {
      const b = el("button", "theme-opt");
      b.dataset.theme = t.id;
      const sw = el("span", "theme-sw");
      t.sw.forEach((c) => { const i = el("i"); i.style.background = c; sw.appendChild(i); });
      const box = el("span");
      box.appendChild(el("div", "theme-opt-name", t.name));
      box.appendChild(el("div", "theme-opt-desc", t.desc));
      b.appendChild(sw);
      b.appendChild(box);
      b.appendChild(el("span", "theme-check", "✓"));
      b.addEventListener("click", () => { applyTheme(t.id); menu.classList.remove("open"); });
      menu.appendChild(b);
    });

    // CRT 扫描线开关（默认关：扫描线会盖在图片上，像蒙了一层条纹阴影）
    let crtOn = false;
    try { crtOn = localStorage.getItem("mail.crt") === "on"; } catch (e) {}
    document.documentElement.classList.toggle("crt-on", crtOn);
    const wrap = el("label", "theme-crt");
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.checked = crtOn;
    cb.addEventListener("change", () => {
      document.documentElement.classList.toggle("crt-on", cb.checked);
      try { localStorage.setItem("mail.crt", cb.checked ? "on" : "off"); } catch (e) {}
    });
    wrap.appendChild(cb);
    wrap.appendChild(el("span", null, "CRT 扫描线（复古终端）"));
    menu.appendChild(wrap);
  }

  /* ------------------------------ 目录 ------------------------------ */
  function folderLabel(name) {
    const map = { INBOX: "收件箱", "Sent Items": "已发送", Drafts: "草稿箱", Trash: "已删除", Archive: "归档", "Junk E-mail": "垃圾邮件", "Virus Items": "病毒箱", Scheduled: "定时发送" };
    return map[name] || name;
  }

  async function loadFolders() {
    try {
      const data = await api("/api/folders");
      state.folders = data.folders || [];
    } catch (e) {
      state.folders = [{ name: "INBOX", total: 0, unread: 0 }];
    }
    renderFolders();
  }

  function renderFolders() {
    const ul = $("#folders");
    ul.innerHTML = "";
    state.folders.forEach((f) => {
      const li = el("li", "folder-item" + (f.name === state.folder ? " is-active" : ""));
      li.appendChild(el("span", "folder-name", folderLabel(f.name)));
      li.appendChild(el("span", "folder-count", f.unread ? `${f.total} · ${f.unread} 未读` : String(f.total)));
      li.addEventListener("click", () => selectFolder(f.name));
      ul.appendChild(li);
    });
  }

  function selectFolder(name) {
    state.folder = name;
    state.offset = 0;
    state.current = null;
    $("#listTitle").textContent = folderLabel(name) + (state.viewMode === "thread" ? " · 会话" : "");
    renderFolders();
    $("#reader").innerHTML =
      state.viewMode === "thread" ? '<div class="empty">选择一条会话开始阅读</div>' : '<div class="empty">选择一封邮件开始阅读</div>';
    loadCurrent(false);
  }

  function loadCurrent(append) {
    return state.viewMode === "thread" ? loadThreads(append) : loadMessages(append);
  }

  /* ------------------------------ 列表 ------------------------------ */
  async function loadMessages(append) {
    const list = $("#messageList");
    if (!append) { list.innerHTML = '<div class="empty">加载中…</div>'; state.offset = 0; }

    const params = new URLSearchParams({
      folder: state.folder,
      since: state.since === "all" ? "" : state.since,
      limit: String(state.limit),
      offset: String(state.offset),
      order: "desc",
    });
    if (state.q) params.set("q", state.q);
    if (state.unreadOnly) params.set("unread_only", "true");
    if (state.category === "boring") params.set("boring", "true");
    else if (state.category === "personal") params.set("boring", "false");
    else if (state.category !== "all") params.set("category", state.category);
    if (state.since === "all") params.delete("since");

    let data;
    try {
      data = await api("/api/messages?" + params.toString());
    } catch (e) {
      list.innerHTML = `<div class="empty">加载失败：${e.message}</div>`;
      return;
    }
    state.total = data.total;
    state.items = append ? state.items.concat(data.items) : data.items;

    list.innerHTML = "";
    if (!state.items.length) {
      list.innerHTML = '<div class="empty">没有匹配的邮件</div>';
      $("#listCount").textContent = "0 封";
      $("#moreBtn").style.display = "none";
      return;
    }
    let lastGroup = "";
    state.items.forEach((m) => {
      const g = groupLabel(m.date);
      if (g !== lastGroup) { list.appendChild(el("div", "date-sep", g)); lastGroup = g; }

      const row = el("div", "msg-row" + (m.unread ? " is-unread" : "") +
        (m.is_boring ? " is-boring" : "") +
        (state.current && state.current.uid === m.uid && state.current.folder === m.folder ? " is-selected" : ""));
      row.dataset.uid = m.uid;
      row.dataset.folder = m.folder;

      const line = el("div", "msg-line");
      line.appendChild(el("span", "msg-from", m.from.name || m.from.email));
      const badge = catBadge(m);
      if (badge) line.appendChild(badge);
      line.appendChild(el("span", "msg-time", fmtTime(m.date)));
      row.appendChild(line);
      row.appendChild(el("div", "msg-subject", m.subject || "(无主题)"));
      row.appendChild(el("div", "msg-snippet", m.snippet || ""));

      const tags = el("div", "msg-tags");
      if (m.unread) tags.appendChild(el("span", "badge-unread"));
      if (m.has_attachment) tags.appendChild(el("span", "tag attach", `📎 ${m.attachment_count}`));
      if (m.folder !== state.folder) tags.appendChild(el("span", "tag", folderLabel(m.folder)));
      if (tags.children.length) row.appendChild(tags);

      row.addEventListener("click", () => openMessage(m.folder, m.uid, row));
      list.appendChild(row);
    });

    $("#listCount").textContent = `${state.total} 封`;
    $("#moreBtn").style.display = state.items.length < state.total ? "" : "none";
    $("#moreBtn").textContent = `加载更多（还有 ${state.total - state.items.length} 封）`;
  }

  /* ------------------------------ 阅读 ------------------------------ */
  async function openMessage(folder, uid, row) {
    document.querySelectorAll(".msg-row").forEach((n) => n.classList.remove("is-selected"));
    if (row) row.classList.add("is-selected");

    const reader = $("#reader");
    reader.innerHTML = '<div class="empty">加载中…</div>';
    try {
      const m = await api(`/api/messages/${uid}?folder=${encodeURIComponent(folder)}&include_body=true&body_format=both`);
      state.current = m;
      state.showExternal = false;
      renderReader(m);
      if (m.unread) {
        fetch(`/api/messages/${uid}/flags`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ folder: folder, add: ["\\Seen"] }),
        }).then(() => {
          if (row) row.classList.remove("is-unread");
          loadFolders();
        }).catch(() => {});
      }
    } catch (e) {
      reader.innerHTML = `<div class="empty">读取失败：${e.message}</div>`;
    }
  }

  /* ------------------------------ 正文渲染：富文本 ------------------------------ */
  // 直接丢掉的标签（脚本 / 样式 / 外嵌对象 / 表单）
  const DROP_TAGS = [
    "SCRIPT", "STYLE", "NOSCRIPT", "IFRAME", "OBJECT", "EMBED", "LINK", "META", "BASE",
    "FORM", "INPUT", "BUTTON", "SELECT", "TEXTAREA", "SVG", "MATH", "APPLET",
    "FRAME", "FRAMESET", "AUDIO", "VIDEO", "SOURCE", "TRACK", "CANVAS", "MAP", "AREA", "TITLE",
  ];

  // 保留内容、只剥掉标签的白名单（邮件排版主要靠 table / 内联样式）
  const KEEP_TAGS = new Set([
    "A", "ABBR", "ADDRESS", "ARTICLE", "ASIDE", "B", "BIG", "BLOCKQUOTE", "BR", "CAPTION", "CENTER",
    "CODE", "COL", "COLGROUP", "DD", "DEL", "DETAILS", "DIV", "DL", "DT", "EM", "FIGURE", "FIGCAPTION",
    "FONT", "FOOTER", "H1", "H2", "H3", "H4", "H5", "H6", "HEADER", "HR", "I", "IMG", "INS", "KBD",
    "LABEL", "LI", "MAIN", "MARK", "NAV", "OL", "P", "PRE", "Q", "S", "SAMP", "SECTION", "SMALL",
    "SPAN", "STRIKE", "STRONG", "SUB", "SUMMARY", "SUP", "TABLE", "TBODY", "TD", "TFOOT", "TH",
    "THEAD", "TIME", "TR", "TT", "U", "UL", "VAR",
  ]);

  const ATTR_ALL = new Set([
    "style", "class", "id", "title", "lang", "dir", "role",
    "align", "valign", "colspan", "rowspan", "nowrap", "width", "height",
    "bgcolor", "border", "cellpadding", "cellspacing", "size", "color", "face",
    "cite", "datetime", "start", "type", "scope", "summary", "span",
  ]);
  const ATTR_BY_TAG = {
    A: new Set(["href", "target", "rel", "name"]),
    IMG: new Set(["src", "alt", "width", "height", "border", "align", "hspace", "vspace", "loading"]),
  };

  function sanitizeStyle(v) {
    let s = String(v || "");
    s = s.replace(/expression\s*\(/gi, "");
    s = s.replace(/javascript\s*:/gi, "");
    s = s.replace(/vbscript\s*:/gi, "");
    s = s.replace(/-moz-binding\s*:/gi, "");
    // 远程背景图属于追踪像素，直接去掉
    s = s.replace(/url\s*\(\s*['"]?\s*(?:https?:)?\/\/[^)]*\)/gi, "");
    // 防止邮件样式盖住整个界面
    s = s.replace(/position\s*:\s*(fixed|absolute)\s*;?/gi, "");
    s = s.replace(/z-index\s*:\s*[^;]*;?/gi, "");
    return s;
  }

  function sanitizeHtml(html) {
    const doc = new DOMParser().parseFromString(String(html || ""), "text/html");
    doc.querySelectorAll(DROP_TAGS.join(",")).forEach((n) => n.remove());

    const walk = (parent) => {
      Array.from(parent.children).forEach((child) => {
        const tag = child.tagName;
        if (!KEEP_TAGS.has(tag)) {
          // 不在白名单：保留文字内容，剥掉标签本身
          const frag = doc.createDocumentFragment();
          while (child.firstChild) frag.appendChild(child.firstChild);
          child.replaceWith(frag);
          return;
        }
        Array.from(child.attributes).forEach((a) => {
          const n = a.name.toLowerCase();
          if (n.indexOf("on") === 0) { child.removeAttribute(a.name); return; }
          const ok = (ATTR_BY_TAG[tag] && ATTR_BY_TAG[tag].has(n)) || ATTR_ALL.has(n);
          if (!ok) { child.removeAttribute(a.name); return; }
          if (n === "style") child.setAttribute("style", sanitizeStyle(a.value));
          if ((n === "href" || n === "src") && /^\s*(javascript|vbscript|data:text\/html)\s*:/i.test(a.value)) {
            child.removeAttribute(a.name);
          }
        });
        if (tag === "A") {
          child.setAttribute("target", "_blank");
          child.setAttribute("rel", "noopener noreferrer");
        }
        walk(child);
      });
    };
    walk(doc.body);
    return doc.body.innerHTML;
  }

  function cidMap(m) {
    const map = {};
    (m.inline_images || []).forEach((a) => {
      const cid = String(a.content_id || "").replace(/[<>]/g, "").toLowerCase();
      if (cid) {
        map[cid] = a;
        map[cid.split("@")[0]] = a;
        map[cid.split("@")[0].split("$")[0]] = a;
      }
      if (a.filename) map[String(a.filename).toLowerCase()] = a;
    });
    return map;
  }

  /** 纯文本正文 -> HTML：把 [cid:xxx] 占位还原成真实图片 */
  function textToHtml(text, m) {
    const map = cidMap(m);
    let s = String(text || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
    s = s.replace(/\[\s*cid:([^\]\s]+)\s*\]/gi, (full, cid) => {
      const a = map[cid.toLowerCase()] || map[cid.split("@")[0].toLowerCase()];
      if (!a) return "";
      return `<img class="inline-img" src="${a.inline_url}" alt="${a.filename}">`;
    });
    s = s.replace(/(https?:\/\/[^\s<>"']+)/gi, '<a href="$1" target="_blank" rel="noopener noreferrer">$1</a>');
    return s.replace(/\n/g, "<br>");
  }

  /** 外链图片默认屏蔽（防追踪），未匹配的 cid 也占位，可加载的本地 cid 直接显示 */
  function applyImagePolicy(root) {
    let blocked = 0;
    root.querySelectorAll("img").forEach((img) => {
      const src = img.getAttribute("src") || "";
      if (/^https?:/i.test(src)) {
        blocked++;
        if (state.showExternal) {
          img.setAttribute("referrerpolicy", "no-referrer");
        } else {
          const ph = el("span", "ext-img");
          ph.dataset.src = src;
          ph.title = "点击加载这张外部图片";
          ph.addEventListener("click", () => {
            const img = new Image();
            img.src = src;
            img.referrerPolicy = "no-referrer";
            img.className = "inline-img";
            img.loading = "lazy";
            ph.replaceWith(img);
          });
          img.replaceWith(ph);
        }
      } else if (/^cid:/i.test(src)) {
        // 后端没找到匹配的 cid（极少见：邮件引用了不在 multipart 里的图）
        const ph = el("span", "ext-img");
        ph.textContent = "🖼 引用了未随邮件发送的图片";
        img.replaceWith(ph);
      } else {
        img.classList.add("inline-img");
        img.setAttribute("loading", "lazy");
      }
    });
    return blocked;
  }

  function renderReader(m) {
    const reader = $("#reader");
    reader.innerHTML = "";

    const hasHtml = !!(m.body_html && m.body_html.trim());
    const hasText = !!(m.body_text && m.body_text.trim());
    let mode = state.bodyMode;
    if (mode === "source" && !hasHtml) mode = "rich";

    const bar = el("div", "reader-bar");
    const seg = el("div", "seg");
    [
      ["rich", "富文本", true],
      ["text", "纯文本", hasText],
      ["source", "源码", hasHtml],
    ].forEach(([id, label, enabled]) => {
      const b = el("button", mode === id ? "active" : "", label);
      b.disabled = !enabled;
      if (!enabled) b.style.opacity = "0.35";
      b.onclick = () => { state.bodyMode = id; renderReader(m); };
      seg.appendChild(b);
    });
    bar.appendChild(seg);

    const extBtn = el("button", "btn", "");
    extBtn.style.display = "none";
    extBtn.onclick = () => { state.showExternal = !state.showExternal; renderReader(m); };
    bar.appendChild(extBtn);

    const replyBtn = el("button", "btn", "↩ 回复");
    replyBtn.onclick = () => openCompose({
      to: replyTargetOf(m),
      subject: /^re:/i.test(m.subject || "") ? m.subject : "Re: " + (m.subject || ""),
      reply: { folder: m.folder, uid: m.uid },
    });
    bar.appendChild(replyBtn);

    const delBtn = el("button", "btn btn-danger", "🗑 删除");
    delBtn.onclick = () => deleteMessage(m.folder, m.uid);
    bar.appendChild(delBtn);

    const unreadBtn = el("button", "btn", m.unread ? "标记已读" : "标记未读");
    unreadBtn.onclick = () => toggleUnread(m, unreadBtn);
    bar.appendChild(unreadBtn);
    bar.appendChild(el("div", "spacer"));
    if (m.inline_count) bar.appendChild(el("span", "attach-size", `内嵌 ${m.inline_count} 张`));
    bar.appendChild(el("span", "attach-size", `UID ${m.uid} · ${fmtBytes(m.size)}`));
    reader.appendChild(bar);

    const inner = el("div", "reader-inner");
    inner.appendChild(el("h2", "reader-subject", m.subject || "(无主题)"));

    const meta = el("div", "reader-meta");
    const addRow = (label, value) => {
      if (!value) return;
      const r = el("div", "meta-row");
      r.appendChild(el("span", "meta-label", label));
      r.appendChild(el("span", "meta-value", value));
      meta.appendChild(r);
    };
    addRow("发件人", `${m.from.name || ""} <${m.from.email}>`);
    if (m.to && m.to.length) addRow("收件人", m.to.map((t) => `${t.name || ""} <${t.email}>`).join("、"));
    if (m.cc && m.cc.length) addRow("抄送", m.cc.map((t) => `${t.name || ""} <${t.email}>`).join("、"));
    addRow("时间", fmtFull(m.date));
    addRow("目录", folderLabel(m.folder));
    inner.appendChild(meta);

    // 只列真实附件；内嵌图片已经渲染在正文里，不再重复列出
    if (m.attachments && m.attachments.length) {
      const box = el("div", "attach-list");
      m.attachments.forEach((a) => {
        const chip = el("a", "attach-chip");
        chip.href = a.url;
        chip.target = "_blank";
        chip.rel = "noopener";
        chip.appendChild(el("span", null, `📎 ${a.filename}`));
        chip.appendChild(el("span", "attach-size", fmtBytes(a.size)));
        box.appendChild(chip);
      });
      inner.appendChild(box);
    } else if (m.inline_count) {
      inner.appendChild(el("div", "attach-note", `${m.inline_count} 张内嵌图片已显示在正文中`));
    }

    const body = el("div", "reader-body");
    if (mode === "rich") body.classList.add("rich");
    if (mode === "source") {
      const f = document.createElement("iframe");
      f.className = "body-html-frame";
      f.setAttribute("sandbox", "");
      f.srcdoc = m.body_html;
      body.appendChild(f);
    } else if (mode === "text") {
      const pre = el("pre", "body-text");
      pre.textContent = (m.body_text || "").trim() || "（此邮件没有纯文本正文）";
      body.appendChild(pre);
    } else if (hasHtml) {
      body.innerHTML = sanitizeHtml(m.body_html);
    } else {
      body.innerHTML = textToHtml(m.body_text, m);
    }

    if (mode === "rich") {
      const blocked = applyImagePolicy(body);
      if (blocked) {
        extBtn.style.display = "";
        extBtn.textContent = state.showExternal ? "隐藏外部图片" : `显示 ${blocked} 张外部图片`;
      }
    }

    inner.appendChild(body);
    reader.appendChild(inner);
    inner.scrollTop = 0;
  }

  async function toggleUnread(m, btn) {
    try {
      const r = await api(`/api/messages/${m.uid}/flags`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ folder: m.folder, add: m.unread ? ["\\Seen"] : [], remove: m.unread ? [] : ["\\Seen"] }),
      });
      m.unread = r.unread;
      btn.textContent = m.unread ? "标记已读" : "标记未读";
      loadFolders();
      loadMessages(false);
    } catch (e) { toast("操作失败：" + e.message); }
  }

  /* ------------------------------ 会话视图 ------------------------------ */
  function currentParams(extra) {
    const params = new URLSearchParams({
      since: state.since === "all" ? "" : state.since,
      limit: String(state.limit),
      offset: String(state.offset),
    });
    if (state.q) params.set("q", state.q);
    if (state.unreadOnly) params.set("unread_only", "true");
    if (state.category === "boring") params.set("boring", "true");
    else if (state.category === "personal") params.set("boring", "false");
    else if (state.category !== "all") params.set("category", state.category);
    if (state.since === "all") params.delete("since");
    if (extra) extra(params);
    return params;
  }

  async function loadThreads(append) {
    const list = $("#messageList");
    if (!append) { list.innerHTML = '<div class="empty">加载中…</div>'; state.offset = 0; }
    const params = currentParams((p) => p.set("folder", state.folder));
    let data;
    try {
      data = await api("/api/threads?" + params.toString());
    } catch (e) {
      list.innerHTML = `<div class="empty">加载失败：${e.message}</div>`;
      return;
    }
    state.total = data.total;
    state.items = append ? state.items.concat(data.items) : data.items;

    list.innerHTML = "";
    if (!state.items.length) {
      list.innerHTML = '<div class="empty">没有匹配的会话</div>';
      $("#listCount").textContent = "0 条";
      $("#moreBtn").style.display = "none";
      return;
    }
    state.items.forEach((t) => {
      const row = el("div", "thread-row" + (t.unread ? " is-unread" : "") + (t.is_boring ? " is-boring" : ""));
      row.dataset.thread = t.thread_id;

      const av = el("div", "avatar", t.is_boring ? CAT_META[t.category].emoji : initials(t.last_from.name, t.last_from.email));
      row.appendChild(av);

      const main = el("div", "thread-main");
      const line1 = el("div", "thread-line1");
      line1.appendChild(el("span", "thread-title", t.subject));
      line1.appendChild(el("span", "thread-time", fmtTime(t.last_date)));
      main.appendChild(line1);

      const line2 = el("div", "thread-line2");
      const names = (t.participants || []).map((p) => p.name).slice(0, 3).join("、");
      line2.appendChild(el("span", "thread-people", names + (t.participant_count > 3 ? ` 等 ${t.participant_count} 人` : "")));
      if (t.message_count > 1) line2.appendChild(el("span", "thread-count", t.message_count));
      const badge = catBadge(t);
      if (badge) line2.appendChild(badge);
      main.appendChild(line2);

      main.appendChild(el("div", "thread-snippet", t.snippet || ""));
      row.appendChild(main);
      if (t.unread) row.appendChild(el("span", "badge-unread"));

      row.addEventListener("click", () => openThread(t.thread_id, row));
      list.appendChild(row);
    });

    $("#listCount").textContent = `${state.total} 条会话`;
    $("#moreBtn").style.display = state.items.length < state.total ? "" : "none";
    $("#moreBtn").textContent = `加载更多（还有 ${state.total - state.items.length} 条）`;
  }

  async function openThread(threadId, row) {
    document.querySelectorAll(".thread-row").forEach((n) => n.classList.remove("is-selected"));
    if (row) row.classList.add("is-selected");

    const reader = $("#reader");
    reader.innerHTML = '<div class="empty">加载中…</div>';
    try {
      const t = await api(`/api/threads/${threadId}?body_chars=60000&body_format=both`);
      renderChat(t);
      // 把未读的都顺手标已读（和点开单封一致）
      const unreadMsgs = (t.messages || []).filter((m) => m.unread && m.folder !== "Drafts");
      if (unreadMsgs.length) {
        Promise.all(
          unreadMsgs.map((m) =>
            fetch(`/api/messages/${m.uid}/flags`, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ folder: m.folder, add: ["\\Seen"] }),
            }).catch(() => {})
          )
        ).then(() => {
          if (row) { row.classList.remove("is-unread"); }
          loadFolders();
        });
      }
    } catch (e) {
      reader.innerHTML = `<div class="empty">读取失败：${e.message}</div>`;
    }
  }

  /** 把长引用折叠起来（聊天感觉的关键） */
  function foldQuotes(root) {
    root.querySelectorAll("blockquote").forEach((q) => {
      if ((q.textContent || "").trim().length < 120) return;
      const det = document.createElement("details");
      det.className = "quote-fold";
      const sum = el("summary", null, "⋯ 显示引用的历史内容");
      det.appendChild(sum);
      q.replaceWith(det);
      det.appendChild(q);
    });
    // 常见的「-----原始邮件-----」分隔块（div 形式）
    root.querySelectorAll("div").forEach((d) => {
      if (d.closest(".quote-fold")) return;
      const head = (d.textContent || "").trim().slice(0, 60);
      if (!/^(?:[-=_]{3,}\s*)?(?:原始邮件|Original Message|发件人[:：]|From\s*:)/i.test(head)) return;
      if ((d.textContent || "").trim().length < 120) return;
      const det = document.createElement("details");
      det.className = "quote-fold";
      det.appendChild(el("summary", null, "⋯ 显示引用的历史内容"));
      d.replaceWith(det);
      det.appendChild(d);
    });
  }

  function renderChat(t) {
    const reader = $("#reader");
    reader.innerHTML = "";

    const bar = el("div", "reader-bar");
    bar.appendChild(el("span", "chat-stat", `${t.message_count} 封 · ${t.participant_count} 人 · ${t.unread ? t.unread + " 未读" : "全部已读"}`));
    bar.appendChild(el("div", "spacer"));
    if (t.is_boring) {
      const meta = CAT_META[t.category] || { emoji: "❓", label: t.category_label };
      bar.appendChild(el("span", "cat-badge " + (CAT_META[t.category] ? CAT_META[t.category].cls : ""), `${meta.emoji} ${meta.label} · 无聊邮件`));
    }
    const backBtn = el("button", "btn", "在邮件视图打开最新一封");
    backBtn.onclick = () => {
      state.viewMode = "message";
      document.querySelectorAll("#viewSeg button").forEach((b) => b.classList.toggle("active", b.dataset.view === "message"));
      loadMessages(false).then(() => {
        const last = t.messages[t.messages.length - 1];
        const row = document.querySelector(`.msg-row[data-uid="${last.uid}"]`);
        openMessage(last.folder, last.uid, row);
      });
    };
    const lastIn = [...t.messages].reverse().find((m) => !m.mine) || t.messages[t.messages.length - 1];
    const replyBtn = el("button", "btn", "↩ 回复");
    replyBtn.onclick = () => openCompose({
      to: replyTargetOf(lastIn),
      subject: /^re:/i.test(t.subject || "") ? t.subject : "Re: " + (t.subject || ""),
      reply: { folder: lastIn.folder, uid: lastIn.uid },
    });
    bar.insertBefore(replyBtn, backBtn);
    bar.appendChild(backBtn);
    reader.appendChild(bar);

    const inner = el("div", "reader-inner chat-wrap");
    inner.appendChild(el("h2", "reader-subject", t.subject || "(无主题)"));

    const meta = el("div", "reader-meta chat-meta");
    const r = el("div", "meta-row");
    r.appendChild(el("span", "meta-label", "参与人"));
    r.appendChild(el("span", "meta-value", (t.participants || []).map((p) => p.name || p.email).join("、") || "-"));
    meta.appendChild(r);
    inner.appendChild(meta);

    const chat = el("div", "chat");
    let lastDay = "";
    let prev = null;

    t.messages.forEach((m) => {
      const d = parseDate(m.date);
      const day = d ? d.toDateString() : "";
      if (day && day !== lastDay) {
        chat.appendChild(el("div", "chat-day", groupLabel(m.date)));
        lastDay = day;
        prev = null;
      }

      const mine = !!m.mine;
      const sender = m.from || {};
      const nearPrev =
        prev && prev.mine === mine &&
        (prev.from || {}).email === sender.email &&
        d && prev._d && d - prev._d < 10 * 60000;

      const rowEl = el("div", "bubble-row" + (mine ? " mine" : ""));
      const av = el("div", "avatar", initials(sender.name, sender.email));
      if (av) rowEl.appendChild(av);

      const bubble = el("div", "bubble" + (mine ? " mine" : "") + (nearPrev ? " grouped" : ""));
      if (!nearPrev) {
        const head = el("div", "bubble-head");
        head.appendChild(el("span", "bubble-name", sender.name || sender.email || "?"));
        head.appendChild(el("span", "bubble-time", fmtFull(m.date)));
        const badge = catBadge(m);
        if (badge) head.appendChild(badge);
        bubble.appendChild(head);
      }

      const body = el("div", "bubble-body");
      if (m.body_html && m.body_html.trim()) {
        body.innerHTML = sanitizeHtml(m.body_html);
      } else {
        body.innerHTML = textToHtml(m.body_text, m);
      }
      foldQuotes(body);
      const blocked = applyImagePolicy(body);
      bubble.appendChild(body);

      if (m.attachments && m.attachments.length) {
        const box = el("div", "attach-list bubble-attach");
        m.attachments.forEach((a) => {
          const chip = el("a", "attach-chip");
          chip.href = a.url;
          chip.target = "_blank";
          chip.rel = "noopener";
          chip.appendChild(el("span", null, `📎 ${a.filename}`));
          chip.appendChild(el("span", "attach-size", fmtBytes(a.size)));
          box.appendChild(chip);
        });
        bubble.appendChild(box);
      }

      const foot = el("div", "bubble-foot");
      foot.appendChild(el("span", "bubble-folder", folderLabel(m.folder)));
      foot.appendChild(el("span", "bubble-size", `UID ${m.uid}`));
      const rBtn = el("button", "bubble-open", "↩ 回复");
      rBtn.onclick = () => openCompose({
        to: replyTargetOf(m),
        subject: /^re:/i.test(m.subject || "") ? m.subject : "Re: " + (m.subject || ""),
        reply: { folder: m.folder, uid: m.uid },
      });
      foot.appendChild(rBtn);
      const dBtn = el("button", "bubble-open bubble-del", "🗑 删除");
      dBtn.onclick = () => deleteMessage(m.folder, m.uid);
      foot.appendChild(dBtn);
      const openBtn = el("button", "bubble-open", "查看原文 ⤢");
      openBtn.onclick = () => {
        state.viewMode = "message";
        document.querySelectorAll("#viewSeg button").forEach((b) => b.classList.toggle("active", b.dataset.view === "message"));
        loadMessages(false).then(() => {
          const row = document.querySelector(`.msg-row[data-uid="${m.uid}"]`);
          openMessage(m.folder, m.uid, row);
        });
      };
      foot.appendChild(openBtn);
      bubble.appendChild(foot);

      rowEl.appendChild(bubble);
      chat.appendChild(rowEl);
      prev = { mine, from: sender, _d: d };
    });

    inner.appendChild(chat);
    reader.appendChild(inner);
    inner.scrollTop = inner.scrollHeight;
  }

  /* ------------------------------ 写邮件 / 删除 ------------------------------ */
  const compose = { reply: null }; // reply = {folder, uid}

  function openCompose(prefill) {
    prefill = prefill || {};
    compose.reply = prefill.reply || null;
    $("#composeTitle").textContent = compose.reply ? "回复邮件" : "写邮件";
    $("#cTo").value = prefill.to || "";
    $("#cCc").value = prefill.cc || "";
    $("#cSubject").value = prefill.subject || "";
    $("#cBody").value = prefill.body || "";
    $("#cFiles").value = "";
    $("#cFileNames").textContent = "";
    $("#composeStatus").textContent = "";
    $("#composeModal").hidden = false;
    setTimeout(() => $("#cTo").focus(), 60);
  }

  function closeCompose() { $("#composeModal").hidden = true; }

  function replyTargetOf(m) {
    // 回复对象：自己发的信 -> 回给收件人；别人发的 -> 回给发件人
    return m.mine || m.folder === "Sent Items" || m.folder === "Drafts"
      ? ((m.to || []).map((t) => t.email).filter(Boolean).join(", "))
      : ((m.from || {}).email || "");
  }

  async function submitCompose(e) {
    e.preventDefault();
    const btn = $("#sendBtn");
    const status = $("#composeStatus");
    btn.disabled = true;
    btn.textContent = "发送中…";
    status.textContent = "";
    try {
      const fd = new FormData();
      fd.set("to", $("#cTo").value.trim());
      fd.set("cc", $("#cCc").value.trim());
      fd.set("subject", $("#cSubject").value.trim());
      fd.set("body", $("#cBody").value);
      if (compose.reply) {
        fd.set("reply_folder", compose.reply.folder);
        fd.set("reply_uid", String(compose.reply.uid));
      }
      for (const f of $("#cFiles").files) fd.append("files", f, f.name);
      const r = await api("/api/send", { method: "POST", body: fd });
      toast(
        `✓ 已发送至 ${r.accepted.join("、")}` +
        (r.sent_uid ? `（副本已存「${folderLabel(r.sent_folder)}」）` : ""),
        4200
      );
      closeCompose();
      loadFolders();
      if (state.folder === r.sent_folder) loadCurrent(false);
    } catch (err) {
      status.textContent = "发送失败：" + err.message;
    } finally {
      btn.disabled = false;
      btn.textContent = "发送 ➤";
    }
  }

  async function deleteMessage(folder, uid) {
    if (!confirm("确定删除这封邮件？将移入服务器的回收站。")) return;
    try {
      const r = await api(`/api/messages/${uid}?folder=${encodeURIComponent(folder)}`, { method: "DELETE" });
      toast(`已移入「${r.trash ? folderLabel(r.trash) : "回收站"}」`);
      state.current = null;
      $("#reader").innerHTML =
        state.viewMode === "thread" ? '<div class="empty">选择一条会话开始阅读</div>' : '<div class="empty">选择一封邮件开始阅读</div>';
      await loadFolders();
      await loadCurrent(false);
    } catch (e) {
      toast("删除失败：" + e.message);
    }
  }

  /* ------------------------------ 同步 ------------------------------ */
  async function doSync() {
    const btn = $("#syncBtn");
    btn.disabled = true;
    const label = btn.textContent;
    btn.textContent = "同步中…";
    try {
      const r = await api("/api/sync", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ all_folders: true, since: state.since === "all" ? "1990-01-01" : state.since, with_body: true }),
      });
      toast(`同步完成：新增 ${r.new} 封，正文 ${r.bodies} 封，附件 ${r.attachments} 个（${r.elapsed}s）`, 4000);
      await loadFolders();
      await loadMessages(false);
    } catch (e) {
      toast("同步失败：" + e.message, 4000);
    } finally {
      btn.disabled = false;
      btn.textContent = label;
    }
  }

  /* ------------------------------ 状态栏 ------------------------------ */
  async function loadStatus() {
    try {
      const s = await api("/api/status");
      $("#account").textContent = s.account.email;
      const st = s.stats || {};
      const c = s.categories || {};
      const th = s.threads || {};
      $("#stats").innerHTML =
        `本地索引 <b>${st.total || 0}</b> 封<br>` +
        `无聊邮件 <b>${c.boring || 0}</b> 封（${Math.round((c.boring_ratio || 0) * 100)}%）<br>` +
        `会话 <b>${th.threads || 0}</b> 条（${th.grouped || 0} 条多人往返）<br>` +
        (st.oldest ? `最早 <b>${st.oldest.slice(0, 10)}</b><br>` : "") +
        `快捷键 <b>J/K</b> 切换 · <b>/</b> 搜索`;
    } catch (e) {
      $("#account").textContent = "未连接";
    }
  }

  /* ------------------------------ 事件 ------------------------------ */
  function bind() {
    let timer = null;

    document.querySelectorAll("#appSeg button").forEach((b) => {
      b.addEventListener("click", () => switchApp(b.dataset.app));
    });
    $("#wxRange").addEventListener("change", (e) => {
      wxState.days = parseInt(e.target.value, 10) || 0;
      wxState.data = null;
      wxState.row = null;
      loadWx(false);
    });
    $("#wxRefresh").addEventListener("click", () => { wxState.data = null; loadWx(true); });

    $("#search").addEventListener("input", (e) => {
      clearTimeout(timer);
      const v = e.target.value.trim();
      timer = setTimeout(() => { state.q = v; state.offset = 0; loadCurrent(false); }, 300);
    });

    $("#range").addEventListener("change", (e) => {
      const v = e.target.value;
      if (v === "all") state.since = "all";
      else if (v.startsWith("d")) {
        const d = new Date(Date.now() - parseInt(v.slice(1)) * 86400000);
        state.since = d.toISOString().slice(0, 10);
      } else state.since = v;
      state.offset = 0;
      loadCurrent(false);
    });

    $("#moreBtn").addEventListener("click", () => { state.offset += state.limit; loadCurrent(true); });
    $("#syncBtn").addEventListener("click", doSync);

    // 写邮件 / 发送
    $("#composeBtn").addEventListener("click", () => openCompose());
    $("#composeClose").addEventListener("click", closeCompose);
    $("#composeCancel").addEventListener("click", closeCompose);
    $("#composeModal").addEventListener("click", (e) => { if (e.target.id === "composeModal") closeCompose(); });
    $("#composeForm").addEventListener("submit", submitCompose);
    $("#cFiles").addEventListener("change", (e) => {
      const fs = [...e.target.files];
      $("#cFileNames").textContent = fs.length ? `已选 ${fs.length} 个：${fs.map((f) => f.name).join("、")}` : "";
    });

    document.querySelectorAll("#viewSeg button").forEach((b) => {
      b.addEventListener("click", () => {
        if (state.viewMode === b.dataset.view) return;
        state.viewMode = b.dataset.view;
        document.querySelectorAll("#viewSeg button").forEach((x) => x.classList.toggle("active", x === b));
        state.current = null;
        state.offset = 0;
        $("#listTitle").textContent = folderLabel(state.folder) + (state.viewMode === "thread" ? " · 会话" : "");
        $("#reader").innerHTML = state.viewMode === "thread"
          ? '<div class="empty">选择一条会话开始阅读</div>'
          : '<div class="empty">选择一封邮件开始阅读</div>';
        loadCurrent(false);
      });
    });

    $("#category").addEventListener("change", (e) => {
      state.category = e.target.value;
      state.offset = 0;
      loadCurrent(false);
    });

    $("#unreadBtn").addEventListener("click", (e) => {
      state.unreadOnly = !state.unreadOnly;
      e.target.classList.toggle("btn-primary", state.unreadOnly);
      state.offset = 0;
      loadCurrent(false);
    });

    const menu = $("#themeMenu");
    $("#themeBtn").addEventListener("click", (e) => { e.stopPropagation(); menu.classList.toggle("open"); });
    document.addEventListener("click", (e) => {
      if (!menu.contains(e.target)) menu.classList.remove("open");
    });

    document.addEventListener("keydown", (e) => {
      const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName);
      if (e.key === "Escape" && !$("#composeModal").hidden) { closeCompose(); return; }
      if (e.key === "/" && !typing) { e.preventDefault(); $("#search").focus(); return; }
      if (typing) return;
      if (e.key === "j" || e.key === "ArrowDown" || e.key === "k" || e.key === "ArrowUp") {
        const rows = [...document.querySelectorAll(".msg-row")];
        if (!rows.length) return;
        e.preventDefault();
        const idx = rows.findIndex((r) => r.classList.contains("is-selected"));
        const next = e.key === "j" || e.key === "ArrowDown"
          ? Math.min(rows.length - 1, idx + 1)
          : Math.max(0, idx <= 0 ? 0 : idx - 1);
        const r = rows[next === -1 ? 0 : next];
        r.scrollIntoView({ block: "nearest" });
        openMessage(r.dataset.folder, parseInt(r.dataset.uid, 10), r);
      }
    });
  }

  /* ------------------------------ 微信统计 ------------------------------ */
  const wxState = { days: 7, data: null, chat: null, row: null };

  function switchApp(name) {
    document.body.dataset.app = name;
    document.querySelectorAll("#appSeg button").forEach((b) =>
      b.classList.toggle("active", b.dataset.app === name)
    );
    $("#mailView").hidden = name !== "mail";
    $("#wxView").hidden = name !== "wechat";
    try { localStorage.setItem("mail.app", name); } catch (e) {}
    if (name === "wechat" && !wxState.data) loadWx(false);
  }

  function wxPanel(title, sub) {
    const p = el("div", "wx-panel");
    const h = el("h3");
    h.appendChild(el("span", null, title));
    if (sub) h.appendChild(el("span", "sub", sub));
    p.appendChild(h);
    return p;
  }

  function wxCard(k, v, s) {
    const c = el("div", "wx-card");
    c.appendChild(el("div", "k", k));
    c.appendChild(el("div", "v", String(v)));
    if (s) c.appendChild(el("div", "s", s));
    return c;
  }

  const BAR_MAX_PX = 96;
  function wxBars(entries) {
    const wrap = el("div", "wx-bars");
    const max = Math.max(1, ...entries.map((e) => e[1]));
    entries.forEach(([lb, v]) => {
      const b = el("div", "wx-bar");
      b.title = `${lb}: ${v} 条`;
      b.appendChild(el("div", "vl", String(v)));
      const col = el("div", "col");
      col.style.height = Math.max(2, Math.round((v / max) * BAR_MAX_PX)) + "px";
      b.appendChild(col);
      b.appendChild(el("div", "lb", String(lb)));
      wrap.appendChild(b);
    });
    return wrap;
  }

  /* 天数太多时按月聚合，否则柱子会挤成一片 */
  function bucketDays(map) {
    const entries = Object.entries(map);
    if (entries.length <= 40) return entries.map(([k, v]) => [k.slice(5), v]);
    const byMonth = {};
    entries.forEach(([k, v]) => { byMonth[k.slice(0, 7)] = (byMonth[k.slice(0, 7)] || 0) + v; });
    return Object.entries(byMonth).map(([k, v]) => [k.slice(2).replace("-", "/"), v]);
  }

  function wxTypes(list) {
    const wrap = el("div", "wx-types");
    if (!list.length) return el("div", "wx-last", "无数据");
    const max = Math.max(1, ...list.map((t) => t.count));
    list.slice(0, 10).forEach((t) => {
      const row = el("div", "wx-type");
      row.appendChild(el("div", "n", t.type));
      const track = el("div", "track");
      const fill = el("div", "fill");
      fill.style.width = (t.count / max) * 100 + "%";
      track.appendChild(fill);
      row.appendChild(track);
      row.appendChild(el("div", "c", `${t.count} · ${t.pct}%`));
      wrap.appendChild(row);
    });
    return wrap;
  }

  function wxChatRows(list, limit) {
    const wrap = el("div", "wx-rows");
    const max = Math.max(1, ...list.map((c) => c.count));
    list.slice(0, limit || 25).forEach((c, i) => {
      const row = el("div", "wx-row");
      row.appendChild(el("div", "rk", String(i + 1)));
      const nm = el("div", "nm");
      nm.appendChild(el("i", null, c.is_group ? "群" : "私"));
      nm.appendChild(document.createTextNode(c.chat));
      nm.title = c.chat;
      row.appendChild(nm);
      row.appendChild(el("div", "ct", String(c.count)));
      const bar = el("div", "bar");
      const bi = el("i");
      bi.style.width = (c.count / max) * 100 + "%";
      bar.appendChild(bi);
      row.appendChild(bar);
      row.addEventListener("click", () => openWxChat(c.username, c.chat, row));
      wrap.appendChild(row);
    });
    return wrap;
  }

  function wxSessionRows(items) {
    const wrap = el("div", "wx-rows");
    items.slice(0, 25).forEach((s) => {
      const row = el("div", "wx-row is-plain");
      row.appendChild(el("div", "rk", s.time.slice(0, 5)));
      const nm = el("div", "nm");
      if (s.unread > 0) nm.appendChild(el("span", "wx-dot"));
      nm.appendChild(el("i", null, s.is_group ? "群" : "私"));
      nm.appendChild(document.createTextNode(s.chat));
      nm.title = s.chat;
      row.appendChild(nm);
      row.appendChild(el("div", "ct", s.unread ? `${s.unread} 未读` : ""));
      const last = el("div", "wx-last");
      last.style.gridColumn = "2 / -1";
      last.textContent = (s.sender ? s.sender + ": " : "") + s.last_message;
      row.appendChild(last);
      wrap.appendChild(row);
    });
    return wrap;
  }

  async function openWxChat(username, name, row) {
    if (wxState.row) wxState.row.classList.remove("is-active");
    wxState.row = row;
    row.classList.add("is-active");
    const box = $("#wxDetail");
    box.innerHTML = "";
    const head = el("div", "wx-detail-head");
    head.appendChild(el("b", null, name));
    head.appendChild(el("span", null, "加载中…"));
    box.appendChild(head);
    try {
      const d = await api(`/api/wechat/history?chat=${encodeURIComponent(username)}&days=${wxState.days}&limit=60`);
      head.lastChild.textContent = `${d.range} · ${d.total} 条`;
      const msgs = el("div", "wx-msgs");
      d.items.slice().reverse().forEach((m) => {
        const line = el("div", "wx-msg");
        line.appendChild(el("div", "t", m.time));
        const b = el("div", "b");
        if (m.sender) b.appendChild(el("u", null, m.sender));
        const txt = m.text || `(${m.type})`;
        b.appendChild(m.text ? document.createTextNode(txt) : el("em", null, txt));
        line.appendChild(b);
        msgs.appendChild(line);
      });
      box.appendChild(msgs);
    } catch (e) {
      head.lastChild.textContent = "加载失败：" + e.message;
    }
  }

  function renderWx(d) {
    const body = $("#wxBody");
    body.innerHTML = "";

    const cards = el("div", "wx-cards");
    cards.appendChild(wxCard("消息总数", d.total, `我发 ${d.mine} · 收到 ${d.others}`));
    cards.appendChild(wxCard("活跃会话", d.chats, `群 ${d.group_chats} · 单聊 ${d.private_chats}`));
    cards.appendChild(wxCard("群聊消息", d.group_messages,
      d.total ? `占 ${Math.round((d.group_messages / d.total) * 100)}%` : "—"));
    cards.appendChild(wxCard("当前未读", d.unread, "全部会话合计"));
    body.appendChild(cards);

    const grid = el("div", "wx-grid");
    const left = el("div");
    const right = el("div");

    const dayCount = Object.keys(d.by_day).length;
    const monthly = dayCount > 40;
    const pDay = wxPanel(
      monthly ? "消息量趋势（按月）" : "每日消息量",
      `${dayCount} 天${monthly ? " · 按月聚合" : ""}`
    );
    pDay.appendChild(wxBars(bucketDays(d.by_day)));
    left.appendChild(pDay);

    const pHour = wxPanel("24 小时分布", "本地时间");
    pHour.appendChild(wxBars(Object.entries(d.by_hour).map(([h, v]) => [h + "时", v])));
    left.appendChild(pHour);

    const pType = wxPanel("消息类型", d.by_type.length + " 类");
    pType.appendChild(wxTypes(d.by_type));
    right.appendChild(pType);

    const pTop = wxPanel("活跃会话 Top", `共 ${d.chats} 个（点击查看最近消息）`);
    pTop.appendChild(wxChatRows(d.top_chats, 25));
    right.appendChild(pTop);

    grid.appendChild(left);
    grid.appendChild(right);
    body.appendChild(grid);
  }

  async function loadWxSessions() {
    const body = $("#wxBody");
    let panel = $("#wxSessions");
    if (!panel) {
      panel = wxPanel("最近会话", "按最后一条消息时间排序");
      panel.id = "wxSessions";
      body.appendChild(panel);
      panel.appendChild(el("div", "empty", "加载中…"));
    }
    try {
      const r = await api("/api/wechat/sessions?limit=30");
      panel.innerHTML = "";
      const h = el("h3");
      h.appendChild(el("span", null, "最近会话"));
      h.appendChild(el("span", "sub", "按最后一条消息时间排序"));
      panel.appendChild(h);
      panel.appendChild(wxSessionRows(r.items));
    } catch (e) {
      panel.innerHTML = '<div class="empty">会话列表加载失败</div>';
    }
  }

  async function loadWx(refresh) {
    const body = $("#wxBody");
    if (!wxState.data) body.innerHTML = '<div class="empty">正在扫描本地微信数据库…</div>';
    $("#wxMeta").textContent = "扫描中…（首次需解密，约数秒）";
    try {
      const d = await api(`/api/wechat/overview?days=${wxState.days}${refresh ? "&refresh=1" : ""}`);
      wxState.data = d;
      wxState.chat = null;
      wxState.row = null;
      const t = new Date(d.generated_at * 1000);
      $("#wxMeta").textContent =
        `${d.range} · ${d.scanned.databases} 库 / ${d.scanned.tables} 张消息表 · ` +
        `${String(t.getHours()).padStart(2, "0")}:${String(t.getMinutes()).padStart(2, "0")} 更新`;
      renderWx(d);
      const detail = el("div", "wx-panel wx-detail");
      detail.id = "wxDetail";
      body.appendChild(detail);
      loadWxSessions();
    } catch (e) {
      body.innerHTML =
        `<div class="empty">微信数据不可用：${esc(e.message)}<br><br>` +
        "需先运行 <b>wechat-cli init</b>（微信保持登录），并确保 mailui 环境装了 pycryptodome / zstandard</div>";
      $("#wxMeta").textContent = "";
    }
  }

  /* ------------------------------ 启动 ------------------------------ */
  function init() {
    buildThemeMenu();
    let saved = "ink";
    try { saved = localStorage.getItem("mail.theme") || "ink"; } catch (e) {}
    const urlTheme = new URLSearchParams(location.search).get("theme");
    applyTheme(urlTheme && THEMES.some((t) => t.id === urlTheme) ? urlTheme : saved);

    bind();
    loadStatus();

    // 恢复上次停留的页签（信箱 / 微信），可用 ?app=wechat 强制指定
    let app0 = "mail";
    try { app0 = localStorage.getItem("mail.app") || "mail"; } catch (e) {}
    const appParam = new URLSearchParams(location.search).get("app");
    if (appParam === "wechat" || appParam === "mail") app0 = appParam;
    switchApp(app0);

    // 支持 /?folder=INBOX&uid=123 直达某封邮件（API 里的 web_url 就指向这里）
    // 也支持 /?thread=txxx 直达某条会话
    const params = new URLSearchParams(location.search);
    const folderParam = params.get("folder");
    const uidParam = params.get("uid");
    const threadParam = params.get("thread");
    if (folderParam) state.folder = folderParam;

    if (threadParam) {
      // 会话直链：切到会话视图
      state.viewMode = "thread";
      document.querySelectorAll("#viewSeg button").forEach((b) =>
        b.classList.toggle("active", b.dataset.view === "thread")
      );
    }

    loadFolders()
      .then(() => loadCurrent(false))
      .then(() => {
        if (threadParam) {
          const row = document.querySelector(`.thread-row[data-thread="${threadParam}"]`);
          openThread(threadParam, row);
        } else if (uidParam) {
          const uid = parseInt(uidParam, 10);
          if (!isNaN(uid)) {
            const row = document.querySelector(`.msg-row[data-uid="${uid}"]`);
            openMessage(state.folder, uid, row);
          }
        }
      });
  }

  document.addEventListener("DOMContentLoaded", init);
})();

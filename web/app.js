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
    $("#listTitle").textContent = folderLabel(name);
    renderFolders();
    $("#reader").innerHTML = '<div class="empty">选择一封邮件开始阅读</div>';
    loadMessages(false);
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
        (state.current && state.current.uid === m.uid && state.current.folder === m.folder ? " is-selected" : ""));
      row.dataset.uid = m.uid;
      row.dataset.folder = m.folder;

      const line = el("div", "msg-line");
      line.appendChild(el("span", "msg-from", m.from.name || m.from.email));
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
          ph.title = src;
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
      $("#stats").innerHTML =
        `本地索引 <b>${st.total || 0}</b> 封<br>` +
        (st.oldest ? `最早 <b>${st.oldest.slice(0, 10)}</b><br>` : "") +
        (st.newest ? `最新 <b>${st.newest.slice(0, 10)}</b><br>` : "") +
        `快捷键 <b>J/K</b> 切换 · <b>/</b> 搜索`;
    } catch (e) {
      $("#account").textContent = "未连接";
    }
  }

  /* ------------------------------ 事件 ------------------------------ */
  function bind() {
    let timer = null;
    $("#search").addEventListener("input", (e) => {
      clearTimeout(timer);
      const v = e.target.value.trim();
      timer = setTimeout(() => { state.q = v; state.offset = 0; loadMessages(false); }, 300);
    });

    $("#range").addEventListener("change", (e) => {
      const v = e.target.value;
      if (v === "all") state.since = "all";
      else if (v.startsWith("d")) {
        const d = new Date(Date.now() - parseInt(v.slice(1)) * 86400000);
        state.since = d.toISOString().slice(0, 10);
      } else state.since = v;
      state.offset = 0;
      loadMessages(false);
    });

    $("#moreBtn").addEventListener("click", () => { state.offset += state.limit; loadMessages(true); });
    $("#syncBtn").addEventListener("click", doSync);

    $("#unreadBtn").addEventListener("click", (e) => {
      state.unreadOnly = !state.unreadOnly;
      e.target.classList.toggle("btn-primary", state.unreadOnly);
      state.offset = 0;
      loadMessages(false);
    });

    const menu = $("#themeMenu");
    $("#themeBtn").addEventListener("click", (e) => { e.stopPropagation(); menu.classList.toggle("open"); });
    document.addEventListener("click", (e) => {
      if (!menu.contains(e.target)) menu.classList.remove("open");
    });

    document.addEventListener("keydown", (e) => {
      const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName);
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

  /* ------------------------------ 启动 ------------------------------ */
  function init() {
    buildThemeMenu();
    let saved = "ink";
    try { saved = localStorage.getItem("mail.theme") || "ink"; } catch (e) {}
    const urlTheme = new URLSearchParams(location.search).get("theme");
    applyTheme(urlTheme && THEMES.some((t) => t.id === urlTheme) ? urlTheme : saved);

    bind();
    loadStatus();

    // 支持 /?folder=INBOX&uid=123 直达某封邮件（API 里的 web_url 就指向这里）
    const params = new URLSearchParams(location.search);
    const folderParam = params.get("folder");
    const uidParam = params.get("uid");
    if (folderParam) state.folder = folderParam;

    loadFolders()
      .then(() => loadMessages(false))
      .then(() => {
        if (uidParam) {
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

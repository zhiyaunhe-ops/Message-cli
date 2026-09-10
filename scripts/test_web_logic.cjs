/* 从 web/app.js 里切出纯逻辑片段，在 Node 里跑一遍做冒烟测试（不是复制品，是原文） */
const fs = require("fs");
const path = require("path");

const src = fs.readFileSync(path.join(__dirname, "..", "web", "app.js"), "utf8");

function slice(startMark, endMark) {
  const i = src.indexOf(startMark);
  if (i < 0) throw new Error("start mark not found: " + startMark);
  const j = src.indexOf(endMark, i);
  if (j < 0) throw new Error("end mark not found: " + endMark);
  return src.slice(i, j);
}

const partA = slice("const CHIP_SEP_RE", "/** 造一个 chip 输入框");
const partB = slice("function replyTargetOf(m) {", "/* ------------------------------ AI 写正文");
const partC = slice("/* ------------------------------ 写信纯逻辑（可单测）", "/* ------------------------------ 写邮件 / 删除");

const harness = `
const state = { me: "me@example.com" };
function el(tag, cls, text) { return { tag, cls, text, style: {}, disabled: false, title: "" }; }
function toast() {}
let lastCompose = null;
function openCompose(o) { lastCompose = o; }
${partA}
${partB}
${partC}
globalThis.T = { parseAddr, normAddrs, replyTargetOf, replyAllTargets, composeReply, replyAllBtn,
                 fileKey, appendFiles, removeFileAt, sumBytes, applyAiOption,
                 getLast: () => lastCompose };
`;
eval(harness);
const T = globalThis.T;

let pass = 0, fail = 0;
function eq(actual, expected, label) {
  const a = JSON.stringify(actual), e = JSON.stringify(expected);
  if (a === e) { pass++; console.log("  ok   " + label); }
  else { fail++; console.log("  FAIL " + label + "\n       got " + a + "\n       want " + e); }
}

console.log("[parseAddr]");
eq(T.parseAddr("zhang.san@example.com"), { name: "", email: "zhang.san@example.com" }, "裸邮箱");
eq(T.parseAddr('Zhang San <sanzhang@x.com>'), { name: "Zhang San", email: "sanzhang@x.com" }, "Name <mail>");
eq(T.parseAddr('"Zhang, San" <w@x.com>'), { name: "Zhang, San", email: "w@x.com" }, "带引号逗号");
eq(T.parseAddr("沃尔特 <w@x.com>"), { name: "沃尔特", email: "w@x.com" }, "中文名");
eq(T.parseAddr("<w@x.com>"), { name: "", email: "w@x.com" }, "只有尖括号");
eq(T.parseAddr("Zhang"), null, "只有名字 -> null");
eq(T.parseAddr("   "), null, "空白 -> null");

console.log("[normAddrs]");
eq(T.normAddrs("a@x.com, b@y.com"), [{ name: "", email: "a@x.com" }, { name: "", email: "b@y.com" }], "逗号串");
eq(T.normAddrs("a@x.com；b@y.com"), [{ name: "", email: "a@x.com" }, { name: "", email: "b@y.com" }], "中文分号");
eq(T.normAddrs("A <a@x.com>, a@X.COM"), [{ name: "A", email: "a@x.com" }], "去重（忽略大小写）");
eq(T.normAddrs(["A <a@x.com>", "b@y.com"]), [{ name: "A", email: "a@x.com" }, { name: "", email: "b@y.com" }], "数组");
eq(T.normAddrs([{ name: "王小明", email: "wang.xiaoming@example.com" }]), [{ name: "王小明", email: "wang.xiaoming@example.com" }], "对象");
eq(T.normAddrs(""), [], "空串");
eq(T.normAddrs(null), [], "null");

/* 下面这些人名 / 域名全是 example.com 占位，不要换成真实往来联系人 */
const inbound = {
  mine: false, folder: "INBOX",
  from: { name: "Alice Chen", email: "alice.chen@partner.example.com" },
  to: [{ name: "", email: "me@example.com" }, { name: "Bob", email: "bob.li@partner.example.com" }],
  cc: [{ name: "Carol", email: "carol.wu@vendor.example.com" }, { name: "Bob", email: "bob.li@partner.example.com" }],
  subject: "Re: HK air cargo",
};

console.log("[回复 / 回复全部：别人发来的]");
eq(T.replyTargetOf(inbound), [{ name: "Alice Chen", email: "alice.chen@partner.example.com" }], "回复=只回发件人");
eq(T.replyAllTargets(inbound), {
  to: [{ name: "Alice Chen", email: "alice.chen@partner.example.com" }, { name: "Bob", email: "bob.li@partner.example.com" }],
  cc: [{ name: "Carol", email: "carol.wu@vendor.example.com" }],
}, "回复全部=发件人+收件人；抄送去掉重复的 Bob；自己不被带上");

const outbound = {
  mine: true, folder: "Sent Items",
  from: { name: "Me", email: "me@example.com" },
  to: [{ name: "Dave", email: "dave.zhang@example.com" }],
  cc: [{ name: "Me", email: "me@example.com" }],
  subject: "Weekly report",
};
console.log("[回复 / 回复全部：自己发出的]");
eq(T.replyTargetOf(outbound), [{ name: "Dave", email: "dave.zhang@example.com" }], "回复=回原收件人");
eq(T.replyAllTargets(outbound), { to: [{ name: "Dave", email: "dave.zhang@example.com" }], cc: [] }, "回复全部：抄送里的自己会被剔掉");

console.log("[composeReply 预填]");
T.composeReply(inbound, true);
eq(T.getLast().to.length, 2, "to 预填 2 人");
eq(T.getLast().cc.length, 1, "cc 预填 1 人");
eq(T.getLast().subject, "Re: HK air cargo", "已有 Re: 不重复加");
eq(T.getLast().reply, { folder: "INBOX", uid: undefined }, "带 reply 头信息");

const single = { mine: false, folder: "INBOX", from: { name: "Solo", email: "solo@example.com" },
                 to: [{ email: "me@example.com" }], cc: [], subject: "hi" };
console.log("[回复全部按钮置灰]");
eq(T.replyAllBtn(single).disabled, true, "只有我一个收件人 -> 置灰");
eq(T.replyAllBtn(inbound).disabled, false, "有其他人 -> 可点");

/* ---- 附件列表 ---- */
const f = (name, size, mtime) => ({ name, size, lastModified: mtime || 1 });
console.log("[附件：fileKey / appendFiles / removeFileAt / sumBytes]");
eq(T.fileKey(f("a.pdf", 10, 5)), "a.pdf|10|5", "同名同大小同时间 -> 同一个 key");
eq(T.fileKey(f("a.pdf", 11, 5)) !== T.fileKey(f("a.pdf", 10, 5)), true, "大小不同 -> 不同 key");

let list = [];
eq(T.appendFiles(list, [f("a.pdf", 10), f("b.docx", 20)]), 2, "首次追加 2 个");
eq(list.map((x) => x.name), ["a.pdf", "b.docx"], "顺序保持添加顺序");
eq(T.appendFiles(list, [f("b.docx", 20), f("c.png", 30)]), 1, "重复的不再加");
eq(list.length, 3, "现在 3 个附件");
eq(T.appendFiles(list, null), 0, "空输入不报错");
eq(T.sumBytes(list), 60, "大小合计 10+20+30");
eq(T.removeFileAt(list, 1), true, "能删掉中间那个");
eq(list.map((x) => x.name), ["a.pdf", "c.png"], "删完顺序不乱");
eq(T.removeFileAt(list, 9), false, "越界删除返回 false");
eq(T.removeFileAt(list, -1), false, "负下标也返回 false");

/* ---- AI 方案套用 ---- */
const opt = { title: "正式", subject: "ERP 上线时间确认", body: "王总：\n\n……\n\n顺颂商祺" };
console.log("[applyAiOption]");
eq(T.applyAiOption({ subject: "", body: "" }, opt, true),
   { body: opt.body, subject: opt.subject, subjectUsed: true }, "勾了补主题 + 原主题为空 -> 填主题");
eq(T.applyAiOption({ subject: "我写的", body: "" }, opt, true),
   { body: opt.body, subject: opt.subject, subjectUsed: true }, "勾了补主题 -> 覆盖原主题");
eq(T.applyAiOption({ subject: "我写的", body: "" }, opt, false),
   { body: opt.body, subject: "我写的", subjectUsed: false }, "不勾 -> 主题原样不动");
eq(T.applyAiOption({ subject: "", body: "" }, { title: "无主题方案", body: "X" }, true),
   { body: "X", subject: "", subjectUsed: false }, "方案没给主题 -> 不标记已用");
eq(T.applyAiOption(null, { body: "Y" }, true),
   { body: "Y", subject: "", subjectUsed: false }, "fields 为空也不炸");

console.log(`\n通过 ${pass} / 失败 ${fail}`);
process.exit(fail ? 1 : 0);

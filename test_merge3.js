/* 写作页冲突合并的单测。函数直接从 write_page.html 的 MERGE3 区间抽出来跑，
   免得两份代码各改各的。  运行：node test_merge3.js  */
const fs = require("fs");
const assert = require("assert");

const html = fs.readFileSync(`${__dirname}/write_page.html`, "utf8");
const after = html.split("/* MERGE3_START")[1];        // 标记行后半截还是注释，跳到它结束
const src = after.slice(after.indexOf("*/") + 2).split("/* MERGE3_END */")[0];
const { merge3, lcsPairs } = new Function(`${src}; return { merge3, lcsPairs };`)();

let passed = 0;
function test(name, fn) {
  try { fn(); passed++; console.log(`  ok  ${name}`); }
  catch (e) { console.error(`  FAIL ${name}\n       ${e.message}`); process.exitCode = 1; }
}

const L = (...lines) => lines.join("\n");

test("两边改了不同的地方，都保住", () => {
  const base = L("第一段", "第二段", "第三段");
  const mine = L("第一段", "第二段改过了", "第三段");
  const theirs = L("第一段", "第二段", "第三段改过了");
  const { text, conflicts } = merge3(base, mine, theirs);
  assert.strictEqual(text, L("第一段", "第二段改过了", "第三段改过了"));
  assert.strictEqual(conflicts, 0);
});

test("她加的删除线不会被电脑那版冲掉（今天第06集的真实情形）", () => {
  const base = L("黄蔚妮对着镜子补口红。", "黄蔚妮从镜子里看一眼，没有认出来。", "她转过身。");
  const mine = L("黄蔚妮对着镜子补口红。", "黄蔚妮~~从镜子里看一眼，没有认出来。~~", "她转过身。");
  const theirs = L("黄蔚妮对着镜子补口红。", "黄蔚妮从镜子里看一眼，没有认出来。", "她转过身。", "颜小莉往前挪了一步。");
  const { text, conflicts } = merge3(base, mine, theirs);
  assert.ok(text.includes("~~从镜子里看一眼，没有认出来。~~"), "删除线没保住");
  assert.ok(text.includes("颜小莉往前挪了一步。"), "电脑那边新增的行丢了");
  assert.strictEqual(conflicts, 0);
});

test("删除线和批注同时加，两样都保住（今天第08集的真实情形）", () => {
  const base = L("黄蔚妮：我要的不是狗。", "", "林淼：那你要什么。");
  const mine = L("~~黄蔚妮：我要的不是狗。~~", "",
                 "<!-- 批注 2026-09-17 17:12 | 冷静地,但其实内心有点崩溃 -->",
                 "", "林淼：那你要什么。");
  const theirs = L("黄蔚妮：我要的不是狗。", "", "林淼：那你要什么。", "", "林淼：一条狗而已。");
  const { text } = merge3(base, mine, theirs);
  assert.ok(text.includes("~~黄蔚妮：我要的不是狗。~~"), "删除线没保住");
  assert.ok(text.includes("<!-- 批注 2026-09-17 17:12"), "批注没保住");
  assert.ok(text.includes("林淼：一条狗而已。"), "电脑那边新增的行丢了");
});

test("同一行两边都改：保留她这边，并记一处冲突", () => {
  const base = L("开头", "有争议的一行", "结尾");
  const mine = L("开头", "她改的版本", "结尾");
  const theirs = L("开头", "电脑改的版本", "结尾");
  const { text, conflicts } = merge3(base, mine, theirs);
  assert.strictEqual(text, L("开头", "她改的版本", "结尾"));
  assert.strictEqual(conflicts, 1);
});

test("两边改成一样的，不算冲突", () => {
  const base = L("开头", "旧的一行", "结尾");
  const same = L("开头", "新的一行", "结尾");
  const { text, conflicts } = merge3(base, same, same);
  assert.strictEqual(text, same);
  assert.strictEqual(conflicts, 0);
});

test("电脑那边删了一段，她没动：跟着删", () => {
  const base = L("A", "B", "C");
  const { text, conflicts } = merge3(base, base, L("A", "C"));
  assert.strictEqual(text, L("A", "C"));
  assert.strictEqual(conflicts, 0);
});

test("她删了一段，电脑没动：跟着删", () => {
  const base = L("A", "B", "C");
  const { text, conflicts } = merge3(base, L("A", "C"), base);
  assert.strictEqual(text, L("A", "C"));
  assert.strictEqual(conflicts, 0);
});

test("没有共同祖先时整份保留她这边", () => {
  const theirs = L("电脑上的旧稿");
  const mine = L("她正在写的新稿", "第二行");
  const { text } = merge3(theirs, mine, theirs);   // base 退化成 theirs
  assert.strictEqual(text, mine);
});

test("两边都在文末追加：都保住", () => {
  const base = L("A", "B");
  const { text } = merge3(base, L("A", "B", "她写的"), L("A", "B", "电脑写的"));
  assert.ok(text.includes("她写的") && text.includes("电脑写的"));
});

test("空文档不炸", () => {
  assert.strictEqual(merge3("", "", "").text, "");
  assert.strictEqual(merge3("", "新写的", "").text, "新写的");
});

test("整章规模跑得动（900 行，1 秒内）", () => {
  const base = Array.from({ length: 900 }, (_, i) => `第 ${i} 行`).join("\n");
  const mine = base.replace("第 500 行", "~~第 500 行~~");
  const theirs = base.replace("第 800 行", "第 800 行（电脑改过）");
  const t0 = Date.now();
  const { text, conflicts } = merge3(base, mine, theirs);
  const ms = Date.now() - t0;
  assert.ok(text.includes("~~第 500 行~~") && text.includes("（电脑改过）"));
  assert.strictEqual(conflicts, 0);
  assert.ok(ms < 1000, `太慢了：${ms}ms`);
  console.log(`       900 行合并耗时 ${ms}ms`);
});

test("随机 300 轮：她新写的行一行都不会丢", () => {
  // 这是整个合并最要紧的性质。丢电脑那边的改动只是麻烦，丢她的是事故。
  let rnd = 20260917;
  const rand = (n) => (rnd = (rnd * 1103515245 + 12345) & 0x7fffffff) % n;
  for (let round = 0; round < 300; round++) {
    const base = Array.from({ length: 12 }, (_, i) => `base-${i}`);
    const edit = (src, tag) => {
      const o = src.slice();
      for (let n = 0; n < 1 + rand(3); n++) {
        const at = rand(o.length + 1);
        const op = rand(3);
        if (op === 0) o.splice(at, 0, `${tag}-新增-${round}-${n}`);
        else if (op === 1 && at < o.length) o[at] = `${tag}-改写-${round}-${n}`;
        else if (at < o.length) o.splice(at, 1);
      }
      return o;
    };
    const mine = edit(base, "她");
    const theirs = edit(base, "电脑");
    const { text } = merge3(base.join("\n"), mine.join("\n"), theirs.join("\n"));
    const got = new Set(text.split("\n"));
    for (const line of mine) {
      if (line.startsWith("她-")) {
        assert.ok(got.has(line), `第 ${round} 轮丢了她写的「${line}」`);
      }
    }
  }
});

test("lcsPairs 对齐正确", () => {
  assert.deepStrictEqual(lcsPairs(["a", "b", "c"], ["a", "x", "c"]), [[0, 0], [2, 2]]);
});

console.log(`\n${passed} 个通过`);

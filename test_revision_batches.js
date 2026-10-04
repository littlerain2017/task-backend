/* 本地浏览器回归。所有接口用夹具拦截，绝不连接真实书稿。
   NODE_PATH=<Playwright 模块目录> node test_revision_batches.js */
const fs = require('fs');
const assert = require('assert/strict');
const { chromium } = require('playwright');

async function main() {
  const html = fs.readFileSync(__dirname + '/write_page.html', 'utf8');
  const first = '测试书/第07集.md', second = '测试书/第19集.md';
  const oldNote = '<!-- 批注 ✓ @codex #flashback-trim-0702-20261004 | 压缩重复追逐。 -->';
  const docs = {
    [first]: '## 07-02 外 · 工地 · 日\n改后正文\n' + oldNote + '\n<!-- 批注 2026-10-04 10:00 | 作者待办 -->',
    [second]: '## 19-01 外 · 水沟 · 日\n抓狗正文\n<!-- 批注 @codex ✓ #batch-20261004-02--1901 2026-10-04 16:00 | 【批次：时间顺序】先抓狗，再放狗。 -->\n## 19-03 外 · 山路 · 日\n流浪正文\n<!-- 批注 ✓ @codex #flashback-order-1903-20261004 | 流浪移到放狗后。 -->',
    '另一书/第01集.md': '## 01-01 外 · 日\n<!-- 批注 @codex #batch-20261005-01--0101 | 另一书的修改。 -->',
  };
  const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROMIUM_EXECUTABLE_PATH });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    const errors = [], writes = [];
    let rejectSave = false, rejectRead = false;
    page.on('pageerror', error => errors.push(error.message));
    await page.addInitScript(first => {
      localStorage.setItem('writing_token', 'test-only-token');
      localStorage.setItem('writing_book', '测试书');
      localStorage.setItem('writing_last_doc', first);
    }, first);
    await page.route('**/*', async route => {
      const url = new URL(route.request().url());
      if (url.pathname === '/write') return route.fulfill({ contentType: 'text/html', body: html });
      if (!url.pathname.startsWith('/writing/')) return route.abort();
      const req = route.request().postDataJSON();
      let data = { ok: true, comments: [] };
      if (url.pathname === '/writing/docs/list') data.docs = Object.keys(docs).map(name => ({ name, updatedAt: 1, readonly: false, marks: [] }));
      if (url.pathname === '/writing/docs/get') data = rejectRead && req.name === second
        ? { ok: false, error: '模拟读取失败' } : { ok: true, content: docs[req.name], updatedAt: 1 };
      if (url.pathname === '/writing/docs/put') {
        writes.push(req);
        data = rejectSave ? { ok: false, error: '模拟保存失败' } : { ok: true, updatedAt: 2, deltaCjk: 0, activeMs: 0 };
        if (!rejectSave) docs[req.name] = req.content;
      }
      await route.fulfill({ json: data });
    });
    await page.goto('http://revision.test/write');
    await page.waitForFunction(() => current !== null);
    assert.equal(await page.evaluate(() => getEditorContent()), docs[first]);
    assert.equal(await page.locator('.ai-note').count(), 1);
    await page.getByRole('button', { name: '本轮修改', exact: true }).click();
    await page.waitForFunction(() => !revisionLoading && revisionBatches.length === 2);
    assert.equal(await page.locator('#revisionBatch option').count(), 2);
    assert.match(await page.locator('#revisionBatch').innerText(), /时间顺序/);
    assert.equal(await page.locator('#revisionItems button').count(), 1);
    assert.equal(writes.length, 0);
    const next = page.getByRole('button', { name: '下一处', exact: true });
    const prev = page.getByRole('button', { name: '上一处', exact: true });
    await next.click();
    await page.waitForFunction(() => revisionIndex === 0 && !revisionJumping);
    assert.equal(await page.evaluate(() => current.name), second);
    assert.match(await page.locator('.revision-target').innerText(), /先抓狗，再放狗/);
    assert.equal(await page.evaluate(() => getEditorContent()), docs[second]);
    assert.equal(writes.length, 0);
    await page.locator('#revisionBatch').selectOption('20261004-flashback');
    assert.equal(await page.locator('#revisionItems button').count(), 2);
    await next.click();
    await page.waitForFunction(() => revisionIndex === 0 && !revisionJumping);
    assert.equal(await page.evaluate(() => current.name), first);
    await next.click();
    await page.waitForFunction(() => revisionIndex === 1 && !revisionJumping);
    assert.equal(await page.evaluate(() => current.name), second);
    await prev.click();
    await page.waitForFunction(() => revisionIndex === 0 && !revisionJumping);
    assert.equal(writes.length, 0);
    await page.locator('#editor .line').nth(1).fill('作者刚写的新正文');
    rejectSave = true;
    await next.click();
    await page.waitForFunction(() => !revisionJumping);
    assert.equal(await page.evaluate(() => current.name), first);
    assert.match(await page.locator('#revisionStatus').innerText(), /未保存成功/);
    assert.match(await page.evaluate(() => getEditorContent()), /作者刚写/);
    rejectSave = false;
    await next.click();
    await page.waitForFunction(() => revisionIndex === 1 && !revisionJumping);
    assert.match(docs[first], /作者刚写/);
    assert.ok(docs[first].includes(oldNote), '原批注格式须逐字保留');
    assert.equal(await page.evaluate(() => stripNotes('正文\n<!-- 批注 旧批注 -->\n尾')), '正文\n\n尾');
    assert.equal(await page.evaluate(() => collectNotes('<!-- 批注 ✓ @codex #flashback-a-20261004 | 完成 -->\n<!-- 批注 2026-10-04 | 作者待办 -->')), '作者待办');
    // 作者回复沿用长线程编号，分享页仍正确显示时间和正文。
    const readHtml = fs.readFileSync(__dirname + '/read_page.html', 'utf8');
    const sharedNoteRegex = new Function(readHtml.match(/const NOTE_RE = [^\n]+/)[0] + '; return NOTE_RE;')();
    const sharedNote = sharedNoteRegex.exec('<!-- 批注 ✓ #batch-20261004-02--1901 2026-10-04 16:00 | 作者回复 -->');
    assert.equal(sharedNote[4], '2026-10-04 16:00');
    assert.equal(sharedNote[5], '作者回复');
    await page.evaluate(name => openDoc(name), first);
    rejectRead = true;
    await page.getByRole('button', { name: '刷新批次', exact: true }).click();
    await page.waitForFunction(() => !revisionLoading);
    assert.match(await page.locator('#revisionStatus').innerText(), /读取失败/);
    assert.equal(await next.isDisabled(), true);
    rejectRead = false;
    await page.getByRole('button', { name: '刷新批次', exact: true }).click();
    await page.waitForFunction(() => !revisionLoading && revisionBatches.length === 2);
    await page.evaluate(() => openDoc('另一书/第01集.md'));
    await page.waitForFunction(() => !revisionLoading && revisionBook === '另一书');
    assert.equal(await page.locator('#revisionItems button').count(), 1);
    assert.match(await page.locator('#revisionItems').innerText(), /另一书的修改/);
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await page.locator('#revisionNext').isVisible(), true);
    const overflow = await page.evaluate(() => [...document.querySelectorAll('body *')].filter(el => {
      const rect = el.getBoundingClientRect(); return rect.width && (rect.right > window.innerWidth + 1 || rect.left < -1);
    }).map(el => ({ tag: el.tagName, id: el.id, className: el.className, width: el.getBoundingClientRect().width })));
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), '手机不能横向溢出: ' + JSON.stringify(overflow));
    assert.deepEqual(errors, []);
    console.log('PASS: 批次分组、跨章跳转、原文保留、保存失败保护、读取恢复、书籍隔离');
  } finally { await browser.close(); }
}
main().catch(error => { console.error(error); process.exitCode = 1; });

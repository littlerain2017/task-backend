# task-backend · WRITING MACHINE 后端

FastAPI，部署在 Railway（项目 `practical-simplicity` → service `web`，push 到 main 自动部署）。
同时服务三样东西：写作网页 `/write`、只读分享页 `/read`、以及「写作进度」小程序的接口。

## 文件

| 文件 | 是什么 |
|---|---|
| `main.py` | 全部端点。前 ~150 行是遗留的任务助手（`/login` `/submit-tasks`，仍在用），其余是写作系统 |
| `write_page.html` | **写作网页全部前端**，单文件、无构建、无框架 |
| `read_page.html` | 只读分享页 |
| `writing_logic.py` | 纯函数：字数统计、每日进度聚合。**有单测** |
| `watcher_client.py` | 电脑端同步脚本的分发副本，由 `/writing/watcher.py` 提供下载。**真身在 `~/WeChatProjects/miniprogram-1/watcher/`，改完要两边同步** |
| `scifi_advisor_prompt.md` | 科幻顾问的通用底座，镜像自 `~/.claude/skills/scifi-advisor/SKILL.md`。**改了要手动 cp 过来再 push** |

## 改前必读：这个编辑器里没有纯粹的显示 bug

`getEditorContent()` 把 **DOM 当作正文的唯一真源**——保存时逐个遍历 `editor.childNodes` 序列化成文本。

后果：**任何让节点从 DOM 消失的渲染 bug，都会在下一次保存时变成数据删除。**

2026-09-10 就是这样丢过三条批注：`loadComments` 无差别 remove 了所有 `.inline-note`
（当时朋友留言与作者批注共用这个类名），用户一打字触发保存，序列化时那些节点已经不在了，
于是一份不含批注的正文覆盖了云端，watcher 再写回磁盘。**"看不见"直接升级成了"没有了"。**

所以，**动任何触碰 `editor` 子节点的代码之前**，先跑一遍：

```bash
grep -n "\.remove()\|editor\.innerHTML\|querySelectorAll\|node\.textContent =" write_page.html
```

把所有会删节点或重写内容的地方列出来，逐个确认它不会误伤你正在加的东西。
只验证"数据在磁盘/在云端/正则能匹配"是**不够的**——那三项只覆盖数据层，
渲染链末端还有别的东西在动 DOM。

## 作者的编辑不可被覆盖（两道机械护栏）

她在 `/write` 上的改动会经 watcher 落到 `~/Documents/Books/`，因此**任何程序整文件覆盖
那里的稿子，都等于删掉她刚写的东西**。2026-09-17 装了两道拦截：

1. **写前拦截**：`~/.claude/settings.json` 的 PreToolUse 钩子跑 `~/.claude/hooks/guard-books-write.py`，
   对 `Books/` 下**已存在**文件的 `Write` 一律 deny（新建放行）。只能 `Edit`，且动手前先 Read。
2. **推前拦截**：`watcher_client.guard_note_loss()`。磁盘版本的批注数比上次同步点少，就不推，
   把磁盘那份存进 `~/.writing-watcher-quarantine/`，再用云端版本恢复文件。
   基准存在 state 的 `note_counts`；她在网页删批注会经写回把基准调低，所以不会误伤。

钩子管不到 Bash 里的 `>` / `cp` / `mv`——别用那些去改书稿。

## 为什么"批注和划线老是闪退"——总根源

**同一份文档有三个写入方，以前只有一个会让路。**

| 写入方 | 写什么 | 2026-09-17 之前 |
|---|---|---|
| 她的浏览器 `/write` | 云端 | 带 `baseUpdatedAt`，版本过期就报冲突 |
| watcher | 云端（整份磁盘内容） | **不带版本号，服务端也只校验 `editor=="web"`，无条件覆盖** |
| Claude Code / 任何本地工具 | 磁盘 | 改完就被 watcher 推上云端 |

于是这条链每天都在发生：她在网页上写 → 云端是最新的 → 别的程序动了磁盘上那份（哪怕只是
一次 `Edit`）→ watcher 把整份磁盘内容盖上去，她刚写的东西在云端没了 → 浏览器下一轮
`refresh()` 发现云端更新，重新加载 → **屏幕上的字凭空消失**。整个过程没有一条报错，
因为在旧规则下这是"正常同步"。

三处都补齐了，缺一处这个循环就会回来：
1. **服务端**（`docs/put`）：只要带了 `baseUpdatedAt` 就校验，不再只认 `editor=="web"`。
2. **watcher**：推送必须带 `baseUpdatedAt`（`state["cloud_at"]`）。**版本号不明就绝不裸推**，
   走 `reconcile()`：先 `docs/get` 取云端，一样就记同步点，不一样就三方合并——共同祖先是
   `~/.writing-watcher-base/` 里上次同步的内容，**ours 传云端**（她正在打字的那边优先），
   合并结果写回磁盘并带新版本号重推，磁盘原版进 `~/.writing-watcher-quarantine/`。
   启动时 `seed_cloud_versions()` 用 `docs/list` 对齐版本号，**只认磁盘与云端 hash 相同的**
   （服务端 `content_hash` 与 watcher 的 `sha` 同算法）。这里踩过坑：一开始无条件认领，
   等于宣称"磁盘这份基于云端最新版"，重启后第一次推送照样盖掉她网页上的内容——
   自检时当场重现，测试 `TestSeedCloudVersions` 钉死了这条。
3. **浏览器**：冲突时三方合并（见下）。

**`merge3` 有两份实现**：`write_page.html`（JS）和 `watcher_client.py`（Python）。
watcher 是单文件分发的（`/writing/watcher.py`），不能 import，所以只能复制。
**改一处必须改另一处**，两边各有测试：`node test_merge3.js`、`python3 -m unittest test_watcher_client`。

## 写入冲突：三方合并，她那边永远不丢

`docs/put` 带 `baseUpdatedAt`，版本过期就返回 `conflict`。触发条件很常见：她在网页上写的
同时，电脑上那份被改了（AI 改稿，或 watcher 把磁盘版推了上去）。

2026-09-17 之前这里只抢救**批注**（`rescueNotesOnConflict`）：拿云端那份当底稿，把她的批注
插回去，其余一律丢弃，提示还写着"已保住你的 N 条批注"，看着像成功。她报的
**"划线闪退"就是这个**——删除线是正文里的 `~~` 标记，不是批注，一合并就没了。备份目录里
第二章/第06集/第08集都留着"删除线数掉到 0、批注数不变"的痕迹。

现在走 `merge3(base, mine, theirs)`（`MERGE3_START`…`MERGE3_END` 之间，纯函数）：
以她上次成功保存的 `current.baseContent` 为共同祖先做**行级三方合并**，按改动块对齐，
两边改不同地方时都保留。同一块两边都改了才算冲突，冲突处**保留她这边**，电脑那版
`backupConflict(.., "电脑")` 另存，控制台 `restoreConflictBackup(0)` 可取回，提示用 err 色。

**改这段一定要跑 `node test_merge3.js`**（13 个测试，含 300 轮随机：她新写的行一行都不能丢）。
测试直接从 HTML 里抽函数源码，不存在两份代码各改各的。

## 现有的两道护栏（别拆）

1. **类名分家**：`.note` 是共同基类（`isNote()` 认它），`.friend-note` 朋友留言，`.my-note` 作者批注。
   消费方一律**正向选择**（`querySelectorAll(".friend-note")`），不要用 `:not()` 排除——
   排除写错会静默误伤。
2. **保存前完整性检查**：`save()` 里比对批注数，比上次落盘时少、又抵不掉 `noteDeletes` 就报警。

   **2026-09-17 大改，别改回去。** 原来这道闸干两件错事：①「用户主动删除」只认三条路
   （两个 × 按钮、批注清空失焦），她**选中一段含批注的正文直接删掉**一条都不算；
   ② 对不上就**中止保存**。合起来就是她说的「删不掉、会卡」——删除永远存不进去、
   刷新后又回来，而且 `dirty` 一直为真，页面从此不再同步，红字提示还会被下一次
   `setSync` 覆盖掉。**挡住她的编辑本身就是一种数据丢失。**

   现在：`noteObserver`（MutationObserver）盯 `#editor` 的 childList，**在 `input` 事件里**
   用 `takeRecords()` 同步结算被摘走的 `.my-note`——只在 input 里结算是关键，留到 `save()`
   再算的话，渲染 bug 摘掉的批注也会被算成她删的，护栏就哑了。按钮式删除走
   `dropNoteByUser()`，自己记账并丢弃记录，避免重复计数。仍然对不上时**照常保存**，
   只是先 `backupConflict(baseContent, "掉批注前·…")` 留底并用 err 色提示。

   三个场景都有浏览器实测（选中删除 / 点 × / 程序摘走），改这段要重跑。

## 分享页的排版必须跟写作页一致

`read_page.html` 的 `.script` / `.para` 抄的是 `write_page.html` 里 `#editor` / `.line` 那套剧本
排版（等宽 Courier Prime、15px、行距 1.6、74ch 字幅、8ch 左右留白、段间距 0、不首行缩进）。
**改了一边必须改另一边**，数值源头是 `write_page.html` 的 `PREFS_TYPO`。
唯一有意不同的一处：`@media (max-width: 720px)` 下分享页把 `--side` 收到 `2ch`——
等宽字 8ch 留白在手机上会把正文压成二十来字一行。读者多半在手机上看。

**分享出去的批注只放行她本人打了 ✓ 的那些**，在服务端筛（`keep_shared_notes`），不是前端——
前端筛的话原文照样过了网线。没打 ✓ 的是她还没改的待办，`@claude` 的是 AI 写的，都不发。
筛完整行删除，不留空行，否则打乱剧本行律。分享页把它们渲染成写作页同一套鎏金卡片
（✓ ＋ `批注 · 时间` ＋ 正文）；**不套用写作页 `.done` 的变灰样式**——那是给她自己看
"还剩哪些没改"的，分享页上本来就只有已处理的，全变灰就只剩一片灰盒子。

## 编辑器里三种"标注"，别搞混

| | 类名 | 谁写的 | 存在哪 | 计字数 |
|---|---|---|---|---|
| 书签 | `.line.marked` | 作者 | 云端 `docs.marks`，按**段落原文**锚定 | — |
| 高亮 | `.hl` | 作者 | 云端 `docs.highlights` | — |
| 作者批注 | `.note.my-note` | 作者 | **正文里的 HTML 注释**，位置即锚点 | 否，前后端各剥一次 |
| 朋友留言 | `.note.friend-note` | 朋友（经分享链接） | 云端 `comments` 集合 | 否 |

批注格式：`<!-- 批注 2026-09-10 14:32 | 内容 -->`，换行存成 `\n`，`-->` 转义成 `--\>`。
剥离规则在两处，**改一处必须改另一处**：前端 `stripNotes()`、后端 `writing_logic.strip_notes()`。

## 部署与验证

- push 到 main 即部署，约 25 秒
- `/write` `/read` 已设 `no-cache`。**在此之前没有缓存头，导致多次"部署了却看不到效果"被误判成代码 bug**
- 验证部署用 `curl -s .../write | grep <新符号>`，但**这只能证明代码上线，证明不了页面表现对**
- **改环境变量后必须点 Railway 顶部的 Deploy。** 改完只是 staged changes，不会自动生效，
  之后的 git push 部署用的也是旧变量。2026-09-16 切 Kimi 官方站就是这样"改了但从没生效"，
  五次验证全被误判。查进程实际读到的配置用 `POST /kimi-diag`（返回 base_host 与 key 的 sha8），
  不要用行为探针推断

## 命名约定（与 watcher 联动）

- `_` 开头的**目录**：完全不同步到云端（`_archive/` `_tools/`）
- `_` 开头的**文件**：同步但**不计写作字数**（`_advisor.md` `_conflicts.md`）
- `CLAUDE.md` / `AGENTS.md` / `README.md`：不同步

## 密钥

全部走环境变量。`main.py` 里那些 `os.environ.get(..., "默认值")` 的默认值是**真实凭证**，
是历史遗留，不要照抄这个模式写新代码。

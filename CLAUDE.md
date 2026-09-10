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

## 现有的两道护栏（别拆）

1. **类名分家**：`.note` 是共同基类（`isNote()` 认它），`.friend-note` 朋友留言，`.my-note` 作者批注。
   消费方一律**正向选择**（`querySelectorAll(".friend-note")`），不要用 `:not()` 排除——
   排除写错会静默误伤。
2. **保存前完整性检查**：`save()` 里比对批注数，比上次落盘时少、又抵不掉 `noteDeletes`
   （× 按钮与清空失焦各记一次）就中止保存。它拦的是同类**还没发生**的 bug。

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

## 命名约定（与 watcher 联动）

- `_` 开头的**目录**：完全不同步到云端（`_archive/` `_tools/`）
- `_` 开头的**文件**：同步但**不计写作字数**（`_advisor.md` `_conflicts.md`）
- `CLAUDE.md` / `AGENTS.md` / `README.md`：不同步

## 密钥

全部走环境变量。`main.py` 里那些 `os.environ.get(..., "默认值")` 的默认值是**真实凭证**，
是历史遗留，不要照抄这个模式写新代码。

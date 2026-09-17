#!/usr/bin/env python3
"""写作进度 · 电脑端同步（通用版，Mac / Windows / Linux）

功能：
- 监控写作文件夹（.md / .txt / .docx），内容变化即推送到云端
- 手机写作页编辑的内容会在 15 秒内写回本地文件（写回前自动备份）
- Word 文档只上行（网页端只读），不会被写回覆盖

用法:
    python3 watcher.py          # 常驻同步
    python3 watcher.py --once   # 同步一轮后退出
    python3 watcher.py --reset  # 重新配置
"""
import hashlib
import json
import re
import sys
import time
import urllib.request
import urllib.error
import zipfile
from datetime import date, datetime
from pathlib import Path

SERVER = "https://web-production-e223e.up.railway.app"
CONFIG_PATH = Path.home() / ".writing-watcher.json"
STATE_PATH = Path.home() / ".writing-watcher-state.json"
BACKUP_DIR = Path.home() / ".writing-watcher-backups"
QUARANTINE_DIR = Path.home() / ".writing-watcher-quarantine"
BASE_DIR = Path.home() / ".writing-watcher-base"      # 每份文件"上次同步时的样子"，冲突时当共同祖先
POLL_SECONDS = 2
PULL_EVERY_SECONDS = 15
IDLE_GAP_SECONDS = 180  # 两次文件变化间隔超过 3 分钟不计入写作时长
HTTP_TIMEOUT = 20
SYNC_EXTENSIONS = (".md", ".txt")      # 双向同步
READONLY_EXTENSIONS = (".docx",)       # 只上行（网页只读）

# 完全不同步的东西：归档、工具目录、Agent 配置文件。服务端用不到它们。
# 注意只看**目录**前缀——根目录下的 _advisor.md / _conflicts.md 仍需同步给顾问，
# 它们不计字数是由后端负责的（见 main.py NO_COUNT）。
SKIP_NAMES = {"CLAUDE.md", "AGENTS.md", "README.md"}


def is_skipped(rel):
    if rel.name in SKIP_NAMES:
        return True
    return any(part.startswith("_") for part in rel.parts[:-1])

XML_TAG_RE = re.compile(r"<[^>]+>")
# 与后端 writing_logic.NOTE_LINE_RE 同一口径。作者批注只存在于正文里，
# 数量只该由她自己在网页上增删——磁盘版本凭空少了批注，就是别的程序拿旧内容覆盖了。
NOTE_LINE_RE = re.compile(r"^[ \t]*<!--\s*批注\s.*?-->[ \t]*$", re.MULTILINE)


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def sha(text):
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def note_count(text):
    return len(NOTE_LINE_RE.findall(text))


# ===== 行级三方合并 =====
# 与前端 write_page.html 的 merge3() 同一套算法，**改一处必须改另一处**。
# 放在这里而不是 import writing_logic：watcher 是单文件分发的（/writing/watcher.py）。
def _lcs_pairs(a, b):
    """a、b 的最长公共子序列，返回配对的行号 [(ai, bi), ...]。"""
    n, m = len(a), len(b)
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        row, nxt = dp[i], dp[i + 1]
        for j in range(m - 1, -1, -1):
            row[j] = nxt[j + 1] + 1 if a[i] == b[j] else max(nxt[j], row[j + 1])
    pairs = []
    i = j = 0
    while i < n and j < m:
        if a[i] == b[j]:
            pairs.append((i, j))
            i += 1
            j += 1
        elif dp[i + 1][j] >= dp[i][j + 1]:
            i += 1
        else:
            j += 1
    return pairs


def _change_blocks(b, o, pairs):
    """一侧相对 base 的改动块：base 的 [b_start, b_end) 被 lines 取代。"""
    out = []
    bi = oi = 0
    for bk, ok in pairs:
        if bk > bi or ok > oi:
            out.append({"b_start": bi, "b_end": bk, "lines": o[oi:ok]})
        bi, oi = bk + 1, ok + 1
    if bi < len(b) or oi < len(o):
        out.append({"b_start": bi, "b_end": len(b), "lines": o[oi:]})
    return out


def _apply_blocks(b, blocks, lo, hi):
    out = []
    p = lo
    for g in blocks:
        out.extend(b[p:g["b_start"]])
        out.extend(g["lines"])
        p = g["b_end"]
    out.extend(b[p:hi])
    return out


def merge3(base, ours, theirs):
    """行级三方合并，返回 (text, conflicts)。同一块两边都改了时保留 ours。

    与前端 write_page.html 的 merge3() 是同一套算法，**改一处必须改另一处**。
    watcher 调用时把云端那份当 ours——她正在网页上打字，那一边优先。
    """
    b, o, t = base.split("\n"), ours.split("\n"), theirs.split("\n")
    ours_blocks = [dict(g, side="o") for g in _change_blocks(b, o, _lcs_pairs(b, o))]
    theirs_blocks = [dict(g, side="t") for g in _change_blocks(b, t, _lcs_pairs(b, t))]
    allb = sorted(ours_blocks + theirs_blocks, key=lambda g: (g["b_start"], g["b_end"]))

    out = []
    bi = k = conflicts = 0
    while k < len(allb):
        lo, hi = allb[k]["b_start"], allb[k]["b_end"]
        group = [allb[k]]
        k2 = k + 1
        while k2 < len(allb) and allb[k2]["b_start"] < hi:
            hi = max(hi, allb[k2]["b_end"])
            group.append(allb[k2])
            k2 += 1
        out.extend(b[bi:lo])
        og = [g for g in group if g["side"] == "o"]
        tg = [g for g in group if g["side"] == "t"]
        if not tg:
            out.extend(_apply_blocks(b, og, lo, hi))
        elif not og:
            out.extend(_apply_blocks(b, tg, lo, hi))
        else:
            orr = _apply_blocks(b, og, lo, hi)
            trr = _apply_blocks(b, tg, lo, hi)
            if orr == trr:
                out.extend(orr)
            elif lo == hi:           # 同一处各自插入，都留着
                out.extend(orr)
                out.extend(trr)
            else:
                out.extend(orr)
                conflicts += 1
        bi, k = hi, k2
    out.extend(b[bi:])
    return "\n".join(out), conflicts


def post(path, payload):
    req = urllib.request.Request(
        f"{SERVER}{path}", data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def read_docx_text(path):
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml").decode("utf-8", errors="ignore")
    return XML_TAG_RE.sub("", xml)


def setup_config():
    print("=== 写作进度 · 首次配置 ===")
    print("支持格式: .md / .txt（双向同步）、.docx（只统计与展示）")
    token = input("1. 粘贴小程序「连接电脑」页里的配对令牌: ").strip()
    raw = input("2. 要监控的文件夹（可多个，用逗号分隔；直接把文件夹拖进来也行）: ")
    folders = [str(Path(p.strip().strip("'\"")).expanduser().resolve())
               for p in raw.split(",") if p.strip()]
    bad = [f for f in folders if not Path(f).is_dir()]
    if not token or not folders or bad:
        print(f"令牌为空或文件夹不存在: {bad}，请重新运行。")
        sys.exit(1)
    cfg = {"token": token, "watch_dirs": folders}
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"配置已保存到 {CONFIG_PATH}\n")
    return cfg


def load_config():
    if "--reset" in sys.argv or not CONFIG_PATH.exists():
        return setup_config()
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    if "watch_dir" in cfg and "watch_dirs" not in cfg:
        cfg["watch_dirs"] = [cfg["watch_dir"]]
    return cfg


def normalize_dirs(cfg):
    """归一化 watch_dirs 为 [(绝对路径, 书名或None)]。

    条目可为字符串（按子文件夹分书，根目录=主书架），
    或 {"path":.., "book":..}（整个目录归为指定的一本书）。
    """
    out = []
    for entry in cfg.get("watch_dirs", []):
        if isinstance(entry, dict):
            out.append((str(Path(entry["path"]).expanduser().resolve()),
                        entry.get("book") or None))
        else:
            out.append((str(Path(entry).expanduser().resolve()), None))
    return out


def resolve_local_path(dirs, name):
    """把云端文件名映射回本地路径（用于写回网页新建/修改的文件）。"""
    for watch_dir, book in dirs:
        if book and (name == book or name.startswith(book + "/")):
            return Path(watch_dir) / name[len(book) + 1:]
    for watch_dir, book in dirs:  # 落到第一个「按子文件夹分书」的目录（主写作目录）
        if not book:
            return Path(watch_dir) / name
    return Path(dirs[0][0]) / name


def load_state():
    state = {"synced_hashes": {}, "since": 0, "note_counts": {}, "cloud_at": {}}
    if STATE_PATH.exists():
        try:
            state.update(json.loads(STATE_PATH.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            pass
    state.setdefault("note_counts", {})  # 旧 state 文件没有这两项
    state.setdefault("cloud_at", {})
    return state


def save_state(state):
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def scan(dirs):
    """dirs = [(路径, 书名或None)]。返回 {name: {...}}，目录不可读时抛 RuntimeError。"""
    result = {}
    for watch_dir, book in dirs:
        try:
            list(Path(watch_dir).iterdir())
        except OSError as e:
            raise RuntimeError(f"无法读取监控目录 {watch_dir}: {e}")
        for p in sorted(Path(watch_dir).rglob("*")):
            ext = p.suffix.lower()
            if not p.is_file() or p.name.startswith("~$") or p.name.startswith("."):
                continue
            rel = p.relative_to(watch_dir)
            if any(part.startswith(".") for part in rel.parts):
                continue  # 跳过隐藏目录（如 .vscode）
            if is_skipped(rel):
                continue  # 跳过配置/AI 产物/归档/工具，不计字数
            if ext in SYNC_EXTENSIONS:
                readonly = False
            elif ext in READONLY_EXTENSIONS:
                readonly = True
            else:
                continue
            try:
                content = read_docx_text(p) if readonly else p.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError, zipfile.BadZipFile, KeyError):
                continue
            # book 指定 → 整个目录归为该书；否则子文件夹=书、根目录=主书架
            name = f"{book}/{rel.as_posix()}" if book else rel.as_posix()
            result[name] = {"path": p, "content": content, "hash": sha(content),
                            "readonly": readonly, "notes": note_count(content)}
    return result


def local_date():
    return date.today().isoformat()


def track_activity(activity, changed_now):
    """打字活动 → 写作时长：变化间隔 ≤ IDLE_GAP 的部分累计为 pending_ms。"""
    if not changed_now:
        return
    now = time.time()
    last = activity["last_ts"]
    if last and now - last <= IDLE_GAP_SECONDS:
        activity["pending_ms"] += int((now - last) * 1000)
    activity["last_ts"] = now


def guard_note_loss(cfg, state, name, f, prev_hashes):
    """本地批注数比上次同步点变少 → 几乎一定是别的程序拿旧内容整文件覆盖了她的稿子。

    这种覆盖一旦推上云端，她在网页上写的东西就真的没了。所以不推：把磁盘上这份
    可疑版本隔离存档，用云端那份把文件恢复回来，下一轮就是干净状态。
    返回 True 表示可以正常推送。
    """
    baseline = state["note_counts"].get(name)
    if f["readonly"] or baseline is None or f["notes"] >= baseline:
        return True
    lost = baseline - f["notes"]
    try:
        data = post("/writing/docs/get", {"token": cfg["token"], "name": name})
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
        log(f"⚠ {name} 少了 {lost} 条批注，取云端版本失败（{e}），本轮不推送")
        return False
    if not data.get("ok"):
        log(f"⚠ {name} 少了 {lost} 条批注，云端取不到（{data.get('error')}），本轮不推送")
        return False
    cloud = data["content"]
    if note_count(cloud) <= f["notes"]:
        state["note_counts"][name] = f["notes"]  # 云端也没有更多批注，是正常删除
        return True
    QUARANTINE_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    kept = QUARANTINE_DIR / f"{stamp}_{name.replace('/', '__')}"
    kept.write_text(f["content"], encoding="utf-8")
    f["path"].write_text(cloud, encoding="utf-8")
    f["content"], f["hash"], f["notes"] = cloud, sha(cloud), note_count(cloud)
    state["synced_hashes"][name] = f["hash"]
    state["note_counts"][name] = f["notes"]
    if prev_hashes is not None:
        prev_hashes[name] = f["hash"]  # 恢复不是打字，别算进写作时长
    log(f"🛡 拦下一次覆盖：{name} 磁盘版本少了 {lost} 条批注，已用云端版本恢复；"
        f"被覆盖的那份存在 {kept}")
    return False


def base_path(name):
    return BASE_DIR / (name.replace("/", "__") + ".base")


def read_base(name):
    try:
        return base_path(name).read_text(encoding="utf-8")
    except OSError:
        return None


def write_base(name, content):
    """记下"这份磁盘内容对应云端哪一版"，供下次冲突时当三方合并的共同祖先。"""
    try:
        BASE_DIR.mkdir(exist_ok=True)
        base_path(name).write_text(content, encoding="utf-8")
    except OSError as e:
        log(f"（基线写入失败 {name}: {e}）")


def sync_point(state, name, f, cloud_at, content=None):
    state["synced_hashes"][name] = f["hash"]
    state["note_counts"][name] = f["notes"]
    if cloud_at:
        state["cloud_at"][name] = cloud_at
        state["since"] = max(state["since"], cloud_at)
    write_base(name, f["content"] if content is None else content)


def merge_conflict(cfg, state, name, f, prev_hashes):
    """云端比我们的基准新 → 不许覆盖，做三方合并。

    共同祖先＝上次同步时的内容；ours＝云端（她正在网页上打字的那一边，优先）；
    theirs＝磁盘。合并结果写回磁盘并推上去，磁盘原样另存进隔离目录。
    """
    fresh = post("/writing/docs/get", {"token": cfg["token"], "name": name})
    if not fresh.get("ok"):
        log(f"⚠ {name} 版本冲突，取云端失败（{fresh.get('error')}），本轮不推")
        return
    cloud = fresh["content"]
    if cloud == f["content"]:                     # 内容本来就一样，只是版本号旧了
        sync_point(state, name, f, fresh.get("updatedAt"))
        return
    # 没有基线就拿磁盘当祖先 → 合并结果等于云端，网页那边优先，磁盘那份进隔离
    base = read_base(name)
    merged, conflicts = merge3(base if base is not None else f["content"], cloud, f["content"])
    QUARANTINE_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    kept = QUARANTINE_DIR / f"{stamp}_{name.replace('/', '__')}"
    kept.write_text(f["content"], encoding="utf-8")
    f["path"].write_text(merged, encoding="utf-8")
    f["content"], f["hash"], f["notes"] = merged, sha(merged), note_count(merged)
    if prev_hashes is not None:
        prev_hashes[name] = f["hash"]             # 合并不是打字，别算进写作时长
    put = post("/writing/docs/put", {
        "token": cfg["token"], "name": name, "content": merged,
        "editor": "computer", "readonly": f["readonly"], "date": local_date(),
        "activeMs": 0, "baseUpdatedAt": fresh.get("updatedAt"),
    })
    if put.get("ok"):
        sync_point(state, name, f, put.get("updatedAt"))
        log(f"⇄ {name} 两边都改了，已合并"
            + (f"（{conflicts} 处冲突以网页为准）" if conflicts else "")
            + f"；磁盘原版留在 {kept.name}")
    else:
        log(f"⇄ {name} 合并后推送又被抢先，下轮重来；磁盘原版留在 {kept.name}")


def seed_cloud_versions(cfg, state):
    """启动时把每份文档的云端版本号取回来。

    没有版本号的推送服务端不校验，等于无条件覆盖——只靠"变化时才记录"的话，
    一直没动过的文件永远没有版本号，哪天被 AI 改一下就又把她网页上的内容盖掉了。
    """
    data = post("/writing/docs/list", {"token": cfg["token"]})
    if not data.get("ok"):
        log(f"（取云端版本号失败：{data.get('error')}，本次启动先不校验版本）")
        return
    n = 0
    for d in data.get("docs", []):
        name, at = d.get("name"), d.get("updatedAt")
        if name and at and state["cloud_at"].get(name) != at:
            state["cloud_at"][name] = at
            n += 1
    log(f"已对上 {len(data.get('docs', []))} 份文档的云端版本号（更新 {n} 份）")


def push_changed(cfg, state, files, activity, prev_hashes=None):
    for name, f in files.items():
        if state["synced_hashes"].get(name) == f["hash"]:
            state["note_counts"].setdefault(name, f["notes"])  # 首轮建立基准
            if name not in state["cloud_at"]:
                continue
            if read_base(name) is None:
                write_base(name, f["content"])
            continue
        if not guard_note_loss(cfg, state, name, f, prev_hashes):
            continue
        data = post("/writing/docs/put", {
            "token": cfg["token"], "name": name, "content": f["content"],
            "editor": "computer", "readonly": f["readonly"], "date": local_date(),
            "activeMs": activity["pending_ms"],
            # 带上"我这份是基于云端哪一版改的"。不带的话服务端不校验，
            # 磁盘上任何一次改动都会无条件盖掉她在网页上刚写的内容。
            "baseUpdatedAt": state["cloud_at"].get(name),
        })
        if data.get("conflict"):
            merge_conflict(cfg, state, name, f, prev_hashes)
            continue
        if data.get("ok"):
            activity["pending_ms"] = 0  # 时长只随第一个成功的推送上报一次
            sync_point(state, name, f, data.get("updatedAt"))
            log(f"↑ 已推送 {name}（今日新增 {data.get('deltaCjk', '?')} 字）")
        else:
            log(f"推送 {name} 被拒: {data.get('error')}")
    save_state(state)


def apply_web_changes(cfg, dirs, state, files, prev_hashes):
    """拉取网页端修改写回本地。写回的文件同步更新 prev_hashes，
    避免下一轮扫描把写回误判成本地打字（虚增写作时长）。"""
    # 曾经同步到过磁盘、现在本地已删除的文件 → 明确通知云端删除
    locally_deleted = [n for n in state["synced_hashes"] if n not in files]
    data = post("/writing/docs/changes", {
        "token": cfg["token"], "since": state["since"],
        "names": list(files.keys()), "deletedNames": locally_deleted,
        "date": local_date(),
    })
    if not data.get("ok"):
        log(f"拉取失败: {data.get('error')}")
        return
    for item in data.get("changed", []):
        name, content = item["name"], item["content"]
        state["since"] = max(state["since"], item.get("updatedAt", 0))
        incoming_hash = sha(content)
        local = files.get(name)
        if local and local["hash"] == incoming_hash:
            sync_point(state, name, local, item.get("updatedAt"))
            continue
        # 本地在离线期间也改过（与上次同步点不一致）→ 以本地为准，跳过写回
        if local and state["synced_hashes"].get(name) not in (None, local["hash"]):
            log(f"⚠ {name} 本地与网页都有修改，保留本地版本（网页版本在云端未丢）")
            continue
        if "" in name.split("/") or ".." in name.split("/") or name.startswith("/"):
            continue  # 防路径穿越
        path = local["path"] if local else resolve_local_path(dirs, name)
        if path.suffix.lower() not in SYNC_EXTENSIONS:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)  # 网页新建的书 → 自动建子文件夹
        if path.exists():
            BACKUP_DIR.mkdir(exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            safe_name = name.replace("/", "__")
            (BACKUP_DIR / f"{stamp}_{safe_name}").write_text(
                path.read_text(encoding="utf-8"), encoding="utf-8")
        path.write_text(content, encoding="utf-8")
        # 写回＝这份磁盘内容就是云端那一版，记下它当下次冲突的共同祖先。
        # 她在网页删批注是合法的，批注基准跟着降。
        sync_point(state, name,
                   {"hash": incoming_hash, "notes": note_count(content), "content": content},
                   item.get("updatedAt"))
        if prev_hashes is not None:
            prev_hashes[name] = incoming_hash
        log(f"↓ 网页修改已写回 {name}（原文件已备份）")
    for name in data.get("removed", []):
        state["synced_hashes"].pop(name, None)
        state["note_counts"].pop(name, None)
        state["cloud_at"].pop(name, None)
        base_path(name).unlink(missing_ok=True)
        log(f"已清理云端残留: {name}")
    save_state(state)


def main():
    cfg = load_config()
    state = load_state()
    once = "--once" in sys.argv
    dirs = normalize_dirs(cfg)
    log("开始同步 " + "、".join(f"{p}" + (f"→《{b}》" if b else "") for p, b in dirs))
    try:
        seed_cloud_versions(cfg, state)
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
        log(f"（取云端版本号失败：{e}，本次启动先不校验版本）")
    last_pull = 0.0
    warned = False
    prev_hashes = None
    activity = {"last_ts": 0.0, "pending_ms": 0}
    while True:
        try:
            files = scan(dirs)
            warned = False
            cur_hashes = {n: f["hash"] for n, f in files.items()}
            track_activity(activity, prev_hashes is not None and cur_hashes != prev_hashes)
            prev_hashes = cur_hashes
            push_changed(cfg, state, files, activity, prev_hashes)
            if time.time() - last_pull > PULL_EVERY_SECONDS or once:
                apply_web_changes(cfg, dirs, state, files, prev_hashes)
                last_pull = time.time()
        except RuntimeError as e:
            if not warned:
                log(f"{e} —— 暂停同步，恢复可读后自动继续")
                warned = True
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
            log(f"网络异常（稍后重试）: {e}")
        if once:
            break
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()

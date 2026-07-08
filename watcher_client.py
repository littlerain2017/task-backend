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
POLL_SECONDS = 2
PULL_EVERY_SECONDS = 15
IDLE_GAP_SECONDS = 180  # 两次文件变化间隔超过 3 分钟不计入写作时长
HTTP_TIMEOUT = 20
SYNC_EXTENSIONS = (".md", ".txt")      # 双向同步
READONLY_EXTENSIONS = (".docx",)       # 只上行（网页只读）

XML_TAG_RE = re.compile(r"<[^>]+>")


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def sha(text):
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


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


def load_state():
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    return {"synced_hashes": {}, "since": 0}


def save_state(state):
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def scan(watch_dirs):
    """返回 {name: {"path", "content", "hash", "readonly"}}，目录不可读时抛 RuntimeError。"""
    result = {}
    for watch_dir in watch_dirs:
        try:
            list(Path(watch_dir).iterdir())
        except OSError as e:
            raise RuntimeError(f"无法读取监控目录 {watch_dir}: {e}")
        for p in sorted(Path(watch_dir).rglob("*")):
            ext = p.suffix.lower()
            if not p.is_file() or p.name.startswith("~$") or p.name.startswith("."):
                continue
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
            result[p.name] = {"path": p, "content": content,
                              "hash": sha(content), "readonly": readonly}
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


def push_changed(cfg, state, files, activity):
    for name, f in files.items():
        if state["synced_hashes"].get(name) == f["hash"]:
            continue
        data = post("/writing/docs/put", {
            "token": cfg["token"], "name": name, "content": f["content"],
            "editor": "computer", "readonly": f["readonly"], "date": local_date(),
            "activeMs": activity["pending_ms"],
        })
        if data.get("ok"):
            activity["pending_ms"] = 0  # 时长只随第一个成功的推送上报一次
            state["synced_hashes"][name] = f["hash"]
            state["since"] = max(state["since"], data.get("updatedAt", 0))
            log(f"↑ 已推送 {name}（今日新增 {data.get('deltaCjk', '?')} 字）")
        else:
            log(f"推送 {name} 被拒: {data.get('error')}")
    save_state(state)


def apply_web_changes(cfg, state, files):
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
            state["synced_hashes"][name] = incoming_hash
            continue
        # 本地在离线期间也改过（与上次同步点不一致）→ 以本地为准，跳过写回
        if local and state["synced_hashes"].get(name) not in (None, local["hash"]):
            log(f"⚠ {name} 本地与网页都有修改，保留本地版本（网页版本在云端未丢）")
            continue
        path = local["path"] if local else Path(cfg["watch_dirs"][0]) / name
        if path.suffix.lower() not in SYNC_EXTENSIONS:
            continue
        if path.exists():
            BACKUP_DIR.mkdir(exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            (BACKUP_DIR / f"{stamp}_{name}").write_text(
                path.read_text(encoding="utf-8"), encoding="utf-8")
        path.write_text(content, encoding="utf-8")
        state["synced_hashes"][name] = incoming_hash
        log(f"↓ 网页修改已写回 {name}（原文件已备份）")
    for name in data.get("removed", []):
        state["synced_hashes"].pop(name, None)
        log(f"已清理云端残留: {name}")
    save_state(state)


def main():
    cfg = load_config()
    state = load_state()
    once = "--once" in sys.argv
    log(f"开始同步 {'、'.join(cfg['watch_dirs'])}")
    last_pull = 0.0
    warned = False
    prev_hashes = None
    activity = {"last_ts": 0.0, "pending_ms": 0}
    while True:
        try:
            files = scan(cfg["watch_dirs"])
            warned = False
            cur_hashes = {n: f["hash"] for n, f in files.items()}
            track_activity(activity, prev_hashes is not None and cur_hashes != prev_hashes)
            prev_hashes = cur_hashes
            push_changed(cfg, state, files, activity)
            if time.time() - last_pull > PULL_EVERY_SECONDS or once:
                apply_web_changes(cfg, state, files)
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

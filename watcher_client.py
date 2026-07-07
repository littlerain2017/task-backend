#!/usr/bin/env python3
"""写作进度 · 电脑端监控（通用版，Mac / Windows / Linux）

首次运行会引导你粘贴小程序里的配对令牌、选择要监控的文件夹，
之后它每 2 秒扫一次 .md 文件，字数一变就同步到你的小程序。

用法:
    python3 watcher.py          # 常驻监控
    python3 watcher.py --once   # 上报一次后退出
    python3 watcher.py --reset  # 重新配置
"""
import json
import re
import sys
import time
import urllib.request
import urllib.error
from datetime import date
from pathlib import Path

SERVER = "https://web-production-e223e.up.railway.app"
CONFIG_PATH = Path.home() / ".writing-watcher.json"
POLL_SECONDS = 2
HEARTBEAT_SECONDS = 30 * 60
HTTP_TIMEOUT = 15

CJK_RE = re.compile(r"[一-鿿]")
EN_WORD_RE = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def setup_config():
    print("=== 写作进度 · 首次配置 ===")
    token = input("1. 粘贴小程序「连接电脑」页里的配对令牌: ").strip()
    folder = input("2. 要监控的文件夹路径（直接把文件夹拖进来也行）: ").strip().strip("'\"")
    folder = str(Path(folder).expanduser().resolve())
    if not token or not Path(folder).is_dir():
        print("令牌为空或文件夹不存在，请重新运行。")
        sys.exit(1)
    cfg = {"token": token, "watch_dir": folder}
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"配置已保存到 {CONFIG_PATH}\n")
    return cfg


def load_config():
    if "--reset" in sys.argv or not CONFIG_PATH.exists():
        return setup_config()
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def scan_dir(watch_dir):
    try:
        list(Path(watch_dir).iterdir())
    except OSError as e:
        raise RuntimeError(f"无法读取监控目录: {e}")
    result = {}
    for p in sorted(Path(watch_dir).rglob("*.md")):
        try:
            text = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        result[p.name] = {
            "cjk": len(CJK_RE.findall(text)),
            "en": len(EN_WORD_RE.findall(text)),
        }
    return result


def report(cfg, counts):
    payload = json.dumps({
        "token": cfg["token"],
        "date": date.today().isoformat(),
        "files": [{"name": n, **c} for n, c in counts.items()],
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{SERVER}/writing/report", data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if not data.get("ok"):
        raise RuntimeError(f"服务器拒绝: {data}")
    return data


def main():
    cfg = load_config()
    once = "--once" in sys.argv
    log(f"开始监控 {cfg['watch_dir']}")
    last_counts = None
    last_report_at = 0.0
    while True:
        try:
            counts = scan_dir(cfg["watch_dir"])
        except RuntimeError as e:
            log(str(e))
            counts = None
        if counts is not None and not counts and last_counts:
            counts = None  # 目录异常清空，跳过

        heartbeat_due = time.time() - last_report_at > HEARTBEAT_SECONDS
        if counts is not None and (counts != last_counts or heartbeat_due):
            try:
                data = report(cfg, counts)
                last_counts = counts
                last_report_at = time.time()
                log(f"已同步 今日新增 {data.get('deltaCjk', '?')} 字，{len(counts)} 个文件")
            except (urllib.error.URLError, RuntimeError, OSError) as e:
                log(f"同步失败（稍后重试）: {e}")
        if once:
            break
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()

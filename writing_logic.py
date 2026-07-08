"""写作进度：纯逻辑（字数统计、每日基线与新增计算），与框架无关，便于单测。"""
import re

CJK_RE = re.compile(r"[一-鿿]")
EN_WORD_RE = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")


def count_text(text):
    """返回 (中文字符数, 英文单词数)，与各客户端口径一致。"""
    return len(CJK_RE.findall(text)), len(EN_WORD_RE.findall(text))


def build_daily(uid, date_id, counts, existing_daily, now_ms):
    """根据当前上报与已有当日记录，生成新的 daily 文档。

    基线规则：当天第一次上报时记录基线；之后基线不变，
    新出现的文件基线视为 0（当天新建的文件）。
    用户在小程序里校准过基线的话，existing_daily 里就是校准后的值，照常沿用。
    """
    total_cjk = sum(c["cjk"] for c in counts.values())
    if existing_daily is None:
        baseline_cjk = total_cjk
        base_per_file = [{"name": n, "cjk": c["cjk"]} for n, c in counts.items()]
    else:
        baseline_cjk = existing_daily["baselineCjk"]
        base_per_file = existing_daily["basePerFile"]

    base_map = {b["name"]: b["cjk"] for b in base_per_file}
    per_file = [
        {"name": n, "cjk": c["cjk"], "en": c["en"], "delta": c["cjk"] - base_map.get(n, 0)}
        for n, c in counts.items()
    ]
    return {
        "uid": uid,
        "date": date_id,
        "baselineCjk": baseline_cjk,
        "basePerFile": base_per_file,
        "currentCjk": total_cjk,
        "deltaCjk": total_cjk - baseline_cjk,
        "perFile": per_file,
        "updatedAt": now_ms,
    }


def aggregate_file_docs(docs):
    """把一个用户全部来源（电脑/网页）的 files 文档聚合成 {name: {cjk, en}}。

    不同来源理论上文件名不同（网页文档带「网页·」前缀）；若真撞名，后者覆盖前者。
    """
    counts = {}
    for d in docs:
        counts[d["name"]] = {"cjk": d["cjk"], "en": d["en"]}
    return counts


def normalize_files(raw_files):
    """校验并归一化客户端上报的文件列表 → {name: {cjk, en}}。

    非法条目直接拒绝（抛 ValueError），不静默丢弃。
    """
    if not isinstance(raw_files, list) or len(raw_files) > 200:
        raise ValueError("files 必须是不超过 200 项的列表")
    counts = {}
    for item in raw_files:
        name = item.get("name")
        cjk = item.get("cjk")
        en = item.get("en")
        if not isinstance(name, str) or not (0 < len(name) <= 200):
            raise ValueError(f"非法文件名: {name!r}")
        if not isinstance(cjk, int) or not isinstance(en, int) or cjk < 0 or en < 0:
            raise ValueError(f"非法字数: {name} cjk={cjk!r} en={en!r}")
        counts[name] = {"cjk": cjk, "en": en}
    return counts

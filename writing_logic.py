"""写作进度：纯逻辑（字数统计、每日基线与新增计算），与框架无关，便于单测。"""
import re

CJK_RE = re.compile(r"[一-鿿]")
EN_WORD_RE = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")


# 作者批注：正文里形如 <!-- 批注 2026-09-10 14:32 | ... --> 的整行，不计字数。
# 前端 stripNotes() 用的是同一套规则，两边口径必须一致。
NOTE_LINE_RE = re.compile(r"^[ \t]*<!--\s*批注\s.*?-->[ \t]*$", re.MULTILINE)


def strip_notes(text):
    return NOTE_LINE_RE.sub("", text)


# 连行尾换行一起删。统计不在乎多一个空行，分享页在乎——
# 剧本排版里空行是有意义的停顿，留下来会打乱整章的行律。
NOTE_WHOLE_LINE_RE = re.compile(r"^[ \t]*<!--\s*批注\s.*?-->[ \t]*\n?", re.MULTILINE)


def drop_note_lines(text):
    return NOTE_WHOLE_LINE_RE.sub("", text)


def count_text(text):
    """返回 (中文字符数, 英文单词数)，与各客户端口径一致。批注不计入。"""
    t = strip_notes(text)
    return len(CJK_RE.findall(t)), len(EN_WORD_RE.findall(t))


# 章节名按字面排会乱序：中文数字的码点顺序是 一(4E00) < 三(4E09) < 二(4E8C) < 四(56DB)，
# 「第三章」于是排到第一章和第二章中间，看列表的人会以为它根本没同步上来。
# 只解析「第…」后面的中文数字——《百年孤独》这类书名里的「百」不该被当成 100。
CJK_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "三": 3, "四": 4,
              "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "两": 2}
CJK_UNITS = {"十": 10, "百": 100, "千": 1000}
CJK_CHAPTER_RE = re.compile(r"第([零〇一二三四五六七八九十百千两]+)")
DIGITS_RE = re.compile(r"\d+")


def cjk_to_int(s):
    """「十七」→17，「二十三」→23，「一百零八」→108。出现非数字字符返回 None。"""
    total = section = 0
    for ch in s:
        if ch in CJK_DIGITS:
            section = CJK_DIGITS[ch]
        elif ch in CJK_UNITS:
            section = section or 1  # 「十七」省略了开头那个「一」
            total += section * CJK_UNITS[ch]
            section = 0
        else:
            return None
    return total + section


def doc_sort_key(name):
    """文件名排序键：中文章节号转成阿拉伯数字，所有数字补零到 6 位再按字面比较。

    「第三章.md」→「第000003章.md」，于是它落在第二章和第四章之间；
    「第05集.md」这类本来就用阿拉伯数字的也一并对齐，两位数不再排到个位数前面。
    """
    def to_arabic(m):
        n = cjk_to_int(m.group(1))
        return m.group(0) if n is None else "第%d" % n

    return DIGITS_RE.sub(lambda m: m.group(0).zfill(6),
                         CJK_CHAPTER_RE.sub(to_arabic, name))


def build_daily(uid, date_id, counts, existing_daily, now_ms, active_ms_add=0, prev_daily=None):
    """根据当前上报与已有当日记录，生成新的 daily 文档。

    基线规则：当天第一次上报时记录基线；之后基线不变，
    新出现的文件基线视为 0（当天新建的文件）。
    用户在小程序里校准过基线的话，existing_daily 里就是校准后的值，照常沿用。
    active_ms_add：本次上报新增的实际写作时长（毫秒），累加到当日。

    字数单位 = 汉字数 + 英文单词数（中英写作等权计入进度）。
    注意：daily 文档里的 baselineCjk/currentCjk/deltaCjk 与 basePerFile 的 cjk
    字段名保留历史命名，实际存的都是该单位。
    """
    def units(c):
        return c["cjk"] + c["en"]

    total_units = sum(units(c) for c in counts.values())
    if existing_daily is not None:
        baseline_units = existing_daily["baselineCjk"]
        base_per_file = existing_daily["basePerFile"]
    elif prev_daily is not None:
        # 当天首次上报：基线继承上一个写作日收笔时的总数——
        # 即使服务器宕机/电脑晚开机，之前写的字也不会被吞进基线
        baseline_units = prev_daily.get("currentCjk", total_units)
        base_per_file = [{"name": p["name"], "cjk": p.get("cjk", 0) + p.get("en", 0)}
                         for p in prev_daily.get("perFile", [])]
    else:
        baseline_units = total_units
        base_per_file = [{"name": n, "cjk": units(c)} for n, c in counts.items()]

    base_map = {b["name"]: b["cjk"] for b in base_per_file}
    per_file = [
        {"name": n, "cjk": c["cjk"], "en": c["en"], "delta": units(c) - base_map.get(n, 0)}
        for n, c in counts.items()
    ]
    prev_active = existing_daily.get("activeMs", 0) if existing_daily else 0
    return {
        "uid": uid,
        "date": date_id,
        "baselineCjk": baseline_units,
        "basePerFile": base_per_file,
        "currentCjk": total_units,
        "deltaCjk": total_units - baseline_units,
        "perFile": per_file,
        "activeMs": prev_active + max(0, active_ms_add),
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

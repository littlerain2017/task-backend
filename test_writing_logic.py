"""writing_logic 单元测试。"""
import unittest

from writing_logic import (aggregate_file_docs, build_daily, cjk_to_int, count_text,
                           doc_sort_key, keep_shared_notes)


class TestKeepSharedNotes(unittest.TestCase):
    """分享页只发作者本人打了 ✓ 的批注：待办和 AI 批注留在服务端。"""

    def test_keeps_author_done_note(self):
        text = "正文\n<!-- 批注 ✓ 2026-09-11 09:00 | 已按这个改了 -->\n尾\n"
        self.assertEqual(keep_shared_notes(text), text)

    def test_drops_undone_note_without_leaving_blank(self):
        text = "第一段\n<!-- 批注 2026-09-10 14:32 | 这段重写 -->\n第二段\n"
        self.assertEqual(keep_shared_notes(text), "第一段\n第二段\n")

    def test_drops_ai_note_even_when_done(self):
        """AI 写的批注 @claude 排在 ✓ 前面，不是她写的，不发给读者。"""
        text = "正文\n<!-- 批注 @claude ✓ 2026-09-11 09:00 | AI 的意见 -->\n尾\n"
        self.assertEqual(keep_shared_notes(text), "正文\n尾\n")

    def test_keeps_done_note_with_thread_id(self):
        text = "正文\n<!-- 批注 ✓ #a1b2 2026-09-11 09:00 | 回复 -->\n尾\n"
        self.assertEqual(keep_shared_notes(text), text)

    def test_undone_note_on_last_line(self):
        self.assertEqual(keep_shared_notes("正文\n<!-- 批注 2026-09-10 14:32 | x -->"), "正文\n")

    def test_keeps_author_blank_lines(self):
        """空行是剧本排版里的停顿，只删批注那一行。"""
        text = "第一段\n\n<!-- 批注 2026-09-10 14:32 | x -->\n\n第二段"
        self.assertEqual(keep_shared_notes(text), "第一段\n\n\n第二段")

    def test_leaves_ordinary_html_comments(self):
        text = "正文\n<!-- 普通注释 -->\n尾"
        self.assertEqual(keep_shared_notes(text), text)


class TestCountText(unittest.TestCase):
    def test_counts(self):
        self.assertEqual(count_text("她说 OK it's fine。"), (2, 3))

    def test_empty(self):
        self.assertEqual(count_text(""), (0, 0))


class TestAggregateFileDocs(unittest.TestCase):
    def test_merges_sources(self):
        docs = [
            {"name": "chapter1.md", "cjk": 100, "en": 5, "source": "computer"},
            {"name": "网页·灵感", "cjk": 30, "en": 0, "source": "web"},
        ]
        counts = aggregate_file_docs(docs)
        self.assertEqual(counts, {
            "chapter1.md": {"cjk": 100, "en": 5},
            "网页·灵感": {"cjk": 30, "en": 0},
        })

    def test_empty(self):
        self.assertEqual(aggregate_file_docs([]), {})


class TestBuildDaily(unittest.TestCase):
    def test_first_report_sets_baseline_with_en_units(self):
        counts = {"a.md": {"cjk": 100, "en": 5}}
        d = build_daily("u1", "2026-07-07", counts, None, 1000)
        self.assertEqual(d["baselineCjk"], 105)  # 汉字 + 英文单词
        self.assertEqual(d["deltaCjk"], 0)
        self.assertEqual(d["uid"], "u1")
        self.assertEqual(d["date"], "2026-07-07")

    def test_later_report_keeps_baseline(self):
        first = build_daily("u1", "2026-07-07", {"a.md": {"cjk": 100, "en": 0}}, None, 1)
        d = build_daily("u1", "2026-07-07", {"a.md": {"cjk": 130, "en": 0}}, first, 2)
        self.assertEqual(d["deltaCjk"], 30)

    def test_first_report_inherits_prev_day_baseline(self):
        # 昨天收笔 100；今早离线写到 130，首次上报不应吞掉这 30
        prev = build_daily("u1", "2026-07-16", {"a.md": {"cjk": 100, "en": 0}}, None, 1)
        d = build_daily("u1", "2026-07-17", {"a.md": {"cjk": 130, "en": 0}}, None, 2, prev_daily=prev)
        self.assertEqual(d["baselineCjk"], 100)
        self.assertEqual(d["deltaCjk"], 30)
        self.assertEqual(d["perFile"][0]["delta"], 30)

    def test_prev_day_baseline_converts_perfile_units(self):
        prev = build_daily("u1", "2026-07-16", {"a.md": {"cjk": 100, "en": 50}}, None, 1)
        d = build_daily("u1", "2026-07-17", {"a.md": {"cjk": 110, "en": 55}}, None, 2, prev_daily=prev)
        self.assertEqual(d["deltaCjk"], 15)
        self.assertEqual(d["perFile"][0]["delta"], 15)

    def test_existing_daily_wins_over_prev(self):
        prev = build_daily("u1", "2026-07-16", {"a.md": {"cjk": 100, "en": 0}}, None, 1)
        existing = build_daily("u1", "2026-07-17", {"a.md": {"cjk": 120, "en": 0}}, None, 2, prev_daily=prev)
        d = build_daily("u1", "2026-07-17", {"a.md": {"cjk": 125, "en": 0}}, existing, 3, prev_daily=None)
        self.assertEqual(d["baselineCjk"], 100)
        self.assertEqual(d["deltaCjk"], 25)

    def test_english_words_count_into_delta(self):
        first = build_daily("u1", "2026-07-07", {"a.md": {"cjk": 100, "en": 50}}, None, 1)
        d = build_daily("u1", "2026-07-07", {"a.md": {"cjk": 100, "en": 80}}, first, 2)
        self.assertEqual(d["deltaCjk"], 30)  # 只写英文也计入
        self.assertEqual(d["perFile"][0]["delta"], 30)

    def test_calibrated_baseline_respected(self):
        calibrated = {"baselineCjk": 50, "basePerFile": [{"name": "a.md", "cjk": 50}]}
        d = build_daily("u1", "2026-07-07", {"a.md": {"cjk": 130, "en": 0}}, calibrated, 2)
        self.assertEqual(d["deltaCjk"], 80)

    def test_active_ms_accumulates(self):
        first = build_daily("u1", "2026-07-08", {"a.md": {"cjk": 1, "en": 0}}, None, 1, active_ms_add=60000)
        self.assertEqual(first["activeMs"], 60000)
        second = build_daily("u1", "2026-07-08", {"a.md": {"cjk": 2, "en": 0}}, first, 2, active_ms_add=30000)
        self.assertEqual(second["activeMs"], 90000)

    def test_active_ms_negative_ignored(self):
        d = build_daily("u1", "2026-07-08", {"a.md": {"cjk": 1, "en": 0}}, None, 1, active_ms_add=-500)
        self.assertEqual(d["activeMs"], 0)

    def test_new_file_midday_counts_from_zero(self):
        first = build_daily("u1", "2026-07-07", {"a.md": {"cjk": 100, "en": 0}}, None, 1)
        d = build_daily("u1", "2026-07-07",
                        {"a.md": {"cjk": 100, "en": 0}, "b.md": {"cjk": 40, "en": 0}}, first, 2)
        self.assertEqual(d["deltaCjk"], 40)


class TestCjkToInt(unittest.TestCase):
    def test_single_digits(self):
        self.assertEqual(cjk_to_int("三"), 3)
        self.assertEqual(cjk_to_int("九"), 9)

    def test_tens_with_implied_one(self):
        self.assertEqual(cjk_to_int("十"), 10)
        self.assertEqual(cjk_to_int("十七"), 17)

    def test_compound(self):
        self.assertEqual(cjk_to_int("二十三"), 23)
        self.assertEqual(cjk_to_int("三十"), 30)
        self.assertEqual(cjk_to_int("一百零八"), 108)
        self.assertEqual(cjk_to_int("一百二十三"), 123)

    def test_non_numeric_returns_none(self):
        self.assertIsNone(cjk_to_int("篇"))
        self.assertIsNone(cjk_to_int("三章"))


class TestDocSortKey(unittest.TestCase):
    def test_third_chapter_lands_between_second_and_fourth(self):
        """码点顺序是 一 < 三 < 二 < 四，第三章曾因此排到第一章和第二章中间。"""
        names = ["结婚员绑定系统/第一章.md", "结婚员绑定系统/第二章.md",
                 "结婚员绑定系统/第二章_v2.md", "结婚员绑定系统/第三章.md",
                 "结婚员绑定系统/第四章.md"]
        self.assertEqual([n.split("/")[1] for n in sorted(names, key=doc_sort_key)],
                         ["第一章.md", "第二章.md", "第二章_v2.md", "第三章.md", "第四章.md"])

    def test_double_digit_chapter_after_single(self):
        names = ["第九章.md", "第十章.md", "第十一章.md", "第二十章.md"]
        self.assertEqual(sorted(names, key=doc_sort_key),
                         ["第九章.md", "第十章.md", "第十一章.md", "第二十章.md"])

    def test_arabic_numerals_zero_padded(self):
        names = ["营救麦克黄/第5集.md", "营救麦克黄/第10集.md", "营救麦克黄/第07集.md"]
        self.assertEqual([n.split("/")[1] for n in sorted(names, key=doc_sort_key)],
                         ["第5集.md", "第07集.md", "第10集.md"])

    def test_book_title_digits_not_parsed(self):
        """《百年孤独》里的「百」不在「第」后面，不该被当成 100。"""
        self.assertEqual(doc_sort_key("百年孤独.md"), "百年孤独.md")

    def test_name_without_numbers_unchanged(self):
        self.assertEqual(doc_sort_key("新篇章.md"), "新篇章.md")


if __name__ == "__main__":
    unittest.main()

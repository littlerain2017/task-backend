"""writing_logic 单元测试。"""
import unittest

from writing_logic import aggregate_file_docs, build_daily, count_text, normalize_files


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


class TestNormalizeFiles(unittest.TestCase):
    def test_valid(self):
        counts = normalize_files([{"name": "a.md", "cjk": 1, "en": 2}])
        self.assertEqual(counts, {"a.md": {"cjk": 1, "en": 2}})

    def test_rejects_negative(self):
        with self.assertRaises(ValueError):
            normalize_files([{"name": "a.md", "cjk": -1, "en": 0}])

    def test_rejects_missing_name(self):
        with self.assertRaises(ValueError):
            normalize_files([{"cjk": 1, "en": 0}])

    def test_rejects_non_list(self):
        with self.assertRaises(ValueError):
            normalize_files({"name": "a.md"})


if __name__ == "__main__":
    unittest.main()

"""writing_logic 单元测试。"""
import unittest

from writing_logic import aggregate_file_docs, build_daily, normalize_files


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
    def test_first_report_sets_baseline(self):
        counts = {"a.md": {"cjk": 100, "en": 5}}
        d = build_daily("u1", "2026-07-07", counts, None, 1000)
        self.assertEqual(d["baselineCjk"], 100)
        self.assertEqual(d["deltaCjk"], 0)
        self.assertEqual(d["uid"], "u1")
        self.assertEqual(d["date"], "2026-07-07")

    def test_later_report_keeps_baseline(self):
        first = build_daily("u1", "2026-07-07", {"a.md": {"cjk": 100, "en": 0}}, None, 1)
        d = build_daily("u1", "2026-07-07", {"a.md": {"cjk": 130, "en": 0}}, first, 2)
        self.assertEqual(d["deltaCjk"], 30)

    def test_calibrated_baseline_respected(self):
        calibrated = {"baselineCjk": 50, "basePerFile": [{"name": "a.md", "cjk": 50}]}
        d = build_daily("u1", "2026-07-07", {"a.md": {"cjk": 130, "en": 0}}, calibrated, 2)
        self.assertEqual(d["deltaCjk"], 80)

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

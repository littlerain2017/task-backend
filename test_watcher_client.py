"""watcher_client（内容同步版）文件扫描测试。"""
import tempfile
import unittest
import zipfile
from pathlib import Path

from watcher_client import scan, read_docx_text, sha


def make_docx(path, text):
    doc_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>"
    )
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", doc_xml)


class TestScan(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)

    def tearDown(self):
        self.dir.cleanup()

    def test_sync_and_readonly_flags(self):
        (self.root / "a.md").write_text("正文", encoding="utf-8")
        (self.root / "b.txt").write_text("笔记", encoding="utf-8")
        make_docx(self.root / "c.docx", "文档")
        files = scan([(str(self.root), None)])
        self.assertEqual(set(files), {"a.md", "b.txt", "c.docx"})
        self.assertFalse(files["a.md"]["readonly"])
        self.assertFalse(files["b.txt"]["readonly"])
        self.assertTrue(files["c.docx"]["readonly"])
        self.assertEqual(files["a.md"]["content"], "正文")

    def test_hash_stable(self):
        (self.root / "a.md").write_text("同样内容", encoding="utf-8")
        files = scan([(str(self.root), None)])
        self.assertEqual(files["a.md"]["hash"], sha("同样内容"))

    def test_skips_lock_hidden_and_unsupported(self):
        make_docx(self.root / "novel.docx", "正文")
        (self.root / "~$novel.docx").write_bytes(b"lock")
        (self.root / ".hidden.md").write_text("隐藏", encoding="utf-8")
        (self.root / "img.png").write_bytes(b"\x89PNG")
        files = scan([(str(self.root), None)])
        self.assertEqual(set(files), {"novel.docx"})

    def test_corrupt_docx_skipped(self):
        (self.root / "bad.docx").write_bytes(b"not a zip")
        (self.root / "ok.md").write_text("好", encoding="utf-8")
        files = scan([(str(self.root), None)])
        self.assertEqual(set(files), {"ok.md"})

    def test_docx_text_extraction(self):
        make_docx(self.root / "d.docx", "第一章 hello")
        self.assertIn("第一章 hello", read_docx_text(self.root / "d.docx"))

    def test_book_mapping_prefixes_name(self):
        (self.root / "第一章.md").write_text("正文", encoding="utf-8")
        files = scan([(str(self.root), "我的书")])
        self.assertEqual(set(files), {"我的书/第一章.md"})

    def test_resolve_local_path(self):
        from watcher_client import resolve_local_path
        dirs = [("/a/TheRoom", None), ("/b/novel", "我的书")]
        # 归属指定书 → 剥掉书名前缀映射到该目录
        self.assertEqual(str(resolve_local_path(dirs, "我的书/第二章.md")), "/b/novel/第二章.md")
        # 其余 → 主写作目录（含子文件夹路径）
        self.assertEqual(str(resolve_local_path(dirs, "diary/x.md")), "/a/TheRoom/diary/x.md")
        self.assertEqual(str(resolve_local_path(dirs, "05.md")), "/a/TheRoom/05.md")


class TestNoteLossGuard(unittest.TestCase):
    """批注护栏：磁盘版本凭空少批注 = 有程序拿旧内容覆盖了稿子，不许推上云端。"""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)
        self.calls = []

    def tearDown(self):
        self.dir.cleanup()

    def fake_post(self, cloud_content, ok=True):
        def _post(path, payload):
            self.calls.append((path, payload))
            if ok:
                return {"ok": True, "content": cloud_content, "updatedAt": 1}
            return {"ok": False, "error": "文件不存在"}
        return _post

    def run_guard(self, disk, cloud, baseline, cloud_ok=True, quarantine=None):
        import watcher_client as wc
        path = self.root / "第01集.md"
        path.write_text(disk, encoding="utf-8")
        f = {"path": path, "content": disk, "hash": wc.sha(disk),
             "readonly": False, "notes": wc.note_count(disk)}
        state = {"synced_hashes": {}, "since": 0, "note_counts": {"第01集.md": baseline}}
        prev_hashes = {"第01集.md": f["hash"]}
        orig_post, orig_q = wc.post, wc.QUARANTINE_DIR
        wc.post = self.fake_post(cloud, ok=cloud_ok)
        wc.QUARANTINE_DIR = quarantine or (self.root / "q")
        try:
            allowed = wc.guard_note_loss({"token": "T"}, state, "第01集.md", f, prev_hashes)
        finally:
            wc.post, wc.QUARANTINE_DIR = orig_post, orig_q
        return allowed, path, state, f, prev_hashes

    def test_note_count_matches_backend_format(self):
        from watcher_client import note_count
        text = ("正文一\n<!-- 批注 2026-09-10 13:14 | 这里改 -->\n正文二\n"
                "<!-- 批注 ✓ 2026-09-11 09:00 | 已改 -->\n")
        self.assertEqual(note_count(text), 2)
        self.assertEqual(note_count("没有批注\n<!-- 普通注释 -->"), 0)

    def test_blocks_push_and_restores_from_cloud(self):
        cloud = "正文\n<!-- 批注 2026-09-10 13:14 | 她写的 -->\n尾巴\n"
        disk = "被覆盖的正文\n"
        allowed, path, state, f, prev = self.run_guard(disk, cloud, baseline=1)
        self.assertFalse(allowed)                                   # 不推
        self.assertEqual(path.read_text(encoding="utf-8"), cloud)   # 磁盘已恢复
        self.assertEqual(state["note_counts"]["第01集.md"], 1)
        self.assertEqual(prev["第01集.md"], f["hash"])              # 不虚增写作时长
        kept = list((self.root / "q").iterdir())
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0].read_text(encoding="utf-8"), disk)  # 覆盖那份留档

    def test_allows_push_when_notes_intact(self):
        text = "正文\n<!-- 批注 2026-09-10 13:14 | 在 -->\n新写的一段\n"
        allowed, path, _, _, _ = self.run_guard(text, "无所谓", baseline=1)
        self.assertTrue(allowed)
        self.assertEqual(path.read_text(encoding="utf-8"), text)  # 没动磁盘
        self.assertEqual(self.calls, [])                          # 没白跑一次网络

    def test_allows_when_cloud_has_no_more_notes(self):
        """云端也没有更多批注 → 是她自己删的，放行。"""
        allowed, path, state, _, _ = self.run_guard("干净正文\n", "云端也干净\n", baseline=2)
        self.assertTrue(allowed)
        self.assertEqual(path.read_text(encoding="utf-8"), "干净正文\n")
        self.assertEqual(state["note_counts"]["第01集.md"], 0)

    def test_cloud_unavailable_blocks_push_without_touching_disk(self):
        allowed, path, _, _, _ = self.run_guard("被覆盖\n", "", baseline=3, cloud_ok=False)
        self.assertFalse(allowed)
        self.assertEqual(path.read_text(encoding="utf-8"), "被覆盖\n")
        self.assertFalse((self.root / "q").exists())

    def test_no_baseline_means_no_guard(self):
        import watcher_client as wc
        f = {"path": self.root / "x.md", "content": "", "hash": "h",
             "readonly": False, "notes": 0}
        state = {"synced_hashes": {}, "since": 0, "note_counts": {}}
        self.assertTrue(wc.guard_note_loss({"token": "T"}, state, "x.md", f, None))


if __name__ == "__main__":
    unittest.main()

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


if __name__ == "__main__":
    unittest.main()

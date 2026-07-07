"""watcher_client 文件解析测试。"""
import tempfile
import unittest
import zipfile
from pathlib import Path

from watcher_client import read_file_text, scan_dirs, CJK_RE, EN_WORD_RE


def make_docx(path, text):
    """构造一个最小可用的 .docx。"""
    doc_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>"
    )
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", doc_xml)


class TestFileParsing(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)

    def tearDown(self):
        self.dir.cleanup()

    def test_docx_text_extracted(self):
        p = self.root / "novel.docx"
        make_docx(p, "第一章 hello world 开头")
        text = read_file_text(p)
        self.assertEqual(len(CJK_RE.findall(text)), 5)
        self.assertEqual(len(EN_WORD_RE.findall(text)), 2)

    def test_scan_mixed_formats(self):
        (self.root / "a.md").write_text("三个字呀", encoding="utf-8")
        (self.root / "b.txt").write_text("hello 你好", encoding="utf-8")
        make_docx(self.root / "c.docx", "文档")
        (self.root / "skip.pdf").write_bytes(b"%PDF")
        counts = scan_dirs([str(self.root)])
        self.assertEqual(set(counts), {"a.md", "b.txt", "c.docx"})
        self.assertEqual(counts["a.md"]["cjk"], 4)
        self.assertEqual(counts["b.txt"]["en"], 1)
        self.assertEqual(counts["c.docx"]["cjk"], 2)

    def test_word_lock_file_skipped(self):
        make_docx(self.root / "novel.docx", "正文")
        (self.root / "~$novel.docx").write_bytes(b"lock")
        counts = scan_dirs([str(self.root)])
        self.assertEqual(set(counts), {"novel.docx"})

    def test_corrupt_docx_skipped(self):
        (self.root / "bad.docx").write_bytes(b"not a zip")
        (self.root / "ok.md").write_text("好", encoding="utf-8")
        counts = scan_dirs([str(self.root)])
        self.assertEqual(set(counts), {"ok.md"})


if __name__ == "__main__":
    unittest.main()

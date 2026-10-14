"""Test suite for competitor-watch. Zero dependencies — runs with either:

    python3 -m unittest discover -s tests
    pytest tests/

Everything runs offline: sources are local files served over file:// URLs,
so no network is touched.
"""
import importlib.util
import json
import os
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

_spec = importlib.util.spec_from_file_location("cw_watch", os.path.join(REPO, "scripts", "watch.py"))
watch = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(watch)

watch.REQUEST_DELAY = 0  # don't sleep during tests


def write(tmp, name, content):
    path = os.path.join(tmp, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


def file_url(path):
    return "file://" + path.replace("\\", "/")


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


RSS = """<?xml version="1.0"?>
<rss version="2.0"><channel>
  <item><title>v1.2 released</title><link>https://ex.dev/v1.2</link>
    <guid>ex-1.2</guid><pubDate>Mon, 13 Oct 2026 09:00:00 GMT</pubDate></item>
  <item><title>v1.1 released</title><link>https://ex.dev/v1.1</link>
    <guid>ex-1.1</guid><pubDate>Sun, 12 Oct 2026 09:00:00 GMT</pubDate></item>
</channel></rss>
"""


class FetchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_page_fingerprint_changes_with_content(self):
        p = write(self.tmp, "c.html", "<h1>Changelog</h1><p>v1.0</p>")
        fp1, text1, items1 = watch.fetch_page({"url": file_url(p)})
        self.assertTrue(text1)
        self.assertEqual(items1, [])
        write(self.tmp, "c.html", "<h1>Changelog</h1><p>v1.1</p>")
        fp2, _, _ = watch.fetch_page({"url": file_url(p)})
        self.assertNotEqual(fp1, fp2)

    def test_page_ignores_script_content(self):
        p = write(self.tmp, "c.html", "<h1>Hi</h1><script>var x=1;</script>")
        _, text, _ = watch.fetch_page({"url": file_url(p)})
        self.assertIn("Hi", text)
        self.assertNotIn("var x", text)

    def test_json_source(self):
        p = write(self.tmp, "s.json", '{"version":"1.0","items":[1,2]}')
        fp, text, items = watch.fetch_json({"url": file_url(p)})
        self.assertTrue(fp)
        self.assertIn('"version": "1.0"', text)
        self.assertEqual(items, [])

    def test_rss_source(self):
        p = write(self.tmp, "feed.xml", RSS)
        fp, text, items = watch.fetch_rss({"url": file_url(p)})
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["id"], "ex-1.2")
        self.assertEqual(items[0]["date"], "2026-10-13")
        self.assertTrue(fp)


class DiffTests(unittest.TestCase):
    def test_baseline_when_no_prev(self):
        items = [{"id": "a", "title": "A", "date": "", "url": ""}]
        res = watch.diff_summary("x", None, "fp", "text", items)
        self.assertEqual(res["status"], "BASELINE")
        self.assertEqual(len(res["new_items"]), 1)

    def test_new_items(self):
        prev = {"item_ids": ["a"], "fingerprint": "fp"}
        items = [{"id": "a", "title": "A", "date": "", "url": ""},
                 {"id": "b", "title": "B", "date": "", "url": ""}]
        res = watch.diff_summary("x", prev, "fp2", "text", items)
        self.assertEqual(res["status"], "NEW ITEMS")
        self.assertEqual([i["id"] for i in res["new_items"]], ["b"])

    def test_changed_when_same_ids_but_content_differs(self):
        prev = {"item_ids": [], "fingerprint": "old", "text": "line one\nline two"}
        res = watch.diff_summary("x", prev, "new", "line one\nline TWO", [])
        self.assertEqual(res["status"], "CHANGED")
        self.assertIn("line TWO", res["diff"])

    def test_unchanged(self):
        prev = {"item_ids": ["a"], "fingerprint": "same", "text": "t"}
        items = [{"id": "a", "title": "A", "date": "", "url": ""}]
        res = watch.diff_summary("x", prev, "same", "t", items)
        self.assertEqual(res["status"], "UNCHANGED")


class EndToEndTests(unittest.TestCase):
    def _run(self, config_path, state, out):
        return watch.main(["--config", config_path, "--state", state, "--out", out])

    def test_baseline_then_change(self):
        tmp = tempfile.mkdtemp()
        page = write(tmp, "page.html", "<h1>Changelog</h1><p>v1.0</p>")
        cfg = write(tmp, "sources.json", json.dumps({"sources": [
            {"name": "demo", "type": "page", "url": file_url(page)}]}))
        state = os.path.join(tmp, ".state")
        out1 = os.path.join(tmp, "brief1.md")

        self.assertEqual(self._run(cfg, state, out1), 0)
        first = read(out1)
        self.assertIn("BASELINE", first)

        write(tmp, "page.html", "<h1>Changelog</h1><p>v1.1 shipped</p>")
        out2 = os.path.join(tmp, "brief2.md")
        self.assertEqual(self._run(cfg, state, out2), 0)
        second = read(out2)
        self.assertIn("CHANGED", second)
        self.assertIn("v1.1 shipped", second)

    def test_unknown_source_type_is_reported_not_fatal(self):
        tmp = tempfile.mkdtemp()
        cfg = write(tmp, "sources.json", json.dumps({"sources": [
            {"name": "bad", "type": "nope", "url": "file:///x"}]}))
        out = os.path.join(tmp, "brief.md")
        self.assertEqual(watch.main(["--config", cfg, "--state", os.path.join(tmp, "s"),
                                     "--out", out]), 0)
        self.assertIn("ERROR", read(out))

    def test_report_is_markdown(self):
        res = [{"name": "x", "status": "NEW ITEMS", "error": None, "diff": "",
                "new_items": [{"id": "a", "title": "Shipped thing", "date": "2026-10-13",
                               "url": "https://ex.dev/a"}]}]
        md = watch.render_report(res, {"generated": "2026-10-14"})
        self.assertIn("## x — NEW ITEMS", md)
        self.assertIn("Shipped thing", md)


if __name__ == "__main__":
    unittest.main(verbosity=2)

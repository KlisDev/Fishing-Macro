"""Verify the offline guide's navigation, installation links, and release context."""
from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path
import re
import unittest
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[2]


class GuideParser(HTMLParser):
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input",
            "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self):
        super().__init__()
        self.ids = []
        self.links = []
        self.resources = []
        self.stack = []
        self.faqs = []
        self.categories = set()
        self.scripts = []
        self.in_script = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "id" in attrs:
            self.ids.append(attrs["id"])
        if tag == "a":
            self.links.append(attrs.get("href", ""))
        if "src" in attrs:
            self.resources.append(attrs["src"])
        if tag == "link":
            self.resources.append(attrs.get("href", ""))
        if tag == "details" and attrs.get("data-category"):
            self.faqs.append(attrs)
        if tag == "button" and "data-category" in attrs:
            self.categories.add(attrs["data-category"])
        if tag == "script":
            self.in_script = True
        if tag not in self.VOID:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag in self.VOID:
            return
        if not self.stack or self.stack.pop() != tag:
            raise AssertionError(f"Unbalanced HTML tag: {tag}")
        if tag == "script":
            self.in_script = False

    def handle_data(self, data):
        if self.in_script:
            self.scripts.append(data)


class GuideTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.content = (ROOT / "GUIDE.html").read_text(encoding="utf-8")
        cls.parser = GuideParser()
        cls.parser.feed(cls.content)
        cls.parser.close()

    def test_all_anchors_and_local_resources_resolve(self):
        self.assertFalse(self.parser.stack)
        self.assertEqual(len(self.parser.ids), len(set(self.parser.ids)))
        for link in self.parser.links:
            with self.subTest(link=link):
                url = urlsplit(link)
                if url.scheme:
                    self.assertEqual(url.scheme, "https")
                elif link.startswith("#"):
                    self.assertIn(unquote(url.fragment), self.parser.ids)
                else:
                    self.assertTrue((ROOT / unquote(url.path)).is_file())

    def test_display_has_no_remote_dependencies(self):
        self.assertFalse(self.parser.resources)
        self.assertNotIn("@import", self.content)
        self.assertNotRegex(self.content, r"url\(\s*['\"]?https?://")
        script = "".join(self.parser.scripts)
        self.assertNotRegex(script, r"\b(fetch|XMLHttpRequest|WebSocket)\b")

    def test_version_and_launchers_match_current_project(self):
        config = (ROOT / "WINDOWS/bloxfish/config.py").read_text(encoding="utf-8")
        version = re.search(r'^VERSION = "([^"]+)"', config, re.M).group(1)
        self.assertIn(f"Version {version}", self.content)
        self.assertIn(f"This guide describes <strong>{version}</strong>", self.content)
        self.assertIn(r"python .\WINDOWS\easy_run.py", self.content)
        self.assertIn("python3 LINUX/easy_run_linux.py", self.content)
        self.assertIn("sudo bash LINUX/install-udev.sh", self.content)

    def test_faq_filters_and_no_script_navigation_have_targets(self):
        self.assertTrue(self.parser.faqs)
        for faq in self.parser.faqs:
            self.assertIn(faq["data-category"], self.parser.categories)
            self.assertIn(faq["id"], self.parser.ids)
        for target in ("help", "windows", "linux", "run", "calibration", "faq",
                       "report", "faq-shiftlock", "faq-anchor", "faq-bait",
                       "faq-reel", "faq-fish-only"):
            self.assertIn(target, self.parser.ids)
        self.assertIn("<noscript>", self.content)
        self.assertNotIn("\u2014", self.content)

    def test_release_fixes_have_searchable_answers_and_honest_limits(self):
        answers = {faq["id"]: faq for faq in self.parser.faqs}
        self.assertEqual(answers["faq-reel-width"]["data-category"], "reel")
        self.assertEqual(answers["faq-restock-stuck"]["data-category"], "bait")
        self.assertIn("53", answers["faq-reel-width"]["data-search"])
        self.assertIn("55", answers["faq-restock-stuck"]["data-search"])
        self.assertIn("release-1821", self.parser.ids)
        self.assertIn("Existing configs remain compatible", self.content)
        self.assertIn("not live-confirmed as resolved", self.content)
        self.assertIn("not live Roblox or Sober certification", self.content)


if __name__ == "__main__":
    unittest.main()

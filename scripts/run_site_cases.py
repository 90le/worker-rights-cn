#!/usr/bin/env python3
"""Dependency-free acceptance cases for the public GitHub Pages site."""

from __future__ import annotations

import re
import sys
import unittest
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"
PAGES_URL = "https://90le.github.io/worker-rights-cn/"
REPOSITORY_URL = "https://github.com/90le/worker-rights-cn"
POLICY_PAGES = {
    "privacy.html": f"{PAGES_URL}privacy.html",
    "terms.html": f"{PAGES_URL}terms.html",
    "security.html": f"{PAGES_URL}security.html",
}


class SiteParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: list[tuple[str, dict[str, str]]] = []
        self.text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append((tag, {key: value or "" for key, value in attrs}))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_data(self, data: str) -> None:
        self.text.append(data)


class PublicSiteCases(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        required = [
            "index.html",
            "styles.css",
            "script.js",
            "404.html",
            "robots.txt",
            "sitemap.xml",
            "llms.txt",
            *POLICY_PAGES,
        ]
        missing = [name for name in required if not (SITE / name).is_file()]
        if missing:
            raise AssertionError(f"missing required site files: {', '.join(missing)}")

        cls.html = (SITE / "index.html").read_text(encoding="utf-8")
        cls.css = (SITE / "styles.css").read_text(encoding="utf-8")
        cls.js = (SITE / "script.js").read_text(encoding="utf-8")
        cls.parser = SiteParser()
        cls.parser.feed(cls.html)
        cls.visible_text = re.sub(r"\s+", " ", " ".join(cls.parser.text)).strip()

    def test_semantic_landmarks_and_single_visible_h1(self) -> None:
        tags = [tag for tag, _ in self.parser.tags]
        for landmark in ("header", "nav", "main", "footer"):
            self.assertIn(landmark, tags)
        h1s = [attrs for tag, attrs in self.parser.tags if tag == "h1"]
        self.assertEqual(len(h1s), 1)
        self.assertNotIn("hidden", h1s[0])
        self.assertIn("先把事实讲清楚，再决定下一步。", self.visible_text)

    def test_approved_sections_and_copy_are_complete(self) -> None:
        required_copy = [
            "本项目帮助你整理事实、保存证据、估算金额和准备草稿。它不替代律师意见，也不保证案件结果。",
            "今天要签协议",
            "仲裁时效",
            "工伤索赔",
            "讲情况",
            "补信息",
            "看建议",
            "确认保存",
            "公司欠薪",
            "突然被辞退",
            "加班没有记录",
            "准备劳动仲裁",
            "事实要点梳理",
            "权利与补偿估算（参考）",
            "证据清单建议",
            "下一步建议",
            "本地优先",
            "明确同意",
            "脱敏导出",
            "可验证删除",
            "来源可追溯",
            "codex plugin marketplace add 90le/worker-rights-cn --ref main",
            "Claude Code",
            "OpenCode",
            "OpenClaw",
            "丘彬彬",
            "广东广州",
            "binstudy",
            "© 2026 Worker Rights CN",
        ]
        for text in required_copy:
            with self.subTest(text=text):
                self.assertIn(text, self.visible_text)

    def test_urls_metadata_and_policy_links(self) -> None:
        self.assertIn(f'<link rel="canonical" href="{PAGES_URL}">', self.html)
        self.assertIn(f'<meta property="og:url" content="{PAGES_URL}">', self.html)
        self.assertIn(REPOSITORY_URL, self.html)
        for url in POLICY_PAGES.values():
            self.assertIn(url, self.html)
        self.assertIn(f"{REPOSITORY_URL}/blob/main/CONTRIBUTING.md", self.html)
        self.assertIn("Apache-2.0", self.visible_text)

    def test_policy_pages_are_static_and_link_home_and_canonical_markdown(self) -> None:
        for filename, url in POLICY_PAGES.items():
            with self.subTest(filename=filename):
                text = (SITE / filename).read_text(encoding="utf-8")
                parser = SiteParser()
                parser.feed(text)
                self.assertNotIn("script", [tag for tag, _ in parser.tags])
                self.assertNotIn("form", [tag for tag, _ in parser.tags])
                self.assertIn(PAGES_URL, text)
                source = {"privacy.html": "PRIVACY.md", "terms.html": "TERMS.md", "security.html": "SECURITY.md"}[filename]
                self.assertIn(f"{REPOSITORY_URL}/blob/main/{source}", text)
                self.assertIn(url, (SITE / "sitemap.xml").read_text(encoding="utf-8"))
                self.assertIn(url, (SITE / "llms.txt").read_text(encoding="utf-8"))

    def test_no_collection_tracking_external_runtime_or_forbidden_claims(self) -> None:
        tags = [tag for tag, _ in self.parser.tags]
        self.assertNotIn("form", tags)
        self.assertNotRegex(self.html.lower(), r"google-analytics|googletagmanager|segment\.com|mixpanel|cookie")
        self.assertNotRegex(self.css.lower(), r"(?:linear|radial|conic)-gradient\s*\(")
        for phrase in ("AI律师", "智能律师", "官方认证", "官方市场", "保证胜诉", "保证仲裁成功"):
            self.assertNotIn(phrase, self.visible_text)
        for tag, attrs in self.parser.tags:
            if tag == "script" and attrs.get("src"):
                self.assertFalse(urlparse(attrs["src"]).scheme, "runtime scripts must be local")
            if tag == "link" and attrs.get("rel") == "stylesheet":
                self.assertFalse(urlparse(attrs.get("href", "")).scheme, "stylesheets must be local")

    def test_local_relative_assets_resolve_and_author_image_is_not_placeholder(self) -> None:
        checked: set[str] = set()
        for tag, attrs in self.parser.tags:
            attribute = "src" if tag in {"img", "script"} else "href" if tag == "link" else ""
            value = attrs.get(attribute, "") if attribute else ""
            if not value or value.startswith(("#", "http://", "https://", "mailto:")):
                continue
            path = value.split("?", 1)[0].split("#", 1)[0]
            checked.add(path)
            self.assertTrue((SITE / path).is_file(), f"missing local asset: {path}")
        self.assertIn("assets/author-wechat.jpg", checked)
        self.assertNotRegex(self.html.lower(), r"placeholder[^\n]*(?:qr|二维码)|(?:qr|二维码)[^\n]*placeholder")
        self.assertRegex(self.css, r"\.author-image\s*\{[^}]*object-fit:\s*contain", re.DOTALL)

    def test_accessibility_and_progressive_enhancement(self) -> None:
        self.assertRegex(self.html, r'<a[^>]+class="[^"]*skip-link[^"]*"[^>]+href="#main-content"')
        self.assertRegex(self.css, r":focus-visible")
        self.assertIn("outline: 3px solid #172019", self.css)
        self.assertIn("box-shadow: 0 0 0 7px #ffffff", self.css)
        self.assertRegex(self.css, r"@media\s*\(prefers-reduced-motion:\s*reduce\)")
        self.assertIn("function copyInstallCommand(button)", self.js)
        self.assertIn("navigator.clipboard", self.js)
        self.assertIn("codex plugin marketplace add 90le/worker-rights-cn --ref main", self.html)
        self.assertNotRegex(self.html, r"<(?:section|main)[^>]+hidden")

    def test_privacy_claims_are_scoped_to_plugin_control(self) -> None:
        self.assertIn("插件本身不自动上传、不自动保存", self.visible_text)
        self.assertIn("Codex、ChatGPT 或另行启用的模型服务", self.visible_text)
        self.assertIn("可能按相应服务的规则处理和保留", self.visible_text)
        self.assertIn("插件控制的案件数据默认不落盘", self.visible_text)

    def test_pages_support_files_use_real_url(self) -> None:
        robots = (SITE / "robots.txt").read_text(encoding="utf-8")
        sitemap = (SITE / "sitemap.xml").read_text(encoding="utf-8")
        llms = (SITE / "llms.txt").read_text(encoding="utf-8")
        not_found = (SITE / "404.html").read_text(encoding="utf-8")
        self.assertIn(f"Sitemap: {PAGES_URL}sitemap.xml", robots)
        self.assertIn(f"<loc>{PAGES_URL}</loc>", sitemap)
        self.assertIn(PAGES_URL, llms)
        self.assertIn(PAGES_URL, not_found)
        self.assertIn('href="/worker-rights-cn/assets/favicon.svg"', not_found)
        self.assertNotIn("<script", not_found.lower())


if __name__ == "__main__":
    result = unittest.main(verbosity=2, exit=False).result
    sys.exit(0 if result.wasSuccessful() else 1)

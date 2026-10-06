"""Parsed page wrapper used by the extraction engine."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional

from bs4 import BeautifulSoup, Tag

from ..util.text import clean_whitespace, fold_words
from ..util.urls import is_probably_file, normalize_url, registered_domain

BOILERPLATE_SELECTORS = (
    "header, footer, nav, aside, [role=navigation], [role=banner], [role=contentinfo], "
    "#header, #footer, #nav, #navbar, #menu, #sidebar, .header, .footer, .site-header, .site-footer, "
    ".navbar, .nav, .menu, .sidebar, .breadcrumb, .breadcrumbs, .cookie, .cookies, #cookie-banner"
)
_NOISE_TAGS = ("script", "style", "noscript", "template", "svg", "iframe", "canvas")


@dataclass
class Link:
    url: str
    text: str
    rel: str
    external: bool
    in_boilerplate: bool
    title: str = ""
    classes: str = ""


def _parse(html: str) -> BeautifulSoup:
    try:
        return BeautifulSoup(html, "lxml")
    except Exception:  # pragma: no cover - lxml missing or exotic markup
        return BeautifulSoup(html, "html.parser")


class Page:
    """An HTML document plus cached derived views (text, links, structured data)."""

    def __init__(self, url: str, html: str, status: int = 200, content_type: str = "text/html"):
        self.url = url
        self.html = html or ""
        self.status = status
        self.content_type = content_type
        self.domain = registered_domain(url)
        self.soup = _parse(self.html)
        # Scripts with structured data are captured before noise tags are removed.
        self.jsonld_blocks: List[str] = [
            s.string or s.get_text() for s in self.soup.find_all("script", attrs={"type": re.compile(r"ld\+json", re.I)})
        ]
        self.script_count = len(self.soup.find_all("script"))
        self.has_next_data = bool(self.soup.find("script", id="__NEXT_DATA__"))
        for tag in self.soup.find_all(_NOISE_TAGS):
            tag.decompose()
        title_tag = self.soup.find("title")
        self.title = clean_whitespace(title_tag.get_text()) if title_tag else ""
        self.text = clean_whitespace(self.soup.get_text(" "))
        self._folded: Optional[str] = None
        self._main_soup: Optional[BeautifulSoup] = None
        self._links: Optional[List[Link]] = None
        canonical = self.soup.find("link", rel=lambda v: v and "canonical" in (v if isinstance(v, list) else [v]))
        self.canonical = normalize_url(canonical.get("href"), self.url) if canonical and canonical.get("href") else None
        html_tag = self.soup.find("html")
        self.lang = (html_tag.get("lang") or "").lower() if isinstance(html_tag, Tag) else ""

    # ------------------------------------------------------------------------------------------
    @property
    def folded_text(self) -> str:
        if self._folded is None:
            self._folded = fold_words(self.title + " " + self.text[:200_000])
        return self._folded

    def main_soup(self) -> BeautifulSoup:
        """A separate copy of the document with header/footer/nav/sidebars removed."""
        if self._main_soup is None:
            soup = _parse(self.html)
            for tag in soup.find_all(_NOISE_TAGS):
                tag.decompose()
            main = soup.select_one("main, [role=main], article")
            if main is None:
                for tag in soup.select(BOILERPLATE_SELECTORS):
                    tag.decompose()
            else:
                # Keep only <main>; wrap it in a fresh soup so selectors still work.
                soup = _parse(str(main))
            self._main_soup = soup
        return self._main_soup

    def main_text(self) -> str:
        return clean_whitespace(self.main_soup().get_text(" "))

    def looks_js_rendered(self) -> bool:
        """Heuristic: an app shell with scripts but almost no text needs a real browser."""
        words = len(self.text.split())
        if words < 40 and (self.script_count >= 3 or self.has_next_data):
            return True
        root = self.soup.select_one("#root, #app, #__next, [data-reactroot], app-root")
        return bool(root is not None and len(root.get_text(strip=True)) < 50 and words < 150)

    @property
    def links(self) -> List[Link]:
        if self._links is None:
            self._links = self._collect_links()
        return self._links

    def _collect_links(self) -> List[Link]:
        boiler = set(id(t) for t in self.soup.select(BOILERPLATE_SELECTORS))
        base_tag = self.soup.find("base", href=True)
        base = normalize_url(base_tag["href"], self.url) if base_tag else self.url
        out: List[Link] = []
        seen = set()
        for a in self.soup.find_all(["a", "link", "area"], href=True):
            href = a.get("href", "").strip()
            if not href or href.startswith(("#", "javascript:", "data:")):
                continue
            if href.lower().startswith(("mailto:", "tel:", "sms:", "whatsapp:", "callto:")):
                continue
            rel_attr = a.get("rel") or []
            rel = " ".join(rel_attr) if isinstance(rel_attr, list) else str(rel_attr)
            if a.name == "link" and "next" not in rel.lower() and "prev" not in rel.lower():
                continue
            url = normalize_url(href, base)
            if not url or is_probably_file(url):
                continue
            text = clean_whitespace(a.get_text(" ")) if a.name != "link" else ""
            key = (url, text)
            if key in seen:
                continue
            seen.add(key)
            in_boiler = False
            parent = a.parent
            while parent is not None:
                if id(parent) in boiler:
                    in_boiler = True
                    break
                parent = parent.parent
            classes = a.get("class") or []
            out.append(Link(
                url=url, text=text[:200], rel=rel.lower(), external=registered_domain(url) != self.domain,
                in_boilerplate=in_boiler, title=str(a.get("title") or a.get("aria-label") or ""),
                classes=" ".join(classes) if isinstance(classes, list) else str(classes),
            ))
        return out

    def raw_hrefs(self, prefix: str) -> List[str]:
        """``href`` values starting with ``prefix`` (e.g. ``mailto:``), in document order."""
        out = []
        for a in self.soup.find_all("a", href=True):
            href = a["href"].strip()
            if href.lower().startswith(prefix):
                out.append(href)
        return out

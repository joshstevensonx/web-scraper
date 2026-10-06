"""User-defined extraction: CSS selectors, XPath and regular expressions."""

from __future__ import annotations

import re
from typing import Any, List, Optional

from bs4 import BeautifulSoup, Tag

from ..util.text import clean_whitespace
from ..util.urls import normalize_url

_URL_ATTRS = {"href", "src", "data-src", "data-href", "action", "poster"}


def _tag_value(tag: Tag, attribute: Optional[str], base_url: str) -> Optional[str]:
    if attribute:
        if attribute in ("text", "innerText"):
            return clean_whitespace(tag.get_text(" ")) or None
        if attribute in ("html", "innerHTML"):
            return tag.decode_contents()
        val = tag.get(attribute)
        if isinstance(val, list):
            val = " ".join(val)
        if val and attribute in _URL_ATTRS:
            return normalize_url(val, base_url) or val
        return clean_whitespace(val) if val else None
    if tag.name == "meta":
        return clean_whitespace(tag.get("content") or "") or None
    if tag.name in ("img",) and tag.get("src"):
        return normalize_url(tag["src"], base_url) or tag["src"]
    return clean_whitespace(tag.get_text(" ")) or None


def select_css(root: Any, selector: str, attribute: Optional[str], base_url: str, multiple: bool = False) -> List[str]:
    try:
        tags = root.select(selector) if multiple else [root.select_one(selector)]
    except Exception as exc:  # soupsieve raises on invalid selectors
        raise ValueError("Invalid CSS selector %r: %s" % (selector, exc))
    out = []
    for tag in tags:
        if tag is None:
            continue
        v = _tag_value(tag, attribute, base_url)
        if v:
            out.append(v)
    return out


def select_xpath(html_or_tree: Any, xpath: str, attribute: Optional[str], base_url: str) -> List[str]:
    from lxml import etree, html as lxml_html

    tree = html_or_tree
    if isinstance(html_or_tree, (str, bytes)):
        tree = lxml_html.fromstring(html_or_tree or "<html></html>")
    elif isinstance(html_or_tree, (BeautifulSoup, Tag)):
        tree = lxml_html.fromstring(str(html_or_tree) or "<html></html>")
    try:
        results = tree.xpath(xpath)
    except etree.XPathError as exc:
        raise ValueError("Invalid XPath %r: %s" % (xpath, exc))
    if not isinstance(results, list):
        results = [results]
    out = []
    for r in results:
        if isinstance(r, str):
            v = clean_whitespace(r)
        elif attribute and hasattr(r, "get"):
            v = r.get(attribute)
            if v and attribute in _URL_ATTRS:
                v = normalize_url(v, base_url) or v
        elif hasattr(r, "text_content"):
            v = clean_whitespace(r.text_content())
        else:
            v = clean_whitespace(str(r))
        if v:
            out.append(v)
    return out


def apply_regex(text: str, pattern: str, multiple: bool = False) -> List[str]:
    rx = re.compile(pattern, re.IGNORECASE | re.MULTILINE)
    out = []
    for m in rx.finditer(text or ""):
        value = next((g for g in m.groups() if g), None) if m.groups() else m.group(0)
        if value:
            out.append(clean_whitespace(value))
            if not multiple:
                break
    return out

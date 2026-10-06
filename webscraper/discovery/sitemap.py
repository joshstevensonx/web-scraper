"""Sitemap and feed readers (URL discovery strategies)."""

from __future__ import annotations

import gzip
import re
from typing import List, Tuple

from lxml import etree

from ..util.urls import normalize_url


def _xml_root(content: bytes, text: str):
    data = content or text.encode("utf-8", errors="ignore")
    if data[:2] == b"\x1f\x8b":
        try:
            data = gzip.decompress(data)
        except OSError:
            return None
    try:
        parser = etree.XMLParser(recover=True, resolve_entities=False, no_network=True, huge_tree=False)
        return etree.fromstring(data.lstrip(), parser=parser)
    except (etree.XMLSyntaxError, ValueError):
        return None


def _local(tag) -> str:
    return tag.rsplit("}", 1)[-1].lower() if isinstance(tag, str) else ""


def parse_sitemap(content: bytes, text: str, base_url: str) -> Tuple[List[str], List[str]]:
    """Return ``(page_urls, child_sitemap_urls)`` from a sitemap or sitemap index (gz supported)."""
    root = _xml_root(content, text)
    pages: List[str] = []
    children: List[str] = []
    if root is None:
        # Plain-text sitemaps: one URL per line.
        for line in (text or "").splitlines():
            u = normalize_url(line.strip(), base_url)
            if u:
                pages.append(u)
        return pages, children
    kind = _local(root.tag)
    for el in root.iter():
        if _local(el.tag) != "loc" or not el.text:
            continue
        u = normalize_url(el.text.strip(), base_url)
        if not u:
            continue
        if kind == "sitemapindex":
            children.append(u)
        else:
            pages.append(u)
    return pages, children


def parse_feed(content: bytes, text: str, base_url: str) -> List[Tuple[str, str]]:
    """Return ``(url, title)`` pairs from an RSS or Atom feed."""
    root = _xml_root(content, text)
    out: List[Tuple[str, str]] = []
    if root is None:
        return out
    for item in root.iter():
        name = _local(item.tag)
        if name not in ("item", "entry"):
            continue
        link, title = None, ""
        for child in item:
            cname = _local(child.tag)
            if cname == "link":
                link = child.get("href") or (child.text or "").strip()
            elif cname == "title":
                title = (child.text or "").strip()
            elif cname == "guid" and not link and re.match(r"https?://", (child.text or "").strip()):
                link = child.text.strip()
        u = normalize_url(link, base_url) if link else None
        if u:
            out.append((u, title))
    return out

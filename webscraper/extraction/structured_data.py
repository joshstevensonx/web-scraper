"""Structured data extraction: JSON-LD and microdata (schema.org)."""

from __future__ import annotations

import html as html_lib
import json
import re
from typing import Any, Dict, Iterable, List, Optional

from bs4 import BeautifulSoup, Tag

from ..util.text import clean_whitespace


def _loads_lenient(raw: str) -> Any:
    text = (raw or "").strip()
    if not text:
        return None
    text = re.sub(r"^\s*<!\[CDATA\[|\]\]>\s*$", "", text)
    text = re.sub(r"^\s*//.*$", "", text, flags=re.M)
    for candidate in (text, html_lib.unescape(text)):
        try:
            return json.loads(candidate)
        except (ValueError, TypeError):
            pass
        # Trailing commas and stray control characters are the most common breakage.
        fixed = re.sub(r",\s*([}\]])", r"\1", candidate)
        fixed = re.sub(r"[\x00-\x1f]", " ", fixed)
        try:
            return json.loads(fixed)
        except (ValueError, TypeError):
            continue
    return None


def _flatten(node: Any, out: List[Dict[str, Any]]) -> None:
    if isinstance(node, list):
        for item in node:
            _flatten(item, out)
    elif isinstance(node, dict):
        if "@graph" in node:
            _flatten(node["@graph"], out)
            rest = {k: v for k, v in node.items() if k not in ("@graph", "@context")}
            if "@type" in rest:
                out.append(rest)
        else:
            out.append(node)


def extract_jsonld(blocks: Iterable[str]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for raw in blocks:
        data = _loads_lenient(raw)
        if data is not None:
            _flatten(data, items)
    return items


def types_of(obj: Any) -> List[str]:
    if not isinstance(obj, dict):
        return []
    t = obj.get("@type") or obj.get("type") or []
    if isinstance(t, str):
        t = [t]
    return [str(x).rsplit("/", 1)[-1] for x in t if x]


def iter_entities(items: List[Dict[str, Any]], max_depth: int = 4) -> Iterable[Dict[str, Any]]:
    """Yield every typed object, including those nested in ItemList / @graph / properties."""
    seen = set()

    def walk(node: Any, depth: int):
        if depth > max_depth:
            return
        if isinstance(node, list):
            for x in node:
                yield from walk(x, depth)
        elif isinstance(node, dict):
            if types_of(node) and id(node) not in seen:
                seen.add(id(node))
                yield node
            for key in ("itemListElement", "item", "mainEntity", "about", "hasPart", "itemReviewed",
                        "subOrganization", "department", "containsPlace", "offers", "makesOffer"):
                if key in node:
                    yield from walk(node[key], depth + 1)

    for item in items:
        yield from walk(item, 0)


def get_path(obj: Any, path: str) -> Any:
    """Resolve a dotted path (``address.addressLocality``); lists resolve to their first match."""
    cur = obj
    for part in path.split("."):
        if isinstance(cur, list):
            nxt = None
            for el in cur:
                val = get_path(el, part)
                if val not in (None, "", [], {}):
                    nxt = val
                    break
            cur = nxt
        elif isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
        if cur is None:
            return None
    return cur


def scalar(value: Any) -> Optional[str]:
    """Reduce a JSON-LD value (string, number, object with name/url/value, list) to text."""
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        v = clean_whitespace(html_lib.unescape(value))
        return v or None
    if isinstance(value, list):
        parts = [scalar(v) for v in value]
        parts = [p for p in parts if p]
        return parts[0] if parts else None
    if isinstance(value, dict):
        if types_of(value) and "PostalAddress" in types_of(value):
            return postal_address_text(value)
        for key in ("name", "value", "url", "@id", "text", "contentUrl"):
            if value.get(key) not in (None, ""):
                return scalar(value[key])
    return None


def postal_address_text(addr: Any) -> Optional[str]:
    if isinstance(addr, str):
        return clean_whitespace(addr) or None
    if isinstance(addr, list):
        for a in addr:
            t = postal_address_text(a)
            if t:
                return t
        return None
    if not isinstance(addr, dict):
        return None
    parts = []
    for key in ("streetAddress", "addressLocality", "addressRegion", "postalCode", "addressCountry"):
        v = scalar(addr.get(key))
        if v and v not in parts:
            parts.append(v)
    return ", ".join(parts) or scalar(addr.get("name"))


# --- microdata -------------------------------------------------------------------------------

def _microdata_value(tag: Tag) -> Any:
    if tag.has_attr("itemscope"):
        return _microdata_item(tag)
    for attr in ("content", "datetime"):
        if tag.has_attr(attr):
            return tag[attr]
    if tag.name in ("a", "link", "area") and tag.has_attr("href"):
        return tag["href"]
    if tag.name in ("img", "audio", "video", "source", "embed", "iframe") and tag.has_attr("src"):
        return tag["src"]
    if tag.name == "meta":
        return tag.get("content")
    return clean_whitespace(tag.get_text(" "))


def _microdata_item(scope: Tag) -> Dict[str, Any]:
    item: Dict[str, Any] = {}
    itemtype = scope.get("itemtype")
    if itemtype:
        item["@type"] = [t.rsplit("/", 1)[-1] for t in str(itemtype).split()]

    def visit(node: Tag):
        for child in node.find_all(True, recursive=False):
            props = child.get("itemprop")
            if props:
                value = _microdata_value(child)
                for prop in str(props if isinstance(props, str) else " ".join(props)).split():
                    if prop in item:
                        if not isinstance(item[prop], list):
                            item[prop] = [item[prop]]
                        item[prop].append(value)
                    else:
                        item[prop] = value
                if child.has_attr("itemscope"):
                    continue
            if not child.has_attr("itemscope"):
                visit(child)

    visit(scope)
    return item


def extract_microdata(soup: BeautifulSoup) -> List[Dict[str, Any]]:
    out = []
    for scope in soup.find_all(attrs={"itemscope": True}):
        if scope.get("itemprop"):
            continue  # nested items are reached through their parent
        item = _microdata_item(scope)
        if item.get("@type"):
            out.append(item)
    return out

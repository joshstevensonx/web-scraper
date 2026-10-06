"""Label-based ("semantic-lite") extraction for fields that have no structured data.

Finds values next to human labels, e.g. ``<dt>Founded</dt><dd>2012</dd>``, table rows
``<th>Employees</th><td>11-50</td>``, or text such as ``Bedrooms: 3``. This is the non-AI fallback
from section 15 of the spec; it is deliberately conservative.
"""

from __future__ import annotations

import re
from typing import List, Optional

from bs4 import BeautifulSoup

from ..util.text import clean_whitespace, fold

ROLE_LABELS = {
    "contact_person": ["contact person", "contact name", "contact", "owner", "founder", "managing director", "ceo"],
    "founded_year": ["founded", "year founded", "established", "since", "founded in"],
    "employees": ["employees", "company size", "number of employees", "staff", "team size", "headcount"],
    "industry": ["industry", "sector", "category", "categories", "business type"],
    "price": ["price", "asking price", "cost"],
    "brand": ["brand", "manufacturer"],
    "sku": ["sku", "product code", "item code", "upc", "isbn", "model number", "part number"],
    "availability": ["availability", "stock", "in stock"],
    "rating": ["rating", "average rating"],
    "review_count": ["reviews", "number of reviews"],
    "cuisine": ["cuisine", "cuisines", "food"],
    "price_range": ["price range"],
    "opening_hours": ["opening hours", "hours", "business hours", "trading hours", "open"],
    "salary": ["salary", "pay", "compensation", "remuneration"],
    "employment_type": ["employment type", "job type", "contract type", "type"],
    "experience_level": ["experience level", "seniority", "experience"],
    "location": ["location", "job location", "city"],
    "date_posted": ["date posted", "posted", "posted on", "published"],
    "valid_through": ["closing date", "deadline", "apply by", "valid through", "expires"],
    "company": ["company", "employer", "hiring organization", "organisation", "organization"],
    "property_type": ["property type", "type of property", "home type"],
    "bedrooms": ["bedrooms", "beds", "bedroom"],
    "bathrooms": ["bathrooms", "baths", "bathroom"],
    "floor_size": ["floor size", "floor area", "size", "living area"],
    "venue": ["venue", "where", "location"],
    "organizer": ["organizer", "organiser", "hosted by", "host"],
    "start_date": ["date", "start date", "starts", "when"],
    "end_date": ["end date", "ends"],
    "address": ["address", "physical address", "street address", "office address", "location"],
    "email": ["email", "e-mail"],
    "phone": ["phone", "telephone", "tel", "call us", "mobile", "cell"],
    "person_job_title": ["position", "job title", "title", "role"],
    "author": ["author", "by", "written by"],
}

_MAX_VALUE_LEN = 300
# Short-valued roles: anything longer is almost certainly a menu or paragraph, not a value.
MAX_LEN_BY_ROLE = {"industry": 80, "founded_year": 40, "employees": 40, "contact_person": 80, "price": 40,
                   "brand": 60, "sku": 60, "availability": 60, "rating": 30, "review_count": 30, "cuisine": 80,
                   "price_range": 30, "salary": 80, "employment_type": 40, "experience_level": 60, "location": 120,
                   "date_posted": 40, "valid_through": 40, "company": 100, "property_type": 60, "bedrooms": 20,
                   "bathrooms": 20, "floor_size": 40, "start_date": 60, "end_date": 60, "person_job_title": 80,
                   "author": 80, "venue": 120, "organizer": 100}
_LABEL_TAGS = {"dt", "th", "label", "strong", "b"}


def _norm_label(text: str) -> str:
    return re.sub(r"[\s:：\-–|]+$", "", fold(text)).strip()


def find_labeled_value(soup: BeautifulSoup, labels: List[str], text: str = "",
                       max_len: int = _MAX_VALUE_LEN) -> Optional[str]:
    """Return the value printed next to one of ``labels`` (definition lists, tables, "Label: value")."""
    value = _find_labeled_value(soup, labels, text)
    if value and len(value) <= max_len:
        return value
    return None


def _find_labeled_value(soup: BeautifulSoup, labels: List[str], text: str = "") -> Optional[str]:
    wanted = [fold(label) for label in labels if label]
    if not wanted:
        return None
    wanted_set = set(wanted)

    # 1. Definition lists.
    for dt in soup.find_all("dt"):
        if _norm_label(dt.get_text(" ")) in wanted_set:
            dd = dt.find_next_sibling("dd")
            if dd:
                v = clean_whitespace(dd.get_text(" "))
                if v and len(v) <= _MAX_VALUE_LEN:
                    return v
    # 2. Two-column table rows.
    for row in soup.find_all("tr"):
        cells = row.find_all(["th", "td"], recursive=False)
        if len(cells) >= 2 and _norm_label(cells[0].get_text(" ")) in wanted_set:
            v = clean_whitespace(cells[1].get_text(" "))
            if v and len(v) <= _MAX_VALUE_LEN:
                return v
    # 3. Label element followed by a value element (e.g. <span class="label">Founded</span><span>2012</span>).
    for tag in soup.find_all(["span", "strong", "b", "label", "th", "div", "p", "li", "h3", "h4", "h5", "h6"],
                             limit=4000):
        if tag.name in ("div", "p", "li") and len(tag.find_all(True, limit=3)) > 2:
            continue
        own = tag.get_text(" ", strip=True)
        if not own or len(own) > 40:
            continue
        classes = " ".join(tag.get("class") or []).lower()
        looks_like_label = tag.name in _LABEL_TAGS or own.rstrip().endswith((":", "：")) or "label" in classes \
            or "key" in classes or "name" in classes or "title" in classes
        if looks_like_label and _norm_label(own) in wanted_set:
            sib = tag.find_next_sibling()
            if sib is not None:
                v = clean_whitespace(sib.get_text(" "))
                if v and len(v) <= _MAX_VALUE_LEN and _norm_label(v) not in wanted_set:
                    return v
    # 4. Inline "Label: value" text.
    body = text or soup.get_text("\n")
    for label in sorted(labels, key=len, reverse=True):
        m = re.search(r"(?:^|[\n.;|•])\s*" + re.escape(label) + r"\s*[:：]\s*([^\n|•]{1,%d})" % _MAX_VALUE_LEN,
                      body, re.IGNORECASE)
        if m:
            v = clean_whitespace(re.split(r"\s{2,}|(?<=[a-z0-9])\.\s", m.group(1))[0])
            if v:
                return v
    return None


def first_paragraph(soup: BeautifulSoup, min_len: int = 80) -> Optional[str]:
    for p in soup.find_all("p", limit=200):
        t = clean_whitespace(p.get_text(" "))
        if len(t) >= min_len and not re.search(r"cookie|javascript|browser|©|copyright", t, re.I):
            return t
    return None

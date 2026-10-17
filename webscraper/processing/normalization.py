"""Data normalization (section 17): phones, locations, URLs, names, numbers, dates."""

from __future__ import annotations

import html
import json
import re
import unicodedata
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import phonenumbers

from ..extraction.patterns import EMAIL_RE, is_plausible_email
from ..job.models import FieldSpec
from ..util.countries import country_code, country_name
from ..util.regions import lookup_region
from ..util.text import clean_whitespace, fold
from ..util.urls import normalize_url, origin, registered_domain

LEGAL_SUFFIXES = re.compile(
    r"[\s,]*\b(?:\(pty\)\s*ltd|pty\.?\s*ltd|\(pty\)|pty|ltd|limited|llc|l\.l\.c|inc|incorporated|corp|corporation|"
    r"co|company|gmbh|ag|s\.?a|s\.?a\.?s|b\.?v|n\.?v|plc|llp|lp|cc|sarl|srl|s\.?r\.?l|oy|ab|as|a/s|kk|pte|"
    r"pvt|private)\b\.?",
    re.IGNORECASE,
)

_SMALL_WORDS = {"and", "or", "of", "the", "for", "in", "on", "at", "to", "a", "an", "by", "de", "la", "van", "der"}


class NormalizationError(ValueError):
    pass


def normalize_name(value: str) -> str:
    v = clean_whitespace(html.unescape(unicodedata.normalize("NFKC", str(value))))
    v = v.strip(" \t\"'“”‘’`|-–—:;,")
    letters = [c for c in v if c.isalpha()]
    if letters and len(letters) > 4 and (all(c.isupper() for c in letters) or all(c.islower() for c in letters)):
        words = []
        for i, w in enumerate(v.split(" ")):
            lw = w.lower()
            words.append(lw if (i and lw in _SMALL_WORDS) else (w if len(w) <= 3 and w.isupper() else w.capitalize()))
        v = " ".join(words)
    return v


def name_key(value: str, strip_words: Tuple[str, ...] = ()) -> str:
    """Comparison key for fuzzy deduplication (legal suffixes and location words removed)."""
    v = fold(value)
    v = LEGAL_SUFFIXES.sub(" ", v)
    for w in strip_words:
        if w:
            v = re.sub(r"\b" + re.escape(fold(w)) + r"\b", " ", v)
    v = re.sub(r"[^\w]+", " ", v)
    v = re.sub(r"\b(the|and)\b", " ", v)
    return clean_whitespace(v)


def parse_number(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    m = re.search(r"-?\d[\d\s.,'’]*", s)
    if not m:
        return None
    num = re.sub(r"[\s'’]", "", m.group(0)).rstrip(".,")
    if "," in num and "." in num:
        if num.rfind(",") > num.rfind("."):
            num = num.replace(".", "").replace(",", ".")
        else:
            num = num.replace(",", "")
    elif "," in num:
        head, _, tail = num.rpartition(",")
        num = num.replace(",", "") if len(tail) == 3 and head else num.replace(",", ".")
    elif num.count(".") > 1:
        num = num.replace(".", "")
    try:
        result = float(num)
    except ValueError:
        return None
    if re.search(r"\d\s*[kK]\b", s) and result < 10_000:
        result *= 1000
    elif re.search(r"\d\s*(?:m|mn|million)\b", s, re.I) and result < 10_000:
        result *= 1_000_000
    return result


_DATE_FORMATS = [
    "%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d %B %Y", "%d %b %Y", "%B %d, %Y", "%b %d, %Y",
    "%B %d %Y", "%b %d %Y", "%d %B, %Y", "%A, %d %B %Y", "%A, %B %d, %Y", "%Y%m%d", "%m/%d/%Y", "%B %Y", "%b %Y",
]


def parse_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    s = clean_whitespace(str(value))
    if not s:
        return None
    iso = s.replace("Z", "+00:00")
    if re.match(r"^\d{4}-\d{2}-\d{2}", iso):
        iso = re.sub(r"(\.\d{6})\d+", r"\1", iso)
        try:
            return datetime.fromisoformat(iso)
        except ValueError:
            try:
                return datetime.strptime(iso[:10], "%Y-%m-%d")
            except ValueError:
                pass
    cleaned = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", s)
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(cleaned, fmt)
        except ValueError:
            continue
    m = re.search(r"\b(19|20)\d{2}-\d{2}-\d{2}\b", s)
    if m:
        try:
            return datetime.strptime(m.group(0), "%Y-%m-%d")
        except ValueError:
            return None
    return None


def normalize_phone(value: Any, region: Optional[str]) -> str:
    raw = clean_whitespace(str(value))
    raw = re.sub(r"^(?:tel|phone|ph|t|call|mobile|cell)\s*[:.]?\s*", "", raw, flags=re.I)
    try:
        num = phonenumbers.parse(raw, region or "US")
    except phonenumbers.NumberParseException as exc:
        raise NormalizationError("not a phone number (%s)" % exc)
    if not phonenumbers.is_valid_number(num):
        if not phonenumbers.is_possible_number(num):
            raise NormalizationError("invalid phone number %r" % raw)
    return phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.E164)


def normalize_email(value: Any) -> str:
    v = str(value).strip().lower()
    v = re.sub(r"^mailto:", "", v).split("?", 1)[0].strip(" .,;<>")
    if not EMAIL_RE.fullmatch(v) or not is_plausible_email(v):
        raise NormalizationError("invalid email %r" % value)
    return v


def apply_transform(value: Any, transform: Optional[str]) -> Any:
    if not transform or value is None:
        return value
    if isinstance(value, list):
        return [apply_transform(v, transform) for v in value]
    v = str(value)
    for step in transform.split("|"):
        step = step.strip()
        name, _, arg = step.partition(":")
        name = name.lower()
        if name == "lower":
            v = v.lower()
        elif name == "upper":
            v = v.upper()
        elif name == "title":
            v = v.title()
        elif name in ("strip", "trim"):
            v = v.strip()
        elif name == "digits":
            v = re.sub(r"\D", "", v)
        elif name == "before" and arg:
            v = v.split(arg, 1)[0]
        elif name == "after" and arg:
            v = v.split(arg, 1)[1] if arg in v else v
        elif name == "replace" and "=>" in arg:
            a, b = arg.split("=>", 1)
            v = v.replace(a, b)
        elif name == "truncate" and arg.isdigit():
            v = v[: int(arg)]
        elif name == "prefix":
            v = arg + v
        elif name == "suffix":
            v = v + arg
        elif name == "strip_query":
            v = v.split("?", 1)[0]
    return clean_whitespace(v)


class Normalizer:
    def __init__(self, region: Optional[str] = None):
        self.region = region
        self.https_hosts: set = set()

    def _url(self, value: Any, base: Optional[str]) -> str:
        v = str(value).strip()
        if re.match(r"^(?:www\.)?[a-z0-9-]+(\.[a-z0-9-]+)+(/.*)?$", v, re.I) and "://" not in v:
            v = "http://" + v
        u = normalize_url(v, base)
        if not u or "." not in (u.split("/")[2] if u.count("/") >= 2 else ""):
            raise NormalizationError("invalid URL %r" % value)
        if u.startswith("http://"):
            host = u.split("/")[2].split(":")[0]
            if host in self.https_hosts:
                u = "https://" + u[len("http://"):]
        return u

    def normalize(self, spec: FieldSpec, value: Any, base_url: Optional[str] = None) -> Any:
        """Normalize ``value`` for ``spec``. Raises :class:`NormalizationError` when invalid."""
        if value is None:
            raise NormalizationError("empty")
        t = spec.type
        if t == "array":
            items = value if isinstance(value, list) else re.split(r"\s*[;,|\n]\s*", str(value))
            out: List[Any] = []
            item_spec = FieldSpec(name=spec.name, type=_array_item_type(spec), role=spec.role)
            for item in items:
                try:
                    n = self.normalize(item_spec, item, base_url)
                except NormalizationError:
                    continue
                if n not in out and n not in (None, ""):
                    out.append(n)
            if not out:
                raise NormalizationError("no valid items")
            return out
        if isinstance(value, (dict, list)) and t not in ("object", "json"):
            from ..extraction.structured_data import scalar

            value = scalar(value)
            if value is None:
                raise NormalizationError("empty")
        if t in ("text", "address"):
            v = clean_whitespace(html.unescape(unicodedata.normalize("NFKC", str(value))))
            if spec.role == "name":
                v = normalize_name(v)
            elif spec.role == "country":
                v = country_name(v) or v
            elif spec.role == "province":
                reg = lookup_region(v, self.region)
                v = reg[1] if reg else v
            if t == "address":
                v = re.sub(r"\s*,\s*(,\s*)*", ", ", v).strip(" ,")
            if spec.role == "description" and len(v) > 2000:
                v = v[:1997].rstrip() + "..."
            v = apply_transform(v, spec.transform)
            if not v:
                raise NormalizationError("empty")
            if len(v) > 10000:
                v = v[:10000]
            return v
        if t == "url":
            u = self._url(value, base_url)
            if spec.role == "website":
                u = origin(u).rstrip("/")
            return apply_transform(u, spec.transform)
        if t == "email":
            return normalize_email(value)
        if t == "phone":
            return normalize_phone(value, self.region)
        if t in ("number", "decimal"):
            n = parse_number(value)
            if n is None:
                raise NormalizationError("not a number: %r" % value)
            return round(n, 6)
        if t == "integer":
            if spec.role == "founded_year":
                m = re.search(r"\b(1[6-9]\d{2}|20\d{2})\b", str(value))
                if not m:
                    raise NormalizationError("no year in %r" % value)
                return int(m.group(1))
            n = parse_number(value)
            if n is None:
                raise NormalizationError("not an integer: %r" % value)
            return int(round(n))
        if t == "boolean":
            s = fold(str(value))
            if s in ("true", "yes", "y", "1", "instock", "in stock", "available", "open", "on"):
                return True
            if s in ("false", "no", "n", "0", "outofstock", "out of stock", "unavailable", "closed", "off"):
                return False
            raise NormalizationError("not a boolean: %r" % value)
        if t in ("date", "datetime"):
            d = parse_datetime(value)
            if d is None:
                raise NormalizationError("not a date: %r" % value)
            return d.date().isoformat() if t == "date" else d.isoformat()
        if t in ("object", "json"):
            if isinstance(value, (dict, list)):
                return value
            try:
                return json.loads(str(value))
            except ValueError:
                if t == "json":
                    raise NormalizationError("invalid JSON")
                return {"value": str(value)}
        return clean_whitespace(str(value))


def _array_item_type(spec: FieldSpec) -> str:
    if spec.role in ("email",):
        return "email"
    if spec.role in ("phone",):
        return "phone"
    if spec.role in ("social_links", "image", "website", "source_url") or spec.role.endswith("_url"):
        return "url"
    return "text"


# ------------------------------------------------------------------------------------------------
# Location parsing (17.2)
# ------------------------------------------------------------------------------------------------

_POSTAL_RE = re.compile(
    r"\b(?:\d{4,6}(?:-\d{4})?|[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}|[A-Z]\d[A-Z]\s?\d[A-Z]\d)\b", re.IGNORECASE)


def parse_address(address: str, default_country: Optional[str] = None,
                  known_cities: Tuple[str, ...] = ()) -> Dict[str, str]:
    """Split a free-text address into city / province / postal_code / country (best effort)."""
    out: Dict[str, str] = {}
    if not address:
        return out
    parts = [clean_whitespace(p) for p in re.split(r"[,\n]", address) if clean_whitespace(p)]
    # Postal codes may be glued to a city or region ("Cape Town 8001", "CA 94105").
    cleaned: List[str] = []
    for p in parts:
        m = _POSTAL_RE.search(p)
        if m and p is not parts[0]:
            out.setdefault("postal_code", m.group(0).upper())
            rest = clean_whitespace(p[: m.start()] + " " + p[m.end():])
            if rest:
                cleaned.append(rest)
        else:
            cleaned.append(p)
    parts = cleaned
    cc = country_code(default_country) if default_country else None
    if parts:
        c = country_code(parts[-1])
        if c and len(parts) > 1:
            cc = c
            out["country"] = country_name(c) or parts[-1]
            parts = parts[:-1]
    if parts and len(parts) > 1:
        reg = lookup_region(parts[-1], cc)
        if reg:
            out["province"] = reg[1]
            if not cc:
                cc = reg[0]
            parts = parts[:-1]
    for city in known_cities:
        for p in parts:
            if fold(city) in fold(p):
                out["city"] = city
                break
        if "city" in out:
            break
    if "city" not in out and len(parts) >= 2:
        candidate = parts[-1]
        if not re.search(r"\d", candidate) and len(candidate) <= 40:
            out["city"] = candidate
    if cc and "country" not in out:
        out["country"] = country_name(cc) or ""
    return {k: v for k, v in out.items() if v}


def same_domain(a: Optional[str], b: Optional[str]) -> bool:
    return bool(a and b and registered_domain(a) == registered_domain(b))

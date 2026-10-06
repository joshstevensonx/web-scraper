"""Target matching (section 5): required, preferred and excluded criteria."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import phonenumbers

from ..job.models import JobConfig, Location
from ..util.countries import country_code, country_name
from ..util.text import any_phrase, as_list, contains_phrase, fold_words
from .query_generator import synonyms_for


@dataclass
class MatchResult:
    matched: bool
    score: float  # 0..1 share of required + preferred criteria satisfied
    checks: List[Dict[str, Any]] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)
    matched_industry: Optional[str] = None


def _num(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    m = re.search(r"-?\d+(?:[.,]\d+)?", str(value).replace(" ", ""))
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", "."))
    except ValueError:
        return None


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


class TargetMatcher:
    def __init__(self, job: JobConfig):
        self.job = job
        self.targeting = job.targeting
        self._industry_terms = self._expand(self.targeting.required.industry)
        self._pref_industry_terms = self._expand(self.targeting.preferred.industry)

    def _expand(self, terms: List[str]) -> List[str]:
        out = list(terms)
        if self.targeting.expand_synonyms:
            for t in terms:
                out.extend(s for s in synonyms_for(t) if s not in out)
        return out

    # ------------------------------------------------------------------------------------------
    def _location_ok(self, loc: Location, record: Dict[str, Any], text: str) -> Optional[bool]:
        """None = criterion not set; True/False = matched or not."""
        if loc.is_empty():
            return None
        if loc.latitude is not None and loc.longitude is not None and loc.radius_km:
            lat, lon = _num(record.get("latitude")), _num(record.get("longitude"))
            if lat is not None and lon is not None:
                return _haversine_km(loc.latitude, loc.longitude, lat, lon) <= loc.radius_km
        for level in ("suburb", "city", "postal_code", "province", "country"):
            values = getattr(loc, level)
            if not values:
                continue
            candidates = list(values)
            if level == "country":
                for v in values:
                    name = country_name(v)
                    if name:
                        candidates.append(name)
                # Phone country prefix is strong evidence for country-only targeting.
                phone = str(record.get("phone") or "")
                for v in values:
                    code = country_code(v)
                    if code and phone.startswith("+"):
                        try:
                            if phonenumbers.region_code_for_number(phonenumbers.parse(phone)) == code:
                                return True
                        except phonenumbers.NumberParseException:
                            pass
            record_loc = fold_words(" ".join(str(record.get(k) or "") for k in
                                             ("address", "city", "suburb", "province", "country", "postal_code",
                                              "location")))
            if any_phrase(record_loc, candidates) or any_phrase(text, candidates):
                return True
            return False  # only the most specific level decides
        return None

    def _terms_ok(self, terms: List[str], text: str) -> Optional[str]:
        return any_phrase(text, terms) if terms else None

    def _field_ok(self, name: str, expected: Any, record: Dict[str, Any]) -> bool:
        value = record.get(name)
        if isinstance(expected, dict) and ("min" in expected or "max" in expected):
            n = _num(value)
            if n is None:
                return False
            if expected.get("min") is not None and n < float(expected["min"]):
                return False
            if expected.get("max") is not None and n > float(expected["max"]):
                return False
            return True
        hay = fold_words(" ".join(str(v) for v in as_list(value)))
        return any(contains_phrase(hay, str(e)) for e in as_list(expected))

    # ------------------------------------------------------------------------------------------
    def match(self, record: Dict[str, Any], page_text: str = "", record_text: str = "") -> MatchResult:
        """Evaluate a normalized record.

        ``page_text`` is (folded) text of the pages the record came from: it is used to confirm
        required location/industry. Exclusions are checked against ``record_text`` only (record
        fields plus page title/description) so that footers mentioning e.g. "government" do not
        reject every record.
        """
        tgt = self.targeting
        req, pref, exc = tgt.required, tgt.preferred, tgt.excluded
        record_folded = record_text or fold_words(" ".join(
            str(v) for k, v in record.items() if not k.startswith("_") and v not in (None, "")))
        text = (record_folded + " " + page_text).strip()
        checks: List[Dict[str, Any]] = []
        reasons: List[str] = []
        total = satisfied = 0.0
        matched = True
        matched_industry = None

        # Required ---------------------------------------------------------------------------
        loc_ok = self._location_ok(req.location, record, text)
        if loc_ok is not None:
            total += 2
            checks.append({"check": "location", "kind": "required", "passed": loc_ok,
                           "expected": req.location.label()})
            if loc_ok:
                satisfied += 2
            elif tgt.strict:
                matched = False
                reasons.append("location does not match %s" % req.location.label())
        if self._industry_terms:
            hit = self._terms_ok(self._industry_terms, text)
            total += 2
            checks.append({"check": "industry", "kind": "required", "passed": bool(hit),
                           "expected": ", ".join(req.industry)})
            if hit:
                satisfied += 2
                matched_industry = next((t for t in req.industry if hit in [t] + synonyms_for(t)), req.industry[0])
            elif tgt.strict:
                matched = False
                reasons.append("industry does not match %s" % ", ".join(req.industry))
        if req.keywords:
            hit = self._terms_ok(req.keywords, text)
            total += 1
            checks.append({"check": "keywords", "kind": "required", "passed": bool(hit),
                           "expected": ", ".join(req.keywords)})
            if hit:
                satisfied += 1
            elif tgt.strict:
                matched = False
                reasons.append("none of the required keywords found")
        if req.terms:
            hit = self._terms_ok(req.terms, text)
            total += 1
            checks.append({"check": "terms", "kind": "required", "passed": bool(hit), "expected": ", ".join(req.terms)})
            if hit:
                satisfied += 1
            elif tgt.strict:
                matched = False
                reasons.append("required terms not found")
        for name, expected in req.fields.items():
            ok = self._field_ok(name, expected, record)
            total += 1
            checks.append({"check": name, "kind": "required", "passed": ok, "expected": expected})
            if ok:
                satisfied += 1
            else:
                matched = False
                reasons.append("%s does not match %s" % (name, expected))

        # Preferred (score only) -------------------------------------------------------------
        pref_loc = self._location_ok(pref.location, record, text)
        if pref_loc is not None:
            total += 1
            satisfied += 1 if pref_loc else 0
            checks.append({"check": "location", "kind": "preferred", "passed": pref_loc})
        if self._pref_industry_terms:
            hit = self._terms_ok(self._pref_industry_terms, text)
            total += 1
            satisfied += 1 if hit else 0
            checks.append({"check": "industry", "kind": "preferred", "passed": bool(hit)})
            if hit and not matched_industry:
                matched_industry = pref.industry[0]
        for kw in pref.keywords:
            hit = contains_phrase(text, kw)
            total += 0.5
            satisfied += 0.5 if hit else 0
            checks.append({"check": "keyword:%s" % kw, "kind": "preferred", "passed": hit})
        for name, expected in pref.fields.items():
            ok = self._field_ok(name, expected, record)
            total += 0.5
            satisfied += 0.5 if ok else 0
            checks.append({"check": name, "kind": "preferred", "passed": ok, "expected": expected})

        # Excluded ---------------------------------------------------------------------------
        for term in exc.terms + exc.keywords + exc.industry:
            if contains_phrase(record_folded, term):
                matched = False
                reasons.append("excluded term %r present" % term)
                checks.append({"check": "exclude:%s" % term, "kind": "excluded", "passed": False})
        if not exc.location.is_empty() and self._location_ok(exc.location, record, record_folded):
            matched = False
            reasons.append("record is in an excluded location")
        for name, expected in exc.fields.items():
            if record.get(name) not in (None, "") and self._field_ok(name, expected, record):
                matched = False
                reasons.append("%s matches excluded value %s" % (name, expected))
                checks.append({"check": name, "kind": "excluded", "passed": False})

        score = satisfied / total if total else 1.0
        return MatchResult(matched=matched, score=score, checks=checks, reasons=reasons,
                           matched_industry=matched_industry)

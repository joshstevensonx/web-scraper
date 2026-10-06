"""Record assembly: merges candidate records, normalizes, validates, scores and deduplicates.

Candidates from many pages are folded into :class:`RecordBuilder` objects. Builders are keyed by
strong identifiers (website domain, canonical URL, e-mail, phone, name + address), so exact
duplicates merge as soon as they are seen (section 20); a fuzzy name pass runs at the end.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any, Dict, Iterable, List, Optional, Set

import phonenumbers

from ..extraction.field_extractor import CandidateRecord, FieldValue
from ..extraction.patterns import is_entity_candidate_domain
from ..job.models import FieldSpec, JobConfig
from ..target.matcher import MatchResult, TargetMatcher
from ..util.countries import country_code, country_name
from ..util.text import fold, fold_words
from ..util.urls import normalize_url, registered_domain
from .normalization import NormalizationError, Normalizer, name_key, normalize_email, normalize_phone, \
    parse_address
from .quality import quality_score
from .validation import required_ok, validate_fields

MAX_TEXTS = 6
MAX_TEXT_LEN = 30_000


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class EvaluatedRecord:
    values: Dict[str, Any]
    provenance: Dict[str, Any]
    validation: List[Dict[str, Any]]
    match: MatchResult
    quality: float
    quality_components: Dict[str, int]
    valid: bool
    reasons: List[str]


@dataclass
class RecordBuilder:
    id: str
    values: Dict[str, List[FieldValue]] = field(default_factory=dict)
    sources: List[str] = field(default_factory=list)
    texts: List[str] = field(default_factory=list)
    context: List[str] = field(default_factory=list)
    keys: Set[str] = field(default_factory=set)
    structured: bool = False
    raw: List[Dict[str, Any]] = field(default_factory=list)
    source_score: float = 0.0
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    result: Optional[EvaluatedRecord] = None
    dirty: bool = True
    enrich_requested: bool = False
    discoveries: Set[str] = field(default_factory=set)  # pages that found this record independently

    @property
    def domain(self) -> Optional[str]:
        for k in sorted(self.keys):
            if k.startswith("domain:"):
                return k[7:]
        return None

    def absorb_values(self, fields: Dict[str, List[FieldValue]]) -> None:
        for name, fvs in fields.items():
            bucket = self.values.setdefault(name, [])
            for fv in fvs:
                dup = next((x for x in bucket if x.value == fv.value), None)
                if dup is None:
                    bucket.append(fv)
                elif fv.confidence > dup.confidence:
                    bucket[bucket.index(dup)] = fv

    def add_text(self, text: str) -> None:
        if text and len(self.texts) < MAX_TEXTS:
            self.texts.append(text[:MAX_TEXT_LEN])


class RecordStore:
    def __init__(self, job: JobConfig, normalizer: Optional[Normalizer] = None):
        self.job = job
        self.mode = job.record_mode
        loc = job.targeting.location
        self.region = country_code(loc.country[0]) if loc.country else None
        self.normalizer = normalizer or Normalizer(self.region)
        self.matcher = TargetMatcher(job)
        self.builders: Dict[str, RecordBuilder] = {}
        self.key_index: Dict[str, str] = {}
        self.candidates_seen = 0
        self.duplicates_merged = 0
        self.city_words = tuple(loc.city + loc.suburb + loc.province + loc.country)
        self._spec_by_role: Dict[str, FieldSpec] = {}
        for spec in job.fields:
            self._spec_by_role.setdefault(spec.role, spec)

    # ------------------------------------------------------------------------------------------
    # Keys
    # ------------------------------------------------------------------------------------------
    def candidate_keys(self, cand: CandidateRecord) -> Set[str]:
        keys: Set[str] = set()
        for spec in self.job.fields:
            for fv in cand.fields.get(spec.name, []):
                keys |= self._keys_for_value(spec, fv, cand)
        name_spec = self._spec_by_role.get("name")
        addr_spec = self._spec_by_role.get("address")
        if name_spec and addr_spec:
            names = [fv for fv in cand.fields.get(name_spec.name, []) if fv.confidence >= 0.9]
            addrs = [fv for fv in cand.fields.get(addr_spec.name, []) if fv.confidence >= 0.9]
            if names and addrs:
                nk = name_key(str(names[0].value), self.city_words)
                ak = fold_words(str(addrs[0].value))
                if nk and ak:
                    keys.add("na:%s|%s" % (nk, ak))
        return keys

    def _keys_for_value(self, spec: FieldSpec, fv: FieldValue, cand: CandidateRecord) -> Set[str]:
        out: Set[str] = set()
        values = fv.value if isinstance(fv.value, list) else [fv.value]
        for v in values:
            if v in (None, ""):
                continue
            if spec.role == "website" and self.mode == "site" and fv.confidence >= 0.75:
                u = normalize_url(str(v) if "://" in str(v) else "http://" + str(v))
                if u and is_entity_candidate_domain(u):
                    out.add("domain:" + registered_domain(u))
            elif spec.role == "source_url" and self.mode == "page":
                u = normalize_url(str(v))
                if u:
                    out.add("url:" + u)
            elif spec.role == "email" and self.mode == "site" and fv.confidence >= 0.85:
                try:
                    out.add("email:" + normalize_email(v))
                except NormalizationError:
                    pass
            elif spec.role == "phone" and self.mode == "site" and fv.confidence >= 0.9:
                try:
                    out.add("phone:" + normalize_phone(v, self.region))
                except NormalizationError:
                    pass
            elif spec.role == "sku" and self.mode == "page" and fv.confidence >= 0.9:
                out.add("sku:%s:%s" % (registered_domain(cand.source_url), fold(str(v))))
        return out

    # ------------------------------------------------------------------------------------------
    # Adding candidates
    # ------------------------------------------------------------------------------------------
    def add(self, cand: CandidateRecord, group_key: Optional[str], match_text: str = "", context_text: str = "",
            source_score: float = 1.0, independent: bool = True) -> RecordBuilder:
        """Fold a candidate into the matching record (or start a new one).

        ``independent`` is False for enrichment visits (a company's contact page); the same entity
        found independently on another page counts as a removed duplicate.
        """
        self.candidates_seen += 1
        keys = self.candidate_keys(cand)
        if group_key:
            keys.add(group_key)
        hits: List[str] = []
        for k in keys:
            bid = self.key_index.get(k)
            if bid and bid not in hits and bid in self.builders:
                hits.append(bid)
        if hits:
            hits.sort(key=lambda b: self.builders[b].created_at)
            target = self.builders[hits[0]]
            if independent and target.discoveries and cand.source_url not in target.discoveries:
                self.duplicates_merged += 1
            for other_id in hits[1:]:
                self._merge(target, self.builders[other_id])
                self.duplicates_merged += 1
        else:
            target = RecordBuilder(id=uuid.uuid4().hex[:12])
            self.builders[target.id] = target
        target.absorb_values(cand.fields)
        if independent:
            target.discoveries.add(cand.source_url)
        if cand.source_url not in target.sources:
            target.sources.append(cand.source_url)
        target.add_text(fold_words(match_text))
        if context_text:
            target.context.append(context_text[:500])
        target.structured = target.structured or cand.structured
        if cand.raw:
            target.raw.append(cand.raw)
        target.source_score = max(target.source_score, source_score)
        target.keys |= keys
        for k in keys:
            self.key_index[k] = target.id
        target.updated_at = _now()
        target.dirty = True
        return target

    def _merge(self, into: RecordBuilder, other: RecordBuilder) -> None:
        into.absorb_values(other.values)
        for s in other.sources:
            if s not in into.sources:
                into.sources.append(s)
        for t in other.texts:
            into.add_text(t)
        into.context.extend(other.context[:5])
        into.keys |= other.keys
        into.structured = into.structured or other.structured
        into.raw.extend(other.raw)
        into.source_score = max(into.source_score, other.source_score)
        into.enrich_requested = into.enrich_requested or other.enrich_requested
        into.discoveries |= other.discoveries
        for k in other.keys:
            self.key_index[k] = into.id
        into.dirty = True
        self.builders.pop(other.id, None)

    # ------------------------------------------------------------------------------------------
    # Evaluation
    # ------------------------------------------------------------------------------------------
    def _rank(self, spec: FieldSpec, fv: FieldValue, domain: Optional[str]) -> float:
        score = fv.confidence
        if spec.role == "email" and domain:
            email_domain = registered_domain(str(fv.value).rsplit("@", 1)[-1])
            if email_domain == domain:
                score += 0.12
            elif email_domain in ("gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "icloud.com"):
                score -= 0.02
            else:
                score -= 0.1
            if re.match(r"^(info|hello|contact|enquiries|inquiries|sales|office|admin)@", str(fv.value), re.I):
                score += 0.02
        if spec.role == "phone" and self.region:
            try:
                num = phonenumbers.parse(str(fv.value), self.region)
                if phonenumbers.region_code_for_number(num) == self.region:
                    score += 0.04
            except phonenumbers.NumberParseException:
                score -= 0.2
        return score

    def evaluate(self, b: RecordBuilder) -> EvaluatedRecord:
        if not b.dirty and b.result is not None:
            return b.result
        values: Dict[str, Any] = {}
        provenance: Dict[str, Any] = {}
        attempted: Dict[str, bool] = {}
        format_errors: Dict[str, str] = {}
        domain = b.domain
        for spec in self.job.fields:
            cands = b.values.get(spec.name, [])
            attempted[spec.name] = bool(cands)
            ranked = sorted(cands, key=lambda fv: -self._rank(spec, fv, domain))
            if spec.type == "array":
                items: List[Any] = []
                provs: List[Dict[str, Any]] = []
                for fv in ranked:
                    try:
                        normalized = self.normalizer.normalize(spec, fv.value, fv.source)
                    except NormalizationError as exc:
                        format_errors.setdefault(spec.name, str(exc))
                        continue
                    for item in normalized:
                        if item not in items:
                            items.append(item)
                            p = fv.as_dict()
                            p["value"] = item
                            provs.append(p)
                if items:
                    values[spec.name] = items
                    provenance[spec.name] = provs
            else:
                for fv in ranked:
                    try:
                        normalized = self.normalizer.normalize(spec, fv.value, fv.source)
                    except NormalizationError as exc:
                        format_errors.setdefault(spec.name, str(exc))
                        continue
                    if spec.validation and not re.search(spec.validation, str(normalized)):
                        format_errors.setdefault(spec.name, "does not match validation rule")
                        continue
                    values[spec.name] = normalized
                    p = fv.as_dict()
                    p["value"] = normalized
                    provenance[spec.name] = p
                    break
            if spec.name not in values and spec.default not in (None, ""):
                values[spec.name] = spec.default
                provenance[spec.name] = {"value": spec.default, "method": "default", "confidence": 0.3,
                                         "source": "", "extracted_at": _now()}

        self._derive(b, values, provenance)
        page_text = " ".join(b.texts)
        record_text = fold_words(" ".join(
            [str(v) for k, v in values.items() if not isinstance(v, (list, dict))] + b.context))
        match = self.matcher.match(values, page_text=page_text, record_text=record_text)
        industry_spec = self._spec_by_role.get("industry")
        if industry_spec and industry_spec.name not in values and match.matched_industry:
            values[industry_spec.name] = match.matched_industry
            provenance[industry_spec.name] = {"value": match.matched_industry, "method": "target-match",
                                              "confidence": 0.5, "source": b.sources[0] if b.sources else "",
                                              "extracted_at": _now()}
        validation = validate_fields(self.job.fields, values, format_errors)
        for c in match.checks:
            validation.append({"field": c["check"], "category": "target", "passed": c["passed"],
                               "message": "%s criterion" % c["kind"]})
        strong_id = any(k.split(":", 1)[0] in ("domain", "url", "email", "phone", "sku") for k in b.keys)
        q = quality_score(self.job.fields, values, attempted, provenance, match.score,
                          b.source_score or 0.5, strong_id)
        reasons: List[str] = []
        req_ok = required_ok(validation)
        if not req_ok:
            missing = [r["field"] for r in validation if r["category"] == "schema" and not r["passed"]]
            reasons.append("missing required field(s): %s" % ", ".join(missing))
        bad_format = [r["field"] for r in validation if r["category"] == "format" and not r["passed"]
                      and self.job.field(r["field"]) is not None and self.job.field(r["field"]).required]
        if bad_format:
            reasons.append("invalid value for %s" % ", ".join(bad_format))
        reasons.extend(match.reasons)
        valid = req_ok and not bad_format and match.matched
        if valid and q["score"] < self.job.records.min_quality:
            valid = False
            reasons.append("quality %.0f below minimum %.0f" % (q["score"], self.job.records.min_quality))
        b.result = EvaluatedRecord(values=values, provenance=provenance, validation=validation, match=match,
                                   quality=q["score"], quality_components=q["components"], valid=valid,
                                   reasons=reasons)
        b.dirty = False
        return b.result

    def _derive(self, b: RecordBuilder, values: Dict[str, Any], provenance: Dict[str, Any]) -> None:
        """Fill location components from the address, the phone prefix or the target location."""
        loc = self.job.targeting.location
        wanted = {role: self._spec_by_role[role].name for role in ("city", "province", "country", "postal_code",
                                                                     "suburb") if role in self._spec_by_role}
        if not wanted:
            return
        addr_spec = self._spec_by_role.get("address")
        address = values.get(addr_spec.name) if addr_spec else None
        derived_src = (provenance.get(addr_spec.name) or {}).get("source", "") if addr_spec else ""

        def put(role: str, value: Optional[str], method: str, conf: float, source: str = "") -> None:
            name = wanted.get(role)
            if name and value and values.get(name) in (None, ""):
                values[name] = value
                provenance[name] = {"value": value, "method": method, "confidence": conf,
                                    "source": source or (b.sources[0] if b.sources else ""), "extracted_at": _now()}

        if address:
            parts = parse_address(str(address), loc.country[0] if loc.country else None,
                                  tuple(loc.suburb + loc.city))
            for role in ("city", "province", "country", "postal_code"):
                put(role, parts.get(role), "address-parse", 0.7, derived_src)
        city_name = wanted.get("city")
        if city_name and values.get(city_name) in (None, "") and loc.city:
            text = " ".join(b.texts)
            for city in loc.city:
                if city and re.search(r"(?<!\w)" + re.escape(fold_words(city)) + r"(?!\w)", text):
                    put("city", city, "target-inference", 0.5)
                    break
        city_val = values.get(city_name) if city_name else None
        if city_val and any(fold(city_val) == fold(c) for c in loc.city):
            if loc.province:
                put("province", loc.province[0], "target-location", 0.6)
            if loc.country:
                put("country", country_name(loc.country[0]) or loc.country[0], "target-location", 0.6)
        phone_spec = self._spec_by_role.get("phone")
        phone = values.get(phone_spec.name) if phone_spec else None
        if isinstance(phone, list):
            phone = phone[0] if phone else None
        if phone and "country" in wanted:
            try:
                code = phonenumbers.region_code_for_number(phonenumbers.parse(str(phone)))
                put("country", country_name(code), "phone-prefix", 0.6)
            except phonenumbers.NumberParseException:
                pass

    # ------------------------------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------------------------------
    def evaluate_all(self) -> None:
        for b in list(self.builders.values()):
            if b.dirty:
                self.evaluate(b)

    def valid_count(self) -> int:
        self.evaluate_all()
        return sum(1 for b in self.builders.values() if b.result and b.result.valid)

    def counts(self) -> Dict[str, Any]:
        self.evaluate_all()
        valid = [b for b in self.builders.values() if b.result and b.result.valid]
        avg_q = sum(b.result.quality for b in valid) / len(valid) if valid else 0.0
        return {
            "records_extracted": self.candidates_seen,
            "records_total": len(self.builders),
            "records_valid": len(valid),
            "records_rejected": len(self.builders) - len(valid),
            "duplicates_removed": self.duplicates_merged,
            "average_quality": round(avg_q, 1),
        }

    def find(self, key: str) -> Optional[RecordBuilder]:
        bid = self.key_index.get(key)
        return self.builders.get(bid) if bid else None

    def fuzzy_dedupe(self, threshold: float = 0.92) -> int:
        """Merge records whose names are near-identical and whose websites do not conflict."""
        if self.mode != "site":
            return 0
        name_spec = self._spec_by_role.get("name")
        if not name_spec:
            return 0
        self.evaluate_all()
        blocks: Dict[str, List[RecordBuilder]] = {}
        for b in self.builders.values():
            nm = b.result.values.get(name_spec.name) if b.result else None
            if not nm:
                continue
            k = name_key(str(nm), self.city_words)
            if not k:
                continue
            blocks.setdefault(k.split(" ")[0], []).append(b)
        merged = 0
        for group in blocks.values():
            if len(group) < 2:
                continue
            alive = list(group)
            i = 0
            while i < len(alive):
                a = alive[i]
                ka = name_key(str(a.result.values.get(name_spec.name)), self.city_words)
                j = i + 1
                while j < len(alive):
                    c = alive[j]
                    kc = name_key(str(c.result.values.get(name_spec.name)), self.city_words)
                    same_name = ka == kc or SequenceMatcher(None, ka, kc).ratio() >= threshold
                    da, dc = a.domain, c.domain
                    if same_name and not (da and dc and da != dc):
                        self._merge(a, c)
                        self.evaluate(a)
                        merged += 1
                        alive.pop(j)
                    else:
                        j += 1
                i += 1
        self.duplicates_merged += merged
        return merged

    def final(self, include_rejected: bool = False) -> List[RecordBuilder]:
        self.evaluate_all()
        valid = sorted((b for b in self.builders.values() if b.result.valid),
                       key=lambda b: (-b.result.quality, b.created_at))
        out = valid[: self.job.records.maximum]
        if include_rejected:
            out += sorted((b for b in self.builders.values() if not b.result.valid),
                          key=lambda b: -b.result.quality)
        return out

    def all_builders(self) -> Iterable[RecordBuilder]:
        return self.builders.values()

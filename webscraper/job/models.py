"""Scraping job configuration: input parsing and validation (sections 2-7, 21-24 of the spec).

A job is plain JSON. :func:`parse_job` accepts the full schema from the spec as well as a few
shorthands (``"count": 100``, ``"range": "100-500"``, plain URL strings as sources, flat location
keys) and returns a fully-defaulted :class:`JobConfig`.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from ..target.entities import DEFAULT_TYPE_BY_ROLE, EntityProfile, field_role, get_entity
from ..util.text import as_list, clean_whitespace, slugify

DATA_TYPES = {
    "text", "number", "integer", "decimal", "boolean", "date", "datetime", "url", "email", "phone",
    "address", "array", "object", "json",
}
TYPE_ALIASES = {"string": "text", "str": "text", "int": "integer", "float": "decimal", "bool": "boolean",
                "list": "array", "dict": "object", "link": "url", "telephone": "phone", "time": "datetime"}
OUTPUT_FORMATS = ("csv", "xlsx", "json", "jsonl", "ndjson", "xml", "sql", "parquet")
SOURCE_TYPES = ("auto", "directory", "listing", "website", "detail", "sitemap", "api", "feed")
SEARCH_PROVIDERS = ("auto", "duckduckgo", "brave", "searxng", "none")

LOCATION_KEYS = {
    "country": "country", "province": "province", "state": "province", "region": "province",
    "city": "city", "town": "city", "suburb": "suburb", "district": "suburb", "neighbourhood": "suburb",
    "neighborhood": "suburb", "postal_code": "postal_code", "postcode": "postal_code", "zip": "postal_code",
    "zip_code": "postal_code", "address": "address", "radius": "radius_km", "radius_km": "radius_km",
    "latitude": "latitude", "lat": "latitude", "longitude": "longitude", "lng": "longitude", "lon": "longitude",
}
INDUSTRY_KEYS = {"industry", "industries", "sub_industry", "sector", "sectors", "category", "categories",
                 "business_category", "business_type", "niche"}
KEYWORD_KEYS = {"keywords", "keyword", "terms", "phrases", "tags", "technology", "technologies"}


class JobConfigError(ValueError):
    """Raised when a job configuration is invalid. ``errors`` lists every problem found."""

    def __init__(self, errors: List[str]):
        self.errors = errors
        super().__init__("Invalid job configuration:\n  - " + "\n  - ".join(errors))


@dataclass
class Location:
    country: List[str] = field(default_factory=list)
    province: List[str] = field(default_factory=list)
    city: List[str] = field(default_factory=list)
    suburb: List[str] = field(default_factory=list)
    postal_code: List[str] = field(default_factory=list)
    address: List[str] = field(default_factory=list)
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    radius_km: Optional[float] = None

    def is_empty(self) -> bool:
        return not (self.country or self.province or self.city or self.suburb or self.postal_code
                    or self.address or (self.latitude is not None and self.longitude is not None))

    def most_specific(self) -> List[str]:
        """Values of the most specific populated level (suburb > city > postal code > province > country)."""
        for level in (self.suburb, self.city, self.postal_code, self.province, self.country):
            if level:
                return level
        return []

    def label(self) -> str:
        parts = [", ".join(v) for v in (self.suburb, self.city, self.province, self.country) if v]
        return ", ".join(parts)


@dataclass
class Criteria:
    location: Location = field(default_factory=Location)
    industry: List[str] = field(default_factory=list)
    keywords: List[str] = field(default_factory=list)
    terms: List[str] = field(default_factory=list)
    fields: Dict[str, Any] = field(default_factory=dict)

    def is_empty(self) -> bool:
        return self.location.is_empty() and not (self.industry or self.keywords or self.terms or self.fields)


@dataclass
class Targeting:
    description: str = ""
    required: Criteria = field(default_factory=Criteria)
    preferred: Criteria = field(default_factory=Criteria)
    excluded: Criteria = field(default_factory=Criteria)
    expand_synonyms: bool = True
    # When true a record must mention the required location/industry somewhere on its pages.
    strict: bool = True

    @property
    def location(self) -> Location:
        """Location used for queries/normalization: required first, then preferred."""
        return self.required.location if not self.required.location.is_empty() else self.preferred.location

    def industries(self) -> List[str]:
        return self.required.industry or self.preferred.industry

    def keywords(self) -> List[str]:
        return self.required.keywords + [k for k in self.preferred.keywords if k not in self.required.keywords]


@dataclass
class FieldSpec:
    name: str
    label: str = ""
    type: str = "text"
    required: bool = False
    priority: str = "medium"
    selector: Optional[str] = None
    xpath: Optional[str] = None
    attribute: Optional[str] = None
    regex: Optional[str] = None
    jsonld: Optional[str] = None
    meta: Optional[str] = None
    validation: Optional[str] = None
    transform: Optional[str] = None
    default: Any = None
    role: str = "custom"

    @property
    def weight(self) -> float:
        return {"high": 3.0, "medium": 2.0, "low": 1.0}.get(self.priority, 2.0)


@dataclass
class SourceProfile:
    url: str
    name: str = ""
    type: str = "auto"
    priority: int = 5
    max_pages: Optional[int] = None
    max_depth: Optional[int] = None
    rate_limit: Optional[float] = None
    render_js: Optional[str] = None
    record_selector: Optional[str] = None
    detail_link_selector: Optional[str] = None
    detail_link_pattern: Optional[str] = None
    pagination_selector: Optional[str] = None
    follow_pattern: Optional[str] = None
    exclude_pattern: Optional[str] = None
    fields: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    records_path: Optional[str] = None
    field_map: Dict[str, str] = field(default_factory=dict)
    page_param: Optional[str] = None
    max_api_pages: int = 10
    headers: Dict[str, str] = field(default_factory=dict)
    origin: str = "user"  # "user" or "discovered"
    score: float = 1.0


@dataclass
class SourcesConfig:
    mode: str = "auto"  # auto | manual | hybrid
    urls: List[SourceProfile] = field(default_factory=list)
    search_provider: str = "auto"
    max_queries: int = 8
    results_per_query: int = 15
    max_sources: int = 30
    use_sitemaps: bool = True
    include_domains: List[str] = field(default_factory=list)
    exclude_domains: List[str] = field(default_factory=list)
    discover_search_engines: bool = True
    discover_directories: bool = True
    discover_company_websites: bool = True
    discover_industry_directories: bool = True


@dataclass
class RecordsConfig:
    minimum: int = 10
    maximum: int = 100
    stop_at_minimum: bool = False
    min_quality: float = 0.0


@dataclass
class CrawlConfig:
    max_pages: int = 0  # 0 = derive from the record target
    max_depth: int = 3
    concurrency: int = 6
    delay: float = 1.0
    timeout: float = 20.0
    retries: int = 2
    respect_robots: bool = True
    render_js: str = "auto"  # auto | never | always
    user_agent: str = ""
    max_pages_per_domain: int = 150
    enrich_pages: int = 3
    enrich_from_website: bool = True
    max_response_bytes: int = 5_000_000
    keep_raw_pages: bool = False
    record_mode: str = "auto"  # auto | site | page
    max_runtime_minutes: float = 0  # 0 = unlimited


@dataclass
class OutputConfig:
    format: str = "xlsx"
    formats: List[str] = field(default_factory=list)
    filename: str = ""
    directory: str = ""
    encoding: str = "utf-8"
    include_source: bool = True
    include_timestamp: bool = True
    include_quality_score: bool = True
    include_provenance: bool = False
    include_raw_data: bool = False
    include_rejected: bool = False

    def all_formats(self) -> List[str]:
        seen: List[str] = []
        for fmt in [self.format] + list(self.formats):
            fmt = "jsonl" if fmt == "ndjson" else fmt
            if fmt and fmt not in seen:
                seen.append(fmt)
        return seen


@dataclass
class JobConfig:
    id: str
    name: str
    entity: str
    targeting: Targeting
    fields: List[FieldSpec]
    sources: SourcesConfig
    records: RecordsConfig
    crawl: CrawlConfig
    output: OutputConfig

    @property
    def entity_profile(self) -> EntityProfile:
        return get_entity(self.entity)

    @property
    def record_mode(self) -> str:
        if self.crawl.record_mode in ("site", "page"):
            return self.crawl.record_mode
        return self.entity_profile.record_mode

    def field(self, name: str) -> Optional[FieldSpec]:
        for f in self.fields:
            if f.name == name:
                return f
        return None

    @property
    def page_budget(self) -> int:
        if self.crawl.max_pages > 0:
            return self.crawl.max_pages
        per_record = 5 if self.record_mode == "site" else 3
        return max(100, min(5000, self.records.maximum * per_record))

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def summary(self) -> Dict[str, Any]:
        loc = self.targeting.location.label()
        return {
            "id": self.id,
            "name": self.name,
            "entity": self.entity_profile.label,
            "target": self.targeting.description or ", ".join(self.targeting.industries()),
            "location": loc,
            "fields": [f.name for f in self.fields],
            "sources": "Automatic discovery" if self.sources.mode == "auto" else
                       "%d source(s) (%s)" % (len(self.sources.urls), self.sources.mode),
            "records": "%d–%d" % (self.records.minimum, self.records.maximum),
            "output": ", ".join(f.upper() for f in self.output.all_formats()),
        }


# ---------------------------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------------------------

def _bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in ("1", "true", "yes", "y", "on")


def _int(value: Any, default: int, errors: List[str], name: str) -> int:
    if value is None or value == "":
        return default
    try:
        return int(str(value).replace(",", "").replace(" ", ""))
    except ValueError:
        errors.append("%s must be a whole number (got %r)" % (name, value))
        return default


def _float(value: Any, default: Optional[float], errors: List[str], name: str) -> Optional[float]:
    if value is None or value == "":
        return default
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        errors.append("%s must be a number (got %r)" % (name, value))
        return default


def _strings(value: Any) -> List[str]:
    return [clean_whitespace(str(v)) for v in as_list(value) if clean_whitespace(str(v))]


def _parse_location(raw: Dict[str, Any], errors: List[str], where: str) -> Location:
    loc = Location()
    for key, value in raw.items():
        target = LOCATION_KEYS.get(key)
        if target is None:
            continue
        if target in ("latitude", "longitude", "radius_km"):
            setattr(loc, target, _float(value, None, errors, "%s.%s" % (where, key)))
        else:
            getattr(loc, target).extend(v for v in _strings(value) if v not in getattr(loc, target))
    return loc


def _parse_criteria(raw: Any, errors: List[str], where: str) -> Criteria:
    crit = Criteria()
    if raw in (None, ""):
        return crit
    if isinstance(raw, list):  # ["Government", "Non-profit"] shorthand (mostly for exclusions)
        crit.terms = _strings(raw)
        return crit
    if not isinstance(raw, dict):
        errors.append("%s must be an object" % where)
        return crit
    flat_location = {k: v for k, v in raw.items() if k in LOCATION_KEYS}
    loc_raw = raw.get("location")
    if isinstance(loc_raw, str):
        loc_raw = {"city": loc_raw}
    merged = dict(flat_location)
    if isinstance(loc_raw, dict):
        merged.update(loc_raw)
    crit.location = _parse_location(merged, errors, where + ".location")
    for key, value in raw.items():
        if key == "location" or key in LOCATION_KEYS:
            continue
        lowered = key.lower()
        if lowered in INDUSTRY_KEYS:
            crit.industry.extend(_strings(value))
        elif lowered in KEYWORD_KEYS:
            crit.keywords.extend(_strings(value))
        elif lowered.endswith("types") or lowered.endswith("_type") or lowered in ("exclude", "words", "statuses"):
            crit.terms.extend(_strings(value))
        else:
            crit.fields[key] = value
    return crit


def _parse_field(raw: Any, entity: EntityProfile, errors: List[str], index: int) -> Optional[FieldSpec]:
    if isinstance(raw, str):
        raw = {"name": raw}
    if not isinstance(raw, dict) or not raw.get("name"):
        errors.append("fields[%d] needs a name" % index)
        return None
    name = re.sub(r"[^a-z0-9_]+", "_", clean_whitespace(str(raw["name"])).lower()).strip("_")
    if not name:
        errors.append("fields[%d] has an invalid name %r" % (index, raw["name"]))
        return None
    role = field_role(name, entity)
    type_ = str(raw.get("type") or DEFAULT_TYPE_BY_ROLE.get(role, "text")).lower()
    type_ = TYPE_ALIASES.get(type_, type_)
    if type_ not in DATA_TYPES:
        errors.append("fields[%d] (%s) has unsupported type %r; use one of %s"
                      % (index, name, type_, ", ".join(sorted(DATA_TYPES))))
        type_ = "text"
    priority = str(raw.get("priority") or "medium").lower()
    if priority not in ("high", "medium", "low"):
        errors.append("fields[%d] (%s) priority must be high, medium or low" % (index, name))
        priority = "medium"
    for key in ("regex", "validation"):
        if raw.get(key):
            try:
                re.compile(raw[key])
            except re.error as exc:
                errors.append("fields[%d] (%s) %s is not a valid regular expression: %s" % (index, name, key, exc))
    label = raw.get("label") or name.replace("_", " ").title()
    return FieldSpec(
        name=name, label=str(label), type=type_, required=_bool(raw.get("required"), False), priority=priority,
        selector=raw.get("selector") or raw.get("css"), xpath=raw.get("xpath"), attribute=raw.get("attribute"),
        regex=raw.get("regex"), jsonld=raw.get("jsonld"), meta=raw.get("meta"), validation=raw.get("validation"),
        transform=raw.get("transform"), default=raw.get("default"), role=role,
    )


def _parse_source(raw: Any, errors: List[str], index: int) -> Optional[SourceProfile]:
    if isinstance(raw, str):
        raw = {"url": raw}
    if not isinstance(raw, dict) or not raw.get("url"):
        errors.append("sources.urls[%d] needs a url" % index)
        return None
    url = str(raw["url"]).strip()
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    stype = str(raw.get("type") or "auto").lower()
    stype = {"company_website": "website", "site": "website", "search": "listing", "category": "listing",
             "rss": "feed", "atom": "feed", "json": "api"}.get(stype, stype)
    if stype not in SOURCE_TYPES:
        errors.append("sources.urls[%d] type must be one of %s" % (index, ", ".join(SOURCE_TYPES)))
        stype = "auto"
    for key in ("detail_link_pattern", "follow_pattern", "exclude_pattern"):
        if raw.get(key):
            try:
                re.compile(raw[key])
            except re.error as exc:
                errors.append("sources.urls[%d].%s is not a valid regular expression: %s" % (index, key, exc))
    known = set(SourceProfile.__dataclass_fields__)
    kwargs = {k: v for k, v in raw.items() if k in known and k not in ("url", "type")}
    profile = SourceProfile(url=url, type=stype, **kwargs)
    profile.priority = _int(profile.priority, 5, errors, "sources.urls[%d].priority" % index)
    return profile


def _parse_records(raw: Dict[str, Any], top: Dict[str, Any], errors: List[str]) -> RecordsConfig:
    rec = RecordsConfig()
    count = raw.get("count", top.get("count"))
    rng = raw.get("range", top.get("range"))
    if count is not None:
        n = _int(count, 10, errors, "records.count")
        rec.minimum = rec.maximum = n
        rec.stop_at_minimum = True
    if isinstance(rng, str) and rng.strip():
        m = re.match(r"^\s*([\d,\s]+)\s*(?:-|–|to)\s*([\d,\s]+)\s*$", rng)
        if m:
            rec.minimum = _int(m.group(1), rec.minimum, errors, "records.range")
            rec.maximum = _int(m.group(2), rec.maximum, errors, "records.range")
        else:
            errors.append("records.range must look like '100-500' (got %r)" % rng)
    if "minimum" in raw or "min" in raw:
        rec.minimum = _int(raw.get("minimum", raw.get("min")), rec.minimum, errors, "records.minimum")
    if "maximum" in raw or "max" in raw:
        rec.maximum = _int(raw.get("maximum", raw.get("max")), rec.maximum, errors, "records.maximum")
        if "minimum" not in raw and "min" not in raw and count is None and rng is None:
            rec.minimum = min(rec.minimum, rec.maximum)
    elif ("minimum" in raw or "min" in raw) and count is None and rng is None:
        rec.maximum = max(rec.maximum, rec.minimum)
    rec.stop_at_minimum = _bool(raw.get("stop_at_minimum"), rec.stop_at_minimum)
    rec.min_quality = _float(raw.get("min_quality"), 0.0, errors, "records.min_quality") or 0.0
    if rec.minimum < 1:
        errors.append("records.minimum must be at least 1")
    if rec.maximum < rec.minimum:
        errors.append("records.maximum (%d) must be >= records.minimum (%d)" % (rec.maximum, rec.minimum))
    if rec.maximum > 100_000:
        errors.append("records.maximum must be 100,000 or less")
    if not 0 <= rec.min_quality <= 100:
        errors.append("records.min_quality must be between 0 and 100")
    return rec


_GENERIC_NOUNS = {"companies", "company", "businesses", "business", "firms", "firm", "list", "of", "the", "all",
                  "best", "top", "near", "me", "agencies", "providers", "services", "shops", "stores"}


def _infer_from_description(targeting: Targeting, entity: EntityProfile) -> None:
    """Turn a free-text target such as "Software companies in Cape Town" into criteria."""
    m = re.match(r"^(.*?)\s+(?:in|near|around|within)\s+(.+)$", targeting.description, re.I)
    if not m:
        return  # without a location the text is only used as a search query
    what, where = m.group(1), m.group(2)
    if where:
        parts = [p.strip() for p in where.split(",") if p.strip()]
        loc = targeting.required.location
        if parts:
            loc.city = [parts[0]]
        if len(parts) >= 3:
            loc.province, loc.country = [parts[1]], [parts[-1]]
        elif len(parts) == 2:
            loc.country = [parts[1]]
    nouns = {n.lower() for n in entity.nouns} | _GENERIC_NOUNS
    words = [w for w in re.split(r"\s+", what.strip()) if w and w.lower() not in nouns]
    if words:
        targeting.required.industry = [" ".join(words)]


def parse_job(data: Dict[str, Any], base_dir: Optional[str] = None) -> JobConfig:
    """Parse and validate a job definition (dict). Raises :class:`JobConfigError`."""
    if not isinstance(data, dict):
        raise JobConfigError(["The job must be a JSON object"])
    errors: List[str] = []

    entity_key = str(data.get("entity") or data.get("entity_type") or "business")
    entity = get_entity(entity_key)

    # --- targeting -------------------------------------------------------------------------
    t_raw = data.get("targeting") or data.get("target") or {}
    if not isinstance(t_raw, dict):
        errors.append("targeting must be an object")
        t_raw = {}
    targeting = Targeting(
        description=clean_whitespace(str(t_raw.get("description") or data.get("description") or "")),
        expand_synonyms=_bool(t_raw.get("expand_synonyms"), True),
        strict=_bool(t_raw.get("strict"), True),
    )
    has_sections = any(k in t_raw for k in ("required", "preferred", "excluded"))
    if has_sections:
        targeting.required = _parse_criteria(t_raw.get("required"), errors, "targeting.required")
        targeting.preferred = _parse_criteria(t_raw.get("preferred"), errors, "targeting.preferred")
        targeting.excluded = _parse_criteria(t_raw.get("excluded") or t_raw.get("exclude"), errors,
                                             "targeting.excluded")
    else:
        # Shorthand: top-level location/industry/keywords are required criteria.
        shorthand = {k: v for k, v in t_raw.items() if k not in ("description", "expand_synonyms", "strict")}
        targeting.required = _parse_criteria(shorthand, errors, "targeting")
    if not targeting.description:
        bits = targeting.industries() or targeting.keywords()[:2]
        noun = entity.nouns[0] if entity.nouns else "records"
        loc = targeting.location.label()
        if bits:
            targeting.description = "%s %s%s" % (" / ".join(bits), noun, (" in " + loc) if loc else "")

    # --- fields ------------------------------------------------------------------------------
    raw_fields = data.get("fields")
    if raw_fields in (None, [], ""):
        raw_fields = entity.default_fields
    if not isinstance(raw_fields, list):
        errors.append("fields must be a list")
        raw_fields = entity.default_fields
    fields: List[FieldSpec] = []
    seen_names = set()
    for i, raw in enumerate(raw_fields):
        spec = _parse_field(raw, entity, errors, i)
        if spec is None:
            continue
        if spec.name in seen_names:
            errors.append("Field %r is listed twice" % spec.name)
            continue
        seen_names.add(spec.name)
        fields.append(spec)
    if not fields:
        errors.append("At least one field to extract is required")

    # --- sources -----------------------------------------------------------------------------
    s_raw = data.get("sources") or {}
    if isinstance(s_raw, list):
        s_raw = {"mode": "manual", "urls": s_raw}
    if not isinstance(s_raw, dict):
        errors.append("sources must be an object")
        s_raw = {}
    sources = SourcesConfig()
    raw_urls = list(s_raw.get("urls") or [])
    url_file = s_raw.get("url_file")
    if url_file:
        path = url_file if os.path.isabs(url_file) or not base_dir else os.path.join(base_dir, url_file)
        try:
            with open(os.path.expanduser(path), "r", encoding="utf-8") as fh:
                raw_urls.extend(line.strip() for line in fh if line.strip() and not line.startswith("#"))
        except OSError as exc:
            errors.append("Could not read sources.url_file %r: %s" % (url_file, exc))
    for sm in as_list(s_raw.get("sitemaps")):
        raw_urls.append({"url": sm, "type": "sitemap"})
    for i, raw in enumerate(raw_urls):
        prof = _parse_source(raw, errors, i)
        if prof:
            sources.urls.append(prof)
    mode = str(s_raw.get("mode") or ("manual" if sources.urls else "auto")).lower()
    if mode not in ("auto", "manual", "hybrid"):
        errors.append("sources.mode must be auto, manual or hybrid")
        mode = "auto"
    if mode == "manual" and not sources.urls:
        errors.append("sources.mode is 'manual' but no source URLs were given")
    if mode == "auto" and sources.urls:
        mode = "hybrid"
    sources.mode = mode
    if mode in ("auto", "hybrid") and targeting.description and targeting.required.is_empty() and \
            targeting.preferred.is_empty() and targeting.excluded.is_empty():
        # "Software companies in Cape Town" -> industry + location criteria for discovery and matching.
        _infer_from_description(targeting, entity)
    if mode == "auto" and not targeting.description and targeting.required.is_empty() and \
            targeting.preferred.is_empty():
        errors.append("Describe the target (targeting.description, industry, keywords or location) "
                      "or provide source URLs")
    provider = str(s_raw.get("search_provider") or "auto").lower()
    if provider not in SEARCH_PROVIDERS:
        errors.append("sources.search_provider must be one of %s" % ", ".join(SEARCH_PROVIDERS))
        provider = "auto"
    sources.search_provider = provider
    sources.max_queries = _int(s_raw.get("max_queries"), sources.max_queries, errors, "sources.max_queries")
    sources.results_per_query = _int(s_raw.get("results_per_query"), sources.results_per_query, errors,
                                     "sources.results_per_query")
    sources.max_sources = _int(s_raw.get("max_sources"), sources.max_sources, errors, "sources.max_sources")
    sources.use_sitemaps = _bool(s_raw.get("use_sitemaps"), True)
    sources.include_domains = [d.lower() for d in _strings(s_raw.get("include_domains"))]
    sources.exclude_domains = [d.lower() for d in _strings(s_raw.get("exclude_domains"))]
    disc = s_raw.get("discovery") or {}
    if isinstance(disc, dict):
        sources.discover_search_engines = _bool(disc.get("search_engines"), True)
        sources.discover_directories = _bool(disc.get("directories", disc.get("business_directories")), True)
        sources.discover_company_websites = _bool(disc.get("company_websites"), True)
        sources.discover_industry_directories = _bool(disc.get("industry_directories"), True)

    # --- records / crawl / output ----------------------------------------------------------
    records = _parse_records(data.get("records") or {}, data, errors)

    c_raw = data.get("crawl") or {}
    crawl = CrawlConfig()
    crawl.max_pages = _int(c_raw.get("max_pages"), 0, errors, "crawl.max_pages")
    crawl.max_depth = _int(c_raw.get("max_depth"), crawl.max_depth, errors, "crawl.max_depth")
    crawl.concurrency = max(1, min(32, _int(c_raw.get("concurrency"), crawl.concurrency, errors, "crawl.concurrency")))
    crawl.delay = max(0.0, _float(c_raw.get("delay", c_raw.get("rate_limit")), crawl.delay, errors, "crawl.delay") or 0.0)
    crawl.timeout = max(1.0, _float(c_raw.get("timeout"), crawl.timeout, errors, "crawl.timeout") or 20.0)
    crawl.retries = max(0, min(10, _int(c_raw.get("retries"), crawl.retries, errors, "crawl.retries")))
    crawl.respect_robots = _bool(c_raw.get("respect_robots"), True)
    crawl.render_js = str(c_raw.get("render_js") or "auto").lower()
    if crawl.render_js in ("true", "yes"):
        crawl.render_js = "always"
    if crawl.render_js in ("false", "no"):
        crawl.render_js = "never"
    if crawl.render_js not in ("auto", "never", "always"):
        errors.append("crawl.render_js must be auto, never or always")
        crawl.render_js = "auto"
    crawl.user_agent = str(c_raw.get("user_agent") or "")
    crawl.max_pages_per_domain = _int(c_raw.get("max_pages_per_domain"), crawl.max_pages_per_domain, errors,
                                      "crawl.max_pages_per_domain")
    crawl.enrich_pages = _int(c_raw.get("enrich_pages"), crawl.enrich_pages, errors, "crawl.enrich_pages")
    crawl.enrich_from_website = _bool(c_raw.get("enrich_from_website"), True)
    crawl.max_response_bytes = _int(c_raw.get("max_response_bytes"), crawl.max_response_bytes, errors,
                                    "crawl.max_response_bytes")
    crawl.keep_raw_pages = _bool(c_raw.get("keep_raw_pages"), False)
    crawl.record_mode = str(c_raw.get("record_mode") or "auto").lower()
    if crawl.record_mode not in ("auto", "site", "page"):
        errors.append("crawl.record_mode must be auto, site or page")
        crawl.record_mode = "auto"
    crawl.max_runtime_minutes = _float(c_raw.get("max_runtime_minutes"), 0, errors, "crawl.max_runtime_minutes") or 0

    o_raw = data.get("output") or {}
    if isinstance(o_raw, str):
        o_raw = {"format": o_raw}
    output = OutputConfig()
    fmt = o_raw.get("format") or "xlsx"
    fmts = as_list(fmt)
    output.format = str(fmts[0]).lower() if fmts else "xlsx"
    output.formats = [str(f).lower() for f in fmts[1:]] + [str(f).lower() for f in as_list(o_raw.get("formats"))]
    for f in output.all_formats():
        if f not in OUTPUT_FORMATS:
            errors.append("output format %r is not supported; use one of %s" % (f, ", ".join(OUTPUT_FORMATS)))
    name = clean_whitespace(str(data.get("name") or targeting.description or entity.label + " scrape"))
    output.filename = slugify(str(o_raw.get("filename") or name))
    output.directory = str(o_raw.get("directory") or "")
    output.encoding = str(o_raw.get("encoding") or "utf-8")
    try:
        "x".encode(output.encoding)
    except LookupError:
        errors.append("output.encoding %r is not a known encoding" % output.encoding)
        output.encoding = "utf-8"
    for key in ("include_source", "include_timestamp", "include_quality_score", "include_provenance",
                "include_raw_data", "include_rejected"):
        setattr(output, key, _bool(o_raw.get(key), getattr(output, key)))

    if errors:
        raise JobConfigError(errors)

    return JobConfig(
        id=str(data.get("id") or uuid.uuid4().hex[:12]),
        name=name[:120],
        entity=entity.key,
        targeting=targeting,
        fields=fields,
        sources=sources,
        records=records,
        crawl=crawl,
        output=output,
    )


def load_job_file(path: str) -> JobConfig:
    with open(os.path.expanduser(path), "r", encoding="utf-8") as fh:
        try:
            data = json.load(fh)
        except json.JSONDecodeError as exc:
            raise JobConfigError(["%s is not valid JSON: %s" % (path, exc)])
    return parse_job(data, base_dir=os.path.dirname(os.path.abspath(path)))

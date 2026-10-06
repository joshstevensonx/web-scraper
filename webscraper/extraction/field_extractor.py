"""Field extraction (sections 15-16): turns a page into candidate records with provenance.

Every field is extracted by the best available method, in this order:

1. user-defined rules for the field (CSS selector, XPath, regex, JSON-LD path, meta tag)
2. structured data (JSON-LD / microdata) for the entity
3. metadata (OpenGraph, ``<meta>``) and links (``mailto:``, ``tel:``, social profiles)
4. visible page text (patterns and labelled values)

All candidate values are kept with their method and confidence; the processing stage picks the
best valid value per field and keeps the provenance.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from bs4 import BeautifulSoup, Tag

from ..job.models import FieldSpec, JobConfig, SourceProfile
from ..target.entities import entity_matches_schema_type
from ..util.countries import country_code
from ..util.text import clean_whitespace
from ..util.urls import normalize_url, origin, registered_domain
from . import patterns
from .page import Link, Page
from .selectors import apply_regex, select_css, select_xpath
from .semantic import MAX_LEN_BY_ROLE, ROLE_LABELS, find_labeled_value, first_paragraph
from .structured_data import extract_jsonld, extract_microdata, get_path, iter_entities, postal_address_text, \
    scalar, types_of

# JSON-LD property paths per field role (first non-empty wins).
ROLE_JSONLD_PATHS: Dict[str, List[str]] = {
    "name": ["name", "legalName", "title", "headline"],
    "website": ["url", "@id"],
    "source_url": ["url"],
    "email": ["email", "contactPoint.email"],
    "phone": ["telephone", "contactPoint.telephone", "address.telephone"],
    "address": ["address", "location.address", "jobLocation.address"],
    "city": ["address.addressLocality", "location.address.addressLocality", "jobLocation.address.addressLocality"],
    "province": ["address.addressRegion", "location.address.addressRegion", "jobLocation.address.addressRegion"],
    "country": ["address.addressCountry", "location.address.addressCountry", "jobLocation.address.addressCountry"],
    "postal_code": ["address.postalCode", "location.address.postalCode", "jobLocation.address.postalCode"],
    "suburb": ["address.addressLocality"],
    "description": ["description", "abstract", "disambiguatingDescription"],
    "industry": ["industry", "knowsAbout", "category", "additionalType"],
    "contact_person": ["founder.name", "founder", "employee.name", "contactPoint.name", "author.name"],
    "logo": ["logo.url", "logo"],
    "image": ["image.url", "image", "photo.url"],
    "price": ["offers.price", "offers.lowPrice", "price", "offers.priceSpecification.price", "priceSpecification.price"],
    "currency": ["offers.priceCurrency", "priceCurrency", "offers.priceSpecification.priceCurrency"],
    "sku": ["sku", "mpn", "gtin13", "gtin", "gtin12", "isbn", "productID"],
    "brand": ["brand.name", "brand", "manufacturer.name"],
    "availability": ["offers.availability", "availability"],
    "rating": ["aggregateRating.ratingValue", "reviewRating.ratingValue"],
    "review_count": ["aggregateRating.reviewCount", "aggregateRating.ratingCount"],
    "price_range": ["priceRange"],
    "cuisine": ["servesCuisine"],
    "opening_hours": ["openingHours", "openingHoursSpecification"],
    "founded_year": ["foundingDate", "foundingYear"],
    "employees": ["numberOfEmployees.value", "numberOfEmployees"],
    "latitude": ["geo.latitude", "location.geo.latitude", "latitude"],
    "longitude": ["geo.longitude", "location.geo.longitude", "longitude"],
    "company": ["hiringOrganization.name", "hiringOrganization", "worksFor.name", "organizer.name"],
    "salary": ["baseSalary", "estimatedSalary", "salary"],
    "employment_type": ["employmentType"],
    "date_posted": ["datePosted", "datePublished", "uploadDate"],
    "valid_through": ["validThrough"],
    "location": ["jobLocation.address.addressLocality", "jobLocation", "location.name", "location",
                 "address.addressLocality"],
    "experience_level": ["experienceRequirements", "qualifications"],
    "start_date": ["startDate"],
    "end_date": ["endDate"],
    "venue": ["location.name", "location"],
    "organizer": ["organizer.name", "organizer"],
    "property_type": ["@type", "additionalType"],
    "bedrooms": ["numberOfBedrooms", "numberOfRooms", "accommodationFloorPlan.numberOfBedrooms"],
    "bathrooms": ["numberOfBathroomsTotal", "numberOfFullBathrooms"],
    "floor_size": ["floorSize.value", "floorSize"],
    "person_job_title": ["jobTitle"],
    "author": ["author.name", "author"],
    "social_links": ["sameAs"],
}
_SOCIAL_ROLE = {"linkedin_url": "linkedin", "facebook_url": "facebook", "twitter_url": "twitter",
                "instagram_url": "instagram", "youtube_url": "youtube", "github_url": "github",
                "tiktok_url": "tiktok"}
_PRICE_SELECTORS = "[itemprop=price], .price, .product-price, .price_color, [class*=price], [data-price]"
_TITLE_SPLIT = re.compile(r"\s+(?:\||-|–|—|·|::|»)\s+")


def _camel(name: str) -> str:
    parts = name.split("_")
    return parts[0] + "".join(p.title() for p in parts[1:])


@dataclass
class FieldValue:
    value: Any
    method: str
    confidence: float
    source: str
    extracted_at: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {"value": self.value, "method": self.method, "confidence": round(self.confidence, 3),
                "source": self.source, "extracted_at": self.extracted_at}


@dataclass
class CandidateRecord:
    source_url: str
    kind: str  # jsonld | container | page | api
    fields: Dict[str, List[FieldValue]] = field(default_factory=dict)
    group_hint: Optional[str] = None
    structured: bool = False
    raw: Dict[str, Any] = field(default_factory=dict)
    text: str = ""  # text used for target matching when the record is part of a larger page
    user_hits: int = 0  # fields filled by user-defined selectors / rules

    def add(self, name: str, value: Any, method: str, confidence: float, source: Optional[str] = None) -> None:
        if value is None:
            return
        if isinstance(value, str):
            value = clean_whitespace(value)
            if not value:
                return
        if isinstance(value, (list, tuple)) and not value:
            return
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        bucket = self.fields.setdefault(name, [])
        for existing in bucket:
            if existing.value == value:
                if confidence > existing.confidence:
                    existing.confidence, existing.method = confidence, method
                return
        bucket.append(FieldValue(value, method, confidence, source or self.source_url, now))

    def has(self, name: str) -> bool:
        return bool(self.fields.get(name))

    def filled(self) -> int:
        return sum(1 for v in self.fields.values() if v)


@dataclass
class ExtractContext:
    role: str  # "entity" (the entity's own website), "directory" (third-party site), "page" (page mode)
    source: SourceProfile
    is_home: bool = False


@dataclass
class PageExtraction:
    candidates: List[CandidateRecord]
    structured_entities: int
    listing_score: float
    external_entity_links: List[Link]


class PageExtractor:
    def __init__(self, job: JobConfig):
        self.job = job
        self.entity = job.entity_profile
        self.fields = job.fields
        countries = job.targeting.location.country
        self.region = country_code(countries[0]) if countries else None

    # ------------------------------------------------------------------------------------------
    def phone_region(self, page: Page) -> str:
        if self.region:
            return self.region
        tld = page.domain.rsplit(".", 1)[-1].upper()
        if len(tld) == 2 and tld != "IO" and tld != "AI" and tld != "CO":
            return "GB" if tld == "UK" else tld
        return "US"

    def structured_items(self, page: Page) -> List[Dict[str, Any]]:
        return extract_jsonld(page.jsonld_blocks) + extract_microdata(page.soup)

    def matching_entities(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not self.entity.schema_types:
            return []
        out = []
        for obj in iter_entities(items):
            if entity_matches_schema_type(self.entity, types_of(obj)) and scalar(obj.get("name") or obj.get("title")):
                out.append(obj)
        return out

    # ------------------------------------------------------------------------------------------
    def extract(self, page: Page, ctx: ExtractContext, want_page_record: Any) -> PageExtraction:
        """Extract candidate records from a page.

        ``want_page_record``: True = the page itself is a record; False = only records embedded in
        the page (JSON-LD items, record containers); "auto" = a page record only when user-defined
        selectors matched something on it.
        """
        items = self.structured_items(page)
        entities = self.matching_entities(items)
        candidates: List[CandidateRecord] = []

        # Repeating containers defined by the user (classic listing scrape).
        if ctx.source.record_selector:
            try:
                containers = page.soup.select(ctx.source.record_selector)
            except Exception as exc:
                raise ValueError("Invalid record_selector %r: %s" % (ctx.source.record_selector, exc))
            for idx, tag in enumerate(containers):
                cand = self._from_container(tag, page, ctx, idx)
                if cand.filled():
                    candidates.append(cand)

        primary: Optional[Dict[str, Any]] = None
        from_items = False
        if ctx.role == "entity":
            # On a company's own site, every Organization object describes that company.
            own = [e for e in entities if registered_domain(scalar(e.get("url")) or page.url) == page.domain]
            primary = own[0] if own else (entities[0] if len(entities) == 1 else None)
        elif len(entities) == 1:
            primary = entities[0]
        elif len(entities) > 1 and not candidates:
            for obj in entities:
                cand = CandidateRecord(source_url=page.url, kind="jsonld", structured=True)
                self._apply_structured(cand, obj, page, ctx)
                cand.text = " ".join(str(v) for v in obj.values() if isinstance(v, (str, int, float)))[:5000]
                addr = postal_address_text(obj.get("address"))
                if addr:
                    cand.text += " " + addr
                if self.job.output.include_raw_data:
                    cand.raw = {"jsonld": obj}
                if cand.filled():
                    candidates.append(cand)
                    from_items = True

        if (want_page_record or primary is not None) and not from_items:
            cand = self._from_page(page, ctx, primary)
            keep = want_page_record is True or primary is not None or cand.user_hits > 0
            if cand.filled() and keep:
                candidates.append(cand)

        listing_score, ext_links = self._listing_signals(page)
        return PageExtraction(candidates=candidates, structured_entities=len(entities),
                              listing_score=listing_score, external_entity_links=ext_links)

    # ------------------------------------------------------------------------------------------
    def _listing_signals(self, page: Page):
        ext: Dict[str, Link] = {}
        for link in page.links:
            if link.external and not link.in_boilerplate and patterns.is_entity_candidate_domain(link.url):
                dom = registered_domain(link.url)
                if dom not in ext:
                    ext[dom] = link
        score = min(1.0, len(ext) / 10.0)
        return score, list(ext.values())

    # ------------------------------------------------------------------------------------------
    def _user_rules(self, spec: FieldSpec, ctx: ExtractContext) -> Dict[str, Any]:
        rules = {"selector": spec.selector, "xpath": spec.xpath, "attribute": spec.attribute, "regex": spec.regex,
                 "jsonld": spec.jsonld, "meta": spec.meta}
        override = ctx.source.fields.get(spec.name) if ctx.source.fields else None
        if isinstance(override, str):
            override = {"selector": override}
        if isinstance(override, dict):
            for k in rules:
                if override.get(k) or (k == "selector" and override.get("css")):
                    rules[k] = override.get(k) or override.get("css")
        return rules

    def _apply_user_rules(self, cand: CandidateRecord, spec: FieldSpec, ctx: ExtractContext, root: Any,
                          page: Page, structured: Optional[Dict[str, Any]], meta: Dict[str, str]) -> bool:
        rules = self._user_rules(spec, ctx)
        multiple = spec.type == "array"
        found = False
        if rules["selector"]:
            for v in select_css(root, rules["selector"], rules["attribute"], page.url, multiple=multiple):
                if rules["regex"]:
                    v = (apply_regex(v, rules["regex"]) or [None])[0]
                if v:
                    cand.add(spec.name, v, "css", 0.97)
                    found = True
        if rules["xpath"] and not found:
            target = page.html if root is page.soup else root
            for v in select_xpath(target, rules["xpath"], rules["attribute"], page.url)[: None if multiple else 1]:
                if rules["regex"]:
                    v = (apply_regex(v, rules["regex"]) or [None])[0]
                if v:
                    cand.add(spec.name, v, "xpath", 0.97)
                    found = True
        if rules["jsonld"] and structured is not None and not found:
            v = get_path(structured, rules["jsonld"])
            if v not in (None, "", [], {}):
                cand.add(spec.name, v if multiple else scalar(v), "json-ld", 0.95)
                found = True
        if rules["meta"] and not found:
            v = meta.get(str(rules["meta"]).lower())
            if v:
                cand.add(spec.name, v, "meta", 0.9)
                found = True
        if rules["regex"] and not rules["selector"] and not rules["xpath"] and not found:
            text = root.get_text(" ") if hasattr(root, "get_text") else page.text
            for v in apply_regex(clean_whitespace(text), rules["regex"], multiple=multiple):
                cand.add(spec.name, v, "regex", 0.9)
                found = True
        return found

    # ------------------------------------------------------------------------------------------
    def _apply_structured(self, cand: CandidateRecord, obj: Dict[str, Any], page: Page, ctx: ExtractContext) -> None:
        cand.structured = True
        for spec in self.fields:
            if spec.jsonld:
                v = get_path(obj, spec.jsonld)
                if v not in (None, "", [], {}):
                    cand.add(spec.name, v if spec.type == "array" else scalar(v), "json-ld", 0.95)
                continue
            role = spec.role
            if role in _SOCIAL_ROLE:
                for url in _as_urls(obj.get("sameAs")):
                    if patterns.is_social_profile(url) == _SOCIAL_ROLE[role]:
                        cand.add(spec.name, url, "json-ld", 0.95)
                continue
            if role == "social_links":
                urls = [u for u in _as_urls(obj.get("sameAs")) if patterns.is_social_profile(u)]
                for u in urls:
                    cand.add(spec.name, u, "json-ld", 0.95)
                continue
            paths = ROLE_JSONLD_PATHS.get(role) or [_camel(spec.name), spec.name]
            for path in paths:
                raw = get_path(obj, path)
                if raw in (None, "", [], {}):
                    continue
                if role == "address":
                    value = postal_address_text(raw) if not isinstance(raw, str) else raw
                elif role in ("website", "source_url"):
                    value = scalar(raw)
                    value = normalize_url(value, page.url) if value else None
                    if role == "website" and value and ctx.role == "directory" and \
                            registered_domain(value) == page.domain:
                        value = None  # directory's own URL is not the company website
                elif role == "salary":
                    value = _salary_text(raw)
                elif role == "opening_hours":
                    value = _hours_text(raw)
                elif role in ("availability", "employment_type", "property_type") or path == "@type":
                    s = scalar(raw)
                    value = s.rsplit("/", 1)[-1] if s else None
                elif spec.type == "array":
                    value = raw if isinstance(raw, list) else [raw]
                    value = [scalar(v) for v in value if scalar(v)]
                else:
                    value = scalar(raw)
                if value not in (None, "", []):
                    cand.add(spec.name, value, "json-ld", 0.95)
                    break

    # ------------------------------------------------------------------------------------------
    def _from_page(self, page: Page, ctx: ExtractContext, structured: Optional[Dict[str, Any]]) -> CandidateRecord:
        cand = CandidateRecord(source_url=page.canonical if page.canonical and registered_domain(page.canonical) ==
                               page.domain else page.url, kind="page")
        meta = patterns.extract_meta(page.soup)
        if structured is not None:
            self._apply_structured(cand, structured, page, ctx)
            if self.job.output.include_raw_data:
                cand.raw["jsonld"] = structured
        if self.job.output.include_raw_data:
            cand.raw["meta"] = {k: v for k, v in meta.items() if k.startswith(("og:", "description", "keywords"))}
            cand.raw["title"] = page.title

        # Directory pages: ignore the directory's own header/footer/nav when reading text.
        scoped = ctx.role == "directory"
        root = page.main_soup() if scoped else page.soup
        text = page.main_text() if scoped else page.text
        lines_text: Optional[str] = None
        links = [l for l in page.links if not (scoped and l.in_boilerplate)]

        for spec in self.fields:
            if self._apply_user_rules(cand, spec, ctx, page.soup, page, structured, meta):
                cand.user_hits += 1
                continue
            role = spec.role
            if role == "name":
                self._name_heuristics(cand, spec, page, ctx, meta, root)
            elif role == "website":
                if ctx.role == "entity":
                    cand.add(spec.name, origin(page.url), "page-origin", 0.9)
                else:
                    for url, conf in patterns.find_website_links(page):
                        cand.add(spec.name, origin(url) if conf < 0.85 else url, "website-link", conf)
            elif role == "source_url":
                cand.add(spec.name, cand.source_url, "page-url", 1.0)
            elif role == "email":
                for email, method, conf in patterns.find_emails(root, text):
                    if ctx.role == "directory" and registered_domain(email.split("@")[-1]) == page.domain:
                        continue
                    cand.add(spec.name, email, method, conf)
            elif role == "phone":
                for raw, method, conf in patterns.find_phones(root, text, self.phone_region(page)):
                    cand.add(spec.name, raw, method, conf if not scoped else conf - 0.05)
            elif role == "address":
                addr = root.find("address")
                if addr is not None:
                    t = clean_whitespace(addr.get_text(", "))
                    if 8 <= len(t) <= 250:
                        cand.add(spec.name, re.sub(r"(,\s*)+", ", ", t), "address-tag", 0.7)
                if not cand.has(spec.name):
                    lines_text = lines_text or root.get_text("\n")
                    v = find_labeled_value(root, ROLE_LABELS["address"], lines_text)
                    if v and 8 <= len(v) <= 250 and re.search(r"\d|street|road|ave|st\b|rd\b", v, re.I):
                        cand.add(spec.name, v, "label", 0.55)
            elif role == "description":
                for key, conf in (("description", 0.8), ("og:description", 0.78), ("twitter:description", 0.7)):
                    if meta.get(key) and len(meta[key]) >= 20:
                        cand.add(spec.name, meta[key], "meta:" + key, conf)
                        break
                if not cand.has(spec.name):
                    para = first_paragraph(root)
                    if para:
                        cand.add(spec.name, para, "first-paragraph", 0.5)
            elif role == "social_links":
                for net, url in patterns.find_social_links(links):
                    cand.add(spec.name, url, "social-link", 0.85)
            elif role in _SOCIAL_ROLE:
                for net, url in patterns.find_social_links(links):
                    if net == _SOCIAL_ROLE[role]:
                        cand.add(spec.name, url, "social-link", 0.85)
                        break
            elif role == "logo":
                for img in root.find_all("img", limit=60):
                    attrs = " ".join([str(img.get("alt", "")), " ".join(img.get("class", []) or []),
                                      str(img.get("id", "")), str(img.get("src", ""))]).lower()
                    if "logo" in attrs and img.get("src"):
                        cand.add(spec.name, normalize_url(img["src"], page.url), "img-logo", 0.7)
                        break
            elif role == "image":
                if meta.get("og:image"):
                    cand.add(spec.name, normalize_url(meta["og:image"], page.url), "meta:og:image", 0.75)
            elif role == "price":
                for key in ("product:price:amount", "og:price:amount", "price"):
                    if meta.get(key):
                        cand.add(spec.name, meta[key], "meta:" + key, 0.85)
                        break
                if not cand.has(spec.name):
                    tag = root.select_one(_PRICE_SELECTORS)
                    if tag is not None:
                        cand.add(spec.name, tag.get("content") or tag.get_text(" "), "price-selector", 0.6)
            elif role == "currency":
                for key in ("product:price:currency", "og:price:currency"):
                    if meta.get(key):
                        cand.add(spec.name, meta[key], "meta:" + key, 0.85)
                        break
                if not cand.has(spec.name):
                    tag = root.select_one(_PRICE_SELECTORS)
                    if tag is not None:
                        cur = _currency_from_text(tag.get_text(" "))
                        if cur:
                            cand.add(spec.name, cur, "price-symbol", 0.55)
            elif role in ("latitude", "longitude"):
                key = "place:location:" + role
                if meta.get(key):
                    cand.add(spec.name, meta[key], "meta:" + key, 0.8)
                elif meta.get("geo.position") and ";" in meta["geo.position"]:
                    lat, lon = meta["geo.position"].split(";", 1)
                    cand.add(spec.name, lat if role == "latitude" else lon, "meta:geo.position", 0.8)
            elif role == "industry":
                pass  # filled from the target match when nothing structured is available
            elif role in ("city", "province", "country", "postal_code", "suburb"):
                pass  # derived from the address during normalization
            if not cand.has(spec.name):
                labels = ROLE_LABELS.get(role)
                if role == "custom" or not labels:
                    labels = [spec.label, spec.name.replace("_", " ")] if role == "custom" else None
                    if role == "custom" and meta.get(spec.name.lower()):
                        cand.add(spec.name, meta[spec.name.lower()], "meta", 0.6)
                        continue
                if labels and role not in ("email", "phone", "address"):
                    lines_text = lines_text or root.get_text("\n")
                    v = find_labeled_value(root, labels, lines_text, MAX_LEN_BY_ROLE.get(role, 300))
                    if v:
                        cand.add(spec.name, v, "label", 0.55)
        return cand

    def _name_heuristics(self, cand: CandidateRecord, spec: FieldSpec, page: Page, ctx: ExtractContext,
                         meta: Dict[str, str], root: Any) -> None:
        h1 = root.find("h1")
        h1_text = clean_whitespace(h1.get_text(" ")) if h1 else ""
        if ctx.role == "entity":
            if meta.get("og:site_name"):
                cand.add(spec.name, meta["og:site_name"], "meta:og:site_name", 0.82)
            if meta.get("application-name"):
                cand.add(spec.name, meta["application-name"], "meta:application-name", 0.7)
            brand = _title_brand(page.title)
            if brand:
                cand.add(spec.name, brand, "title", 0.6)
            if h1_text and len(h1_text) <= 80:
                cand.add(spec.name, h1_text, "h1", 0.4)
        else:
            if h1_text and len(h1_text) <= 200:
                cand.add(spec.name, h1_text, "h1", 0.75)
            og = meta.get("og:title")
            if og:
                cand.add(spec.name, _title_main(og, meta.get("og:site_name")), "meta:og:title", 0.65)
            if page.title:
                cand.add(spec.name, _title_main(page.title, meta.get("og:site_name")), "title", 0.5)

    # ------------------------------------------------------------------------------------------
    @staticmethod
    def _container_detail_url(tag: Tag, page: Page, source: SourceProfile) -> Optional[str]:
        """The link from a listing item to its own detail page, if there is one."""
        from ..discovery.url_classifier import classify_url

        links = []
        for a in tag.find_all("a", href=True):
            u = normalize_url(a["href"], page.url)
            if u and registered_domain(u) == page.domain and u != page.url:
                links.append((a, u))
        if source.detail_link_pattern:
            for _, u in links:
                if re.search(source.detail_link_pattern, u):
                    return u
            return None
        for a, u in links:
            if a.find_parent(["h1", "h2", "h3", "h4", "h5"]) is not None or a.find(["h1", "h2", "h3", "h4", "h5"]):
                return u
        distinct = {u for _, u in links}
        if len(distinct) == 1:  # a card that links to exactly one page
            u = distinct.pop()
            return u if classify_url(u) not in ("skip", "pagination", "listing", "search") else None
        return None

    def _from_container(self, tag: Tag, page: Page, ctx: ExtractContext, idx: int) -> CandidateRecord:
        cand = CandidateRecord(source_url=page.url, kind="container")
        detail_url = self._container_detail_url(tag, page, ctx.source)
        if detail_url:
            cand.source_url = detail_url
            cand.group_hint = detail_url
        sub = BeautifulSoup(str(tag), "lxml")
        text = clean_whitespace(tag.get_text(" "))
        cand.text = text[:5000]
        items = extract_microdata(sub)
        structured = next((i for i in items if entity_matches_schema_type(self.entity, types_of(i))), None)
        if structured is not None:
            self._apply_structured(cand, structured, page, ctx)
        for spec in self.fields:
            if self._apply_user_rules(cand, spec, ctx, tag, page, structured, {}):
                cand.user_hits += 1
                continue
            role = spec.role
            if cand.has(spec.name):
                continue
            if role == "name":
                head = tag.find(["h1", "h2", "h3", "h4", "h5"]) or tag.find("a")
                if head is not None:
                    title = head.get("title") or head.get_text(" ")
                    a = head.find("a", title=True) if hasattr(head, "find") else None
                    if a is not None:
                        title = a["title"]
                    cand.add(spec.name, title, "container-heading", 0.7)
            elif role == "source_url":
                cand.add(spec.name, detail_url or page.url, "container-link" if detail_url else "page-url",
                         0.9 if detail_url else 0.5)
            elif role == "website":
                for a in tag.find_all("a", href=True):
                    u = normalize_url(a["href"], page.url)
                    if u and registered_domain(u) != page.domain and patterns.is_entity_candidate_domain(u):
                        cand.add(spec.name, u, "container-external-link", 0.75)
                        break
            elif role == "email":
                for email, method, conf in patterns.find_emails(tag, text):
                    cand.add(spec.name, email, method, conf)
            elif role == "phone":
                for raw, method, conf in patterns.find_phones(tag, text, self.phone_region(page)):
                    cand.add(spec.name, raw, method, conf)
            elif role == "price":
                ptag = tag.select_one(_PRICE_SELECTORS)
                if ptag is not None:
                    cand.add(spec.name, ptag.get("content") or ptag.get_text(" "), "price-selector", 0.65)
            elif role == "currency":
                ptag = tag.select_one(_PRICE_SELECTORS)
                cur = _currency_from_text(ptag.get_text(" ")) if ptag is not None else None
                if cur:
                    cand.add(spec.name, cur, "price-symbol", 0.55)
            elif role == "image":
                img = tag.find("img", src=True)
                if img is not None:
                    cand.add(spec.name, normalize_url(img["src"], page.url), "container-img", 0.7)
            elif role == "description":
                p = tag.find("p")
                if p is not None and len(p.get_text(strip=True)) > 20:
                    cand.add(spec.name, p.get_text(" "), "container-paragraph", 0.5)
            else:
                labels = ROLE_LABELS.get(role) or ([spec.label, spec.name.replace("_", " ")] if role == "custom" else None)
                if labels:
                    v = find_labeled_value(tag, labels, tag.get_text("\n"), MAX_LEN_BY_ROLE.get(role, 300))
                    if v:
                        cand.add(spec.name, v, "label", 0.55)
        return cand


# ----------------------------------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------------------------------

def _as_urls(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        out = []
        for v in value:
            out.extend(_as_urls(v))
        return out
    if isinstance(value, dict):
        return _as_urls(value.get("url") or value.get("@id"))
    return []


def _title_main(title: str, site_name: Optional[str] = None) -> str:
    parts = _TITLE_SPLIT.split(title)
    if site_name:
        parts = [p for p in parts if p.strip().lower() != site_name.strip().lower()] or parts
    return parts[0].strip()


_GENERIC_TITLE_PARTS = re.compile(r"^(home|homepage|welcome|official site|official website|index)$", re.I)


def _title_brand(title: str) -> Optional[str]:
    """Best guess of the brand in a homepage title ("Home | ABC Software" -> "ABC Software")."""
    if not title:
        return None
    parts = [p.strip() for p in _TITLE_SPLIT.split(title) if p.strip() and not _GENERIC_TITLE_PARTS.match(p.strip())]
    if not parts:
        return None
    # Brand is usually the shortest segment at either end.
    ends = [parts[0], parts[-1]] if len(parts) > 1 else parts
    best = min(ends, key=len)
    return best if len(best) <= 70 else None


_CURRENCY_SYMBOLS = {"$": "USD", "€": "EUR", "£": "GBP", "¥": "JPY", "₹": "INR", "R": "ZAR", "₦": "NGN",
                     "A$": "AUD", "C$": "CAD", "NZ$": "NZD", "CHF": "CHF", "kr": "SEK", "₩": "KRW", "₽": "RUB"}


def _currency_from_text(text: str) -> Optional[str]:
    t = (text or "").strip()
    m = re.search(r"\b([A-Z]{3})\b", t)
    if m and m.group(1) in {"USD", "EUR", "GBP", "ZAR", "AUD", "CAD", "NZD", "INR", "JPY", "CHF", "SEK", "NOK",
                             "DKK", "NGN", "KES", "BRL", "MXN", "SGD", "HKD", "AED", "CNY"}:
        return m.group(1)
    for sym in sorted(_CURRENCY_SYMBOLS, key=len, reverse=True):
        if sym == "R":
            if re.match(r"^R\s?\d", t):
                return "ZAR"
            continue
        if sym in t:
            return _CURRENCY_SYMBOLS[sym]
    return None


def _salary_text(raw: Any) -> Optional[str]:
    if isinstance(raw, dict):
        cur = raw.get("currency") or ""
        val = raw.get("value", raw)
        unit = ""
        if isinstance(val, dict):
            unit = val.get("unitText") or ""
            lo, hi, v = val.get("minValue"), val.get("maxValue"), val.get("value")
            amount = "%s-%s" % (lo, hi) if lo is not None and hi is not None else (v or lo or hi)
        else:
            amount = val
        if amount in (None, ""):
            return None
        return clean_whitespace("%s %s %s" % (cur, amount, ("per " + unit.lower()) if unit else ""))
    return scalar(raw)


def _hours_text(raw: Any) -> Optional[str]:
    if isinstance(raw, list):
        parts = [_hours_text(r) for r in raw]
        return "; ".join(p for p in parts if p) or None
    if isinstance(raw, dict):
        days = raw.get("dayOfWeek")
        if isinstance(days, list):
            days = ",".join(str(d).rsplit("/", 1)[-1] for d in days)
        elif days:
            days = str(days).rsplit("/", 1)[-1]
        if raw.get("opens") or raw.get("closes"):
            return clean_whitespace("%s %s-%s" % (days or "", raw.get("opens", ""), raw.get("closes", "")))
        return None
    return scalar(raw)

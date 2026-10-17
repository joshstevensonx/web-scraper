"""Turn a target definition into search queries (section 9)."""

from __future__ import annotations

from typing import Dict, List

from ..job.models import JobConfig
from ..util.text import fold

# Small industry thesaurus used for query variations and (optionally) target matching.
SYNONYMS: Dict[str, List[str]] = {
    "software": ["software development", "SaaS", "technology", "IT services", "app development"],
    "saas": ["software as a service", "cloud software"],
    "technology": ["tech", "IT", "software"],
    "it": ["IT services", "information technology", "managed IT"],
    "marketing": ["digital marketing", "advertising", "marketing agency", "SEO"],
    "digital marketing": ["online marketing", "SEO", "social media marketing"],
    "accounting": ["accountants", "bookkeeping", "chartered accountants", "tax"],
    "legal": ["law firm", "attorneys", "lawyers"],
    "law": ["law firm", "attorneys", "lawyers"],
    "construction": ["building contractors", "builders", "contractors"],
    "real estate": ["estate agents", "property", "realtors"],
    "property": ["real estate", "estate agents"],
    "restaurant": ["restaurants", "dining", "eatery"],
    "hotel": ["hotels", "accommodation", "lodging", "guest house"],
    "healthcare": ["medical", "clinic", "health"],
    "dental": ["dentist", "dentists", "dental practice"],
    "logistics": ["freight", "transport", "shipping", "courier"],
    "manufacturing": ["manufacturers", "factory", "production"],
    "education": ["school", "training", "tutoring", "academy"],
    "finance": ["financial services", "financial advisors", "wealth management"],
    "insurance": ["insurance brokers", "insurers"],
    "consulting": ["consultants", "advisory", "consultancy"],
    "design": ["design agency", "graphic design", "web design"],
    "web design": ["website design", "web development", "web agency"],
    "ecommerce": ["e-commerce", "online store", "online shop"],
    "fintech": ["financial technology", "payments"],
    "cybersecurity": ["cyber security", "information security", "security"],
    "recruitment": ["recruiters", "staffing", "employment agency"],
    "solar": ["solar energy", "solar installers", "renewable energy"],
    "plumbing": ["plumbers", "plumber"],
    "electrical": ["electricians", "electrician", "electrical contractors"],
    "automotive": ["car dealers", "auto repair", "mechanics"],
    "fitness": ["gym", "gyms", "personal trainers"],
    "beauty": ["beauty salon", "spa", "hair salon"],
    "agriculture": ["farming", "agri", "agribusiness"],
}


def synonyms_for(term: str) -> List[str]:
    key = fold(term)
    out = list(SYNONYMS.get(key, []))
    if key.endswith("s") and key[:-1] in SYNONYMS:
        out.extend(SYNONYMS[key[:-1]])
    return [s for s in out if fold(s) != key]


def generate_queries(job: JobConfig, limit: int = 0) -> List[str]:
    """Build a de-duplicated, priority-ordered list of search queries for the job."""
    entity = job.entity_profile
    tgt = job.targeting
    loc = tgt.location
    places = loc.most_specific()[:2] or [""]
    wider = ""
    if loc.city and (loc.province or loc.country):
        wider = (loc.province or loc.country)[0]
    nouns = entity.nouns or ["companies"]
    industries = tgt.industries()
    keywords = tgt.keywords()
    exclusions = tgt.excluded.terms[:3] + tgt.excluded.keywords[:2]
    neg = " ".join('-"%s"' % e if " " in e else "-%s" % e for e in exclusions)

    terms: List[str] = []
    for t in industries + keywords:
        if t not in terms:
            terms.append(t)
    if tgt.expand_synonyms:
        for t in list(terms)[:3]:
            for syn in synonyms_for(t)[:2]:
                if fold(syn) not in {fold(x) for x in terms}:
                    terms.append(syn)

    queries: List[str] = []

    def add(*parts: str) -> None:
        q = " ".join(p for p in parts if p).strip()
        if neg and q:
            q = q + " " + neg
        if q and fold(q) not in {fold(x) for x in queries}:
            queries.append(q)

    if tgt.description:
        add(tgt.description)
    for place in places:
        for i, term in enumerate(terms or [""]):
            add(term, nouns[0], place)
            if i == 0:
                for noun in nouns[1:2]:
                    add(term, noun, place)
                if entity.record_mode == "site":
                    add(term, nouns[0], "directory", place)
                    add("list of", term, nouns[0], place)
                    add("best", term, nouns[0], place)
                for suffix in entity.query_suffixes[:1]:
                    add(suffix, term, place)
        if wider and terms:
            add(terms[0], nouns[0], place, wider)
    if limit > 0:
        return queries[:limit]
    return queries

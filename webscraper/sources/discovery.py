"""Source discovery engine (sections 7.2, 10): classify, score and select candidate sources."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List

from ..discovery.url_classifier import classify_url, looks_like_list_title
from ..extraction.patterns import NON_ENTITY_DOMAINS, is_entity_candidate_domain
from ..job.models import JobConfig, SourceProfile
from ..util.countries import country_code
from ..util.text import contains_phrase, fold_words
from ..util.urls import origin, registered_domain
from .search import SearchResult

DIRECTORY_HINTS = re.compile(
    r"(directory|directories|yellowpages|yellow-pages|yell\.|clutch\.co|goodfirms|manta\.com|hotfrog|cylex|brabys|"
    r"businesslist|companieslist|kompass|europages|thomasnet|chamber|listings?|sortlist|designrush|upcity|"
    r"themanifest|f6s|startupblink|crunchbase|dealroom|tracxn|bark\.com|houzz|zomato|opentable|booking\.com|"
    r"capterra|g2\.com|getapp|trustradius|softwareadvice|agencyspotter|expertise\.com|threebestrated|"
    r"top\d*|best\w*|list)", re.IGNORECASE)
# Sites that are not useful (or not permitted) to crawl as sources.
DISCOVERY_SKIP = NON_ENTITY_DOMAINS | {
    "linkedin.com", "facebook.com", "instagram.com", "twitter.com", "x.com", "youtube.com", "tiktok.com",
    "pinterest.com", "wikipedia.org", "glassdoor.com", "indeed.com", "yelp.com", "tripadvisor.com",
    "crunchbase.com", "zoominfo.com", "dnb.com", "bloomberg.com", "amazon.com", "ebay.com",
}


@dataclass
class ScoredSource:
    profile: SourceProfile
    kind: str  # directory | website
    score: float
    reasons: Dict[str, float]


def _relevance(job: JobConfig, text: str) -> float:
    terms = job.targeting.industries() + job.targeting.keywords()
    loc_terms = job.targeting.location.most_specific()
    checks = 0
    hits = 0.0
    if terms:
        checks += 1
        if any(contains_phrase(text, t) for t in terms):
            hits += 1
    if loc_terms:
        checks += 1
        if any(contains_phrase(text, t) for t in loc_terms):
            hits += 1
    for noun in job.entity_profile.nouns[:2]:
        if contains_phrase(text, noun):
            hits += 0.25
            break
    return min(1.0, hits / checks) if checks else 0.5


def classify_result(r: SearchResult) -> str:
    path_kind = classify_url(r.url)
    dom = registered_domain(r.url)
    if DIRECTORY_HINTS.search(dom) or looks_like_list_title(r.title) or path_kind in ("listing", "search", "article"):
        return "directory"
    return "website"


def rank_sources(job: JobConfig, results: List[SearchResult]) -> List[ScoredSource]:
    target_cc = country_code(job.targeting.location.country[0]) if job.targeting.location.country else None
    include = set(job.sources.include_domains)
    exclude = set(job.sources.exclude_domains)
    best: Dict[str, ScoredSource] = {}
    per_domain: Dict[str, int] = {}
    for r in results:
        dom = registered_domain(r.url)
        if not dom or dom in exclude or any(dom.endswith("." + e) for e in exclude):
            continue
        # include_domains overrides the built-in skip list (e.g. to allow a specific platform).
        forced = dom in include or any(dom.endswith("." + i) for i in include)
        if not forced and (dom in DISCOVERY_SKIP or not is_entity_candidate_domain(r.url)):
            continue
        kind = classify_result(r)
        if kind == "directory" and not (job.sources.discover_directories or job.sources.discover_industry_directories):
            continue
        if kind == "website" and not job.sources.discover_company_websites:
            continue
        text = fold_words(r.title + " " + r.snippet + " " + r.url.replace("-", " ").replace("/", " "))
        rel = _relevance(job, text)
        coverage = 0.9 if kind == "directory" else 0.3
        tld = dom.rsplit(".", 1)[-1].upper()
        quality = 0.6 + (0.2 if target_cc and tld == ("UK" if target_cc == "GB" else target_cc) else 0.0) + \
            (0.2 if r.url.startswith("https://") else 0.0)
        accessibility = 0.8
        yield_est = (0.8 if kind == "directory" else 0.4) * (1.0 / (1 + 0.1 * (r.rank - 1)))
        score = 0.30 * rel + 0.25 * coverage + 0.20 * quality + 0.15 * accessibility + 0.10 * yield_est
        url = r.url if kind == "directory" else origin(r.url)
        key = url if kind == "directory" else dom
        if kind == "directory":
            per_domain[dom] = per_domain.get(dom, 0) + 1
            if per_domain[dom] > 3:
                continue
        if key in best and best[key].score >= score:
            continue
        profile = SourceProfile(url=url, name=dom, type="directory" if kind == "directory" else "website",
                                origin="discovered", score=round(score, 3),
                                priority=1 if kind == "directory" else 3)
        best[key] = ScoredSource(profile, kind, score, {"relevance": rel, "coverage": coverage, "quality": quality,
                                                       "accessibility": accessibility, "yield": yield_est})
    ranked = sorted(best.values(), key=lambda s: -s.score)
    return ranked[: job.sources.max_sources]

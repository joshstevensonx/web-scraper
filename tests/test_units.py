"""Unit tests for parsing, extraction, normalization, matching, deduplication and search."""

from __future__ import annotations

import json

import pytest

from webscraper.discovery.sitemap import parse_feed, parse_sitemap
from webscraper.discovery.url_classifier import classify_url, is_pagination_link
from webscraper.extraction.field_extractor import CandidateRecord, ExtractContext, PageExtractor
from webscraper.extraction.page import Page
from webscraper.extraction.patterns import decode_cfemail, find_emails, find_phones, is_social_profile
from webscraper.extraction.semantic import find_labeled_value
from webscraper.extraction.structured_data import extract_jsonld, extract_microdata, get_path
from webscraper.job.models import FieldSpec, JobConfigError, SourceProfile, parse_job
from webscraper.processing.normalization import (NormalizationError, Normalizer, name_key, parse_address,
                                                 parse_datetime, parse_number)
from webscraper.processing.records import RecordStore
from webscraper.sources.discovery import rank_sources
from webscraper.sources.search import SearchResult
from webscraper.target.matcher import TargetMatcher
from webscraper.target.query_generator import generate_queries
from webscraper.util.countries import country_code, country_name
from webscraper.util.urls import normalize_url, registered_domain

from .conftest import ddg_results_page


# ------------------------------------------------------------------------------------------------
# URLs
# ------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("HTTPS://Example.COM:443/Path/?utm_source=x&b=2&a=1#frag", "https://example.com/Path?a=1&b=2"),
    ("http://example.com:80", "http://example.com/"),
    ("/about/", "https://site.test/about"),
    ("mailto:a@b.com", None),
    ("javascript:void(0)", None),
    ("https://example.com/a//b/?fbclid=1", "https://example.com/a/b"),
])
def test_normalize_url(raw, expected):
    assert normalize_url(raw, "https://site.test/x/y") == expected


@pytest.mark.parametrize("url,expected", [
    ("https://www.shop.example.co.za/x", "example.co.za"),
    ("https://blog.example.com", "example.com"),
    ("example.org", "example.org"),
    ("https://www.gov.uk/", "www.gov.uk".replace("www.", "")),
    ("http://127.0.0.1:8000/", "127.0.0.1"),
])
def test_registered_domain(url, expected):
    assert registered_domain(url) == expected


def test_classify_url():
    assert classify_url("https://d.test/") == "home"
    assert classify_url("https://d.test/contact-us") == "contact"
    assert classify_url("https://d.test/about-us") == "about"
    assert classify_url("https://d.test/company/acme-ltd") == "detail"
    assert classify_url("https://d.test/companies") == "listing"
    assert classify_url("https://d.test/companies?page=3") == "pagination"
    assert classify_url("https://d.test/search?q=x") == "search"
    assert classify_url("https://d.test/privacy-policy") == "skip"
    assert classify_url("https://d.test/blog/our-news") == "article"
    assert classify_url("https://books.test/catalogue/a-light-in-the-attic_1000/index.html") == "detail"
    assert is_pagination_link("Next »") and is_pagination_link("", rel="next") and not is_pagination_link("Home")


# ------------------------------------------------------------------------------------------------
# Job parsing / validation
# ------------------------------------------------------------------------------------------------

def test_parse_full_spec_example():
    with open("examples/cape-town-software-companies.json") as fh:
        job = parse_job(json.load(fh))
    assert job.entity == "business" and job.record_mode == "site"
    assert job.targeting.required.location.city == ["Cape Town"]
    assert job.targeting.required.industry == ["Software", "Technology"]
    assert job.targeting.excluded.terms == ["Government", "Non-profit"]
    assert job.records.minimum == 50 and job.records.maximum == 100 and job.records.stop_at_minimum
    assert job.output.format == "xlsx" and job.output.include_provenance
    assert job.field("website").type == "url" and job.field("website").required


def test_parse_shorthands_and_defaults():
    job = parse_job({"entity": "products", "sources": ["shop.test/catalogue"], "range": "100-500",
                     "output": "csv"})
    assert job.entity == "product" and job.sources.mode == "manual"
    assert job.sources.urls[0].url == "https://shop.test/catalogue"
    assert (job.records.minimum, job.records.maximum) == (100, 500)
    assert job.output.format == "csv"
    assert [f.name for f in job.fields][:2] == ["name", "price"]  # entity default fields
    job = parse_job({"targeting": {"description": "Plumbers in Durban, South Africa"}, "count": 25})
    assert parse_job({"description": "Books from a catalogue", "sources": ["https://b.test"]}).targeting.required.is_empty()
    assert job.targeting.required.location.city == ["Durban"]
    assert job.targeting.required.location.country == ["South Africa"]
    assert job.targeting.required.industry == ["Plumbers"]
    assert job.records.minimum == job.records.maximum == 25


def test_parse_errors_are_collected():
    with pytest.raises(JobConfigError) as exc:
        parse_job({"entity": "business", "fields": [{"name": "x", "type": "colour"}, {"type": "text"}],
                   "records": {"minimum": 10, "maximum": 5}, "output": {"format": "pdf"},
                   "sources": {"mode": "manual"}})
    msgs = " | ".join(exc.value.errors)
    for needle in ("unsupported type", "needs a name", "maximum (5)", "pdf", "no source URLs"):
        assert needle in msgs


# ------------------------------------------------------------------------------------------------
# Extraction
# ------------------------------------------------------------------------------------------------

ORG_PAGE = """<html><head><title>Home | Acme Software</title>
<meta property="og:site_name" content="Acme Software">
<meta name="description" content="Acme builds SaaS for logistics companies.">
<script type="application/ld+json">{"@context":"https://schema.org","@graph":[
 {"@type":"WebSite","name":"Acme site"},
 {"@type":"Organization","name":"Acme Software (Pty) Ltd","url":"https://acme.test","telephone":"+27 21 123 4567",
  "email":"hello@acme.test","foundingDate":"2012-03-01","numberOfEmployees":{"@type":"QuantitativeValue","value":42},
  "address":{"@type":"PostalAddress","streetAddress":"1 Main Rd","addressLocality":"Cape Town","addressRegion":"WC",
  "postalCode":"8001","addressCountry":"ZA"},
  "sameAs":["https://www.linkedin.com/company/acme","https://twitter.com/intent/tweet?x=1"]},]}
</script></head><body><h1>We ship software</h1>
<a href="mailto:Sales@Acme.test?subject=Hi">Email sales</a> <a href="tel:0211234568">Call</a>
<p>Or write to support [at] acme [dot] test. Logo: logo@2x.png</p>
<dl><dt>Industry</dt><dd>Logistics software</dd></dl>
<a href="/contact">Contact</a></body></html>"""


def make_job(fields, entity="business", **extra):
    data = {"entity": entity, "fields": fields, "sources": {"mode": "manual", "urls": ["https://x.test"]}}
    data.update(extra)
    return parse_job(data)


def test_jsonld_graph_and_lenient_parsing():
    page = Page("https://acme.test/", ORG_PAGE)
    items = extract_jsonld(page.jsonld_blocks)
    assert {i["@type"] for i in items} == {"WebSite", "Organization"}  # trailing comma tolerated
    org = next(i for i in items if i["@type"] == "Organization")
    assert get_path(org, "address.addressLocality") == "Cape Town"


def test_entity_page_extraction_with_provenance():
    job = make_job(["company_name", "website", "email", "phone", "address", "city", "founded_year",
                    {"name": "employees", "type": "integer"}, "industry", "linkedin_url", "social_links",
                    "description"])
    page = Page("https://acme.test/", ORG_PAGE)
    ext = PageExtractor(job).extract(page, ExtractContext("entity", SourceProfile(url=page.url)), True)
    assert len(ext.candidates) == 1
    cand = ext.candidates[0]
    best = {k: max(v, key=lambda f: f.confidence) for k, v in cand.fields.items()}
    assert best["company_name"].value == "Acme Software (Pty) Ltd" and best["company_name"].method == "json-ld"
    assert best["website"].value == "https://acme.test/"
    assert {f.value for f in cand.fields["email"]} >= {"hello@acme.test", "sales@acme.test", "support@acme.test"}
    assert not any("2x.png" in str(f.value) for f in cand.fields["email"])
    assert best["city"].value == "Cape Town"
    assert best["founded_year"].value == "2012-03-01"
    assert best["employees"].value == "42"
    assert best["industry"].value == "Logistics software"
    assert best["linkedin_url"].value == "https://www.linkedin.com/company/acme"
    assert [f.value for f in cand.fields["social_links"]] == ["https://www.linkedin.com/company/acme"]
    assert best["description"].value.startswith("Acme builds SaaS")


def test_directory_page_ignores_boilerplate_contacts():
    html = """<html><head><title>Beta Ltd - Biz Directory</title><meta property="og:site_name" content="Biz Directory">
    </head><body><header><a href="mailto:ads@bizdir.test">Advertise</a></header>
    <main><h1>Beta Ltd</h1><p>Phone: 021 555 9999</p><a href="https://beta.test">Visit website</a></main>
    <footer>Biz Directory · info@bizdir.test · 021 000 0000</footer></body></html>"""
    job = make_job(["company_name", "website", "email", "phone"], targeting={"required": {"country": "ZA"}})
    page = Page("https://bizdir.test/listing/beta-ltd", html)
    cand = PageExtractor(job).extract(page, ExtractContext("directory", SourceProfile(url=page.url)), True).candidates[0]
    assert max(cand.fields["company_name"], key=lambda f: f.confidence).value == "Beta Ltd"
    assert cand.fields["website"][0].value == "https://beta.test/"
    assert "email" not in cand.fields
    assert [f.value for f in cand.fields["phone"]] == ["021 555 9999"]


def test_microdata_and_semantic_labels():
    html = """<html><body><div itemscope itemtype="https://schema.org/Product"><h1 itemprop="name">Lamp</h1>
    <span itemprop="offers" itemscope itemtype="https://schema.org/Offer"><meta itemprop="price" content="19.50">
    <meta itemprop="priceCurrency" content="EUR"></span></div>
    <table><tr><th>Bedrooms</th><td>3</td></tr></table><p>Founded: 1999</p></body></html>"""
    page = Page("https://p.test/x", html)
    items = extract_microdata(page.soup)
    assert items[0]["name"] == "Lamp" and items[0]["offers"]["price"] == "19.50"
    assert find_labeled_value(page.soup, ["bedrooms"]) == "3"
    assert find_labeled_value(page.soup, ["founded"], page.soup.get_text("\n")) == "1999"


def test_email_and_phone_patterns():
    page = Page("https://x.test", '<a href="/cdn-cgi/l/email-protection#%s">x</a><p>call 021 123 4567</p>'
                % ("42" + "".join("%02x" % (ord(c) ^ 0x42) for c in "me@x.test")))
    assert decode_cfemail("42" + "".join("%02x" % (ord(c) ^ 0x42) for c in "a@b.co")) == "a@b.co"
    assert find_emails(page.soup, page.text)[0][0] == "me@x.test"
    phones = find_phones(page.soup, page.text, "ZA")
    assert phones and phones[0][0].strip() == "021 123 4567"


def test_social_profile_detection():
    assert is_social_profile("https://www.linkedin.com/company/acme") == "linkedin"
    assert is_social_profile("https://www.facebook.com/sharer/sharer.php?u=x") is None
    assert is_social_profile("https://github.com/org/repo/issues") is None
    assert is_social_profile("https://www.youtube.com/@acme") == "youtube"


def test_sitemap_and_feed_parsing():
    idx = b'<?xml version="1.0"?><sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' \
          b'<sitemap><loc>https://s.test/a.xml</loc></sitemap></sitemapindex>'
    pages, children = parse_sitemap(idx, "", "https://s.test/")
    assert children == ["https://s.test/a.xml"] and pages == []
    import gzip
    gz = gzip.compress(b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>/p/1</loc></url></urlset>')
    assert parse_sitemap(gz, "", "https://s.test/")[0] == ["https://s.test/p/1"]
    atom = '<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>T</title><link href="https://s.test/e"/></entry></feed>'
    assert parse_feed(b"", atom, "https://s.test/") == [("https://s.test/e", "T")]


# ------------------------------------------------------------------------------------------------
# Normalization
# ------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("raw", ["+27 21 123 4567", "021 123 4567", "+27211234567", "(021) 123-4567"])
def test_phone_normalization(raw):
    assert Normalizer("ZA").normalize(FieldSpec("phone", type="phone"), raw) == "+27211234567"


def test_normalizer_types():
    n = Normalizer("ZA")
    assert n.normalize(FieldSpec("p", type="decimal"), "R 1 299,50") == 1299.5
    assert n.normalize(FieldSpec("p", type="decimal"), "$1,299.00") == 1299.0
    assert n.normalize(FieldSpec("p", type="integer"), "1.234.567") == 1234567
    assert n.normalize(FieldSpec("y", type="integer", role="founded_year"), "Founded in 2012") == 2012
    assert n.normalize(FieldSpec("d", type="date"), "5th March 2024") == "2024-03-05"
    assert n.normalize(FieldSpec("b", type="boolean"), "In stock") is True
    assert n.normalize(FieldSpec("w", type="url", role="website"), "www.Example.com/about?utm_source=x") == \
        "http://www.example.com"
    assert n.normalize(FieldSpec("e", type="email"), "MAILTO:Info@Acme.org?subject=hi") == "info@acme.org"
    assert n.normalize(FieldSpec("c", role="country"), "ZA") == "South Africa"
    assert n.normalize(FieldSpec("p", role="province"), "WC") == "Western Cape"
    assert n.normalize(FieldSpec("n", role="name"), "ACME SOFTWARE AND SONS") == "Acme Software and Sons"
    assert n.normalize(FieldSpec("t", transform="after:Price:|strip|upper"), "Price: abc") == "ABC"
    assert n.normalize(FieldSpec("a", type="array", role="social_links"), "https://a.test/x, nope") == ["https://a.test/x"]
    for spec, bad in ((FieldSpec("e", type="email"), "not-an-email"), (FieldSpec("p", type="phone"), "12"),
                      (FieldSpec("u", type="url"), "nonsense")):
        with pytest.raises(NormalizationError):
            n.normalize(spec, bad)


def test_helpers():
    assert parse_number("1.5k") == 1500
    assert parse_datetime("2024-03-05T10:00:00Z").year == 2024
    assert name_key("ABC Software (Pty) Ltd", ("Cape Town",)) == name_key("abc software, Cape Town", ("Cape Town",)) \
        == "abc software"
    assert parse_address("12 Main Road, Gardens, Cape Town, Western Cape, 8001, South Africa") == {
        "postal_code": "8001", "country": "South Africa", "province": "Western Cape", "city": "Cape Town"}
    assert parse_address("1 Long St, Cape Town, WC", "ZA")["province"] == "Western Cape"
    assert country_code("United States of America") == "US" and country_name("gb") == "United Kingdom"


# ------------------------------------------------------------------------------------------------
# Targeting, queries, matching
# ------------------------------------------------------------------------------------------------

def test_query_generation():
    with open("examples/cape-town-software-companies.json") as fh:
        job = parse_job(json.load(fh))
    queries = generate_queries(job)
    assert queries[0].startswith("Software companies in Cape Town")
    assert any(q.startswith("Software companies Cape Town") for q in queries)
    assert any("SaaS" in q for q in queries)
    assert all("-Government" in q for q in queries)


def test_target_matcher():
    job = make_job(["company_name", "city", "rating"], targeting={
        "required": {"city": "Cape Town", "industry": ["Software"], "rating": {"min": 4}},
        "preferred": {"keywords": ["SaaS"]},
        "excluded": {"business_types": ["Government"]}})
    m = TargetMatcher(job)  # page text is passed pre-folded (lower-case, no punctuation)
    ok = m.match({"company_name": "Acme", "city": "Cape Town", "rating": 4.5}, page_text="software development")
    assert ok.matched and ok.matched_industry == "Software" and ok.score < 1
    assert not m.match({"company_name": "Acme", "city": "Durban", "rating": 5}, page_text="software").matched
    assert not m.match({"company_name": "Acme", "city": "Cape Town", "rating": 3}, page_text="software").matched
    assert m.match({"company_name": "Acme", "city": "Cape Town", "rating": 4}, page_text="saas tools").matched  # synonym
    bad = m.match({"company_name": "Government Software Agency", "city": "Cape Town", "rating": 5},
                  page_text="software")
    assert not bad.matched and "excluded" in bad.reasons[0]


# ------------------------------------------------------------------------------------------------
# Record store: merging and deduplication
# ------------------------------------------------------------------------------------------------

def cand(url, **fields):
    c = CandidateRecord(source_url=url, kind="page")
    for k, v in fields.items():
        c.add(k, v, "test", 0.9)
    return c


def test_record_store_merges_by_keys_and_fuzzy_names():
    job = make_job([{"name": "company_name", "required": True}, "website", "email", "phone", "address"],
                   targeting={"required": {"city": "Cape Town"}, "strict": False})
    store = RecordStore(job)
    a = store.add(cand("https://dir.test/a", company_name="ABC Software", website="https://abc.test",
                       phone="+27 21 555 0000"), "url:https://dir.test/a")
    b = store.add(cand("https://abc.test/", company_name="ABC Software Ltd", email="info@abc.test"),
                  "domain:abc.test")
    assert a is b and len(store.builders) == 1 and store.duplicates_merged == 1
    store.add(cand("https://dir2.test/x", company_name="ABC Software Cape Town", phone="+27215550000"), None)
    assert len(store.builders) == 1  # phone key matched
    store.add(cand("https://dir3.test/y", company_name="ABC  Software (Pty) Ltd"), "url:https://dir3.test/y")
    store.add(cand("https://xyz.test/", company_name="ABC Software", website="https://xyz.test"), "domain:xyz.test")
    assert len(store.builders) == 3
    merged = store.fuzzy_dedupe()
    assert merged == 1 and len(store.builders) == 2  # name-only record merged; conflicting website kept apart
    final = store.final()
    assert {r.result.values["website"] for r in final} == {"https://abc.test", "https://xyz.test"}


def test_record_quality_and_validation():
    job = make_job([{"name": "company_name", "required": True}, {"name": "email", "type": "email"},
                    {"name": "website", "type": "url", "required": True}], records={"min_quality": 50})
    store = RecordStore(job)
    good = store.add(cand("https://a.test", company_name="A", email="a@a.test", website="https://a.test"), "k:a")
    bad = store.add(cand("https://b.test", company_name="B", email="nope"), "k:b")
    store.evaluate_all()
    assert good.result.valid and good.result.quality > 80
    assert not bad.result.valid and any("website" in r for r in bad.result.reasons)
    assert any(v["field"] == "email" and not v["passed"] for v in bad.result.validation)


# ------------------------------------------------------------------------------------------------
# Source discovery
# ------------------------------------------------------------------------------------------------

def test_duckduckgo_parser_and_ranking():
    import asyncio

    import httpx

    from webscraper.crawling.fetcher import Fetcher
    from webscraper.sources.search import DuckDuckGoProvider

    html = ddg_results_page([("https://dir.test/software-companies-cape-town", "Top 10 software companies in Cape Town", "x"),
                             ("https://acme.co.za/", "Acme | Software Cape Town", "y")])

    async def go():
        f = Fetcher(transport=httpx.MockTransport(lambda r: httpx.Response(200, text=html)))
        try:
            return await DuckDuckGoProvider(f, "ZA").search("q", 10)
        finally:
            await f.close()

    results = asyncio.run(go())
    assert [r.url for r in results] == ["https://dir.test/software-companies-cape-town", "https://acme.co.za/"]
    job = make_job(["company_name"], targeting={"required": {"city": "Cape Town", "country": "ZA",
                                                             "industry": ["Software"]}})
    ranked = rank_sources(job, results + [SearchResult("https://www.linkedin.com/company/x", "LinkedIn", "", 3, "q")])
    assert [s.kind for s in ranked] == ["directory", "website"]
    assert ranked[1].profile.url == "https://acme.co.za/"

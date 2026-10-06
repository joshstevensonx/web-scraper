"""Shared fixtures: an in-process fake internet built on ``httpx.MockTransport``."""

from __future__ import annotations

import json
import os
from typing import Callable, Dict, Optional, Tuple, Union
from urllib.parse import parse_qs, quote

import httpx
import pytest

Response = Tuple[int, str, Union[str, bytes]]


class MockWeb:
    """Maps URLs to canned responses. Unknown URLs return 404."""

    def __init__(self) -> None:
        self.pages: Dict[str, Response] = {}
        self.handlers: Dict[str, Callable[[httpx.Request], httpx.Response]] = {}
        self.requests: list = []

    def add(self, url: str, body: Union[str, bytes], status: int = 200, content_type: str = "text/html") -> None:
        self.pages[url] = (status, content_type, body)

    def html(self, url: str, body: str, status: int = 200) -> None:
        self.add(url, "<!doctype html><html><head>%s</head></html>" % body if "<html" not in body else body, status)

    def handler(self, host: str, fn: Callable[[httpx.Request], httpx.Response]) -> None:
        self.handlers[host] = fn

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url)
        host = request.url.host
        if host in self.handlers:
            return self.handlers[host](request)
        for candidate in (url, url.rstrip("/"), url + "/" if not url.endswith("/") else url):
            if candidate in self.pages:
                status, ctype, body = self.pages[candidate]
                content = body.encode("utf-8") if isinstance(body, str) else body
                return httpx.Response(status, headers={"content-type": ctype}, content=content)
            secure = "https://" + candidate[len("http://"):] if candidate.startswith("http://") else None
            if secure and secure in self.pages:  # like real sites: http -> https
                return httpx.Response(301, headers={"location": secure})
        return httpx.Response(404, headers={"content-type": "text/html"}, content=b"<h1>Not found</h1>")

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def fetched(self, prefix: str = "") -> list:
        return [str(r.url) for r in self.requests if str(r.url).startswith(prefix)]


def ddg_results_page(results) -> str:
    """Build a DuckDuckGo HTML results page for (url, title, snippet) tuples."""
    blocks = []
    for url, title, snippet in results:
        href = "//duckduckgo.com/l/?uddg=%s&rut=abc" % quote(url, safe="")
        blocks.append(
            '<div class="result results_links results_links_deep web-result">'
            '<div class="links_main links_deep result__body"><h2 class="result__title">'
            '<a rel="nofollow" class="result__a" href="%s">%s</a></h2>'
            '<a class="result__snippet" href="%s">%s</a></div></div>' % (href, title, href, snippet))
    ad = ('<div class="result result--ad"><h2 class="result__title"><a class="result__a" '
          'href="https://ads.example.org/x">Sponsored</a></h2></div>')
    return "<html><body>%s%s</body></html>" % (ad, "".join(blocks))


def _company_home(name: str, domain: str, city: str, extra: str = "", jsonld: Optional[dict] = None) -> str:
    ld = '<script type="application/ld+json">%s</script>' % json.dumps(jsonld) if jsonld else ""
    return """<!doctype html><html lang="en"><head><title>Home | %(name)s</title>
<meta property="og:site_name" content="%(name)s">
<meta name="description" content="%(name)s builds cloud software and SaaS products for businesses in %(city)s.">
%(ld)s</head><body>
<header><nav><a href="/">Home</a><a href="/about-us">About</a><a href="/contact">Contact us</a></nav></header>
<main><h1>Software that works</h1><p>We are a software development company based in %(city)s.</p>%(extra)s</main>
<footer><a href="https://www.linkedin.com/company/%(slug)s">LinkedIn</a>
<a href="https://www.facebook.com/sharer/sharer.php?u=x">Share</a></footer>
</body></html>""" % {"name": name, "city": city, "extra": extra, "ld": ld, "slug": domain.split(".")[0]}


def build_business_web() -> MockWeb:
    """A small fake internet: a search engine, a directory with pagination and five companies."""
    web = MockWeb()
    # --- search engine ------------------------------------------------------------------------
    results = [
        ("https://capedirectory.test/software", "Top Software Companies in Cape Town - Cape Directory",
         "Directory of the best software companies in Cape Town, Western Cape"),
        ("https://www.alphasoft.co.za/", "AlphaSoft | Software development Cape Town",
         "AlphaSoft is a Cape Town software company"),
        ("https://www.facebook.com/somepage", "Facebook", "social"),
    ]

    def ddg(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/robots.txt"):
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        return httpx.Response(200, headers={"content-type": "text/html"}, content=ddg_results_page(results).encode())

    web.handler("html.duckduckgo.com", ddg)

    # --- directory ------------------------------------------------------------------------------
    web.add("https://capedirectory.test/robots.txt", "User-agent: *\nDisallow: /private/\n", content_type="text/plain")
    listing1 = """<html><head><title>Top Software Companies in Cape Town</title></head><body>
<header><a href="/">Cape Directory</a><a href="mailto:hello@capedirectory.test">Contact</a></header>
<main><h1>Software companies in Cape Town</h1>
<ul>
 <li><h3><a href="/company/alphasoft">AlphaSoft</a></h3></li>
 <li><h3><a href="/company/betacloud">BetaCloud</a></h3></li>
 <li><h3><a href="/company/gamma-gov-systems">Gamma Gov Systems</a></h3></li>
 <li><h3><a href="/private/secret-company">Secret</a></h3></li>
</ul>
<a href="/software?page=2" rel="next">Next</a>
</main><footer>Cape Directory, 1 Long Street, Cape Town. Call +27 21 000 0000</footer></body></html>"""
    listing2 = """<html><head><title>Software companies in Cape Town - page 2</title></head><body><main>
<ul><li><h3><a href="/company/deltadev">DeltaDev</a></h3></li>
<li><h3><a href="/company/alphasoft">AlphaSoft</a></h3></li></ul></main></body></html>"""
    web.html("https://capedirectory.test/software", listing1)
    web.html("https://capedirectory.test/software?page=2", listing2)

    def detail(name: str, website: Optional[str], phone: str, desc: str, jsonld: bool = False) -> str:
        site = '<a class="website" href="%s">Visit website</a>' % website if website else ""
        ld = ""
        if jsonld:
            ld = '<script type="application/ld+json">%s</script>' % json.dumps({
                "@context": "https://schema.org", "@type": "LocalBusiness", "name": name, "telephone": phone,
                "url": website, "address": {"@type": "PostalAddress", "streetAddress": "10 Bree Street",
                                            "addressLocality": "Cape Town", "addressRegion": "WC",
                                            "postalCode": "8001", "addressCountry": "ZA"}})
        return """<html><head><title>%s - Cape Directory</title>%s
<meta name="description" content="%s"></head><body>
<header><nav><a href="/software">Back to listings</a></nav></header>
<main><h1>%s</h1><p>%s</p><p>Phone: %s</p>%s</main>
<footer>Cape Directory · hello@capedirectory.test · +27 21 000 0000</footer></body></html>""" % (
            name, ld, desc, name, desc, phone, site)

    web.html("https://capedirectory.test/company/alphasoft",
             detail("AlphaSoft (Pty) Ltd", "https://www.alphasoft.co.za", "021 555 0101",
                    "Software development and SaaS in Cape Town.", jsonld=True))
    web.html("https://capedirectory.test/company/betacloud",
             detail("BetaCloud", "http://betacloud.co.za", "021 555 0202",
                    "Cloud software consultancy, Cape Town."))
    web.html("https://capedirectory.test/company/gamma-gov-systems",
             detail("Gamma Gov Systems", "https://gammagov.co.za", "021 555 0303",
                    "Government software contractor in Cape Town."))
    web.html("https://capedirectory.test/company/deltadev",
             detail("DeltaDev", None, "021 555 0404", "Software developers in Cape Town."))
    web.html("https://capedirectory.test/private/secret-company", detail("Secret", None, "021 555 0505", "x"))

    # --- company websites ----------------------------------------------------------------------
    web.html("https://www.alphasoft.co.za/", _company_home(
        "AlphaSoft", "alphasoft.co.za", "Cape Town",
        jsonld={"@context": "https://schema.org", "@type": "Organization", "name": "AlphaSoft",
                "url": "https://www.alphasoft.co.za", "sameAs": ["https://twitter.com/alphasoft"]}))
    web.html("https://www.alphasoft.co.za/contact", """<html><head><title>Contact | AlphaSoft</title></head><body>
<main><h1>Contact us</h1><p>Email <a href="mailto:info@alphasoft.co.za">info@alphasoft.co.za</a></p>
<p>Tel: <a href="tel:+27215550101">+27 21 555 0101</a></p>
<address>10 Bree Street, Cape Town, Western Cape, 8001</address></main></body></html>""")
    web.html("https://www.alphasoft.co.za/about-us", "<html><head><title>About AlphaSoft</title></head>"
                                                     "<body><p>About us</p></body></html>")
    web.html("https://betacloud.co.za/", _company_home(
        "BetaCloud", "betacloud.co.za", "Cape Town",
        extra='<p>Write to us: sales [at] betacloud [dot] co.za</p>'))
    web.html("https://betacloud.co.za/contact", "<html><body><p>Call +27 (0)21 555 0202</p>"
                                                '<span class="__cf_email__" data-cfemail="%s">[email protected]</span>'
                                                "</body></html>" % _cf_encode("hello@betacloud.co.za"))
    web.html("https://gammagov.co.za/", _company_home("Gamma Gov Systems", "gammagov.co.za", "Cape Town"))
    return web


def _cf_encode(email: str, key: int = 0x42) -> str:
    return "%02x" % key + "".join("%02x" % (ord(c) ^ key) for c in email)


@pytest.fixture
def business_web() -> MockWeb:
    return build_business_web()


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Every test gets its own ~/WebScraper folder and no search API keys."""
    monkeypatch.setenv("WEBSCRAPER_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    monkeypatch.delenv("SEARXNG_URL", raising=False)
    os.makedirs(str(tmp_path / "home"), exist_ok=True)
    return tmp_path / "home"


@pytest.fixture
def no_sleep(monkeypatch):
    """Skip the politeness pauses between search queries."""
    from webscraper.sources import search

    for cls in (search.DuckDuckGoProvider, search.BraveProvider, search.SearxngProvider):
        monkeypatch.setattr(cls, "delay", 0.0)
    return None


def parse_qs_body(request: httpx.Request) -> Dict[str, list]:
    return parse_qs(request.content.decode())

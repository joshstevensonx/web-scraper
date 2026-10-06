"""URL classification (section 12): listing / detail / category / search / contact / other."""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlsplit

SKIP = re.compile(
    r"/(?:log-?in|sign-?in|sign-?up|register|registration|account|my-?account|cart|basket|checkout|wishlist|"
    r"compare|password|logout|privacy|privacy-policy|terms|terms-of-service|terms-and-conditions|tos|cookies?|"
    r"cookie-policy|legal|disclaimer|gdpr|sitemap|wp-admin|wp-login\.php|wp-json|xmlrpc\.php|cdn-cgi|feed|"
    r"print|share|subscribe|unsubscribe|advertise|advertising|careers-login|auth|oauth|captcha|"
    r"calendar|ical|download|downloads|attachment)(?:/|$|\?|\.)",
    re.IGNORECASE,
)
SKIP_QUERY = {"replytocom", "share", "print", "action", "lang", "currency", "sort", "orderby", "order", "view",
              "format", "add-to-cart", "add_to_wishlist", "redirect", "returnurl", "return_url"}
CONTACT = re.compile(r"/(?:contact(?:-?us)?|get-?in-?touch|reach-?us|kontakt|contacto|contato|contactez-nous|"
                     r"enquir(?:y|ies))(?:/|$|\.html?)|/(?:locations?|offices?|find-?us|visit-?us)/?(?:\.html?)?$",
                     re.IGNORECASE)
ABOUT = re.compile(r"/(?:about(?:-?us)?|company|who-?we-?are|our-?(?:story|company|team)|team|people|"
                   r"over-?ons|uber-?uns|qui-sommes-nous|impressum|imprint)/?(?:\.html?)?$", re.IGNORECASE)
PAGINATION_PATH = re.compile(r"/(?:(?:page|p|pg|seite|pagina)/\d+/?|page-?\d+(?:\.html?)?)$", re.IGNORECASE)
PAGINATION_PARAMS = {"page", "p", "pg", "paged", "pagenum", "page_number", "offset", "start", "from", "skip",
                     "pagina", "seite"}
SEARCH_PARAMS = {"q", "query", "s", "search", "keyword", "keywords", "term", "k", "what", "find"}
LISTING = re.compile(
    r"/(?:categor(?:y|ies)|cat|c|collections?|directory|directories|browse|list|listings|results|search|"
    r"companies|businesses|business-directory|members|providers|vendors|suppliers|agencies|firms|products|"
    r"shop|store|catalog|catalogue|jobs|vacancies|careers|properties|property-for-sale|for-sale|to-rent|events|"
    r"industries|industry|sectors|locations|cities|regions|tags?|topics|brands)(?:/|$|\.html?)",
    re.IGNORECASE,
)
DETAIL = re.compile(
    r"/(?:company|companies|business|businesses|biz|listing|listings|profile|profiles|member|members|vendor|"
    r"provider|agency|firm|supplier|store|place|places|venue|restaurant|hotel|product|products|item|items|p|dp|"
    r"sku|job|jobs|vacancy|vacancies|position|career|careers|property|properties|listing-detail|event|events|"
    r"catalogue|people|person|team|directory)/[^/?#]{3,}",
    re.IGNORECASE,
)
ID_SLUG = re.compile(r"(?:[-_/](?:\d{3,}|[a-f0-9]{8,})(?:\.html?)?$|[a-z0-9]+(?:-[a-z0-9]+){2,}(?:\.html?)?$)",
                     re.IGNORECASE)
LIST_WORDS = re.compile(r"\b(?:top|best|list|lists|directory|directories|leading|largest|biggest|\d{1,3})\b",
                        re.IGNORECASE)


CATEGORY = re.compile(r"/(?:categor(?:y|ies)|cat|collections?|tags?|topics|industries|sectors)/", re.IGNORECASE)
ARTICLE = re.compile(r"/(?:blog|news|articles?|posts?|press|media|insights|resources|guides|stories|magazine)(?:/|$)",
                     re.IGNORECASE)


def classify_url(url: str) -> str:
    """Return one of: skip, home, pagination, search, contact, about, listing, detail, article, other."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return "skip"
    path = parts.path or "/"
    query = {k.lower() for k in parse_qs(parts.query, keep_blank_values=True)}
    if SKIP.search(path) or (query & SKIP_QUERY):
        return "skip"
    if path in ("", "/") and not query:
        return "home"
    if PAGINATION_PATH.search(path) or (query & PAGINATION_PARAMS):
        return "pagination"
    if query & SEARCH_PARAMS:
        return "search"
    if CONTACT.search(path):
        return "contact"
    if ABOUT.search(path):
        return "about"
    if ARTICLE.search(path):
        return "article"
    if CATEGORY.search(path):
        return "listing"
    segments = [s for s in path.split("/") if s]
    if len(segments) >= 2 and segments[-1].lower() in ("index.html", "index.htm", "index.php", "default.aspx"):
        segments = segments[:-1]
        path = "/" + "/".join(segments)
    last = segments[-1] if segments else ""
    if DETAIL.search(path) and not re.fullmatch(r"(?:page|p)?-?\d{1,3}(?:\.html?)?", last) and \
            last.lower() not in ("all", "list"):
        return "detail"
    if LISTING.search(path):
        return "listing"
    if last and ID_SLUG.search(last):
        return "detail"
    return "other"


def is_pagination_link(text: str, rel: str = "", classes: str = "", title: str = "") -> bool:
    label = (text or title or "").strip().lower()
    if "next" in (rel or "").split():
        return True
    if label in ("next", "next page", "next »", "next ›", "›", "»", ">", ">>", "more", "load more", "older posts",
                 "older", "show more", "more results", "next →", "→"):
        return True
    return bool(re.search(r"\bnext\b", classes or "", re.I) or re.search(r"\bnext\b", title or "", re.I))


def looks_like_list_title(title: str) -> bool:
    return bool(LIST_WORDS.search(title or ""))

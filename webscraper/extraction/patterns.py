"""Pattern-based extraction: metadata, emails, phone numbers, social profiles, website links."""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple
from urllib.parse import unquote, urlsplit

import phonenumbers
from bs4 import BeautifulSoup

from ..util.text import clean_whitespace
from ..util.urls import normalize_url, registered_domain
from .page import Link, Page

# ------------------------------------------------------------------------------------------------
# Metadata
# ------------------------------------------------------------------------------------------------


def extract_meta(soup: BeautifulSoup) -> Dict[str, str]:
    """``<meta>`` name/property -> content (lower-cased keys; first value wins)."""
    meta: Dict[str, str] = {}
    for tag in soup.find_all("meta"):
        key = tag.get("property") or tag.get("name") or tag.get("itemprop") or tag.get("http-equiv")
        content = tag.get("content")
        if key and content and str(key).lower() not in meta:
            meta[str(key).lower()] = clean_whitespace(content)
    return meta


# ------------------------------------------------------------------------------------------------
# Emails
# ------------------------------------------------------------------------------------------------

EMAIL_RE = re.compile(r"(?<![\w.+-])[A-Za-z0-9][A-Za-z0-9._%+-]{0,63}@[A-Za-z0-9](?:[A-Za-z0-9-]{0,62}\.)+[A-Za-z]{2,24}(?![\w-])")
_AT = r"(?:\s*\[\s*at\s*\]\s*|\s*\(\s*at\s*\)\s*|\s*\{\s*at\s*\}\s*|\s+at\s+(?=[A-Za-z0-9-]+\s*[\[({]\s*dot))"
_DOT = r"(?:\s*\[\s*dot\s*\]\s*|\s*\(\s*dot\s*\)\s*|\s*\{\s*dot\s*\}\s*|\.(?=[A-Za-z0-9-]))"
OBFUSCATED_RE = re.compile(r"([A-Za-z0-9._%+-]+)" + _AT + r"([A-Za-z0-9-]+(?:" + _DOT + r"[A-Za-z0-9-]+)+)",
                           re.IGNORECASE)
_BAD_EMAIL_DOMAINS = {
    "example.com", "example.org", "example.net", "domain.com", "email.com", "yourdomain.com", "yoursite.com",
    "sentry.io", "wixpress.com", "sentry-next.wixpress.com", "sentry.wixpress.com", "mysite.com", "company.com",
    "website.com", "test.com", "address.com", "yourcompany.com", "placeholder.com", "godaddy.com",
}
_BAD_EMAIL_TLDS = {"png", "jpg", "jpeg", "gif", "svg", "webp", "css", "js", "ico", "bmp", "tiff", "mp4", "pdf"}


def is_plausible_email(email: str) -> bool:
    local, _, domain = email.rpartition("@")
    if not local or not domain or "." not in domain:
        return False
    domain = domain.lower()
    if domain.rsplit(".", 1)[-1] in _BAD_EMAIL_TLDS or domain in _BAD_EMAIL_DOMAINS:
        return False
    if re.search(r"@\d+x\.", email) or len(local) > 64 or local.startswith(".") or ".." in email:
        return False
    if re.fullmatch(r"[a-f0-9]{24,}", local.lower()):  # tracking ids (e.g. Sentry DSNs)
        return False
    return True


def decode_cfemail(hex_string: str) -> Optional[str]:
    """Decode Cloudflare's e-mail obfuscation (``data-cfemail``)."""
    try:
        data = bytes.fromhex(hex_string)
    except ValueError:
        return None
    if len(data) < 2:
        return None
    key = data[0]
    try:
        return "".join(chr(b ^ key) for b in data[1:])
    except ValueError:
        return None


def _hrefs(soup: BeautifulSoup, prefix: str) -> List[str]:
    out = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.lower().startswith(prefix):
            out.append(href)
    return out


def find_emails(soup: BeautifulSoup, text: str) -> List[Tuple[str, str, float]]:
    """Return ``(email, method, confidence)`` tuples in priority order (mailto first)."""
    results: List[Tuple[str, str, float]] = []
    seen = set()

    def add(raw: str, method: str, conf: float) -> None:
        email = unquote(raw).strip().strip(".,;:").lower()
        email = re.sub(r"^(?:u003e|u0022|%20|mailto:)+", "", email)
        if EMAIL_RE.fullmatch(email) and is_plausible_email(email) and email not in seen:
            seen.add(email)
            results.append((email, method, conf))

    for href in _hrefs(soup, "mailto:"):
        addr = href.split(":", 1)[1].split("?", 1)[0]
        for part in addr.split(","):
            add(part, "mailto", 0.92)
    for tag in soup.select("[data-cfemail]"):
        decoded = decode_cfemail(tag.get("data-cfemail", ""))
        if decoded:
            add(decoded, "cloudflare-decoded", 0.9)
    for href in _hrefs(soup, "/cdn-cgi/l/email-protection#"):
        decoded = decode_cfemail(href.split("#", 1)[1])
        if decoded:
            add(decoded, "cloudflare-decoded", 0.9)
    body = text or ""
    for m in EMAIL_RE.finditer(body):
        add(m.group(0), "text", 0.75)
    for m in OBFUSCATED_RE.finditer(body):
        domain = re.sub(r"\s*(?:\[\s*dot\s*\]|\(\s*dot\s*\)|\{\s*dot\s*\})\s*", ".", m.group(2), flags=re.I)
        add("%s@%s" % (m.group(1), domain.replace(" ", "")), "text-deobfuscated", 0.6)
    return results


# ------------------------------------------------------------------------------------------------
# Phones
# ------------------------------------------------------------------------------------------------


def find_phones(soup: BeautifulSoup, text: str, region: Optional[str]) -> List[Tuple[str, str, float]]:
    """Return ``(raw_phone, method, confidence)``; numbers are normalized later."""
    out: List[Tuple[str, str, float]] = []
    seen = set()
    region = region or "US"

    def key_for(raw: str) -> str:
        try:
            num = phonenumbers.parse(raw, region)
            return phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.E164)
        except phonenumbers.NumberParseException:
            return re.sub(r"\D", "", raw)

    for href in _hrefs(soup, "tel:") + _hrefs(soup, "callto:"):
        raw = unquote(href.split(":", 1)[1]).strip()
        k = key_for(raw)
        if len(re.sub(r"\D", "", raw)) >= 7 and k not in seen:
            seen.add(k)
            out.append((raw, "tel-link", 0.9))
    body = (text or "")[:150_000]
    try:
        matcher = phonenumbers.PhoneNumberMatcher(body, region, leniency=phonenumbers.Leniency.VALID, max_tries=200)
        for match in matcher:
            k = phonenumbers.format_number(match.number, phonenumbers.PhoneNumberFormat.E164)
            if k not in seen:
                seen.add(k)
                out.append((match.raw_string, "text", 0.7))
    except Exception:  # pragma: no cover - defensive: matcher should not fail on odd text
        pass
    return out


# ------------------------------------------------------------------------------------------------
# Social profiles and websites
# ------------------------------------------------------------------------------------------------

SOCIAL_DOMAINS = {
    "facebook.com": "facebook", "fb.com": "facebook", "linkedin.com": "linkedin", "twitter.com": "twitter",
    "x.com": "twitter", "instagram.com": "instagram", "youtube.com": "youtube", "youtu.be": "youtube",
    "github.com": "github", "tiktok.com": "tiktok", "pinterest.com": "pinterest", "threads.net": "threads",
}
_SHARE_PATTERNS = re.compile(
    r"/(?:sharer|share|intent|shareArticle|dialog|plugins|login|signup|hashtag|search|watch\?|embed|tr\?)",
    re.IGNORECASE,
)


def social_network(url: str) -> Optional[str]:
    return SOCIAL_DOMAINS.get(registered_domain(url))


_PROFILE_HOST = re.compile(r"^(?:www\.|m\.|mobile\.|[a-z]{2}\.)?[^.]+\.[a-z]+$")


def is_social_profile(url: str) -> Optional[str]:
    """Return the network name if ``url`` looks like a profile/page (not a share link, post or doc)."""
    net = social_network(url)
    if not net:
        return None
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if not _PROFILE_HOST.match(host) or _SHARE_PATTERNS.search(url):
        return None
    segs = [s for s in parts.path.split("/") if s]
    if not segs:
        return None
    first = segs[0].lower()
    if net == "linkedin":
        ok = len(segs) == 2 and first in ("company", "in", "school", "showcase")
    elif net == "youtube":
        ok = (len(segs) == 1 and first.startswith("@")) or (len(segs) == 2 and first in ("channel", "c", "user"))
    elif net == "github":
        ok = len(segs) <= 2 and first not in ("sponsors", "orgs", "topics", "features", "about", "pricing",
                                               "marketplace", "settings", "login", "explore") and \
            not (len(segs) == 2 and segs[1].lower() in ("issues", "pulls", "discussions"))
    elif net == "facebook":
        ok = len(segs) == 1 or first in ("pages", "groups") or first == "profile.php"
    else:  # twitter / x, instagram, tiktok, pinterest, threads: a single handle
        ok = len(segs) == 1
    if not ok:
        return None
    if net in ("facebook", "twitter", "instagram", "tiktok", "pinterest", "threads") and first in (
            "home", "explore", "about", "privacy", "policies", "help", "share", "tos", "login", "signup", "i",
            "intent", "hashtag", "search", "legal", "settings"):
        return None
    return net


def find_social_links(links: List[Link]) -> List[Tuple[str, str]]:
    """Return ``(network, url)`` pairs for profile links (share buttons are ignored)."""
    out: List[Tuple[str, str]] = []
    seen = set()
    for link in links:
        net = is_social_profile(link.url)
        if net and link.url not in seen:
            seen.add(link.url)
            out.append((net, link.url))
    return out


WEBSITE_LINK_TEXT = re.compile(r"^(?:visit\s+(?:the\s+)?)?(?:official\s+)?(?:web\s?site|homepage|home page|visit|"
                               r"go to (?:the )?(?:web)?site|company website|www\..+)$", re.IGNORECASE)

# Domains that are never "the company's website" when found as outbound links.
NON_ENTITY_DOMAINS = set(SOCIAL_DOMAINS) | {
    "google.com", "goo.gl", "maps.app.goo.gl", "apple.com", "microsoft.com", "wikipedia.org", "wikimedia.org",
    "wordpress.org", "wordpress.com", "wix.com", "squarespace.com", "shopify.com", "godaddy.com",
    "cloudflare.com", "w3.org", "schema.org", "bing.com", "yahoo.com", "duckduckgo.com", "amazon.com",
    "whatsapp.com", "wa.me", "t.me", "telegram.org", "medium.com", "reddit.com", "quora.com",
    "trustpilot.com", "yelp.com", "tripadvisor.com", "glassdoor.com", "indeed.com", "crunchbase.com",
    "bit.ly", "tinyurl.com", "mailchimp.com", "hubspot.com", "gstatic.com", "googleapis.com",
    "googletagmanager.com", "doubleclick.net", "addthis.com", "sharethis.com", "vimeo.com", "spotify.com",
    "apps.apple.com", "play.google.com", "creativecommons.org", "gravatar.com", "fonts.com", "jquery.com",
    "github.io", "netlify.app", "vercel.app", "elementor.com", "wpengine.com", "hostgator.com",
    "bluehost.com", "siteground.com", "paypal.com", "stripe.com", "calendly.com", "zoom.us", "eventbrite.com",
    "linktr.ee", "gov.za", "europa.eu", "archive.org", "issuu.com", "slideshare.net", "scribd.com",
}


def is_entity_candidate_domain(url: str) -> bool:
    dom = registered_domain(url)
    if not dom or dom in NON_ENTITY_DOMAINS:
        return False
    return not any(dom.endswith("." + d) for d in NON_ENTITY_DOMAINS)


def find_website_links(page: Page, main_only: bool = True) -> List[Tuple[str, float]]:
    """External links that look like "visit our website" for directory detail pages."""
    out: List[Tuple[str, float]] = []
    for link in page.links:
        if not link.external or (main_only and link.in_boilerplate) or not is_entity_candidate_domain(link.url):
            continue
        label = (link.text or link.title).strip()
        if WEBSITE_LINK_TEXT.match(label) or "website" in link.classes.lower():
            out.append((link.url, 0.88))
        elif re.match(r"^(?:https?://)?(?:www\.)?[a-z0-9-]+(\.[a-z0-9-]+)+/?$", label, re.I):
            out.append((normalize_url(link.url) or link.url, 0.8))
    return out

"""URL normalization and domain helpers."""

from __future__ import annotations

import re
from typing import Optional
from urllib.parse import parse_qsl, unquote, urlencode, urljoin, urlsplit, urlunsplit

# Query parameters that only track clicks/campaigns and never change page content.
TRACKING_PARAMS = {
    "fbclid", "gclid", "dclid", "gbraid", "wbraid", "msclkid", "yclid", "mc_cid", "mc_eid",
    "_ga", "_gl", "igshid", "ref_src", "spm", "srsltid", "trk", "trackingid", "si",
    "hsctatracking", "_hsenc", "_hsmi", "mkt_tok", "oly_anon_id", "oly_enc_id", "vero_id",
}
TRACKING_PREFIXES = ("utm_", "pk_", "mtm_", "hsa_")

# Second-level labels commonly used under country-code TLDs (example: example.co.za).
_COMMON_SLDS = {
    "co", "com", "org", "net", "ac", "gov", "edu", "ltd", "plc", "sch", "nom", "mil",
    "or", "ne", "go", "gob", "gouv", "info", "biz", "web", "firm", "gen", "int", "med",
    "nhs", "police", "res", "law", "asn", "id",
}

SKIP_EXTENSIONS = re.compile(
    r"\.(?:jpe?g|png|gif|webp|svg|ico|bmp|tiff?|avif|heic|mp[34]|m4a|wav|ogg|webm|mov|avi|mkv|"
    r"pdf|docx?|xlsx?|pptx?|odt|ods|zip|rar|7z|gz|tgz|bz2|dmg|exe|msi|pkg|apk|iso|css|js|mjs|"
    r"woff2?|ttf|eot|otf|rss|atom|ics|vcf|csv|json)(?:$|\?)",
    re.IGNORECASE,
)


def normalize_url(url: str, base: Optional[str] = None, keep_fragment: bool = False) -> Optional[str]:
    """Return a canonical form of ``url`` (resolved against ``base``) or None if unusable.

    - lowercases scheme and host, removes default ports and ``www.`` is kept (sites differ)
    - drops fragments and tracking parameters, sorts the remaining query parameters
    - removes a trailing slash from non-root paths
    """
    if not url:
        return None
    url = url.strip()
    if base:
        url = urljoin(base, url)
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        return None
    host = (parts.hostname or "").lower().rstrip(".")
    if not host or " " in host:
        return None
    try:
        port = parts.port
    except ValueError:
        return None
    netloc = host
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        netloc = "%s:%d" % (host, port)
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/") or "/"
    query_items = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() not in TRACKING_PARAMS and not k.lower().startswith(TRACKING_PREFIXES)
    ]
    query = urlencode(sorted(query_items), doseq=True)
    fragment = parts.fragment if keep_fragment else ""
    return urlunsplit((scheme, netloc, path, query, fragment))


def host_of(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def registered_domain(url_or_host: str) -> str:
    """Best-effort registrable domain (``www.shop.example.co.za`` -> ``example.co.za``)."""
    host = url_or_host
    if "://" in url_or_host or "/" in url_or_host:
        host = host_of(url_or_host if "://" in url_or_host else "http://" + url_or_host)
    host = host.lower().strip(".")
    if host.startswith("www."):
        host = host[4:]
    if re.fullmatch(r"[\d.]+", host) or ":" in host:
        return host
    labels = host.split(".")
    if len(labels) <= 2:
        return host
    tld, sld = labels[-1], labels[-2]
    if len(tld) == 2 and sld in _COMMON_SLDS:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def same_site(a: str, b: str) -> bool:
    return registered_domain(a) == registered_domain(b)


def origin(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, "/", "", ""))


def is_probably_file(url: str) -> bool:
    return bool(SKIP_EXTENSIONS.search(urlsplit(url).path or ""))


def display_url(url: str) -> str:
    """Short human-friendly version of a URL for tables (no scheme, no trailing slash)."""
    parts = urlsplit(url)
    text = parts.netloc + (parts.path if parts.path not in ("", "/") else "")
    return unquote(text)

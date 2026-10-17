"""Province/state names and abbreviations for common countries (location normalization)."""

from __future__ import annotations

from typing import Dict, Optional, Tuple

from .text import fold

_REGIONS: Dict[str, Dict[str, str]] = {
    "ZA": {
        "WC": "Western Cape", "EC": "Eastern Cape", "NC": "Northern Cape", "GP": "Gauteng", "GT": "Gauteng",
        "KZN": "KwaZulu-Natal", "NL": "KwaZulu-Natal", "FS": "Free State", "LP": "Limpopo", "MP": "Mpumalanga",
        "NW": "North West",
    },
    "US": {
        "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado",
        "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia",
        "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
        "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts",
        "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana",
        "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico",
        "NY": "New York", "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
        "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
        "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
        "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming", "PR": "Puerto Rico",
    },
    "CA": {
        "AB": "Alberta", "BC": "British Columbia", "MB": "Manitoba", "NB": "New Brunswick",
        "NL": "Newfoundland and Labrador", "NS": "Nova Scotia", "NT": "Northwest Territories", "NU": "Nunavut",
        "ON": "Ontario", "PE": "Prince Edward Island", "QC": "Quebec", "SK": "Saskatchewan", "YT": "Yukon",
    },
    "AU": {
        "NSW": "New South Wales", "VIC": "Victoria", "QLD": "Queensland", "WA": "Western Australia",
        "SA": "South Australia", "TAS": "Tasmania", "ACT": "Australian Capital Territory",
        "NT": "Northern Territory",
    },
    "GB": {"ENG": "England", "SCT": "Scotland", "WLS": "Wales", "NIR": "Northern Ireland"},
    "NZ": {"AUK": "Auckland", "WGN": "Wellington", "CAN": "Canterbury", "OTA": "Otago", "WKO": "Waikato"},
    "IN": {"MH": "Maharashtra", "KA": "Karnataka", "TN": "Tamil Nadu", "DL": "Delhi", "TG": "Telangana",
           "GJ": "Gujarat", "WB": "West Bengal", "UP": "Uttar Pradesh", "RJ": "Rajasthan", "KL": "Kerala"},
}

_BY_NAME: Dict[str, Tuple[str, str]] = {}
for _country, _table in _REGIONS.items():
    for _abbr, _name in _table.items():
        _BY_NAME.setdefault(fold(_name), (_country, _name))


def lookup_region(value: str, country: Optional[str] = None) -> Optional[Tuple[str, str]]:
    """Return ``(country_code, canonical_region_name)`` for a region name or abbreviation."""
    if not value:
        return None
    raw = value.strip().strip(".")
    folded = fold(raw)
    if folded in _BY_NAME:
        c, n = _BY_NAME[folded]
        if country is None or country == c:
            return c, n
    upper = raw.upper().replace(".", "")
    countries = [country] if country else list(_REGIONS)
    for c in countries:
        table = _REGIONS.get(c or "", {})
        if upper in table and (country is not None or len(upper) >= 2):
            return c, table[upper]
    return None

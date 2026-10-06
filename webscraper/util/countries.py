"""Country names and ISO 3166-1 alpha-2 codes (used for phone and location normalization)."""

from __future__ import annotations

from typing import Optional

from .text import fold

_TABLE = """
AD|Andorra
AE|United Arab Emirates
AF|Afghanistan
AG|Antigua and Barbuda
AI|Anguilla
AL|Albania
AM|Armenia
AN|Netherlands Antilles
AO|Angola
AQ|Antarctica
AR|Argentina
AS|American Samoa
AT|Austria
AU|Australia
AW|Aruba
AX|Åland Islands
AZ|Azerbaijan
BA|Bosnia and Herzegovina
BB|Barbados
BD|Bangladesh
BE|Belgium
BF|Burkina Faso
BG|Bulgaria
BH|Bahrain
BI|Burundi
BJ|Benin
BL|Saint Barthélemy
BM|Bermuda
BN|Brunei
BO|Bolivia
BQ|Bonaire, Sint Eustatius and Saba
BR|Brazil
BS|Bahamas
BT|Bhutan
BV|Bouvet Island
BW|Botswana
BY|Belarus
BZ|Belize
CA|Canada
CC|Cocos Islands
CD|The Democratic Republic Of Congo
CF|Central African Republic
CG|Congo
CH|Switzerland
CI|Côte d'Ivoire
CK|Cook Islands
CL|Chile
CM|Cameroon
CN|China
CO|Colombia
CR|Costa Rica
CU|Cuba
CV|Cape Verde
CW|Curaçao
CX|Christmas Island
CY|Cyprus
CZ|Czech Republic
DE|Germany
DJ|Djibouti
DK|Denmark
DM|Dominica
DO|Dominican Republic
DZ|Algeria
EC|Ecuador
EE|Estonia
EG|Egypt
EH|Western Sahara
ER|Eritrea
ES|Spain
ET|Ethiopia
FI|Finland
FJ|Fiji
FK|Falkland Islands
FM|Micronesia
FO|Faroe Islands
FR|France
GA|Gabon
GB|United Kingdom
GD|Grenada
GE|Georgia
GF|French Guiana
GG|Guernsey
GH|Ghana
GI|Gibraltar
GL|Greenland
GM|Gambia
GN|Guinea
GP|Guadeloupe
GQ|Equatorial Guinea
GR|Greece
GS|South Georgia And The South Sandwich Islands
GT|Guatemala
GU|Guam
GW|Guinea-Bissau
GY|Guyana
HK|Hong Kong
HM|Heard Island And McDonald Islands
HN|Honduras
HR|Croatia
HT|Haiti
HU|Hungary
ID|Indonesia
IE|Ireland
IL|Israel
IM|Isle Of Man
IN|India
IO|British Indian Ocean Territory
IQ|Iraq
IR|Iran
IS|Iceland
IT|Italy
JE|Jersey
JM|Jamaica
JO|Jordan
JP|Japan
KE|Kenya
KG|Kyrgyzstan
KH|Cambodia
KI|Kiribati
KM|Comoros
KN|Saint Kitts And Nevis
KP|North Korea
KR|South Korea
KW|Kuwait
KY|Cayman Islands
KZ|Kazakhstan
LA|Laos
LB|Lebanon
LC|Saint Lucia
LI|Liechtenstein
LK|Sri Lanka
LR|Liberia
LS|Lesotho
LT|Lithuania
LU|Luxembourg
LV|Latvia
LY|Libya
MA|Morocco
MC|Monaco
MD|Moldova
ME|Montenegro
MF|Saint Martin
MG|Madagascar
MH|Marshall Islands
MK|Macedonia
ML|Mali
MM|Myanmar
MN|Mongolia
MO|Macao
MP|Northern Mariana Islands
MQ|Martinique
MR|Mauritania
MS|Montserrat
MT|Malta
MU|Mauritius
MV|Maldives
MW|Malawi
MX|Mexico
MY|Malaysia
MZ|Mozambique
NA|Namibia
NC|New Caledonia
NE|Niger
NF|Norfolk Island
NG|Nigeria
NI|Nicaragua
NL|Netherlands
NO|Norway
NP|Nepal
NR|Nauru
NU|Niue
NZ|New Zealand
OM|Oman
PA|Panama
PE|Peru
PF|French Polynesia
PG|Papua New Guinea
PH|Philippines
PK|Pakistan
PL|Poland
PM|Saint Pierre And Miquelon
PN|Pitcairn
PR|Puerto Rico
PS|Palestine
PT|Portugal
PW|Palau
PY|Paraguay
QA|Qatar
RE|Reunion
RO|Romania
RS|Serbia
RU|Russia
RW|Rwanda
SA|Saudi Arabia
SB|Solomon Islands
SC|Seychelles
SD|Sudan
SE|Sweden
SG|Singapore
SH|Saint Helena
SI|Slovenia
SJ|Svalbard And Jan Mayen
SK|Slovakia
SL|Sierra Leone
SM|San Marino
SN|Senegal
SO|Somalia
SR|Suriname
SS|South Sudan
ST|Sao Tome And Principe
SV|El Salvador
SX|Sint Maarten (Dutch part)
SY|Syria
SZ|Swaziland
TC|Turks And Caicos Islands
TD|Chad
TF|French Southern Territories
TG|Togo
TH|Thailand
TJ|Tajikistan
TK|Tokelau
TL|Timor-Leste
TM|Turkmenistan
TN|Tunisia
TO|Tonga
TR|Turkey
TT|Trinidad and Tobago
TV|Tuvalu
TW|Taiwan
TZ|Tanzania
UA|Ukraine
UG|Uganda
UM|United States Minor Outlying Islands
US|United States
UY|Uruguay
UZ|Uzbekistan
VA|Vatican
VC|Saint Vincent And The Grenadines
VE|Venezuela
VG|British Virgin Islands
VI|U.S. Virgin Islands
VN|Vietnam
VU|Vanuatu
WF|Wallis And Futuna
WS|Samoa
YE|Yemen
YT|Mayotte
ZA|South Africa
ZM|Zambia
ZW|Zimbabwe
"""

# Common alternative names that people type into a targeting form.
_ALIASES = {
    "usa": "US", "us": "US", "u.s.": "US", "u.s.a.": "US", "america": "US",
    "united states of america": "US", "uk": "GB", "u.k.": "GB", "great britain": "GB",
    "britain": "GB", "england": "GB", "scotland": "GB", "wales": "GB",
    "northern ireland": "GB", "uae": "AE", "rsa": "ZA", "s. africa": "ZA",
    "south korea": "KR", "korea": "KR", "north korea": "KP", "russia": "RU",
    "czech republic": "CZ", "czechia": "CZ", "holland": "NL", "the netherlands": "NL",
    "ivory coast": "CI", "vietnam": "VN", "laos": "LA", "iran": "IR", "syria": "SY",
    "bolivia": "BO", "venezuela": "VE", "tanzania": "TZ", "moldova": "MD",
    "macedonia": "MK", "taiwan": "TW", "hong kong": "HK", "macau": "MO", "macao": "MO",
    "palestine": "PS", "burma": "MM", "swaziland": "SZ", "eswatini": "SZ",
    "drc": "CD", "dr congo": "CD", "republic of the congo": "CG", "turkiye": "TR",
}

_BY_CODE: dict = {}
_BY_NAME: dict = {}
for _line in _TABLE.strip().splitlines():
    _code, _name = _line.split("|", 1)
    _BY_CODE[_code] = _name
    _BY_NAME[fold(_name)] = _code
for _alias, _code in _ALIASES.items():
    _BY_NAME.setdefault(fold(_alias), _code)


def country_code(value: Optional[str]) -> Optional[str]:
    """Return the ISO alpha-2 code for a country name or code (case-insensitive)."""
    if not value:
        return None
    raw = value.strip()
    if len(raw) == 2 and raw.upper() in _BY_CODE:
        return raw.upper()
    return _BY_NAME.get(fold(raw))


def country_name(code_or_name: Optional[str]) -> Optional[str]:
    """Return the English country name for a code or name."""
    code = country_code(code_or_name)
    return _BY_CODE.get(code) if code else None


def all_country_names() -> list:
    return list(_BY_CODE.values())

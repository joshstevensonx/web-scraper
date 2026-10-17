"""Entity types and the field knowledge that drives extraction, queries and validation.

The platform is *schema-first*: the entity type decides default fields, how search queries are
phrased, which schema.org types count as a matching record, and whether a record is one website
(``site`` mode, e.g. a company) or one page (``page`` mode, e.g. a product or a job).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from ..util.text import fold

ORG_TYPES = {
    "organization", "corporation", "localbusiness", "professionalservice", "store", "ngo",
    "educationalorganization", "medicalorganization", "sportsorganization", "governmentorganization",
    "onlinebusiness", "onlinestore", "brand", "company",
}
# Common LocalBusiness subtypes (schema.org has hundreds; unknown ones still match via suffix rules).
LOCAL_BUSINESS_TYPES = {
    "restaurant", "cafeorcoffeeshop", "barorpub", "bakery", "fastfoodrestaurant", "foodestablishment",
    "hotel", "motel", "hostel", "lodgingbusiness", "bedandbreakfast", "resort", "campground",
    "dentist", "physician", "medicalclinic", "hospital", "pharmacy", "attorney", "legalservice",
    "accountingservice", "financialservice", "insuranceagency", "realestateagent", "travelagency",
    "autodealer", "autorepair", "beautysalon", "hairsalon", "dayspa", "healthclub", "plumber",
    "electrician", "generalcontractor", "roofingcontractor", "housepainter", "locksmith",
    "movingcompany", "homeandconstructionbusiness", "employmentagency", "itservice",
    "softwarecompany", "clothingstore", "electronicsstore", "furniturestore", "grocerystore",
    "hardwarestore", "bookstore", "petstore", "shoestore", "sportinggoodsstore", "jewelrystore",
    "automotivebusiness", "entertainmentbusiness", "sportsactivitylocation", "childcare",
    "dryCleaningorlaundry".lower(), "emergencyservice", "governmentoffice", "library", "school",
}


@dataclass
class EntityProfile:
    key: str
    label: str
    nouns: List[str]
    schema_types: set
    record_mode: str  # "site" (one record per website) or "page" (one record per detail page)
    name_field: str
    default_fields: List[dict]
    query_suffixes: List[str] = field(default_factory=list)


def _f(name: str, type_: str = "text", required: bool = False, priority: str = "medium", label: str = "") -> dict:
    return {"name": name, "type": type_, "required": required, "priority": priority, "label": label}


_BUSINESS_FIELDS = [
    _f("company_name", "text", True, "high", "Company Name"),
    _f("website", "url", True, "high", "Website"),
    _f("email", "email", False, "high", "Email"),
    _f("phone", "phone", False, "high", "Phone"),
    _f("address", "address", False, "medium", "Address"),
    _f("city", "text", False, "medium", "City"),
    _f("province", "text", False, "low", "Province / State"),
    _f("country", "text", False, "low", "Country"),
    _f("industry", "text", False, "medium", "Industry"),
    _f("description", "text", False, "medium", "Description"),
    _f("social_links", "array", False, "low", "Social Links"),
]

ENTITIES: Dict[str, EntityProfile] = {}


def _register(profile: EntityProfile) -> None:
    ENTITIES[profile.key] = profile


_register(EntityProfile("business", "Business", ["companies", "businesses"], ORG_TYPES | LOCAL_BUSINESS_TYPES,
                        "site", "company_name", _BUSINESS_FIELDS))
_register(EntityProfile("company", "Company", ["companies", "firms"], ORG_TYPES | LOCAL_BUSINESS_TYPES,
                        "site", "company_name", _BUSINESS_FIELDS))
_register(EntityProfile("supplier", "Supplier", ["suppliers", "manufacturers", "distributors"],
                        ORG_TYPES | LOCAL_BUSINESS_TYPES, "site", "company_name", _BUSINESS_FIELDS))
_register(EntityProfile("restaurant", "Restaurant", ["restaurants"],
                        {"restaurant", "foodestablishment", "cafeorcoffeeshop", "barorpub", "bakery",
                         "fastfoodrestaurant", "localbusiness"},
                        "site", "name",
                        [_f("name", "text", True, "high", "Restaurant"), _f("website", "url", False, "high"),
                         _f("phone", "phone", False, "high"), _f("email", "email", False, "medium"),
                         _f("address", "address", False, "high"), _f("city"), _f("cuisine"),
                         _f("price_range"), _f("rating", "decimal"), _f("opening_hours"),
                         _f("description")]))
_register(EntityProfile("hotel", "Hotel", ["hotels", "guest houses", "accommodation"],
                        {"hotel", "motel", "hostel", "lodgingbusiness", "bedandbreakfast", "resort",
                         "localbusiness"},
                        "site", "name",
                        [_f("name", "text", True, "high", "Hotel"), _f("website", "url", False, "high"),
                         _f("phone", "phone", False, "high"), _f("email", "email", False, "medium"),
                         _f("address", "address", False, "high"), _f("city"), _f("rating", "decimal"),
                         _f("price_range"), _f("description")]))
_register(EntityProfile("website", "Website", ["websites", "sites", "blogs"], {"website", "organization"},
                        "site", "name",
                        [_f("name", "text", True, "high", "Site Name"), _f("website", "url", True, "high"),
                         _f("description"), _f("email", "email"), _f("social_links", "array")]))
_register(EntityProfile("product", "Product", ["products"], {"product", "productgroup", "individualproduct",
                                                             "productmodel", "offer", "book", "vehicle"},
                        "page", "name",
                        [_f("name", "text", True, "high", "Product"), _f("price", "decimal", False, "high"),
                         _f("currency"), _f("availability"), _f("brand"), _f("sku"),
                         _f("rating", "decimal"), _f("description"), _f("image", "url"),
                         _f("source_url", "url", False, "low", "Product URL")],
                        ["buy", "shop"]))
_register(EntityProfile("person", "Person", ["people", "professionals"], {"person"}, "page", "full_name",
                        [_f("full_name", "text", True, "high", "Name"), _f("job_title"),
                         _f("organization"), _f("email", "email"), _f("phone", "phone"),
                         _f("linkedin_url", "url"), _f("city"), _f("description")]))
_register(EntityProfile("contact", "Contact", ["contacts", "team"], {"person", "contactpoint"}, "page",
                        "full_name",
                        [_f("full_name", "text", True, "high", "Name"), _f("job_title"),
                         _f("organization"), _f("email", "email", False, "high"),
                         _f("phone", "phone", False, "high"), _f("linkedin_url", "url")]))
_register(EntityProfile("job", "Job", ["jobs", "vacancies", "careers"], {"jobposting"}, "page", "job_title",
                        [_f("job_title", "text", True, "high", "Job Title"), _f("company"),
                         _f("location"), _f("employment_type"), _f("salary"),
                         _f("date_posted", "date"), _f("description"),
                         _f("source_url", "url", False, "low", "Job URL")]))
_register(EntityProfile("property", "Property", ["properties for sale", "property listings", "homes"],
                        {"residence", "singlefamilyresidence", "apartment", "house", "accommodation",
                         "realestatelisting", "place", "offer", "product"},
                        "page", "name",
                        [_f("name", "text", True, "high", "Listing"), _f("price", "decimal", False, "high"),
                         _f("property_type"), _f("bedrooms", "integer"), _f("bathrooms", "decimal"),
                         _f("floor_size"), _f("address", "address"), _f("city"), _f("description"),
                         _f("source_url", "url", False, "low", "Listing URL")]))
_register(EntityProfile("event", "Event", ["events"], {"event", "musicevent", "businessevent", "festival",
                                                        "sportsevent", "educationevent", "socialevent",
                                                        "theaterevent", "exhibitionevent"},
                        "page", "name",
                        [_f("name", "text", True, "high", "Event"), _f("start_date", "datetime", False, "high"),
                         _f("end_date", "datetime"), _f("venue"), _f("address", "address"), _f("city"),
                         _f("organizer"), _f("price", "decimal"), _f("description"),
                         _f("source_url", "url", False, "low", "Event URL")]))
_register(EntityProfile("generic", "Generic record", ["pages"], set(), "page", "title",
                        [_f("title", "text", True, "high", "Title"), _f("description"),
                         _f("source_url", "url", False, "low", "URL")]))

ENTITY_ALIASES = {"businesses": "business", "companies": "company", "org": "company", "organization": "company",
                  "organisation": "company", "products": "product", "jobs": "job", "people": "person",
                  "events": "event", "hotels": "hotel", "restaurants": "restaurant", "suppliers": "supplier",
                  "websites": "website", "properties": "property", "contacts": "contact", "custom": "generic",
                  "page": "generic", "record": "generic"}


def get_entity(key: Optional[str]) -> EntityProfile:
    k = fold(key or "business").replace(" ", "_")
    k = ENTITY_ALIASES.get(k, k)
    return ENTITIES.get(k, ENTITIES["generic"])


def entity_matches_schema_type(entity: EntityProfile, types: List[str]) -> bool:
    """True if any JSON-LD/microdata ``@type`` corresponds to the entity."""
    for t in types:
        t = fold(str(t)).rsplit("/", 1)[-1].replace(" ", "")
        if not t:
            continue
        if t in entity.schema_types:
            return True
        if entity.record_mode == "site" and entity.key in ("business", "company", "supplier") and (
            t.endswith("business") or t.endswith("store") or t.endswith("service") or t.endswith("organization")
            or t.endswith("agency") or t.endswith("contractor")
        ):
            return True
    return False


# ---------------------------------------------------------------------------------------------
# Field roles: maps the many ways people name a field to a single extraction "role".
# ---------------------------------------------------------------------------------------------

ROLE_ALIASES: Dict[str, Tuple[str, ...]] = {
    "name": ("company_name", "business_name", "name", "title", "product_name", "job_title", "event_name",
             "restaurant_name", "hotel_name", "full_name", "supplier_name", "organization_name",
             "listing_title", "site_name", "entity_name", "store_name"),
    "website": ("website", "homepage", "site", "web", "company_website", "website_url", "domain"),
    "source_url": ("source_url", "url", "page_url", "link", "detail_url", "listing_url", "product_url",
                   "job_url", "event_url"),
    "email": ("email", "email_address", "contact_email", "e_mail", "emails"),
    "phone": ("phone", "telephone", "phone_number", "tel", "mobile", "contact_number", "phones", "cell"),
    "address": ("address", "street_address", "full_address", "physical_address", "office_address"),
    "city": ("city", "town", "locality"),
    "suburb": ("suburb", "district", "neighbourhood", "neighborhood"),
    "province": ("province", "state", "region", "county"),
    "country": ("country",),
    "postal_code": ("postal_code", "postcode", "zip", "zip_code", "zipcode"),
    "description": ("description", "summary", "about", "bio", "overview", "details"),
    "industry": ("industry", "category", "sector", "business_category", "business_type", "categories"),
    "social_links": ("social_links", "social", "social_media", "socials"),
    "linkedin_url": ("linkedin_url", "linkedin"),
    "facebook_url": ("facebook_url", "facebook"),
    "twitter_url": ("twitter_url", "twitter", "x_url"),
    "instagram_url": ("instagram_url", "instagram"),
    "youtube_url": ("youtube_url", "youtube"),
    "github_url": ("github_url", "github"),
    "tiktok_url": ("tiktok_url", "tiktok"),
    "contact_person": ("contact_person", "contact_name", "owner", "founder", "ceo", "director"),
    "logo": ("logo", "logo_url"),
    "image": ("image", "image_url", "photo", "thumbnail", "picture"),
    "price": ("price", "amount", "cost", "asking_price"),
    "currency": ("currency", "price_currency"),
    "sku": ("sku", "product_id", "item_id", "mpn", "gtin", "isbn", "upc"),
    "brand": ("brand", "manufacturer"),
    "availability": ("availability", "stock", "in_stock", "stock_status"),
    "rating": ("rating", "stars", "rating_value", "average_rating", "score"),
    "review_count": ("review_count", "reviews", "number_of_reviews", "rating_count"),
    "price_range": ("price_range",),
    "cuisine": ("cuisine", "cuisines", "food_type"),
    "opening_hours": ("opening_hours", "hours", "business_hours", "trading_hours"),
    "founded_year": ("founded_year", "founded", "year_founded", "founding_date", "established"),
    "employees": ("employees", "number_of_employees", "employee_count", "company_size", "staff", "headcount"),
    "latitude": ("latitude", "lat"),
    "longitude": ("longitude", "lng", "lon", "long"),
    "company": ("company", "hiring_organization", "employer", "organization", "organisation", "works_for"),
    "salary": ("salary", "pay", "compensation", "base_salary"),
    "employment_type": ("employment_type", "job_type", "contract_type"),
    "date_posted": ("date_posted", "posted", "posted_date", "published", "date_published", "publish_date"),
    "valid_through": ("valid_through", "closing_date", "deadline", "expires"),
    "location": ("location", "job_location", "area"),
    "experience_level": ("experience_level", "seniority", "experience"),
    "start_date": ("start_date", "date", "event_date", "starts", "start_time"),
    "end_date": ("end_date", "ends", "end_time"),
    "venue": ("venue", "place", "location_name"),
    "organizer": ("organizer", "organiser", "host"),
    "property_type": ("property_type", "home_type", "listing_type"),
    "bedrooms": ("bedrooms", "beds", "bedroom_count"),
    "bathrooms": ("bathrooms", "baths", "bathroom_count"),
    "floor_size": ("floor_size", "size", "area_sqm", "floor_area", "square_feet", "sqft"),
    "job_title": (),  # resolved through ``name`` for jobs, but persons have a separate job title
    "author": ("author", "writer", "by"),
}
_ROLE_BY_ALIAS: Dict[str, str] = {}
for _role, _aliases in ROLE_ALIASES.items():
    for _alias in _aliases:
        _ROLE_BY_ALIAS.setdefault(_alias, _role)

DEFAULT_TYPE_BY_ROLE = {
    "website": "url", "source_url": "url", "email": "email", "phone": "phone", "address": "address",
    "social_links": "array", "linkedin_url": "url", "facebook_url": "url", "twitter_url": "url",
    "instagram_url": "url", "youtube_url": "url", "github_url": "url", "tiktok_url": "url", "logo": "url",
    "image": "url", "price": "decimal", "rating": "decimal", "review_count": "integer",
    "founded_year": "integer", "employees": "text", "latitude": "decimal", "longitude": "decimal",
    "date_posted": "date", "valid_through": "date", "start_date": "datetime", "end_date": "datetime",
    "bedrooms": "integer", "bathrooms": "decimal",
}


def field_role(field_name: str, entity: Optional[EntityProfile] = None) -> str:
    """Return the extraction role for a field name (unknown names map to ``custom``)."""
    key = fold(field_name).replace(" ", "_").replace("-", "_")
    if entity is not None and key == "job_title" and entity.key in ("person", "contact"):
        return "person_job_title"
    if entity is not None and key == "title" and entity.key == "job":
        return "name"
    return _ROLE_BY_ALIAS.get(key, "custom")

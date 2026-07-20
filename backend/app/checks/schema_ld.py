import json
from typing import TYPE_CHECKING

from bs4 import BeautifulSoup

from app.audit_context import AuditContext
from app.models import CheckResult

if TYPE_CHECKING:
    from app.qa2_evidence import QA2EvidenceBundle

_REQUIRED_FIELDS: dict[str, list[str]] = {
    "Organization": ["name", "url"],
    "LocalBusiness": ["name", "address", "telephone"],
    "Product": ["name", "description", "offers"],
    "Offer": ["price", "priceCurrency", "availability"],
    "WebSite": ["name", "url"],
    "SoftwareApplication": ["name", "applicationCategory", "operatingSystem"],
    "Article": ["headline", "author", "datePublished"],
    "BreadcrumbList": ["itemListElement"],
    "FAQPage": ["mainEntity"],
    "MerchantReturnPolicy": ["applicableCountry", "returnPolicyCategory"],
}

_RECOMMENDED_FIELDS: dict[str, list[str]] = {
    "Organization": ["logo", "sameAs"],
    "LocalBusiness": ["url", "sameAs"],
    "Product": ["image", "brand"],
    "WebSite": ["description"],
    "SoftwareApplication": ["description", "offers"],
    "Article": ["dateModified", "image"],
    "BreadcrumbList": [],
    "FAQPage": [],
}

_OFFER_RECOMMENDED_FIELDS = ["price", "priceCurrency", "availability"]
_GTIN_FIELDS = ["gtin", "gtin8", "gtin12", "gtin13", "gtin14"]
_COMMERCE_SCHEMA_TYPES = {"Product", "Offer"}
_RETURN_POLICY_FIELDS = ["hasMerchantReturnPolicy", "merchantReturnPolicy"]

_TEMPLATES = {
    "pass": (
        "Found complete {schema_type} JSON-LD schema. LLMs and search crawlers "
        "can extract structured facts about this page reliably.",
        "No action needed. Your structured data is well-formed.",
    ),
    "partial": (
        "Found {schema_type} JSON-LD schema but missing recommended fields: {missing}. "
        "Incomplete schema reduces extraction accuracy.",
        "Add the missing fields to your JSON-LD block. For ecommerce pages, Product "
        "and Offer schema should include identifiers, brand/image, offer availability, "
        "price validity, shipping details, return policy, and review signals.",
    ),
    "unknown_schema": (
        "JSON-LD is present, but no high-value entity schema was found. Detected types: {types}.",
        "Add Organization, WebSite, Product, BreadcrumbList, FAQPage, or other "
        "schema that directly describes the page and business.",
    ),
    "no_schema": (
        "No JSON-LD structured data found on the homepage. LLMs cannot extract "
        "structured facts about your organisation, product, or content.",
        "Add an Organization or WebSite JSON-LD block to your homepage. Product "
        "and Offer schema are high-value on ecommerce pages.",
    ),
    "invalid_json": (
        "Found JSON-LD script tags but none could be parsed as JSON.",
        "Validate your JSON-LD with a structured data testing tool and fix syntax errors.",
    ),
    "fetch_error": (
        "Could not fetch homepage to check for JSON-LD schema.",
        "Ensure the homepage is publicly accessible and returns a 200 response.",
    ),
}


def _normalise_types(schema_type) -> list[str]:
    if isinstance(schema_type, list):
        return [str(item) for item in schema_type]
    if isinstance(schema_type, str):
        return [schema_type]
    return []


def _flatten_schemas(value) -> list[dict]:
    schemas: list[dict] = []
    if isinstance(value, list):
        for item in value:
            schemas.extend(_flatten_schemas(item))
    elif isinstance(value, dict):
        graph = value.get("@graph")
        if graph:
            schemas.extend(_flatten_schemas(graph))
        if value.get("@type"):
            schemas.append(value)
    return schemas


def _extract_schemas(soup: BeautifulSoup) -> tuple[list[dict], int]:
    schemas: list[dict] = []
    invalid_count = 0

    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(tag.string or "")
        except (json.JSONDecodeError, TypeError):
            invalid_count += 1
            continue
        schemas.extend(_flatten_schemas(data))

    return schemas, invalid_count


def _has_speakable(schemas: list[dict]) -> bool:
    """Return True if any schema advertises a SpeakableSpecification.

    Speakable markup is a tracked-only emerging signal — voice assistants
    and audio agents use it to pick sections worth reading aloud. It is
    intentionally reported on every audit (site-type-agnostic) and does
    NOT influence the score, state, or fix text.
    """
    for schema in schemas:
        if not isinstance(schema, dict):
            continue
        # Direct ``speakable`` property on the schema node.
        if schema.get("speakable"):
            return True
        # Schema whose @type is SpeakableSpecification itself.
        schema_types = _normalise_types(schema.get("@type"))
        if any(str(t).lower() == "speakablespecification" for t in schema_types):
            return True
        # hasPart sub-blocks with their own @type / speakable property.
        for part in _schema_dicts(schema.get("hasPart")):
            if part.get("speakable"):
                return True
            part_types = _normalise_types(part.get("@type"))
            if any(str(t).lower() == "speakablespecification" for t in part_types):
                return True
    return False


def _field_present(schema: dict, field: str) -> bool:
    value = schema.get(field)
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list):
        return bool(value)
    return True


def _has_any_field(schema: dict, fields: list[str]) -> bool:
    return any(_field_present(schema, field) for field in fields)


def _schema_dicts(value: object) -> list[dict]:
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def _offer_items(schema: dict) -> list[dict]:
    offers = schema.get("offers")
    return _schema_dicts(offers)


def _offer_price_validity_present(offer: dict) -> bool:
    if _field_present(offer, "priceValidUntil"):
        return True
    price_specifications = _schema_dicts(offer.get("priceSpecification"))
    return any(
        _field_present(specification, "validThrough") for specification in price_specifications
    )


def _return_policy_present(schema: dict) -> bool:
    return _has_any_field(schema, _RETURN_POLICY_FIELDS)


def _offer_commerce_missing_fields(offer: dict) -> list[str]:
    missing: list[str] = []
    if not _offer_price_validity_present(offer):
        missing.append("priceValidUntil")
    if not _field_present(offer, "shippingDetails"):
        missing.append("shippingDetails")
    if not _return_policy_present(offer):
        missing.append("hasMerchantReturnPolicy")
    return missing


def _product_commerce_missing_fields(schema: dict) -> list[str]:
    missing: list[str] = []
    if not _field_present(schema, "sku"):
        missing.append("sku")
    if not _has_any_field(schema, _GTIN_FIELDS):
        missing.append("gtin")
    if not _has_any_field(schema, ["aggregateRating", "review"]):
        missing.append("aggregateRating or review")
    return missing


def _offer_missing_fields(schema: dict, include_commerce_details: bool = False) -> list[str]:
    offer_items = _offer_items(schema)

    if not offer_items:
        missing = list(_OFFER_RECOMMENDED_FIELDS)
        if include_commerce_details:
            missing.extend(["priceValidUntil", "shippingDetails", "hasMerchantReturnPolicy"])
        return missing

    best_offer = min(
        (
            [
                *[field for field in _OFFER_RECOMMENDED_FIELDS if not _field_present(offer, field)],
                *(_offer_commerce_missing_fields(offer) if include_commerce_details else []),
            ]
            for offer in offer_items
        ),
        key=len,
    )
    return best_offer


def _score_schema(schema: dict, include_ecommerce: bool = False) -> tuple[int, str, list[str]]:
    schema_types = _normalise_types(schema.get("@type"))
    for schema_type in schema_types:
        required = _REQUIRED_FIELDS.get(schema_type)
        if not required:
            continue
        missing = [field for field in required if not _field_present(schema, field)]
        recommended_missing = [
            field
            for field in _RECOMMENDED_FIELDS.get(schema_type, [])
            if not _field_present(schema, field)
        ]
        if include_ecommerce and schema_type == "Product":
            recommended_missing.extend(_product_commerce_missing_fields(schema))
            offer_missing = _offer_missing_fields(schema, include_commerce_details=True)
            if _field_present(schema, "shippingDetails"):
                offer_missing = [field for field in offer_missing if field != "shippingDetails"]
            if _return_policy_present(schema):
                offer_missing = [field for field in offer_missing if field != "hasMerchantReturnPolicy"]
            recommended_missing.extend(f"offers.{field}" for field in offer_missing)
        elif include_ecommerce and schema_type == "Offer":
            recommended_missing.extend(_offer_commerce_missing_fields(schema))

        score = 5 - (len(missing) * 2)
        if recommended_missing:
            score -= min(2, len(recommended_missing))

        visible_missing = [*missing, *[f"recommended {field}" for field in recommended_missing]]
        return max(1 if missing else 3, score), schema_type, visible_missing
    return 2, schema_types[0] if schema_types else "Unknown", []


def _schema_fix(template_key: str, include_ecommerce: bool) -> str:
    if template_key == "partial" and include_ecommerce:
        return (
            "Add the missing fields to your JSON-LD block. For ecommerce pages, Product "
            "and Offer schema should include SKU/GTIN identifiers, brand/image, ratings "
            "or reviews, offer price/currency/availability, price validity, shipping "
            "details, and return policy."
        )
    if template_key == "partial":
        return "Add the missing fields to the JSON-LD block that best describes this page."
    if template_key == "unknown_schema" and include_ecommerce:
        return (
            "Add Organization, WebSite, Product, BreadcrumbList, FAQPage, or other "
            "schema that directly describes the page and business."
        )
    if template_key == "unknown_schema":
        return (
            "Add Organization, WebSite, LocalBusiness, Article, BreadcrumbList, FAQPage, "
            "or other schema that directly describes the page and business."
        )
    if template_key == "no_schema" and include_ecommerce:
        return (
            "Add an Organization or WebSite JSON-LD block to your homepage. Product "
            "and Offer schema are high-value on ecommerce pages."
        )
    if template_key == "no_schema":
        return "Add Organization, WebSite, LocalBusiness, Article, or FAQPage JSON-LD to your homepage."
    return _TEMPLATES[template_key][1]


_SPEAKABLE_NOTE = (
    " Speakable markup detected — voice-content signal, tracked-only."
)


def _maybe_annotate_speakable(
    result: CheckResult, schemas: list[dict]
) -> CheckResult:
    """Append a tracked-only Speakable note when present in the schemas.

    Never alters score, state, fix, or effort — speakable is a tracked-only
    emerging signal. Applied on every audit path (site-type-agnostic).
    """
    if not _has_speakable(schemas):
        return result
    if "Speakable markup detected" in result.finding:
        return result
    return result.model_copy(update={"finding": result.finding + _SPEAKABLE_NOTE})


def _with_extraction_context(
    result: CheckResult,
    include_ecommerce: bool,
    qa2_evidence: "QA2EvidenceBundle | None",
) -> CheckResult:
    if qa2_evidence is None:
        return result

    extraction = qa2_evidence.extraction_readiness
    finding = result.finding
    fix = result.fix
    if extraction.schema_traversal_truncated:
        finding += (
            " Extraction-readiness caveat: bounded JSON-LD traversal reached a "
            "script, depth, node, or schema-type safety cap, so excess data was ignored."
        )
        fix = (
            " Keep JSON-LD focused, valid, and reasonably bounded so important entity "
            "facts remain reachable to deterministic parsers."
            if fix == "No action needed. Your structured data is well-formed."
            else fix
            + " Keep JSON-LD focused, valid, and reasonably bounded so important entity facts remain reachable to deterministic parsers."
        )

    if include_ecommerce and extraction.commerce_schema_visible_mismatches:
        fields = ", ".join(extraction.commerce_schema_visible_mismatches)
        finding += (
            " Visible product content and JSON-LD extraction affordances are not "
            f"coherent for: {fields}."
        )
        fix = (
            " Align visible product facts with the corresponding Product and Offer "
            f"JSON-LD values for {fields}."
            if fix == "No action needed. Your structured data is well-formed."
            else fix
            + " Align visible product facts with the corresponding Product and Offer JSON-LD values."
        )

    return result.model_copy(update={"finding": finding, "fix": fix})


async def check_schema_ld(
    context: AuditContext,
    include_ecommerce: bool = False,
    qa2_evidence: "QA2EvidenceBundle | None" = None,
) -> CheckResult:
    """Extract and validate JSON-LD structured data from the homepage."""
    if not context.homepage.ok:
        finding, fix = _TEMPLATES["fetch_error"]
        return _maybe_annotate_speakable(_with_extraction_context(CheckResult(
            pillar="scrapability",
            check_name="schema_ld",
            label="JSON-LD Structured Data",
            state="warn",
            evidence_level="unknown",
            score=0,
            max_score=5,
            finding=finding,
            fix=fix,
            effort="medium",
        ), include_ecommerce, qa2_evidence), [])

    soup = BeautifulSoup(context.homepage.text, "lxml")
    script_tags = soup.find_all("script", attrs={"type": "application/ld+json"})
    if not script_tags:
        finding = _TEMPLATES["no_schema"][0]
        fix = _schema_fix("no_schema", include_ecommerce)
        return _maybe_annotate_speakable(_with_extraction_context(CheckResult(
            pillar="scrapability",
            check_name="schema_ld",
            label="JSON-LD Structured Data",
            state="fail",
            score=0,
            max_score=5,
            finding=finding,
            fix=fix,
            effort="medium",
        ), include_ecommerce, qa2_evidence), [])

    schemas, invalid_count = _extract_schemas(soup)
    if not schemas:
        finding, fix = _TEMPLATES["invalid_json"]
        return _maybe_annotate_speakable(_with_extraction_context(CheckResult(
            pillar="scrapability",
            check_name="schema_ld",
            label="JSON-LD Structured Data",
            state="fail",
            score=1 if invalid_count else 0,
            max_score=5,
            finding=finding,
            fix=fix,
            effort="low",
        ), include_ecommerce, qa2_evidence), [])

    scored = [_score_schema(schema, include_ecommerce) for schema in schemas]
    commerce_scored = [
        scored_item for scored_item in scored if include_ecommerce and scored_item[1] in _COMMERCE_SCHEMA_TYPES
    ]
    score, schema_type, missing = max(commerce_scored or scored, key=lambda item: item[0])

    if score == 5 and not missing:
        state = "pass"
        finding, fix = _TEMPLATES["pass"]
        finding = finding.format(schema_type=schema_type)
    elif schema_type in _REQUIRED_FIELDS:
        state = "partial"
        finding = _TEMPLATES["partial"][0]
        fix = _schema_fix("partial", include_ecommerce)
        finding = finding.format(schema_type=schema_type, missing=", ".join(missing))
    else:
        state = "partial"
        score = 2
        finding = _TEMPLATES["unknown_schema"][0]
        fix = _schema_fix("unknown_schema", include_ecommerce)
        types = sorted({schema_type for _, schema_type, _ in scored if schema_type != "Unknown"})
        finding = finding.format(types=", ".join(types) or "unknown")

    return _maybe_annotate_speakable(_with_extraction_context(CheckResult(
        pillar="scrapability",
        check_name="schema_ld",
        label="JSON-LD Structured Data",
        state=state,
        score=score,
        max_score=5,
        finding=finding,
        fix=fix,
        effort="medium",
    ), include_ecommerce, qa2_evidence), schemas)

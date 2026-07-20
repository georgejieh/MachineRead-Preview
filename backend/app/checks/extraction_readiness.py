import json
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from bs4 import BeautifulSoup

from app.checks.llms_txt import _analyse_llms_txt, analyse_markdown_response
from app.checks.sitemap_analysis import parse_sitemap
from app.checks.ssr import (
    _analyse_rendering,
    _cleaned_text,
    _extraction_efficiency,
    _has_app_shell_signals,
)
from app.fetching import FetchResult


_MAX_HTML_BYTES = 15 * 1024 * 1024
_MAX_MARKDOWN_BYTES = 1024 * 1024
_MAX_MARKDOWN_ALTERNATES = 5
_MAX_LLMS_TXT_BYTES = 512 * 1024
_MAX_SITEMAP_BYTES = 1024 * 1024
_MAX_SCHEMA_SCRIPTS = 50
_MAX_SCHEMA_NODES = 2000
_MAX_SCHEMA_DEPTH = 30
_MAX_SCHEMA_TYPES = 32
_MAX_SCHEMA_TYPE_LENGTH = 64
_MAX_COMPARISON_TOKENS = 10_000
_SCHEMA_TYPE_RE = re.compile(r"^[A-Z][A-Za-z0-9]{0,63}$")
_WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9'_-]*")
_SPACE_RE = re.compile(r"\s+")
_HIDDEN_STYLE_RE = re.compile(
    r"(?:^|;)\s*(?:display\s*:\s*none|visibility\s*:\s*hidden)"
    r"(?:\s*!important)?\s*(?:;|$)",
    re.IGNORECASE,
)
_PRICE_RE = re.compile(
    r"(?:[$€£¥]\s*|\b(?:USD|EUR|GBP|CAD|AUD|JPY)\s*)"
    r"(\d{1,3}(?:[,.]\d{3})+(?:[,.]\d{2})?|\d{1,7}(?:[,.]\d{1,2})?)",
    re.IGNORECASE,
)
_SKU_RE = re.compile(r"\b(?:SKU|GTIN|UPC|EAN)\s*[:#-]?\s*([A-Za-z0-9._-]{3,32})\b", re.IGNORECASE)
_AVAILABILITY_RE = re.compile(
    r"\b(in stock|out of stock|sold out|available|pre-?order)\b", re.IGNORECASE
)
_COMMERCE_FIELD_ORDER = (
    "product_name",
    "price",
    "availability",
    "variants",
    "sku_or_gtin",
    "image",
    "return_details",
    "shipping_details",
)


@dataclass(frozen=True)
class ExtractionReadinessInput:
    url: str
    raw_html: str
    markdown_responses: tuple[FetchResult, ...] | list[FetchResult] = ()
    llms_txt: str | None = None
    sitemap_xml: str | None = None
    include_ecommerce: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "markdown_responses", tuple(self.markdown_responses))


@dataclass(frozen=True)
class ExtractionReadinessAnalysis:
    raw_html_bytes: int
    raw_html_truncated: bool
    hidden_node_count: int
    cleaned_text_bytes: int
    cleaned_text_words: int
    main_content_words: int
    boilerplate_ratio: float
    navigation_ratio: float
    script_style_ratio: float
    html_to_text_ratio: float
    rendering_state: str
    app_shell_signals_present: bool
    app_shell_risk: bool
    markdown_alternates_supplied: int
    markdown_alternates_checked: int
    markdown_usable_count: int
    best_markdown_bytes: int
    best_markdown_words: int
    best_markdown_token_coverage_ratio: float
    best_markdown_word_delta: int
    best_markdown_to_raw_byte_ratio: float
    best_markdown_to_main_word_ratio: float
    llms_txt_supplied: bool
    llms_txt_present: bool
    llms_txt_valid: bool
    sitemap_xml_supplied: bool
    sitemap_xml_present: bool
    sitemap_valid: bool
    sitemap_url_count: int
    sitemap_has_lastmod: bool
    sitemap_is_index: bool
    schema_types: tuple[str, ...]
    invalid_schema_count: int
    schema_traversal_truncated: bool
    schema_types_ignored: int
    product_schema_present: bool
    offer_schema_present: bool
    commerce_missing_fields: tuple[str, ...]
    commerce_visible_fields: tuple[str, ...]
    commerce_visible_missing_fields: tuple[str, ...]
    commerce_schema_visible_mismatches: tuple[str, ...]
    issues: tuple[str, ...]
    positives: tuple[str, ...]
    caveats: tuple[str, ...]


def _byte_len(value: str) -> int:
    return len(value.encode("utf-8", errors="ignore"))


def _bounded_tokens(value: str) -> set[str]:
    tokens: set[str] = set()
    for match in _WORD_RE.finditer(value.casefold()):
        tokens.add(match.group(0))
        if len(tokens) >= _MAX_COMPARISON_TOKENS:
            break
    return tokens


def _cap_text(value: str, byte_limit: int) -> tuple[str, bool]:
    encoded = value.encode("utf-8", errors="ignore")
    if len(encoded) <= byte_limit:
        return value, False
    return encoded[:byte_limit].decode("utf-8", errors="ignore"), True


def _capped_response(response: FetchResult) -> tuple[FetchResult, bool]:
    text, truncated = _cap_text(response.text, _MAX_MARKDOWN_BYTES)
    if not truncated:
        return response, False
    return (
        FetchResult(
            requested_url=response.requested_url,
            final_url=response.final_url,
            status_code=response.status_code,
            headers=dict(response.headers),
            text=text,
            elapsed_ms=response.elapsed_ms,
            redirect_chain=list(response.redirect_chain),
            error=response.error,
        ),
        True,
    )


def _html_without_hidden_nodes(html: str) -> tuple[str, int]:
    soup = BeautifulSoup(html, "lxml")
    hidden_count = 0
    for tag in list(soup.find_all(True)):
        if tag.parent is None or tag.attrs is None:
            continue
        aria_hidden = str(tag.get("aria-hidden", "")).strip().casefold() == "true"
        inline_hidden = bool(_HIDDEN_STYLE_RE.search(str(tag.get("style", ""))))
        if tag.has_attr("hidden") or aria_hidden or inline_hidden:
            tag.decompose()
            hidden_count += 1
    return str(soup), hidden_count


def _field_present(schema: dict, field: str) -> bool:
    value = schema.get(field)
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict)):
        return bool(value)
    return True


def _schema_type_values(value: object) -> list[object]:
    return value if isinstance(value, list) else [value]


def _sanitise_schema_type(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if candidate.startswith(("https://schema.org/", "http://schema.org/")):
        candidate = candidate.rstrip("/").rsplit("/", 1)[-1]
    elif "://" in candidate or "#" in candidate or "/" in candidate:
        return None
    if len(candidate) > _MAX_SCHEMA_TYPE_LENGTH or not _SCHEMA_TYPE_RE.fullmatch(candidate):
        return None
    return candidate


def _extract_json_ld_nodes(
    soup: BeautifulSoup,
) -> tuple[tuple[str, ...], list[tuple[dict, tuple[str, ...]]], int, bool, int]:
    script_tags = soup.find_all("script", attrs={"type": "application/ld+json"})
    invalid_count = 0
    traversal_truncated = len(script_tags) > _MAX_SCHEMA_SCRIPTS
    types_ignored = max(len(script_tags) - _MAX_SCHEMA_SCRIPTS, 0)
    roots: list[object] = []
    for tag in script_tags[:_MAX_SCHEMA_SCRIPTS]:
        try:
            roots.append(json.loads(tag.string or ""))
        except (ValueError, TypeError, RecursionError):
            invalid_count += 1

    schema_types: list[str] = []
    typed_nodes: list[tuple[dict, tuple[str, ...]]] = []
    stack: list[tuple[object, int]] = [(root, 0) for root in reversed(roots)]
    visited = 0
    while stack:
        value, depth = stack.pop()
        if not isinstance(value, (dict, list)):
            continue
        if visited >= _MAX_SCHEMA_NODES:
            traversal_truncated = True
            break
        visited += 1

        if isinstance(value, dict):
            node_types: list[str] = []
            for raw_type in _schema_type_values(value.get("@type")):
                schema_type = _sanitise_schema_type(raw_type)
                if schema_type is None:
                    if raw_type is not None:
                        types_ignored += 1
                    continue
                if schema_type not in schema_types:
                    if len(schema_types) >= _MAX_SCHEMA_TYPES:
                        types_ignored += 1
                        traversal_truncated = True
                        continue
                    schema_types.append(schema_type)
                if schema_type not in node_types:
                    node_types.append(schema_type)
            if node_types:
                typed_nodes.append((value, tuple(node_types)))
            children = value.values()
        else:
            children = value

        if depth >= _MAX_SCHEMA_DEPTH:
            if any(isinstance(child, (dict, list)) for child in children):
                traversal_truncated = True
            continue
        child_values: list[object] = []
        available_slots = _MAX_SCHEMA_NODES - visited - len(stack)
        for child in children:
            if not isinstance(child, (dict, list)):
                continue
            if len(child_values) >= available_slots:
                traversal_truncated = True
                break
            child_values.append(child)
        stack.extend((child, depth + 1) for child in reversed(child_values))

    return tuple(schema_types), typed_nodes, invalid_count, traversal_truncated, types_ignored


def _schema_dicts(value: object) -> list[dict]:
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def _has_any_field(schema: dict, fields: tuple[str, ...]) -> bool:
    return any(_field_present(schema, field) for field in fields)


def _offer_items(product: dict) -> list[dict]:
    return _schema_dicts(product.get("offers"))


def _return_policy_present(schema: dict) -> bool:
    return _has_any_field(schema, ("hasMerchantReturnPolicy", "merchantReturnPolicy"))


def _price_validity_present(offer: dict) -> bool:
    if _field_present(offer, "priceValidUntil"):
        return True
    return any(
        _field_present(specification, "validThrough")
        for specification in _schema_dicts(offer.get("priceSpecification"))
    )


def _offer_missing_fields(offer: dict) -> list[str]:
    missing = [
        field
        for field in ("price", "priceCurrency", "availability")
        if not _field_present(offer, field)
    ]
    if not _price_validity_present(offer):
        missing.append("priceValidUntil")
    if not _field_present(offer, "shippingDetails"):
        missing.append("shippingDetails")
    if not _return_policy_present(offer):
        missing.append("hasMerchantReturnPolicy")
    return missing


def _best_product_and_offer(
    typed_nodes: list[tuple[dict, tuple[str, ...]]],
) -> tuple[dict | None, dict | None]:
    products = [node for node, node_types in typed_nodes if "Product" in node_types]
    if not products:
        standalone_offers = [node for node, node_types in typed_nodes if "Offer" in node_types]
        return None, standalone_offers[0] if standalone_offers else None

    def candidate(product: dict) -> tuple[int, dict | None]:
        offers = _offer_items(product)
        best_offer = min(offers, key=lambda offer: len(_offer_missing_fields(offer))) if offers else None
        return len(_offer_missing_fields(best_offer or {})), best_offer

    product_offer_pairs = [(product, *candidate(product)) for product in products]
    product, _, offer = min(product_offer_pairs, key=lambda item: item[1])
    return product, offer


def _commerce_schema_evidence(
    typed_nodes: list[tuple[dict, tuple[str, ...]]],
) -> tuple[tuple[str, ...], dict[str, str | None]]:
    product, offer = _best_product_and_offer(typed_nodes)
    if product is None:
        return ("Product schema",), {}

    missing = [
        field
        for field in ("name", "description", "offers", "image", "brand", "sku")
        if not _field_present(product, field)
    ]
    if not _has_any_field(product, ("gtin", "gtin8", "gtin12", "gtin13", "gtin14")):
        missing.append("gtin")
    if not _has_any_field(product, ("aggregateRating", "review")):
        missing.append("aggregateRating or review")
    offer_missing = _offer_missing_fields(offer or {})
    if _field_present(product, "shippingDetails"):
        offer_missing = [field for field in offer_missing if field != "shippingDetails"]
    if _return_policy_present(product):
        offer_missing = [field for field in offer_missing if field != "hasMerchantReturnPolicy"]
    missing.extend(f"offers.{field}" for field in offer_missing)

    fields: dict[str, str | None] = {
        "product_name": _scalar_text(product.get("name")),
        "price": _scalar_text((offer or {}).get("price")),
        "availability": _scalar_text((offer or {}).get("availability")),
        "variants": "present" if _has_any_field(product, ("hasVariant", "isVariantOf")) else None,
        "sku_or_gtin": _first_scalar(product, ("sku", "gtin", "gtin8", "gtin12", "gtin13", "gtin14")),
        "image": "present" if _field_present(product, "image") else None,
        "return_details": "present"
        if _return_policy_present(product) or _return_policy_present(offer or {})
        else None,
        "shipping_details": "present"
        if _field_present(product, "shippingDetails") or _field_present(offer or {}, "shippingDetails")
        else None,
    }
    return tuple(missing), fields


def _scalar_text(value: object) -> str | None:
    if isinstance(value, (str, int, float)):
        text = _SPACE_RE.sub(" ", str(value)).strip()
        return text[:200] or None
    if isinstance(value, dict):
        return _scalar_text(value.get("name") or value.get("value"))
    return None


def _first_scalar(schema: dict, fields: tuple[str, ...]) -> str | None:
    for field in fields:
        value = _scalar_text(schema.get(field))
        if value:
            return value
    return None


def _tag_value(tag: object) -> str | None:
    if not hasattr(tag, "get"):
        return None
    for attribute in ("content", "value", "href", "data-price", "data-sku", "data-gtin"):
        value = _scalar_text(tag.get(attribute))
        if value:
            return value
    if hasattr(tag, "get_text"):
        return _scalar_text(tag.get_text(" ", strip=True))
    return None


def _first_selector_value(soup: BeautifulSoup, selectors: tuple[str, ...]) -> str | None:
    for selector in selectors:
        tag = soup.select_one(selector)
        value = _tag_value(tag)
        if value:
            return value
    return None


def _visible_commerce_evidence(soup: BeautifulSoup) -> dict[str, str | None]:
    page_text = _SPACE_RE.sub(" ", soup.get_text(" ", strip=True))[:200_000]
    product_name = _first_selector_value(
        soup, ('[itemprop~="name"]', "[data-product-name]", "main h1", "article h1", "h1")
    )
    price = _first_selector_value(soup, ('[itemprop~="price"]', "[data-price]"))
    if not price:
        match = _PRICE_RE.search(page_text)
        price = match.group(1) if match else None
    availability = _first_selector_value(
        soup, ('[itemprop~="availability"]', "[data-availability]")
    )
    if not availability:
        match = _AVAILABILITY_RE.search(page_text)
        availability = match.group(1) if match else None
    sku_or_gtin = _first_selector_value(
        soup,
        (
            '[itemprop~="sku"]',
            '[itemprop^="gtin"]',
            "[data-sku]",
            "[data-gtin]",
        ),
    )
    if not sku_or_gtin:
        match = _SKU_RE.search(page_text)
        sku_or_gtin = match.group(1) if match else None

    variant_select = soup.select_one(
        'select[name*="variant" i], select[id*="variant" i], '
        'select[name*="size" i], select[id*="size" i], '
        'select[name*="color" i], select[id*="color" i], [data-variant]'
    )
    variants = None
    if variant_select is not None:
        options = variant_select.find_all("option") if hasattr(variant_select, "find_all") else []
        if not options or len(options) > 1:
            variants = "present"

    image = soup.select_one(
        '[itemprop~="image"], [data-product-image], main img[src], article img[src]'
    )
    return_hint = soup.select_one('a[href*="return" i], a[href*="refund" i]')
    shipping_hint = soup.select_one('a[href*="shipping" i], a[href*="delivery" i]')
    return_present = bool(return_hint) or bool(
        re.search(r"\b(returns?|refunds?|exchanges?)\b", page_text, re.IGNORECASE)
    )
    shipping_present = bool(shipping_hint) or bool(re.search(r"\b(shipping|delivery)\b", page_text, re.IGNORECASE))
    return {
        "product_name": product_name,
        "price": price,
        "availability": availability,
        "variants": variants,
        "sku_or_gtin": sku_or_gtin,
        "image": "present" if image is not None else None,
        "return_details": "present" if return_present else None,
        "shipping_details": "present" if shipping_present else None,
    }


def _normalise_comparison(field: str, value: str) -> object:
    if field == "price":
        match = re.search(
            r"\d{1,3}(?:[,.]\d{3})+(?:[,.]\d{1,2})?|\d{1,12}(?:[,.]\d{1,2})?",
            value,
        )
        if not match:
            return ""
        number = match.group(0)
        if "," in number and "." in number:
            decimal_separator = "," if number.rfind(",") > number.rfind(".") else "."
            grouping_separator = "." if decimal_separator == "," else ","
            number = number.replace(grouping_separator, "").replace(decimal_separator, ".")
        elif number.count(",") + number.count("."):
            separator = "," if "," in number else "."
            parts = number.split(separator)
            if len(parts) > 2:
                if len(parts[-1]) in {1, 2}:
                    number = "".join(parts[:-1]) + "." + parts[-1]
                else:
                    number = "".join(parts)
            elif len(parts[-1]) == 3:
                number = "".join(parts)
            else:
                number = parts[0] + "." + parts[1]
        try:
            return Decimal(number)
        except InvalidOperation:
            return ""
    if field == "availability":
        compact = re.sub(r"[^a-z]", "", value.casefold())
        for token in ("outofstock", "soldout", "preorder", "instock", "available"):
            if token in compact:
                return token
        return compact
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def _commerce_mismatches(
    visible: dict[str, str | None], schema: dict[str, str | None]
) -> tuple[str, ...]:
    mismatches: list[str] = []
    for field in _COMMERCE_FIELD_ORDER:
        visible_value = visible.get(field)
        schema_value = schema.get(field)
        if bool(visible_value) != bool(schema_value):
            mismatches.append(field)
            continue
        if not visible_value or not schema_value or field not in {
            "product_name",
            "price",
            "availability",
            "sku_or_gtin",
        }:
            continue
        if _normalise_comparison(field, visible_value) != _normalise_comparison(field, schema_value):
            mismatches.append(field)
    return tuple(mismatches)


def _append_unique(values: list[str], value: str) -> None:
    if value and value not in values:
        values.append(value)


def analyse_extraction_readiness(
    profile_input: ExtractionReadinessInput,
) -> ExtractionReadinessAnalysis:
    """Assess local extraction affordances from already-fetched source responses."""
    issues: list[str] = []
    positives: list[str] = []
    caveats: list[str] = [
        "This is local source-response readiness only; no Firecrawl or other extraction-provider API was called.",
        "Browser-rendered output, extraction fidelity, screenshots, and crawl or map behavior were not verified.",
        "External CSS and browser-computed visibility were not evaluated; only conservative source-level hidden attributes and inline styles were excluded.",
        "Actual product extraction was not verified.",
    ]

    raw_html_bytes = _byte_len(profile_input.raw_html)
    raw_html, raw_html_truncated = _cap_text(profile_input.raw_html, _MAX_HTML_BYTES)
    if raw_html_truncated:
        caveats.append("Raw HTML exceeded 15 MiB and was truncated before parsing.")

    visible_html, hidden_node_count = _html_without_hidden_nodes(raw_html)
    if hidden_node_count:
        caveats.append(
            f"{hidden_node_count} conservatively hidden source node(s) were excluded from text metrics."
        )
    efficiency = _extraction_efficiency(visible_html)
    cleaned_text = _cleaned_text(visible_html)
    rendering_state, _ = _analyse_rendering(visible_html)
    visible_soup = BeautifulSoup(visible_html, "lxml")
    app_shell_signals_present = _has_app_shell_signals(visible_soup)
    app_shell_risk = rendering_state == "probable_js_shell"

    for issue in efficiency.issues:
        _append_unique(issues, f"Raw HTML {issue}.")
    if rendering_state == "readable":
        positives.append("Raw HTML exposes substantial text without JavaScript execution.")
    elif rendering_state == "thin":
        _append_unique(issues, "Raw HTML is thin for local extraction.")
    elif rendering_state == "probable_js_shell":
        _append_unique(issues, "Raw HTML has probable JavaScript app-shell risk.")
    else:
        _append_unique(issues, "Raw HTML has no meaningful body text for local extraction.")

    markdown_responses = tuple(profile_input.markdown_responses)
    checked_responses = markdown_responses[:_MAX_MARKDOWN_ALTERNATES]
    if len(markdown_responses) > _MAX_MARKDOWN_ALTERNATES:
        caveats.append(
            f"Only the first {_MAX_MARKDOWN_ALTERNATES} Markdown alternates were checked; "
            f"{len(markdown_responses) - _MAX_MARKDOWN_ALTERNATES} were ignored."
        )

    markdown_usable_count = 0
    best_markdown_bytes = 0
    best_markdown_words = 0
    best_markdown_tokens: set[str] = set()
    best_markdown_selection_key = (-1.0, -1)
    comparison_main = visible_soup.select_one('main, article, [role="main"]')
    comparison_text = _cleaned_text(comparison_main) if comparison_main else cleaned_text
    comparison_tokens = _bounded_tokens(comparison_text)
    for index, response in enumerate(checked_responses, start=1):
        capped_response, truncated = _capped_response(response)
        if truncated:
            caveats.append(
                f"Markdown alternate {index} exceeded 1 MiB and was truncated before analysis."
            )
        markdown_result = analyse_markdown_response(
            capped_response,
            f"Markdown alternate {index}",
        )
        if markdown_result.available:
            markdown_usable_count += 1
            markdown_words = len(_WORD_RE.findall(capped_response.text[:_MAX_MARKDOWN_BYTES]))
            markdown_tokens = _bounded_tokens(capped_response.text)
            overlap = (
                len(markdown_tokens & comparison_tokens) / len(comparison_tokens)
                if comparison_tokens
                else 0.0
            )
            selection_key = (
                (overlap, markdown_words)
                if comparison_tokens
                else (0.0, markdown_words)
            )
            if selection_key > best_markdown_selection_key:
                best_markdown_selection_key = selection_key
                best_markdown_words = markdown_words
                best_markdown_bytes = _byte_len(capped_response.text)
                best_markdown_tokens = markdown_tokens

    best_markdown_token_coverage_ratio = (
        len(best_markdown_tokens & comparison_tokens) / len(comparison_tokens)
        if comparison_tokens and best_markdown_tokens
        else 0.0
    )
    best_markdown_word_delta = best_markdown_words - efficiency.visible_words
    best_markdown_to_raw_byte_ratio = (
        best_markdown_bytes / raw_html_bytes if raw_html_bytes and best_markdown_bytes else 0.0
    )
    best_markdown_to_main_word_ratio = (
        best_markdown_words / efficiency.main_content_words
        if efficiency.main_content_words and best_markdown_words
        else 0.0
    )

    if markdown_usable_count:
        positives.append(
            f"{markdown_usable_count} of {len(checked_responses)} checked Markdown alternate(s) "
            "returned usable Markdown or text."
        )
        if best_markdown_token_coverage_ratio >= 0.8:
            positives.append(
                "The best usable Markdown alternate overlaps at least 80% of bounded main-content tokens."
            )
        elif best_markdown_token_coverage_ratio < 0.5:
            issues.append(
                "The best usable Markdown alternate overlaps less than half of bounded main-content tokens."
            )
    elif checked_responses:
        issues.append("No checked Markdown alternate returned usable Markdown or text.")
    else:
        caveats.append("No already-fetched Markdown alternates were supplied for analysis.")

    llms_txt_supplied = profile_input.llms_txt is not None
    llms_txt_present = bool(profile_input.llms_txt and profile_input.llms_txt.strip())
    llms_txt_valid = False
    if profile_input.llms_txt is None:
        caveats.append("llms.txt was not supplied for analysis.")
    elif not llms_txt_present:
        issues.append("The supplied llms.txt response was empty.")
    else:
        llms_txt, llms_truncated = _cap_text(profile_input.llms_txt, _MAX_LLMS_TXT_BYTES)
        if llms_truncated:
            caveats.append("llms.txt exceeded 512 KiB and was truncated before parsing.")
        llms_txt_valid, llms_issues = _analyse_llms_txt(llms_txt)
        if llms_txt_valid:
            positives.append("The supplied llms.txt content is present and valid.")
        else:
            issues.append("The supplied llms.txt content is invalid: " + "; ".join(llms_issues) + ".")

    sitemap_xml_supplied = profile_input.sitemap_xml is not None
    sitemap_xml_present = bool(profile_input.sitemap_xml and profile_input.sitemap_xml.strip())
    sitemap_valid = False
    sitemap_url_count = 0
    sitemap_has_lastmod = False
    sitemap_is_index = False
    if profile_input.sitemap_xml is None:
        caveats.append("Sitemap XML was not supplied for analysis.")
    elif not sitemap_xml_present:
        issues.append("The supplied sitemap XML response was empty.")
    else:
        sitemap_xml, sitemap_truncated = _cap_text(
            profile_input.sitemap_xml, _MAX_SITEMAP_BYTES
        )
        if sitemap_truncated:
            caveats.append("Sitemap XML exceeded 1 MiB and was truncated before parsing.")
        parsed_sitemap = parse_sitemap(sitemap_xml)
        sitemap_valid = parsed_sitemap.is_valid
        sitemap_url_count = parsed_sitemap.loc_count
        sitemap_has_lastmod = parsed_sitemap.has_lastmod
        sitemap_is_index = parsed_sitemap.is_index
        if sitemap_valid:
            positives.append(
                f"The supplied sitemap is valid and exposes {sitemap_url_count} URL location(s)."
            )
        else:
            issues.append("The supplied sitemap XML is malformed or has no URL locations.")

    schema_soup = BeautifulSoup(raw_html, "lxml")
    (
        schema_types,
        typed_nodes,
        invalid_schema_count,
        schema_traversal_truncated,
        schema_types_ignored,
    ) = _extract_json_ld_nodes(schema_soup)
    product_schema_present = "Product" in schema_types
    offer_schema_present = "Offer" in schema_types
    if schema_types:
        positives.append(f"JSON-LD exposes {len(schema_types)} distinct schema type(s).")
    elif invalid_schema_count:
        issues.append("JSON-LD script blocks were present but could not be parsed.")
    else:
        issues.append("No typed JSON-LD schema was found in raw HTML.")
    if invalid_schema_count:
        issues.append(f"{invalid_schema_count} JSON-LD script block(s) were invalid.")
    if schema_traversal_truncated:
        caveats.append(
            "JSON-LD traversal reached a script, depth, node, or schema-type safety cap; excess data was ignored."
        )
    if schema_types_ignored:
        caveats.append(
            f"{schema_types_ignored} invalid or excess JSON-LD schema type/script value(s) were ignored."
        )

    commerce_missing_fields: tuple[str, ...] = ()
    commerce_visible_fields: tuple[str, ...] = ()
    commerce_visible_missing_fields: tuple[str, ...] = ()
    commerce_schema_visible_mismatches: tuple[str, ...] = ()
    if profile_input.include_ecommerce:
        commerce_missing_fields, schema_commerce = _commerce_schema_evidence(typed_nodes)
        visible_commerce = _visible_commerce_evidence(visible_soup)
        commerce_visible_fields = tuple(
            field for field in _COMMERCE_FIELD_ORDER if visible_commerce.get(field)
        )
        commerce_visible_missing_fields = tuple(
            field
            for field in _COMMERCE_FIELD_ORDER
            if field != "variants" and not visible_commerce.get(field)
        )
        commerce_schema_visible_mismatches = _commerce_mismatches(
            visible_commerce, schema_commerce
        )
        if commerce_missing_fields:
            issues.append(
                "Commerce schema is missing extraction affordances: "
                + ", ".join(commerce_missing_fields)
                + "."
            )
        else:
            positives.append("Commerce Product and Offer schema expose the checked extraction fields.")
        if commerce_visible_missing_fields:
            issues.append(
                "Visible commerce extraction affordances are missing: "
                + ", ".join(commerce_visible_missing_fields)
                + "."
            )
        else:
            positives.append("Visible product content exposes all checked commerce affordances.")
        if commerce_schema_visible_mismatches:
            issues.append(
                "Visible commerce content and JSON-LD differ for: "
                + ", ".join(commerce_schema_visible_mismatches)
                + "."
            )

    return ExtractionReadinessAnalysis(
        raw_html_bytes=raw_html_bytes,
        raw_html_truncated=raw_html_truncated,
        hidden_node_count=hidden_node_count,
        cleaned_text_bytes=_byte_len(cleaned_text),
        cleaned_text_words=efficiency.visible_words,
        main_content_words=efficiency.main_content_words,
        boilerplate_ratio=efficiency.boilerplate_ratio,
        navigation_ratio=efficiency.navigation_ratio,
        script_style_ratio=efficiency.script_style_ratio,
        html_to_text_ratio=efficiency.html_to_text_ratio,
        rendering_state=rendering_state,
        app_shell_signals_present=app_shell_signals_present,
        app_shell_risk=app_shell_risk,
        markdown_alternates_supplied=len(markdown_responses),
        markdown_alternates_checked=len(checked_responses),
        markdown_usable_count=markdown_usable_count,
        best_markdown_bytes=best_markdown_bytes,
        best_markdown_words=best_markdown_words,
        best_markdown_token_coverage_ratio=best_markdown_token_coverage_ratio,
        best_markdown_word_delta=best_markdown_word_delta,
        best_markdown_to_raw_byte_ratio=best_markdown_to_raw_byte_ratio,
        best_markdown_to_main_word_ratio=best_markdown_to_main_word_ratio,
        llms_txt_supplied=llms_txt_supplied,
        llms_txt_present=llms_txt_present,
        llms_txt_valid=llms_txt_valid,
        sitemap_xml_supplied=sitemap_xml_supplied,
        sitemap_xml_present=sitemap_xml_present,
        sitemap_valid=sitemap_valid,
        sitemap_url_count=sitemap_url_count,
        sitemap_has_lastmod=sitemap_has_lastmod,
        sitemap_is_index=sitemap_is_index,
        schema_types=schema_types,
        invalid_schema_count=invalid_schema_count,
        schema_traversal_truncated=schema_traversal_truncated,
        schema_types_ignored=schema_types_ignored,
        product_schema_present=product_schema_present,
        offer_schema_present=offer_schema_present,
        commerce_missing_fields=commerce_missing_fields,
        commerce_visible_fields=commerce_visible_fields,
        commerce_visible_missing_fields=commerce_visible_missing_fields,
        commerce_schema_visible_mismatches=commerce_schema_visible_mismatches,
        issues=tuple(issues),
        positives=tuple(positives),
        caveats=tuple(caveats),
    )

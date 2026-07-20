from bs4 import BeautifulSoup

from app.audit_context import AuditContext
from app.models import CheckResult

_SEMANTIC_TAGS = {"header", "nav", "main", "article", "section", "footer", "aside"}
_AMBIGUOUS_LINK_TEXT = {"", "here", "click here", "more", "learn more", "read more"}
_NON_FIELD_INPUT_TYPES = {"hidden", "submit", "button", "reset", "image"}
_GENERIC_FIELD_NAMES = {"", "field", "input", "value", "data", "form", "formfield", "form_field"}
_VALID_FORM_METHODS = {"get", "post", "dialog"}
_AUTOCOMPLETE_PURPOSE_HINTS = {
    "address",
    "city",
    "company",
    "country",
    "email",
    "family-name",
    "first-name",
    "given-name",
    "last-name",
    "name",
    "organization",
    "phone",
    "postal",
    "tel",
    "username",
    "zip",
}

_TEMPLATES = {
    "pass": (
        "Page has clear headings, semantic landmarks, useful metadata, and basic "
        "agent navigation affordances.",
        "No action needed. Your page structure is well-formed for LLM extraction.",
    ),
    "partial": (
        "Page structure has {issues}. These gaps reduce how accurately LLMs and "
        "agents can extract and navigate your content.",
        "Fix the flagged issues. Semantic HTML, clear link text, labelled forms, "
        "explicit form actions, and useful alt text are fast improvements for "
        "AI readability.",
    ),
    "fail": (
        "Page is missing critical structural elements: {issues}. Agents may "
        "struggle to identify the primary content or navigation path.",
        "Add a single descriptive H1, organise headings in H1-H2-H3 order, use "
        "semantic tags, label form fields, and replace vague link text.",
    ),
    "fetch_error": (
        "Could not fetch homepage HTML for structural analysis.",
        "Ensure the homepage is publicly accessible and returns a 200 response.",
    ),
}


def _has_accessible_name(tag) -> bool:
    return bool(
        tag.get_text(strip=True)
        or tag.get("aria-label")
        or tag.get("title")
        or tag.find("img", alt=True)
    )


def _control_has_accessible_name(tag) -> bool:
    if tag.name == "input":
        return bool(
            tag.get("value", "").strip()
            or tag.get("aria-label")
            or tag.get("title")
            or tag.get("alt")
        )
    return _has_accessible_name(tag)


def _form_field_has_label(field, soup: BeautifulSoup) -> bool:
    field_id = field.get("id")
    if field_id and soup.find("label", attrs={"for": field_id}):
        return True
    if field.find_parent("label"):
        return True
    return bool(field.get("aria-label") or field.get("aria-labelledby") or field.get("placeholder"))


def _is_form_field(tag) -> bool:
    return tag.name in {"select", "textarea"} or (
        tag.name == "input" and tag.get("type", "text").lower() not in _NON_FIELD_INPUT_TYPES
    )


def _field_name_is_useful(field) -> bool:
    name = field.get("name", "").strip().lower()
    if name in _GENERIC_FIELD_NAMES:
        return False
    return bool(name)


def _field_needs_autocomplete(field) -> bool:
    if field.name != "input":
        return False
    field_type = field.get("type", "text").lower()
    if field_type in {"email", "tel", "url", "password"}:
        return True
    haystack = " ".join(
        str(value).lower()
        for value in (
            field.get("name", ""),
            field.get("id", ""),
            field.get("aria-label", ""),
            field.get("placeholder", ""),
        )
    )
    return any(hint in haystack for hint in _AUTOCOMPLETE_PURPOSE_HINTS)


def _field_has_autocomplete(field) -> bool:
    value = field.get("autocomplete", "").strip().lower()
    return bool(value and value != "off")


def _form_action_is_explicit(action: str | None) -> bool:
    return action is not None and bool(action.strip())


def _form_action_is_non_js(action: str | None) -> bool:
    if not _form_action_is_explicit(action):
        return False
    value = (action or "").strip().lower()
    return not value.startswith(("javascript:", "#"))


def _form_method_is_valid(method: str | None) -> bool:
    return bool(method and method.strip().lower() in _VALID_FORM_METHODS)


def _form_submit_controls(form) -> list:
    controls = []
    for control in form.find_all(["button", "input"]):
        control_type = control.get("type", "submit" if control.name == "button" else "text").lower()
        if control.name == "button" and control_type in {"", "submit"}:
            controls.append(control)
        elif control.name == "input" and control_type in {"submit", "image"}:
            controls.append(control)
    return controls


def _form_has_named_submit_control(form) -> bool:
    controls = _form_submit_controls(form)
    return bool(controls and any(_control_has_accessible_name(control) for control in controls))


def _form_has_non_js_fallback(form) -> bool:
    return _form_action_is_non_js(form.get("action")) and _form_method_is_valid(form.get("method"))


def _form_affordance_issues(soup: BeautifulSoup) -> list[str]:
    forms = soup.find_all("form")
    if not forms:
        return []

    missing_action = [form for form in forms if not _form_action_is_explicit(form.get("action"))]
    js_only_action = [
        form
        for form in forms
        if _form_action_is_explicit(form.get("action"))
        and not _form_action_is_non_js(form.get("action"))
    ]
    missing_method = [form for form in forms if not form.get("method")]
    unsupported_method = [
        form
        for form in forms
        if form.get("method") and not _form_method_is_valid(form.get("method"))
    ]
    fields = [
        field
        for form in forms
        for field in form.find_all(["input", "select", "textarea"])
        if _is_form_field(field)
    ]
    unnamed_fields = [field for field in fields if not _field_name_is_useful(field)]
    autocomplete_fields = [field for field in fields if _field_needs_autocomplete(field)]
    missing_autocomplete = [field for field in autocomplete_fields if not _field_has_autocomplete(field)]
    unnamed_submit_forms = [form for form in forms if not _form_has_named_submit_control(form)]
    fallback_forms = [form for form in forms if _form_has_non_js_fallback(form)]

    issues: list[str] = []
    if missing_action:
        issues.append(f"{len(missing_action)} form(s) lack explicit action URLs")
    if js_only_action:
        issues.append(f"{len(js_only_action)} form(s) use JavaScript-only actions")
    if missing_method:
        issues.append(f"{len(missing_method)} form(s) lack explicit methods")
    if unsupported_method:
        issues.append(f"{len(unsupported_method)} form(s) use unsupported methods")
    if unnamed_fields:
        issues.append(f"{len(unnamed_fields)} form field(s) lack useful name attributes")
    if missing_autocomplete:
        issues.append(f"{len(missing_autocomplete)} form field(s) lack autocomplete hints")
    if unnamed_submit_forms:
        issues.append(f"{len(unnamed_submit_forms)} form(s) lack named submit controls")
    if forms and not fallback_forms:
        issues.append("no form exposes an explicit non-JS action/method fallback")
    return issues


def _analyse(soup: BeautifulSoup) -> tuple[int, list[str]]:
    issues: list[str] = []

    h1_tags = soup.find_all("h1")
    if len(h1_tags) == 0:
        issues.append("no H1 tag")
    elif len(h1_tags) > 1:
        issues.append(f"{len(h1_tags)} H1 tags")

    h2_tags = soup.find_all("h2")
    h3_tags = soup.find_all("h3")
    if h3_tags and not h2_tags:
        issues.append("H3 headings without any H2")

    semantic_present = {tag for tag in _SEMANTIC_TAGS if soup.find(tag)}
    if len(semantic_present) < 3:
        missing = _SEMANTIC_TAGS - semantic_present
        issues.append(f"missing semantic tags: {', '.join(sorted(missing)[:3])}")

    title_tag = soup.find("title")
    if not title_tag or not title_tag.get_text(strip=True):
        issues.append("missing page title")
    elif len(title_tag.get_text(strip=True)) > 70:
        issues.append("title is unusually long")

    meta_desc = soup.find("meta", attrs={"name": "description"})
    if not meta_desc or not meta_desc.get("content", "").strip():
        issues.append("missing meta description")
    elif len(meta_desc.get("content", "")) > 180:
        issues.append("meta description is unusually long")

    links = soup.find_all("a", href=True)
    ambiguous_links = [
        link
        for link in links
        if not _has_accessible_name(link)
        or link.get_text(" ", strip=True).lower() in _AMBIGUOUS_LINK_TEXT
    ]
    if len(links) >= 8 and len(ambiguous_links) / len(links) > 0.25:
        issues.append("many links have vague or missing text")
    non_crawlable_links = [
        link
        for link in links
        if link.get("href", "").strip().lower().startswith(("javascript:", "#"))
    ]
    if len(links) >= 8 and len(non_crawlable_links) / len(links) > 0.25:
        issues.append("many navigation links are not crawlable URLs")

    fields = [
        field
        for field in soup.find_all(["input", "select", "textarea"])
        if _is_form_field(field)
    ]
    unlabeled_fields = [field for field in fields if not _form_field_has_label(field, soup)]
    if unlabeled_fields:
        issues.append(f"{len(unlabeled_fields)} form field(s) lack labels")
    issues.extend(_form_affordance_issues(soup))

    images = soup.find_all("img")
    missing_alt = [image for image in images if image.get("alt") is None]
    if len(images) >= 3 and len(missing_alt) / len(images) > 0.5:
        issues.append("most images lack alt text")

    interactive = soup.find_all(["button", "summary"])
    unnamed_interactive = [tag for tag in interactive if not _control_has_accessible_name(tag)]
    if unnamed_interactive:
        issues.append(f"{len(unnamed_interactive)} interactive control(s) lack accessible names")

    score = max(4 - len(issues), 1) if issues else 4
    return score, issues


async def check_html_structure(context: AuditContext) -> CheckResult:
    """Check semantic HTML and static agent navigation affordances."""
    if not context.homepage.ok:
        finding, fix = _TEMPLATES["fetch_error"]
        return CheckResult(
            pillar="scrapability",
            check_name="html_structure",
            label="Semantic HTML & Agent Navigation",
            state="warn",
            evidence_level="unknown",
            score=0,
            max_score=4,
            finding=finding,
            fix=fix,
            effort="medium",
        )

    soup = BeautifulSoup(context.homepage.text, "lxml")

    score, issues = _analyse(soup)

    if not issues:
        state = "pass"
        finding, fix = _TEMPLATES["pass"]
    elif score >= 2:
        state = "partial"
        finding, fix = _TEMPLATES["partial"]
        finding = finding.format(issues="; ".join(issues))
    else:
        state = "fail"
        finding, fix = _TEMPLATES["fail"]
        finding = finding.format(issues="; ".join(issues))

    return CheckResult(
        pillar="scrapability",
        check_name="html_structure",
        label="Semantic HTML & Agent Navigation",
        state=state,
        score=score,
        max_score=4,
        finding=finding,
        fix=fix,
        effort="medium",
    )

import asyncio
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from app.audit_context import AuditContext
from app.crawler_registry import active_crawler_caveats, fetch_probe_names
from app.fetching import BOT_USER_AGENTS, BROWSER_USER_AGENT, FetchResult, fetch_url
from app.models import CheckResult

_MATRIX_BOTS = fetch_probe_names()

_BLOCK_SIGNATURES = (
    "just a moment",
    "checking your browser",
    "cf-chl",
    "cloudflare ray id",
    "verify you are human",
    "access denied",
    "datadome",
    "perimeterx",
    "akamai bot manager",
)
# Substrings that indicate a generic anti-bot challenge page. The classic
# ``captcha`` substring is intentionally NOT in this list: contact forms and
# many legitimate pages embed Google reCAPTCHA ``<script src="…/recaptcha/api.js">``
# tags, which would otherwise produce a false-positive challenge page for every
# bot probe. CAPTCHA-style pages are matched separately against the page
# ``<title>`` only — see :func:`_has_captcha_title`.
_BODY_SCAN_LENGTH = 20000

# Stagger between bot probes so a multi-user-agent wave does not look like a
# coordinated attack to upstream WAFs. The previous behaviour fired all 9
# crawlers simultaneously via ``asyncio.gather``; one IP hitting the homepage
# with 9 different user agents at once reads as a brute-force scan and the
# WAF's rate-limit response is exactly the friction the probes are trying to
# measure. A paced sequence (0.5s gap between each, ~4.5s total for 9 probes)
# looks like ordinary bot traffic instead.
_STAGGER_DELAY = 0.5      # seconds between bot probes
# When a probe gets HTTP 429, wait this long before the single retry. The
# ``Retry-After`` header is the polite thing to honour, but we do not parse it
# here — a fixed delay keeps the check deterministic and easy to test.
_RETRY_DELAY = 2.0         # seconds before 429 retry


def _title_text(html: str) -> str:
    """Extract just the text content of the first ``<title>`` element.

    Returns an empty string if the document has no title or if parsing fails.
    Used to confine ``captcha`` detection to the visible page title instead of
    the full HTML body, so third-party script references do not trigger
    false positives.
    """
    if not html:
        return ""
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        return ""
    title_tag = soup.find("title")
    if title_tag is None:
        return ""
    return title_tag.get_text(separator=" ", strip=True)


def _has_captcha_title(fetch: FetchResult) -> bool:
    """Return True only when the page ``<title>`` mentions CAPTCHA.

    Real CAPTCHA challenge pages always announce themselves in the title bar
    ("reCAPTCHA", "Security check", "Are you a robot?", etc.). A reCAPTCHA
    ``<script>`` tag embedded in a normal page does NOT affect the title, so
    the body+header substring scan is not appropriate for ``captcha``.
    """
    title = _title_text(fetch.text).lower()
    if not title:
        return False
    return "captcha" in title


@dataclass(frozen=True)
class RoutingSnapshot:
    final_url: str
    status_code: int | None
    canonical_url: str | None
    content_bytes: int
    visible_words: int


def _visible_word_count(html: str) -> int:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "head", "noscript"]):
        tag.decompose()
    return len(soup.get_text(separator=" ").split())


def _normalised_url(url: str) -> str:
    parsed = urlparse(urljoin(url, ""))
    return parsed._replace(fragment="", query="").geturl().rstrip("/")


def _display_url(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.netloc:
        return url[:120]
    display = parsed.netloc + parsed.path.rstrip("/")
    if parsed.query:
        display += "?" + parsed.query
    return display[:120]


def _canonical_url(fetch: FetchResult) -> str | None:
    if not fetch.text:
        return None
    soup = BeautifulSoup(fetch.text, "lxml")
    tag = soup.find("link", attrs={"rel": "canonical"})
    if not tag:
        return None
    href = tag.get("href", "")
    if not href:
        return None
    return _normalised_url(urljoin(fetch.final_url, href))


def _routing_snapshot(fetch: FetchResult) -> RoutingSnapshot:
    return RoutingSnapshot(
        final_url=_normalised_url(fetch.final_url),
        status_code=fetch.status_code,
        canonical_url=_canonical_url(fetch),
        content_bytes=len(fetch.text.encode("utf-8")),
        visible_words=_visible_word_count(fetch.text),
    )


def _has_block_signature(fetch: FetchResult) -> bool:
    """Return True if the response body or headers look like an anti-bot page.

    Substring signatures in :data:`_BLOCK_SIGNATURES` are scanned against the
    first ``_BODY_SCAN_LENGTH`` characters of the body plus the response header
    values. ``captcha`` is intentionally NOT in that list — see
    :func:`_has_captcha_title` for the title-only CAPTCHA detection path.
    """
    sample = (fetch.text[:_BODY_SCAN_LENGTH] + " " + " ".join(fetch.headers.values())).lower()
    return any(signature in sample for signature in _BLOCK_SIGNATURES)


def _looks_like_challenge(
    fetch: FetchResult,
    *,
    baseline: FetchResult | None = None,
) -> bool:
    """Return True if ``fetch`` looks like a challenge page for bots.

    A response is treated as a challenge when **either**:

    * The body / header substring scan in :func:`_has_block_signature` matches
      a known anti-bot fingerprint; **or**
    * The page ``<title>`` mentions CAPTCHA.

    When ``baseline`` is provided and the baseline (browser) probe ALSO matches
    on the same fingerprints, the finding is suppressed: the site serves the
    same fingerprint to real browsers, so it is a site-wide pattern rather than
    bot discrimination. The baseline comparison applies to the title-only
    CAPTCHA check as well, because a site that literally puts ``captcha`` in
    its title is not selectively blocking bots either.
    """
    body_match = _has_block_signature(fetch)
    title_match = _has_captcha_title(fetch)
    if not (body_match or title_match):
        return False
    if baseline is None:
        return True
    baseline_body = _has_block_signature(baseline)
    baseline_title = _has_captcha_title(baseline)
    # If the browser baseline carries the same challenge fingerprints, the
    # site-wide response is the same for everyone — not a bot-only finding.
    if body_match and baseline_body:
        return False
    if title_match and baseline_title:
        return False
    return True


def _blocked_reason(
    fetch: FetchResult,
    baseline_words: int,
    *,
    baseline: FetchResult | None = None,
) -> str | None:
    if fetch.error:
        return "fetch error"
    if fetch.status_code in {401, 403, 429, 503}:
        return f"HTTP {fetch.status_code}"
    if _looks_like_challenge(fetch, baseline=baseline):
        return "challenge page"

    words = _visible_word_count(fetch.text)
    if baseline_words >= 80 and words < max(20, baseline_words * 0.25):
        return f"thin response ({words} words vs {baseline_words} browser words)"
    return None


def _probe_caveat() -> str:
    return " ".join(active_crawler_caveats("fetch"))


def _format_bytes(byte_count: int) -> str:
    if byte_count >= 1024 * 1024:
        return f"{byte_count / (1024 * 1024):.1f} MB"
    if byte_count >= 1024:
        return f"{byte_count / 1024:.1f} KB"
    return f"{byte_count} B"


def _large_ratio_difference(candidate: int, baseline: int, *, minimum_baseline: int) -> bool:
    if baseline < minimum_baseline:
        return False
    return candidate < baseline * 0.6 or candidate > baseline * 1.6


def _routing_differences(
    bot_name: str,
    fetch: FetchResult,
    baseline: RoutingSnapshot,
) -> list[str]:
    snapshot = _routing_snapshot(fetch)
    differences: list[str] = []
    if snapshot.status_code != baseline.status_code:
        differences.append(
            f"status {snapshot.status_code or 'error'} vs browser {baseline.status_code or 'error'}"
        )

    if snapshot.final_url != baseline.final_url:
        differences.append(
            f"final URL {_display_url(snapshot.final_url)} vs browser {_display_url(baseline.final_url)}"
        )

    if snapshot.canonical_url != baseline.canonical_url:
        if snapshot.canonical_url and baseline.canonical_url:
            differences.append(
                "canonical "
                f"{_display_url(snapshot.canonical_url)} vs browser {_display_url(baseline.canonical_url)}"
            )
        elif baseline.canonical_url:
            differences.append("canonical missing vs browser canonical")
        elif snapshot.canonical_url:
            differences.append("canonical present only for bot response")

    if _large_ratio_difference(snapshot.content_bytes, baseline.content_bytes, minimum_baseline=1024):
        differences.append(
            "body size "
            f"{_format_bytes(snapshot.content_bytes)} vs browser {_format_bytes(baseline.content_bytes)}"
        )

    if _large_ratio_difference(snapshot.visible_words, baseline.visible_words, minimum_baseline=80):
        differences.append(
            f"visible words {snapshot.visible_words} vs browser {baseline.visible_words}"
        )

    return [f"{bot_name} ({'; '.join(differences)})"] if differences else []


def _routing_difference_detail(differences: list[str]) -> str:
    if not differences:
        return " No material browser-vs-bot routing differences were inferred from final URL, status, canonical, body size, or visible word count."
    shown = differences[:5]
    hidden_count = len(differences) - len(shown)
    detail = " Inferred browser-vs-bot routing differences: " + "; ".join(shown)
    if hidden_count:
        detail += f"; {hidden_count} more"
    return detail + "."


async def _probe_one_bot(url: str, user_agent: str) -> FetchResult:
    """Fetch ``url`` as a single bot and retry HTTP 429 once after a delay.

    The single retry uses the second response for scoring — the 429 itself is
    discarded. If the retry also 429s (or returns any other blocking status),
    that retry result is returned as-is so :func:`_blocked_reason` can score
    it normally. ``asyncio.sleep`` is used between the initial probe and the
    retry so the WAF gets real wall-clock breathing room.
    """
    fetch = await fetch_url(url, user_agent=user_agent)
    if fetch.status_code == 429 and fetch.error is None:
        await asyncio.sleep(_RETRY_DELAY)
        retry_fetch = await fetch_url(url, user_agent=user_agent)
        # Use the retry result for scoring regardless of its status. If the
        # retry is still 429, _blocked_reason will count it as blocked.
        return retry_fetch
    return fetch


async def _probe_bots(
    url: str,
    bot_user_agents: dict[str, str],
    matrix_bots: list[str],
    *,
    baseline_fetch: FetchResult,
    baseline_snapshot: RoutingSnapshot,
    baseline_words: int,
) -> tuple[list[str], list[str], int]:
    """Run the bot-access matrix probes serially with a stagger between each.

    Replaces the previous ``asyncio.gather`` burst that fired all probes
    simultaneously — see :data:`_STAGGER_DELAY` for the rationale.

    Each probe gets a single 429 retry (see :func:`_probe_one_bot`). The
    stagger applies only between successive initial probes; the retry sleep
    happens inside the probe itself when a 429 is observed and does not push
    the next bot's start time further out.

    Returns ``(blocked, routing_differences, accessible_count)`` — the same
    three values that :func:`check_bot_access` previously derived inline, so
    the scoring / state logic in the caller stays identical.
    """
    blocked: list[str] = []
    routing_differences: list[str] = []
    accessible_count = 0
    for index, bot_name in enumerate(matrix_bots):
        if index > 0:
            # Paced sequence instead of a simultaneous burst. The browser
            # baseline fetch above is already sequential, so this is the only
            # place the wave-pattern fix applies.
            await asyncio.sleep(_STAGGER_DELAY)
        fetch = await _probe_one_bot(url, bot_user_agents[bot_name])
        reason = _blocked_reason(fetch, baseline_words, baseline=baseline_fetch)
        if reason:
            blocked.append(f"{bot_name} ({reason})")
        else:
            accessible_count += 1
        routing_differences.extend(_routing_differences(bot_name, fetch, baseline_snapshot))
    return blocked, routing_differences, accessible_count


async def check_bot_access(context: AuditContext) -> CheckResult:
    """Compare access for common search and AI crawler user agents."""
    browser_fetch = await fetch_url(context.url, user_agent=BROWSER_USER_AGENT)
    baseline_fetch = browser_fetch if browser_fetch.ok else context.homepage
    baseline_snapshot = _routing_snapshot(baseline_fetch)
    baseline_words = baseline_snapshot.visible_words

    blocked, routing_differences, accessible_count = await _probe_bots(
        context.url,
        BOT_USER_AGENTS,
        _MATRIX_BOTS,
        baseline_fetch=baseline_fetch,
        baseline_snapshot=baseline_snapshot,
        baseline_words=baseline_words,
    )
    score = round(accessible_count / len(_MATRIX_BOTS) * 6)

    if not blocked:
        state = "pass"
        finding = (
            "Tracked search and AI crawler user-agent requests from MachineRead received "
            "accessible homepage responses comparable to a normal browser request."
        )
        fix = "No action needed. Keep bot traffic rate-limited rather than hard-blocked."
    elif accessible_count:
        state = "partial"
        finding = (
            "Some tracked bot user-agent requests from MachineRead appear blocked or challenged: "
            + "; ".join(blocked)
            + ". This is strong evidence of friction for generic agents, but actual "
            "verified search crawlers may be treated differently by IP verification rules."
        )
        fix = (
            "Review WAF and bot rules for the listed agents. Prefer explicit allows, "
            "sensible rate limits, and Retry-After responses over CAPTCHA or hard blocks."
        )
    else:
        state = "fail"
        finding = (
            "All tracked bot user-agent requests from MachineRead appear blocked or challenged. "
            "This indicates high friction for generic agents, but it is not proof that "
            "Googlebot or Bingbot from verified crawler IPs are blocked."
        )
        fix = (
            "Create an allowlisted path for legitimate crawlers and agents, then rate-limit "
            "abuse without hiding the public content."
        )

    finding += _routing_difference_detail(routing_differences)
    finding += " Probe caveat: " + _probe_caveat()

    return CheckResult(
        pillar="scrapability",
        check_name="bot_access",
        label="MachineRead Bot Fetch Access",
        state=state,
        evidence_level="inferred",
        score=score,
        max_score=6,
        finding=finding,
        fix=fix,
        effort="medium",
    )

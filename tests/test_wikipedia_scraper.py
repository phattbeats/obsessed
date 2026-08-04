"""PHA-1558 regression: Wikipedia scraper must strip sidebar/nav boilerplate.

The crawl4ai HTML fallback extracts markdown from the full Wikipedia page,
including the left-hand sidebar navigation list. Article body text is
preserved; chrome lines are dropped before the text is fed to question
generation.
"""
import pytest

from app.services.scraper.wikipedia import (
    _clean_wikipedia_output,
    _is_wikipedia_nav_line,
    _scrape_via_html,
)


# Realistic crawl4ai output as the bug repro sees it (PHA-1558).
SAMPLE_NAVY_OUTPUT = """move to sidebar hide

* [Main page](https://en.wikipedia.org/wiki/Main_page "Visit the main page [alt-z]")
* [Contents](https://en.wikipedia.org/wiki/Wikipedia:Contents "Guides to browsing Wikipedia")
* [Current events](https://en.wikipedia.org/wiki/Portal:Current_events "Articles related to current events")
* [Random article](https://en.wikipedia.org/wiki/Special:Random "Visit a randomly selected article [alt-z]")
* [About Wikipedia](https://en.wikipedia.org/wiki/Wikipedia:About "Learn about Wikipedia and how it is edited")
* [Contact Wikipedia](https://en.wikipedia.org/wiki/Wikipedia:Contact_us "How to contact Wikipedia")
* [Donate](https://donate.wikimedia.org/wiki/Special:FundraiserRedirector "Support Wikipedia")

## History

Paris was founded in the 3rd century BC by a Celtic tribe called the Parisii.
The city grew into a major center of European culture and commerce.

## Geography

Paris is located in northern France, on the river Seine.
The city covers an area of 105 square kilometers.
"""


def test_clean_wikipedia_output_strips_sidebar_nav():
    """Sidebar listing must be removed; article body preserved."""
    cleaned = _clean_wikipedia_output(SAMPLE_NAVY_OUTPUT)

    # Every sidebar nav line must be gone.
    for nav_text in [
        "move to sidebar hide",
        "Main page",
        "Contents",
        "Current events",
        "Random article",
        "About Wikipedia",
        "Contact Wikipedia",
        "Donate",
    ]:
        assert nav_text not in cleaned, f"sidebar item leaked: {nav_text!r}"

    # Article body lines must survive.
    assert "Paris was founded in the 3rd century BC" in cleaned
    assert "Paris is located in northern France" in cleaned
    assert "## History" in cleaned
    assert "## Geography" in cleaned


def test_clean_wikipedia_output_empty_input():
    assert _clean_wikipedia_output("") == ""
    assert _clean_wikipedia_output(None) == ""  # type: ignore[arg-type]


def test_clean_wikipedia_output_preserves_body_with_inline_links():
    """Inline links inside article body must NOT be removed."""
    body = (
        "Paris is the capital of [France](https://en.wikipedia.org/wiki/France).\n"
        "The Eiffel Tower is a landmark in Paris."
    )
    cleaned = _clean_wikipedia_output(body)
    assert "[France](https://en.wikipedia.org/wiki/France)" in cleaned
    assert "Eiffel Tower" in cleaned


def test_is_wikipedia_nav_line_detects_patterns():
    assert _is_wikipedia_nav_line('move to sidebar hide')
    assert _is_wikipedia_nav_line('* [Main page](https://en.wikipedia.org/wiki/Main_page "Visit the main page")')
    assert _is_wikipedia_nav_line('* [Contents](https://en.wikipedia.org/wiki/Wikipedia:Contents)')
    assert _is_wikipedia_nav_line('   * [Random article](https://en.wikipedia.org/wiki/Special:Random)')
    # Non-nav lines must NOT be flagged.
    assert not _is_wikipedia_nav_line('Paris was founded in the 3rd century BC.')
    assert not _is_wikipedia_nav_line('The city covers an area of 105 square kilometers.')
    # Empty / whitespace-only lines are dropped (treated as nav noise) —
    # this happens before the regex check returns False.
    assert _is_wikipedia_nav_line('')
    assert _is_wikipedia_nav_line('   ')


@pytest.mark.asyncio
async def test_scrape_via_html_applies_cleaner(monkeypatch):
    """The HTML fallback path must invoke the cleaner before returning."""
    captured = {}

    async def fake_crawl(url):
        captured["url"] = url
        return SAMPLE_NAVY_OUTPUT, {"title": "Paris"}

    # The wikipedia module imports crawl4ai_scrape lazily inside _scrape_via_html.
    from app.services.scraper import crawl4ai as crawl4ai_mod

    monkeypatch.setattr(crawl4ai_mod, "crawl4ai_scrape", fake_crawl)

    text, meta = await _scrape_via_html("Paris")

    assert "Paris was founded in the 3rd century BC" in text
    assert "move to sidebar hide" not in text
    assert "Main page" not in text
    assert "[Contents]" not in text
    assert meta["title"] == "Paris"
    assert captured["url"].endswith("/Paris")


# ── PHA-1558 follow-up ──────────────────────────────────────────────────────
# The first fix was written against the lines pasted into the bug report. Live
# verification after the redeploy showed production still serving chrome as
# answers: the REST path was returning nothing (Wikimedia 403s generic
# User-Agents, and mobile-sections is retired), so every scrape fell through to
# the HTML path — where a second, different layer of chrome survived the
# filter. These tests pin both halves.

# Chrome captured from the live 10.0.0.100:10198 questions after the first fix.
SAMPLE_LIVE_CHROME = """[ ![](https://en.wikipedia.org/static/images/icons/enwiki-25.svg) ![Wikipedia](https://en.wikipedia.org/static/images/mobile/copyright/wikipedia-wordmark-en-25.svg) ](https://en.wikipedia.org/wiki/Main_Page)

[ Search ](https://en.wikipedia.org/wiki/Special:Search "Search Wikipedia [f]")
Toggle the table of contents
This page always uses small font size
[Edit links](https://www.wikidata.org/wiki/Special:EntityPage/Q90#sitelinks-wikipedia "Edit interlanguage links")
* [View source](https://en.wikipedia.org/w/index.php?title=Paris&action=edit "This page is protected.
You can view its source [e]")

Paris is the capital and largest city of France.
The city covers an area of 105 square kilometers.
"""


def test_clean_output_strips_live_header_and_tools_chrome():
    cleaned = _clean_wikipedia_output(SAMPLE_LIVE_CHROME)
    for chrome in [
        "static/images/icons/enwiki-25.svg",
        "Special:Search",
        "Toggle the table of contents",
        "This page always uses small font size",
        "Special:EntityPage",
        "action=edit",
    ]:
        assert chrome not in cleaned, f"chrome survived the filter: {chrome}"


def test_clean_output_drops_both_halves_of_a_wrapped_link():
    """A markdown link whose title wraps across lines must not leak either half."""
    cleaned = _clean_wikipedia_output(SAMPLE_LIVE_CHROME)
    assert "You can view its source" not in cleaned


def test_clean_output_keeps_article_body():
    cleaned = _clean_wikipedia_output(SAMPLE_LIVE_CHROME)
    assert "Paris is the capital and largest city of France." in cleaned
    assert "105 square kilometers" in cleaned


def test_wikipedia_client_sends_a_compliant_user_agent():
    """Wikimedia 403s python-httpx/* — a generic UA silently kills the REST path."""
    from app.services.scraper.wikipedia import USER_AGENT, _HEADERS

    assert _HEADERS["User-Agent"] == USER_AGENT
    assert "python-httpx" not in USER_AGENT
    assert "obsessed" in USER_AGENT.lower()
    assert "@" in USER_AGENT or "http" in USER_AGENT  # contact per Wikimedia policy


def test_split_extract_sections_keeps_facts_and_drops_references():
    from app.services.scraper.wikipedia import _split_extract_sections

    extract = (
        "Paris is the capital and largest city of France, on the river Seine.\n"
        "\n== Etymology ==\n"
        "The name comes from the Parisii, a Celtic tribe of the Iron Age.\n"
        "\n=== Middle Ages ===\n"
        "Paris became the largest city in the Western world by the 12th century.\n"
        "\n== References ==\n"
        "1. Smith, John. A History of Paris. 1999.\n"
        "\n== External links ==\n"
        "Official website of the City of Paris and its tourism board.\n"
    )
    sections = _split_extract_sections(extract)
    joined = "\n".join(sections)

    assert any("Parisii" in s for s in sections)
    assert any("Middle Ages" in s for s in sections)
    assert "Smith, John" not in joined
    assert "Official website" not in joined


@pytest.mark.asyncio
async def test_scrape_via_rest_uses_action_api_not_retired_mobile_sections(monkeypatch):
    """mobile-sections was retired and 403s for every title — never call it."""
    import httpx
    from app.services.scraper import wikipedia as wp

    called = []

    class FakeResponse:
        def __init__(self, payload):
            self.status_code = 200
            self._payload = payload

        def json(self):
            return self._payload

    class FakeClient:
        def __init__(self, *a, **kw):
            self.headers = kw.get("headers", {})

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None):
            called.append((url, params))
            if "summary" in url:
                return FakeResponse({"title": "Paris", "description": "Capital of France",
                                     "extract": "Paris is the capital of France."})
            return FakeResponse({"query": {"pages": {"1": {
                "extract": "Paris is the capital of France, on the river Seine.\n"
                           "\n== Etymology ==\n"
                           "Named for the Parisii, a Celtic tribe of the Iron Age.\n"}}}})

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)

    parts, meta = await wp._scrape_via_rest("Paris", "Paris")

    assert not any("mobile-sections" in url for url, _ in called)
    assert any(url == wp.WIKIPEDIA_ACTION_API for url, _ in called)
    assert parts, "Action API extract must produce article body sections"
    assert any("Parisii" in p for p in parts)

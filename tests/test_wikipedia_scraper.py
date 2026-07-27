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

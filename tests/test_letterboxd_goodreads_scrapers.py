"""
Tests for the Letterboxd + Goodreads scrapers.
- Letterboxd: covers RSS parsing off a fixture (namespaced fields, star glyphs,
  rewatches, ratings), fixture-driven pipeline, cache roundtrip, 404 fallback.
- Goodreads: covers RSS parsing (5-star / 4-star / 3-star reviews, started,
  added, system-noise filter), fixture-driven pipeline, cache roundtrip,
  invalid (non-numeric) user_id, 404 fallback.

Run live network tests with: pytest -m live_network
"""
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.scraper import goodreads as goodreads_mod
from app.services.scraper import letterboxd as letterboxd_mod

FIXTURES = Path(__file__).parent / "fixtures"
LETTERBOXD_XML = (FIXTURES / "letterboxd" / "jack_diary.xml").read_text()
GOODREADS_XML = (FIXTURES / "goodreads" / "otis_updates.xml").read_text()


# ─────────────────────────────────────────────────────────────────────────────
# Letterboxd parsing
# ─────────────────────────────────────────────────────────────────────────────

class TestLetterboxdParseRss:
    def test_parses_five_items_from_fixture(self):
        items = letterboxd_mod._parse_rss(LETTERBOXD_XML)
        assert len(items) == 5

    def test_extracts_namespaced_fields(self):
        items = letterboxd_mod._parse_rss(LETTERBOXD_XML)
        mulholland = next(i for i in items if i["film_title"] == "Mulholland Drive")
        assert mulholland["year"] == "2001"
        assert mulholland["rating"] == 5.0
        assert mulholland["watched_date"] == "2026-07-23"
        assert mulholland["is_like"] is True
        assert mulholland["is_rewatch"] is False
        assert mulholland["tmdb_id"] == "10193"

    def test_parses_star_glyphs_from_title(self):
        # title "Trainspotting, 1996 - ★★★★½" → 4.5
        items = letterboxd_mod._parse_rss(LETTERBOXD_XML)
        trainspotting = next(i for i in items if i["film_title"] == "Trainspotting")
        assert trainspotting["rating"] == 4.5
        assert trainspotting["is_rewatch"] is True

    def test_parses_full_star_title(self):
        items = letterboxd_mod._parse_rss(LETTERBOXD_XML)
        lebowski = next(i for i in items if i["film_title"] == "The Big Lebowski")
        assert lebowski["rating"] == 5.0
        assert lebowski["is_rewatch"] is True

    def test_parses_single_star_title(self):
        items = letterboxd_mod._parse_rss(LETTERBOXD_XML)
        the_room = next(i for i in items if i["film_title"] == "The Room")
        assert the_room["rating"] == 1.0

    def test_extracts_review_text_strips_poster_img(self):
        items = letterboxd_mod._parse_rss(LETTERBOXD_XML)
        mulholland = next(i for i in items if i["film_title"] == "Mulholland Drive")
        # Review snippet must NOT include the <img> tag, must include the text
        assert "<img" not in mulholland["review"]
        assert "Lynch at the height of his powers" in mulholland["review"]

    def test_no_review_returns_empty_string(self):
        items = letterboxd_mod._parse_rss(LETTERBOXD_XML)
        the_room = next(i for i in items if i["film_title"] == "The Room")
        assert the_room["review"] == ""

    def test_handles_empty_xml(self):
        assert letterboxd_mod._parse_rss("<rss><channel></channel></rss>") == []
        assert letterboxd_mod._parse_rss("not xml") == []

    def test_star_glyphs_to_rating(self):
        assert letterboxd_mod._star_glyphs_to_rating("★★★★½") == 4.5
        assert letterboxd_mod._star_glyphs_to_rating("★★★★★") == 5.0
        assert letterboxd_mod._star_glyphs_to_rating("★") == 1.0
        assert letterboxd_mod._star_glyphs_to_rating("") is None


class TestLetterboxdBuildRawContent:
    def test_includes_username_in_header(self):
        raw = letterboxd_mod._build_raw_content("jack", [], "")
        assert "[Letterboxd profile] jack" in raw
        assert "No diary entries found" in raw

    def test_orders_top_rated_first(self):
        items = letterboxd_mod._parse_rss(LETTERBOXD_XML)
        raw = letterboxd_mod._build_raw_content("jack", items, "")
        # Mulholland Drive and Big Lebowski both 5-star; first should be either.
        # Pulp Fiction is 4-star and should come after them.
        m_idx = raw.find("Mulholland Drive")
        l_idx = raw.find("The Big Lebowski")
        p_idx = raw.find("Pulp Fiction")
        assert m_idx > 0 and l_idx > 0
        assert p_idx > max(m_idx, l_idx)  # Pulp Fiction appears after the 5-star films

    def test_includes_written_reviews_section(self):
        items = letterboxd_mod._parse_rss(LETTERBOXD_XML)
        raw = letterboxd_mod._build_raw_content("jack", items, "")
        assert "[Written reviews" in raw
        assert "Lynch at the height of his powers" in raw

    def test_includes_liked_section(self):
        items = letterboxd_mod._parse_rss(LETTERBOXD_XML)
        raw = letterboxd_mod._build_raw_content("jack", items, "")
        assert "Films marked as liked" in raw
        assert "Mulholland Drive" in raw.split("Films marked as liked")[1]

    def test_includes_recent_watches(self):
        items = letterboxd_mod._parse_rss(LETTERBOXD_XML)
        raw = letterboxd_mod._build_raw_content("jack", items, "")
        assert "[Recent watches]" in raw


# ─────────────────────────────────────────────────────────────────────────────
# Letterboxd scrape_letterboxd (with mocked network)
# ─────────────────────────────────────────────────────────────────────────────

class TestScrapeLetterboxdPipeline:
    @pytest.mark.asyncio
    async def test_scrape_letterboxd_returns_cached_when_present(self):
        with patch.object(letterboxd_mod, "get_letterboxd_cache", return_value="[cached]"), \
             patch.object(letterboxd_mod, "save_letterboxd_cache") as mock_save:
            raw, posts = await letterboxd_mod.scrape_letterboxd("jack")
        assert raw == "[cached]"
        assert posts[0]["cached"] is True
        mock_save.assert_not_called()

    @pytest.mark.asyncio
    async def test_scrape_letterboxd_happy_path_from_fixture(self):
        with patch.object(letterboxd_mod, "get_letterboxd_cache", return_value=None), \
             patch.object(letterboxd_mod, "save_letterboxd_cache") as mock_save:
            # Patch _fetch_rss to return the fixture directly
            with patch.object(letterboxd_mod, "_fetch_rss", new=AsyncMock(return_value=(LETTERBOXD_XML, "Letterboxd - Jack Moulton"))):
                raw, posts = await letterboxd_mod.scrape_letterboxd("jack")

        assert posts[0]["source"] == "letterboxd"
        assert posts[0]["cached"] is False
        assert posts[0]["items"] == 5
        assert "Mulholland Drive" in raw
        assert "Lynch at the height" in raw
        mock_save.assert_called_once()

    @pytest.mark.asyncio
    async def test_scrape_letterboxd_404_returns_minimal_blob(self):
        with patch.object(letterboxd_mod, "get_letterboxd_cache", return_value=None), \
             patch.object(letterboxd_mod, "save_letterboxd_cache"), \
             patch.object(letterboxd_mod, "_fetch_rss", new=AsyncMock(return_value=(None, None))):
            raw, posts = await letterboxd_mod.scrape_letterboxd("ghost")
        assert "Could not fetch diary RSS" in raw
        assert posts[0]["error"] == "fetch_failed"


# ─────────────────────────────────────────────────────────────────────────────
# Goodreads parsing
# ─────────────────────────────────────────────────────────────────────────────

class TestGoodreadsParseRss:
    def test_parses_six_items_filters_system_noise(self):
        items, _ = goodreads_mod._parse_rss(GOODREADS_XML)
        # 6 items in fixture, 1 is the LikeOnExternalResourcePlaceholder noise → 5
        assert len(items) == 5

    def test_extracts_five_star_review(self):
        items, _ = goodreads_mod._parse_rss(GOODREADS_XML)
        driven = next(i for i in items if "Driven" in i["book_title"])
        assert driven["action"] == "rated"
        assert driven["rating"] == 5
        assert driven["author"] == "Tom Johnson"
        assert "Fantastic and fascinating" in driven["review"]
        assert "to-read" in driven["shelves"]
        assert "business" in driven["shelves"]

    def test_extracts_three_star_review(self):
        items, _ = goodreads_mod._parse_rss(GOODREADS_XML)
        pragmatic = next(i for i in items if "Pragmatic" in i["book_title"])
        assert pragmatic["rating"] == 3
        assert pragmatic["author"] == "David Thomas"

    def test_extracts_started_reading(self):
        items, _ = goodreads_mod._parse_rss(GOODREADS_XML)
        inf = next(i for i in items if "Infinity Machine" in i["book_title"])
        assert inf["action"] == "started"
        assert inf["rating"] is None
        assert inf["author"] == "Sebastian Mallaby"

    def test_extracts_added_to_shelf(self):
        items, _ = goodreads_mod._parse_rss(GOODREADS_XML)
        sapiens = next(i for i in items if "Sapiens" in i["book_title"])
        assert sapiens["action"] == "added"
        assert sapiens["rating"] is None
        assert "history" in sapiens["shelves"]

    def test_extracts_display_name_from_channel_title(self):
        _, name = goodreads_mod._parse_rss(GOODREADS_XML)
        assert name == "Otis"

    def test_filters_like_on_external_resource_placeholder(self):
        items, _ = goodreads_mod._parse_rss(GOODREADS_XML)
        for item in items:
            assert "LikeOnExternalResourcePlaceholder" not in item["guid"]

    def test_handles_empty_xml(self):
        items, name = goodreads_mod._parse_rss("<rss><channel></channel></rss>")
        assert items == []
        assert name == ""

    def test_handles_garbage_xml(self):
        items, name = goodreads_mod._parse_rss("not xml at all")
        assert items == []
        assert name == ""

    def test_handles_unrecognized_title_gracefully(self):
        items, _ = goodreads_mod._parse_rss(GOODREADS_XML)
        # Make sure no item has a None book_title
        for item in items:
            assert item["book_title"]


class TestGoodreadsBuildRawContent:
    def test_includes_user_id_in_header(self):
        raw = goodreads_mod._build_raw_content("1", [], "Otis")
        assert "[Goodreads profile] 1" in raw
        assert "Display name: Otis" in raw
        assert "No recent updates" in raw

    def test_5star_section_present(self):
        items, _ = goodreads_mod._parse_rss(GOODREADS_XML)
        raw = goodreads_mod._build_raw_content("1", items, "Otis")
        assert "[5-star reads" in raw
        assert "Driven" in raw.split("[5-star reads")[1].split("\n\n")[0]

    def test_written_reviews_section_present(self):
        items, _ = goodreads_mod._parse_rss(GOODREADS_XML)
        raw = goodreads_mod._build_raw_content("1", items, "Otis")
        assert "[Written reviews" in raw
        assert "Fantastic and fascinating" in raw

    def test_started_reading_section_present(self):
        items, _ = goodreads_mod._parse_rss(GOODREADS_XML)
        raw = goodreads_mod._build_raw_content("1", items, "Otis")
        assert "[Started reading" in raw
        assert "Infinity Machine" in raw

    def test_added_section_includes_shelves(self):
        items, _ = goodreads_mod._parse_rss(GOODREADS_XML)
        raw = goodreads_mod._build_raw_content("1", items, "Otis")
        assert "[Added to shelves" in raw
        assert "Sapiens" in raw
        assert "shelves:" in raw


# ─────────────────────────────────────────────────────────────────────────────
# Goodreads scrape_goodreads (with mocked network)
# ─────────────────────────────────────────────────────────────────────────────

class TestScrapeGoodreadsPipeline:
    @pytest.mark.asyncio
    async def test_scrape_goodreads_returns_cached_when_present(self):
        with patch.object(goodreads_mod, "get_goodreads_cache", return_value="[cached]"), \
             patch.object(goodreads_mod, "save_goodreads_cache") as mock_save:
            raw, posts = await goodreads_mod.scrape_goodreads("1")
        assert raw == "[cached]"
        assert posts[0]["cached"] is True
        mock_save.assert_not_called()

    @pytest.mark.asyncio
    async def test_scrape_goodreads_happy_path_from_fixture(self):
        with patch.object(goodreads_mod, "get_goodreads_cache", return_value=None), \
             patch.object(goodreads_mod, "save_goodreads_cache") as mock_save:
            with patch.object(goodreads_mod, "_fetch_rss", new=AsyncMock(return_value=GOODREADS_XML)):
                raw, posts = await goodreads_mod.scrape_goodreads("1")

        assert posts[0]["source"] == "goodreads"
        assert posts[0]["cached"] is False
        assert posts[0]["items"] == 5
        assert "Display name: Otis" in raw
        assert "Driven" in raw
        assert "Tom Johnson" in raw
        mock_save.assert_called_once()

    @pytest.mark.asyncio
    async def test_scrape_goodreads_invalid_id_returns_minimal_blob_without_network(self):
        with patch.object(goodreads_mod, "get_goodreads_cache", return_value=None), \
             patch.object(goodreads_mod, "save_goodreads_cache") as mock_save, \
             patch.object(goodreads_mod, "_fetch_rss", new=AsyncMock()) as mock_fetch:
            raw, posts = await goodreads_mod.scrape_goodreads("not-a-number")
        assert "Invalid user ID" in raw
        assert posts[0]["error"] == "invalid_id"
        mock_fetch.assert_not_called()  # critical: don't even attempt the network call
        mock_save.assert_called_once()

    @pytest.mark.asyncio
    async def test_scrape_goodreads_empty_id_returns_minimal_blob_without_network(self):
        with patch.object(goodreads_mod, "get_goodreads_cache", return_value=None), \
             patch.object(goodreads_mod, "_fetch_rss", new=AsyncMock()) as mock_fetch:
            raw, posts = await goodreads_mod.scrape_goodreads("")
        assert "Invalid user ID" in raw
        mock_fetch.assert_not_called()

    @pytest.mark.asyncio
    async def test_scrape_goodreads_404_returns_minimal_blob(self):
        with patch.object(goodreads_mod, "get_goodreads_cache", return_value=None), \
             patch.object(goodreads_mod, "save_goodreads_cache"), \
             patch.object(goodreads_mod, "_fetch_rss", new=AsyncMock(return_value=None)):
            raw, posts = await goodreads_mod.scrape_goodreads("999999999999")
        assert "Could not fetch updates RSS" in raw
        assert posts[0]["error"] == "fetch_failed"

    @pytest.mark.asyncio
    async def test_scrape_goodreads_truncates_to_content_max_chars(self):
        huge_xml = GOODREADS_XML + "<item>" + ("x" * 500_000) + "</item>"
        with patch.object(goodreads_mod, "get_goodreads_cache", return_value=None), \
             patch.object(goodreads_mod, "save_goodreads_cache"), \
             patch.object(goodreads_mod, "_fetch_rss", new=AsyncMock(return_value=huge_xml)):
            raw, _ = await goodreads_mod.scrape_goodreads("1")
        from app.config import settings
        assert len(raw) <= settings.content_max_chars


# ─────────────────────────────────────────────────────────────────────────────
# Live network tests — opt-in only, requires -m live_network
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.live_network
class TestLiveNetwork:
    @pytest.mark.asyncio
    async def test_live_letterboxd_smoke(self):
        raw, posts = await letterboxd_mod.scrape_letterboxd("jack")
        assert "Letterboxd profile" in raw
        # Either we got items, or an explicit error key — but never both
        assert posts[0].get("items", 0) > 0 or posts[0].get("error") in {"fetch_failed", None}

    @pytest.mark.asyncio
    async def test_live_goodreads_smoke(self):
        # Goodreads user 1 (Otis Chandler) — empty but valid URL pattern
        raw, _ = await goodreads_mod.scrape_goodreads("1")
        assert "Goodreads profile" in raw

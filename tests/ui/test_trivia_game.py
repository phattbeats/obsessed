"""
Playwright UI tests for Obsessed trivia game.
Requires: PLAYWRIGHT_BASE_URL (default http://10.0.0.100:10198)
Install: pip install playwright && playwright install chromium
Run:    pytest tests/ui/test_trivia_game.py -v
CI:     PLAYWRIGHT_BASE_URL=http://10.0.0.100:10198 pytest tests/ui/test_trivia_game.py -v
        (runs on workflow_dispatch or when 'ui-test' is in the commit message)
"""

import os
import pytest

BASE_URL = os.environ.get("PLAYWRIGHT_BASE_URL", "http://10.0.0.100:10198")


class TestAppLoads:
    """Verify the app root and static assets load correctly."""

    def test_app_loads_at_root(self, page):
        """App should render the home screen on first load."""
        page.goto(BASE_URL)
        assert page.locator("#screen-home").is_visible(), "Home screen not visible on load"
        assert page.locator(".app-title").is_visible(), "App title not visible"

    def test_home_screen_has_new_game_button(self, page):
        """New Game button should be present on the home screen."""
        page.goto(BASE_URL)
        btn = page.locator('button:has-text("New Game")')
        assert btn.is_visible(), "New Game button not visible on home screen"

    def test_static_css_serves(self, page):
        """CSS file should be served with correct content-type."""
        response = page.request.get(f"{BASE_URL}/static/css/style.css")
        assert response.status_code == 200, "style.css returned non-200"
        assert "text/css" in response.headers.get("content-type", ""), "style.css not served as CSS"

    def test_static_js_serves(self, page):
        """JS bundle should be served."""
        response = page.request.get(f"{BASE_URL}/static/js/app.js")
        assert response.status_code == 200, "app.js returned non-200"


class TestProfileCreation:
    """
    Profile creation flow.
    Regression guard: threads_handle, instagram_handle, google_places_handle must
    survive a create → GET round-trip (fixes PHA-404).
    """

    def test_profile_form_renders(self, page):
        """Clicking New Game should show the profile form."""
        page.goto(BASE_URL)
        page.locator('button:has-text("New Game")').click()
        assert page.locator("#screen-profile").is_visible(), "Profile screen not visible"
        assert page.locator('input[name="name"]').is_visible(), "Name input not visible"

    def test_profile_persists_handles_via_api(self):
        """
        POST /api/profiles with threads/instagram/google_places handles must
        return those same values on GET.  Validates PHA-404 fix.
        """
        import httpx

        payload = {
            "name": "Playwright Handle Test",
            "entity_type": "person",
            "threads_handle": "test_threads_user",
            "instagram_handle": "test_insta_user",
            "google_places_handle": "Joe's Cafe NYC",
        }
        post = httpx.post(f"{BASE_URL}/api/profiles", json=payload, timeout=10.0)
        assert post.status_code == 200, f"POST /api/profiles failed: {post.text}"
        created = post.json()
        profile_id = created["id"]

        try:
            get = httpx.get(f"{BASE_URL}/api/profiles/{profile_id}", timeout=10.0)
            assert get.status_code == 200
            data = get.json()
            assert data["threads_handle"] == "test_threads_user", \
                f"threads_handle mismatch: got {data['threads_handle']!r}"
            assert data["instagram_handle"] == "test_insta_user", \
                f"instagram_handle mismatch: got {data['instagram_handle']!r}"
            assert data["google_places_handle"] == "Joe's Cafe NYC", \
                f"google_places_handle mismatch: got {data['google_places_handle']!r}"
        finally:
            httpx.delete(f"{BASE_URL}/api/profiles/{profile_id}", timeout=5.0)

    def test_profile_list_includes_created(self):
        """A newly created profile should appear in the profile list."""
        import httpx

        unique = f"List Test {__import__('uuid').uuid4().hex[:6]}"
        post = httpx.post(
            f"{BASE_URL}/api/profiles",
            json={"name": unique, "entity_type": "person"},
            timeout=10.0,
        )
        assert post.status_code == 200
        new_id = post.json()["id"]

        try:
            listing = httpx.get(f"{BASE_URL}/api/profiles", timeout=10.0)
            assert listing.status_code == 200
            ids = [p["id"] for p in listing.json()]
            assert new_id in ids, f"Newly created profile id {new_id} not in listing"
        finally:
            httpx.delete(f"{BASE_URL}/api/profiles/{new_id}", timeout=5.0)


class TestTriviaGameFlow:
    """Game room creation and SPA navigation."""

    @pytest.fixture
    def game_profile_id(self):
        """Create a profile for use in game tests; cleanup after."""
        import httpx
        resp = httpx.post(
            f"{BASE_URL}/api/profiles",
            json={"name": "Game Flow Test", "entity_type": "person"},
            timeout=10.0,
        )
        assert resp.status_code == 200
        pid = resp.json()["id"]
        yield pid
        try:
            httpx.delete(f"{BASE_URL}/api/profiles/{pid}", timeout=5.0)
        except Exception:
            pass

    def test_create_game_returns_room_code(self, game_profile_id):
        """POST /api/games should return a room_code."""
        import httpx
        resp = httpx.post(
            f"{BASE_URL}/api/games",
            json={"profile_id": game_profile_id},
            timeout=10.0,
        )
        assert resp.status_code == 200, f"Create game failed: {resp.text}"
        data = resp.json()
        assert "room_code" in data, "room_code not in game response"
        assert len(data["room_code"]) == 6, f"Expected 6-char room code, got {data['room_code']}"

    def test_get_game_returns_room_state(self, game_profile_id):
        """GET /api/games/{room} should return the game state."""
        import httpx
        # Create game
        create = httpx.post(
            f"{BASE_URL}/api/games",
            json={"profile_id": game_profile_id},
            timeout=10.0,
        )
        room_code = create.json()["room_code"]
        # Fetch it
        get = httpx.get(f"{BASE_URL}/api/games/{room_code}", timeout=10.0)
        assert get.status_code == 200
        data = get.json()
        assert data["room_code"] == room_code
        assert "status" in data

    def test_spa_navigates_to_profile(self, page):
        """SPA navigation: New Game button should switch to profile screen."""
        page.goto(BASE_URL)
        page.locator('button:has-text("New Game")').click()
        assert page.locator("#screen-profile").is_visible()
        # Back button should return home
        page.locator(".back-btn").click()
        assert page.locator("#screen-home").is_visible()

    def test_profile_screen_has_all_social_inputs(self, page):
        """Profile form should show all social handle fields."""
        page.goto(BASE_URL)
        page.locator('button:has-text("New Game")').click()
        page.locator('select[name="entity_type"]').select_option("person")
        # These fields are only shown for entity_type=person
        page.wait_for_selector('input[name="threads_handle"]', timeout=3000)
        page.wait_for_selector('input[name="instagram_handle"]', timeout=3000)
        assert page.locator('input[name="threads_handle"]').is_visible()
        assert page.locator('input[name="instagram_handle"]').is_visible()


class TestWebSocketNoCrash:
    """
    WebSocket smoke: loading the game screen should not throw JS errors.
    Full WS event testing requires a live game (players, questions) which is
    not practical in a headless CI environment without a running game server.
    """

    def test_game_screen_has_no_critical_console_errors(self, page, game_profile_id):
        """Loading the page with a room should not emit JS errors to the console."""
        import httpx

        # Create a game to get a valid room code
        resp = httpx.post(
            f"{BASE_URL}/api/games",
            json={"profile_id": game_profile_id},
            timeout=10.0,
        )
        room_code = resp.json()["room_code"]

        errors = []

        def on_console(msg):
            # Ignore favicon/font errors — those are external resource noise
            if msg.type == "error" and "favicon" not in msg.text and "fonts" not in msg.text.lower():
                errors.append(msg.text)

        page.on("console", on_console)
        page.goto(f"{BASE_URL}/?room={room_code}")
        page.wait_for_timeout(2000)

        critical = [e for e in errors if "TypeError" in e or "ReferenceError" in e]
        assert not critical, f"JS errors on game screen: {critical}"

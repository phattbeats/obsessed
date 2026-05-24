# OBSESSED — UI Redesign Handoff

**Drop date:** v1.0 · six-pass UI rebuild
**Status:** Ready for integration into `app/static/`. Backend changes flagged below.
**Source brief:** `CLAUDE_DESIGN_PROMPT.md` + `SPEC.md` §2 (design language)
**Demo files:** `obsessed-design.html` (Pass 1 inventory) · `Obsessed v2.html` (Pass 2 logo+lobby) · `Obsessed v3/v4.html` (player game) · `Obsessed v5.html` (host TV view) · `Obsessed v6.html` (profile editor) · `Obsessed v7.html` (results)

---

## 1. TL;DR for whoever's wiring this in

- **No build step.** Pure vanilla HTML / CSS / JS. Drop the files into `app/static/`, add the two referenced FastAPI routes, and you're live.
- **One source of truth for design tokens** — `app/static/css/tokens.css`. Existing `:root` variables are preserved; new ones added per `CLAUDE_DESIGN_PROMPT.md §4.1`.
- **Component factories** (wedge, category icon, platform icon, confetti) are stand-alone vanilla scripts that expose globals. No imports, no dependencies between files except where noted.
- **Five screen controllers** — `GamePlayer`, `GameHost`, `ProfileEditor`, `ResultsScreen`, plus a planned `Home`/`Lobby` (designs in Pass 2 demo, not yet extracted). Each follows the same pattern: `Foo.attach(rootEl)` returns a controller; the page calls render methods in response to WebSocket events.
- **Backend changes** are documented inline with `// NEEDS BACKEND:` comments and consolidated in §8 of this doc. None are blocking — the UI gracefully handles missing data.
- **Mobile**: player and profile screens are mobile-first. Host view targets 1280×720+. Results screen scales fluidly via container queries.
- **Accessibility**: reduced-motion guarded everywhere, focus-visible states on every interactive, WCAG AA contrast verified for category-color combinations.

---

## 2. File map — what's new, what changes

### New files (drop these into the repo)

```
app/static/
├── host.html                              [NEW]  Production TV view
├── css/
│   ├── tokens.css                         [NEW]  Token foundation — pulled out of style.css and extended
│   ├── components.css                     [NEW]  btn / card / player-bubble / room-code / score-pill / toast / fx-shake
│   ├── category-icons.css                 [NEW]  Icon container + badge pill
│   ├── wedge.css                          [NEW]  Wedge component variants + spring fill
│   ├── confetti.css                       [NEW]  60-particle CSS-keyframe confetti
│   ├── game-player.css                    [NEW]  Player view — top bar, question card, answer tiles, timer, reveals
│   ├── game-host.css                      [NEW]  TV view — header, main, sidebar, legend, six host states
│   ├── profile-editor.css                 [NEW]  Dossier-flavored editor — tabs, status chips, scrape loader, preview drawer
│   └── results.css                        [NEW]  Results screen — winner card, breakdown, hardest-question callout
├── js/
│   ├── components/
│   │   ├── wedge.js                       [NEW]  renderWedge(host, opts)
│   │   ├── category-icon.js               [NEW]  renderCatIcon / renderCatBadge
│   │   ├── platform-icon.js               [NEW]  renderPlatformIcon — 12 glyphs
│   │   └── confetti.js                    [NEW]  confettiBurst({origin, count, stage})
│   └── screens/
│       ├── game-player.js                 [NEW]  GamePlayer.attach(root)
│       ├── game-host.js                   [NEW]  GameHost.attach(root)
│       ├── profile-editor.js              [NEW]  ProfileEditor.attach(root)
│       └── results.js                     [NEW]  ResultsScreen.attach(root)
```

### Files to modify

```
app/static/
├── index.html                             [MODIFY]
│   • Replace inline <style> in #things-modal with .modal classes (move to components.css)
│   • Replace #screen-profile section with the profile editor markup from Obsessed v6.html
│   • Replace #screen-game body with the markup from Obsessed v4.html (player game)
│   • Replace #screen-results body with the markup from Obsessed v7.html
│   • Strip inline style="…" attributes — they're now in components.css / per-screen CSS
│
├── css/style.css                          [REPLACE or merge]
│   • The existing :root block moves into tokens.css
│   • Most of the rest is superseded by the new per-screen CSS files
│   • Keep only what's still useful for screens that haven't been redesigned yet (history, settings)
│
└── js/app.js                              [MODIFY — light touch]
    • Keep the WebSocket connection + screen router
    • In handleWSMessage(), call the new controllers' methods instead of the inline render funcs
    • Examples:
        case 'new_question':   GamePlayer.renderQuestion(msg); break;
        case 'answer_result':  GamePlayer.showAnswerResult(msg); break;
        case 'round_end':      GamePlayer.showRoundEnd(msg); GameHost.renderRoundEnd(msg); break;
        case 'game_over':      ResultsScreen.render(msg); break;
```

### FastAPI routes to add

In `app/main.py`:

```python
from fastapi.responses import FileResponse

@app.get("/host.html")
@app.get("/host/{room}")
async def host_view(room: str = ""):
    return FileResponse("app/static/host.html")
```

That's the only Python change required.

---

## 3. Token foundation — `tokens.css`

Single source of truth for color, type, space, motion, radii, shadows, z-layers. Every other stylesheet references vars from here.

**Categories that didn't exist in the old `style.css`:**
- Spacing scale (`--space-1` through `--space-9`, base-8)
- Radii (`--radius-sm` 8 / `--radius-md` 12 / `--radius-lg` 16 / `--radius-xl` 24 / `--radius-pill` 999)
- Strokes (`--stroke-hair` 1 / `--stroke-thick` 3 / `--stroke-bold` 8)
- Elevation (`--shadow-card` / `--shadow-modal` / `--shadow-press` / `--shadow-tile` / `--glow-accent`)
- Motion (`--ease-out` / `--ease-overshoot` / `--ease-bounce` / `--dur-quick…--dur-wedge`)
- Type scale (`--fs-display` 72 / `--fs-h1` 48 / `--fs-question` 36 / `--fs-h2` 28 / `--fs-h3` 24 / `--fs-answer` 22 / `--fs-body` 16 / `--fs-caption` 13 / `--fs-micro` 11)
- Z layers (`--z-base` 0 / `--z-card` 10 / `--z-sticky` 50 / `--z-modal` 100 / `--z-toast` 200 / `--z-confetti` 300)
- Reusable backgrounds (`--dot-grid` + `--dot-grid-size` for the subtle texture used on every major screen)

**Rule:** no magic numbers in any new CSS file outside `tokens.css` and inside `@keyframes`. If you find a hardcoded value, lift it into a token.

**Reduced-motion:** `tokens.css` collapses all `--dur-*` to 0ms inside `@media (prefers-reduced-motion: reduce)`. Per-screen CSS adds further guards for specific animations.

---

## 4. Components

### Wedge — the brand object

**The persistent visual identity.** A six-slice pie in the category colors. Same component renders as:
- `--inline` (24px) — top bar player chip
- `--sm` (48px) — profile card / sidebar progress
- `--md` (200px) — between-rounds, lobby player view
- `--lg` (400px) — host TV hero / between-rounds host

**API:**
```js
const ctrl = renderWedge(hostEl, { size: 'lg', fills: { history: true, … } });
ctrl.fillCategory('entertainment', { burst: true });   // animated fill, spring-from-centroid
ctrl.setFills({ history: true, science: true });       // bulk set, no animation
ctrl.isComplete();                                      // → bool
```

**Fill technique:** SVG `<path>` per slice, `transform: scale(0)→scale(1)` from the slice's centroid with `--ease-bounce`, 500ms. Pure transform/opacity, no layout thrash.

### Category icons — six geometric glyphs

History (clock) · Entertainment (clapper) · Geography (globe) · Science (atom) · Sports (ball) · Art & Lit (book). All geometric primitives, thick rounded strokes, no detailed illustration. Color inherits from `currentColor`, sized 24 / 32 / 64.

```js
renderCatIcon(hostEl, 'history', 'lg');
renderCatBadge(hostEl, 'history', { label: 'History' });   // icon + pill
```

### Platform icons — 12 glyphs

Reddit · Twitter/X · Steam · Pinterest · Facebook · Instagram · TikTok · Wikipedia · News · Court · SOS · Auditor · Manual link. Same authoring style as category icons.

**Aligned to the actual backend scrapers in `app/services/scraper/`.** Discord was removed because there's no Discord scraper in the pipeline. Pinterest and Facebook were missing from the old UI and have been added.

### Buttons

`.btn` base + modifiers: `--primary` (hot pink), `--ghost` (transparent), `--danger` (red outline), `--icon` (44×44), `--lg` (28px copy), `--pulse` (gentle ring pulse for "ready to act" CTAs).

Every variant has hover (translateY -2px), active (translateY +2px, shadow gone), focus-visible (3px accent outline at 3px offset), and disabled states.

### Player bubble

Initials in a colored circle. Sizes `--sm` (32) / `--md` (48) / `--lg` (72). Pop-in animation via `.is-entering` class (one-shot, scale 0→1.18→1, overshoot). State dots on corner: `--correct` (green) / `--wrong` (red) / `--thinking` (yellow, pulsing).

### Room code · Score pill · Screen header · Toast · Flash overlays · Screen shake

All in `components.css`. All token-driven, all accessibility-guarded, all documented in their CSS section headers.

### Confetti

`confettiBurst({ origin?, count?, stage? })`. Pure CSS keyframes, 60-particle cap, particles auto-clean on `animationend`. Stage element is auto-created if not provided. **Reduced-motion = noop.**

---

## 5. Screen controllers

All five follow the same shape:

```js
const Controller = ScreenName.attach('#root');   // pick the root element
Controller.someRender({ … });                    // call in response to WS events
Controller.onSomething(callback);                // wire to your event handlers
```

Markup contracts (data-role attributes) are documented at the top of each `.js` file.

### `GamePlayer` — the phone view

**Markup:** see `Obsessed v4.html` or the inline markup contract at the top of `game-player.js`.

**API:**
- `setPlayer({ name, initials, color, score, fills })`
- `renderQuestion(q)` — `q` matches the existing `new_question` WS payload, plus `round_num` / `total_rounds`
- `setSelection(answerText)` — visually highlights; called by tile clicks
- `confirmSelection()` — submits; called by the "Lock it in" button or by timer expiry
- `showAnswerResult(msg)` — reveal correct/wrong; matches `answer_result` payload
- `showWaiting({ label, title, progress })` — between questions
- `showRoundEnd({ round_num, category, won, score_round, correct, total, on_continue })`
- `fillWedge(category)` — animate a slice
- `rollScoreTo(target, durMs)` — score counter pop
- `onSelect(cb)` · `onSubmit(cb)` — fired on user tile tap and on lock-in

**Behavior model:** tap to **select** (highlight only, you can switch freely between answers), "Lock it in" button or timer-expiry submits. Wedge slices fill at **round end**, not after every correct answer — this is the major behavioral change from the old code.

### `GameHost` — the TV view

**Production page:** `app/static/host.html`. Boot via `?room=XK7T2P` query or `/host/XK7T2P` path.

**Six render states:**
- `renderLobby({ guest, code, players })`
- `renderPreRound({ round_num, total_rounds, category, category_label })`
- `renderQuestion(q)` — same shape as player
- `highlightAnswer(letter)` — for the host's "I think it's B" gesture (keys 1–4)
- `markPlayerAnswered(playerId)` — bubble flips to "locked"
- `revealAnswer({ correct_answer, per_player: [{id, is_correct, points_earned}] })`
- `renderRoundEnd({ round_num, category, winner, … })`
- `renderResults({ winner, players, rounds_won, total_rounds })`

**Container queries.** The host shell uses `container-type: inline-size` and sizes text in `cqi` units so it works correctly at 1920×1080, 1280×720, and inside a demo TV frame at 1000px.

**Keyboard:** `Space` → POST `/api/games/<room>/next` · `Esc` → confirm + POST `/api/games/<room>/abort` · `1–4` → highlight an answer preview.

### `ProfileEditor`

**Markup:** see `Obsessed v6.html`.

**API:**
- `setEntityType('person'|'place'|'thing'|'event')` — rebuilds the platforms section
- `getFormData()` — serialized snapshot of every filled field
- `setScrapeStatus(fieldKey, 'idle'|'ready'|'scraping'|'done'|'failed')` — update one chip
- `runScrapeAnimation({ stepMs, results, onDone })` — animated platform rail
- `openPreview({ subject, questions })` / `closePreview()` — slide-up drawer

**Entity config** lives in `ENTITY_FIELDS` at the top of `profile-editor.js`. To add a platform: one entry, no other code changes.

**Aesthetic:** dark navy game UI with **dossier-flavored accents** — Special Elite for small labels and the eyebrow, a red tilted "stamp" in the top-right, red marginalia for section numbers. (An earlier experiment turned the whole editor into a manila folder; we reverted — too much.)

### `ResultsScreen`

**Three modes**, inferred from data if not passed explicitly:
- `standard` — clear winner, mixed wedges
- `perfect` — top player has all 6 wedges, "Clean sweep" green badge
- `tie` — top 2 share top score, side-by-side winner halves

**API:**
```js
Results.render({
  players: [{ id, name, initials, color, score, fills,
              correct, total, fastest_count, avg_time_s, is_you? }],
  hardest_question: { category, category_label, text, answer, percent_correct },
  game_mode?: 'standard'|'perfect'|'tie',
});
Results.onRematch(fn);
Results.onHome(fn);
```

**Choreography:** winner card lands first (overshoot), hardest-question card next, breakdown rows stagger 80ms apart. Confetti fires 350ms after mount so the winner card lands first.

---

## 6. New gameplay behavior

### Rounds replace flat questions

A game is **N rounds × 5 questions** (default 3 rounds). Each round is **one category**. Winning a round (≥3 correct out of 5) earns that category's wedge slice. The wedge stays six-sliced — `which six` is the per-game choice.

**This changes:**
- Game-create endpoint needs a round-count + category-pack picker (default: 3 rounds, host picks 3 categories, "random N" and "marathon all 6" as alts).
- `new_question` WS payload needs `round_num` and `total_rounds`.
- New WS events: `round_start`, `round_end`, `player_answered`.
- Wedge fill no longer happens on `answer_result` — it happens on `round_end` based on the round winner.

### Tap-to-select, lock-to-submit

Player view used to lock on tap. Now: tap selects (highlight, no submit). The "Lock it in" button arms when a selection exists. Timer expiry auto-confirms the current selection. **Players can change their answer freely while the timer runs.**

**Server-side change:** none. The submit path is unchanged — `POST /api/games/<room>/answer`. Only the client decides when to call it.

### Per-player live status (host view)

The host TV shows which players have locked in (white ring around bubble), then who got it right/wrong (green/red) after the reveal. This needs a new `player_answered` WS event with just `{ player_id }` — fired when a player POSTs an answer, before the reveal goes out.

---

## 7. Brand decisions (logged for reference)

### Logo

**Ship lockup B** — wedge-as-O. The first "O" of OBSESSED is replaced by an inline wedge at the same height as the wordmark. Concept C (wordmark + mark beside it) is the wide marketing lockup. Concept D (stacked stamp) is for stickers / loading splash / merch.

The wedge reads as a logo even without the type — favicon test passes at 16px.

### Categories

**Default 6** stays (History · Entertainment · Geography · Science · Sports · Art & Lit) — broad coverage, reads as Trivia Night.

**Proposed addition: "Obsessed Mode" 6** (Receipts · The Feed · People · The Cringe Vault · Deep Cuts · Hot Takes) — questions framed by *how* you found them. Implementation: each category gets a stable `key` and color in tokens.css; the wedge factory takes the keys as input, so swapping the pack is a config change. Question generator needs a prompt template per category. Scraper tags facts with one or more categories.

Game-setup UI lets the host pick a pack. Wedge always stays 6-sliced.

### Alt metaphors (explored, then declined)

Considered: dossier folder · polaroid grid · receipt scroll · cork-board · pixel-reveal. Verdict: **keep the wedge as the primary brand object** — it's the only one that scales from 24px to 400px without losing meaning. **Borrowed:** the dossier metaphor as an accent for the profile editor (Special Elite labels + stamp), the receipt idea is held for a future "session log" feature.

---

## 8. Backend asks — consolidated

All are non-blocking; the UI falls back to "—" when fields are missing.

| Where | What | Why |
|---|---|---|
| `routes/games.py` — game create | accept `round_count` and `categories[]` params | round-based game setup |
| `routes/games.py` — `new_question` WS event | add `round_num`, `total_rounds` to payload | host pre-round card + player progress text |
| `routes/games.py` — `answer_result` WS event | add `category`, `per_player: [{id, is_correct, points_earned}]` | wedge fill at round end + host sidebar pulses |
| `routes/games.py` | new `round_start` WS event `{round_num, total_rounds, category, category_label}` | host pre-round card |
| `routes/games.py` | new `round_end` WS event `{round_num, total_rounds, category, winner: {…}, scores: […]}` | round-end celebration |
| `routes/games.py` | new `player_answered` WS event `{player_id}` (no reveal yet) | host shows "X locked in" |
| `routes/games.py` | new `POST /api/games/<room>/abort` | host Esc key |
| `routes/games.py` — `/scores` | extend response with `correct, total, fastest_count, avg_time_s, fills` per player | results breakdown |
| `routes/games.py` | new `GET /api/games/<room>/hardest` | "the one that got you" callout |
| `routes/profiles.py` | already has the right shape — no changes needed | — |

---

## 9. Wiring guide — how to drop this into the repo

1. **Copy the new files** under `app/static/` exactly as listed in §2.
2. **Add the FastAPI route** for `host.html` (§2 bottom).
3. **In `index.html`:**
   - In the `<head>`, link the new CSS files in this order: `tokens → components → category-icons → wedge → confetti → game-player → profile-editor → results` (only the ones a given page needs).
   - Remove the inline `<style>` block in `#things-modal` and the inline `style="…"` attributes scattered around.
   - Replace the markup of `#screen-profile`, `#screen-game`, `#screen-results` with the contracts from `Obsessed v6.html`, `v4.html`, `v7.html` respectively.
4. **In `app/static/js/app.js`:**
   - At boot, call `GamePlayer.attach('#game-player-root')` and `ResultsScreen.attach('#results-root')`, store the controllers.
   - In `handleWSMessage()`, route events to the controllers (§5 lists the mapping).
   - Wire the player's `onSubmit` to your `POST /answer` flow.
5. **Build the round-start payload server-side** and emit `round_start` before the first `new_question` of each round. The player and host both subscribe.
6. **Run.** Existing happy path (create profile → start game → answer question → see results) should still work, with the new visual language.

A single end-to-end smoke test before merging:
- Create a person profile with at least Reddit + 5 manual facts
- Trigger scrape — confirm question generation produces ≥15 questions
- Start a 3-round game, join from a phone, play through one full round
- Verify: wedge fills only at round end · score updates with the roll animation · timer color shifts at 10s and 5s · "Lock it in" button arms after first tap · reveal flash + shake on wrong · confetti on game over

---

## 10. Open questions / followups

- **Consent flow.** The current profile cards show "⚠ GUEST CONSENT REQUIRED" but there's no consent-prompt screen for the guest themselves. The dossier-flavored editor leans hard into "you are scraping a real person" — would benefit from a separate consent screen *before* the editor opens for users that haven't accepted yet.
- **Home / lobby production extraction.** Designs are in `Obsessed v2.html` §B and §C — they need to be pulled into production CSS/JS the way Pass 3–6 were. Should be a quick pass.
- **Settings + admin restyle.** Lower priority. Inherit the new tokens; structure stays as-is.
- **Question quality feedback UI.** The DB tracks `times_asked`, `times_correct`, `quality_score` but the host has no way to flag a bad question after the game. Worth a small post-results "hide this question" affordance.
- **Sound design.** Out of scope for this drop. The screen-shake / flash / starburst are visual stand-ins for audio cues — a future SFX pass should map to them.
- **"Best question" feed.** The hardest-question callout in the results screen is one-question. Could become a "top 3 that got you" mini-feed if there's room.
- **Speaker / cards mode for the player who's currently revealing.** Brief mentions Jack Box energy — there's room for a "host gets one screen, player gets another" mode where the host TV shows the reveal animation 1s before the player phones do.

---

## 11. Demo files (don't ship — for reference only)

These live at the project root and demonstrate each pass against the production CSS/JS:

| File | Pass | What it shows |
|---|---|---|
| `obsessed-design.html` | 1 | Token inventory, all wedge variants, category icons at three sizes, lobby hero, mini home/lobby preview |
| `Obsessed v2.html` | 2 | 4 logo lockups (recommend B), home cold-open directions, polished lobby, motion lab with live triggers, category expansion proposal, alt-metaphor exploration |
| `Obsessed v4.html` | 3 | Player view — 3-round demo (5Q each) inside a phone bezel with controls sidecar. Tap-to-select model + Lock-it-in button + per-round wedge fills. |
| `Obsessed v5.html` | 4 | Host TV view inside a TV frame, six state-cycle buttons |
| `Obsessed v6.html` | 5 | Profile editor with 4 state cycle (blank · filled · scraping · ready) |
| `Obsessed v7.html` | 6 | Results screen with 3 mode cycle (standard · perfect · tie) |

These should be deleted (or moved under `docs/`) before deploy.

---

**Built across 6 passes. Questions on any of the above → check the file's header comments first; most APIs are documented in-source.**

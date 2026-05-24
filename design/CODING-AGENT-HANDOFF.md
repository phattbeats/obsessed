# Coding Agent Handoff — Obsessed! UI Implementation

**Repo:** `phattbeats/obsessed` (main branch)
**Target:** Land the !BSESSED rebrand + new UI system into production
**Date:** May 24, 2026
**Supersedes:** `OBSESSED-DESIGN-HANDOFF.md` v1 (the wedge system — DEPRECATED; see §0 below)

---

## 0. CRITICAL — read before you start

The previous handoff doc (`OBSESSED-DESIGN-HANDOFF.md`) specified a **six-slice pie wedge** as the brand object. **That is dead.** It reads exactly like Trivial Pursuit from a distance. The new system is:

- **Logo:** the "!" in OBSESSED! becomes the mark. Pink circle + italic black "!" replaces the O. Pulsating 2.4s loop.
- **Scoring:** 6-slot rack of bang slots — fill one per category/round won. NOT a pie.
- **Typeface:** Rubik 900 (logo + headings + questions), Nunito (body), JetBrains Mono (captions/code/timestamps).
- **Tagline:** "the hyper-personal trivia game" (not "a").

Wherever you see references to "wedge", "pie", or "six slices" in the old handoff doc, replace with "bang", "rack", or "six slots". Reference design files in this project for ground truth (see §3).

---

## 1. Current state of the repo (live)

| File | State | Verdict |
|---|---|---|
| `app/static/index.html` | All 5 screens inline; uses Fredoka One; gradient-text "OBSESSED" logo; wedge-board div placeholder | **Rebuild** |
| `app/static/css/style.css` | ~250 lines; partial old token system; no Rubik; no Bang | **Replace** |
| `app/static/js/app.js` | ~600 lines; WebSocket router; flat 50-question game (no rounds) | **Refactor** in place (controllers per screen) |
| `app/static/admin.html` | Separate admin page | Restyle later (low priority) |
| `app/main.py` | FastAPI mount; serves `/`, `/admin.html` | **Add** `/host.html` route + `/host/{room}` |
| `app/routes/games.py` | Flat 50-question game; no round events | **Defer**: keep flat-question mode for now; add rounds in a later PR |

**Categories already exist** in `CATEGORY_COLORS` (`games.py`) with the same hex values our design system uses. ✅

**WebSocket events that already work:** `player_joined`, `game_started`, `new_question`, `answer_result`, `game_over`. ✅

**WebSocket events that are MISSING (needed for the full new design):** `round_start`, `round_end`, `player_answered`. ⚠️ See §6.

---

## 2. Two-phase implementation plan

### Phase 1 — Visual rebrand only (1 sprint, ships standalone)

Lands the new logo, Rubik 900, Bang component, and component library **without changing gameplay**. The game still plays flat 50-question mode. Goal: kill the Trivial Pursuit collision, ship the Bang.

### Phase 2 — Rounds + host TV view (1–2 sprints)

Adds `round_start`/`round_end`/`player_answered` WebSocket events, the host TV view (`host.html`), the bang-rack scoring mechanic that fills per round, and the round-end celebration.

Phase 2 depends on Phase 1 but Phase 1 ships first and is valuable alone.

---

## 3. Source-of-truth design files (in THIS project, not the repo)

Copy these into the repo as reference (e.g. into a `design/` folder, gitignored from the runtime). Coding agent should READ them, not import them as runtime assets.

| File | What it specifies |
|---|---|
| `LOGO-DECISION.md` | The locked typography + logo decision |
| `Bang Logo Variations.html` | All 8 logo lockups with exact CSS (A primary, B stacked, C icon-only, D compact, E wide+tagline, F square, G mini badge, H hero) |
| `Bang Logo Studies.html` | Rubik 900 typography specimen across all UI text styles (h1/h2/h3/question/body/caption/button) |
| `Obsessed v9 - Bang Refined.html` | The Bang direction in the broader v8 doc — has full player/host context, mini phone mocks, in-context rack |
| `OBSESSED-DESIGN-HANDOFF.md` | **Read for screen structure, component APIs, and FastAPI route stubs ONLY** — ignore all wedge / pie / six-slice references; substitute Bang. |

---

## 4. Phase 1 — PR-sized tasks (in order)

### PR-1: Token foundation

**Goal:** One source of truth for color, type, space, motion. No other file uses magic numbers.

- Create `app/static/css/tokens.css`. Use the token block in `LOGO-DECISION.md` plus the larger one inside `Obsessed v9 - Bang Refined.html` `<style>` (the `:root` near the top).
- Add `--font-logo: 'Rubik', system-ui, sans-serif;` and remove `Fredoka One` from any new code (it stays in `style.css` only until that file is replaced).
- Add the Rubik Google Fonts link to `index.html` `<head>`:
  ```html
  <link href="https://fonts.googleapis.com/css2?family=Rubik:wght@400;600;700;800;900&family=Nunito:wght@400;700;800;900&family=JetBrains+Mono:wght@500;700&display=swap" rel="stylesheet">
  ```
- Remove the Fredoka One link.

**Acceptance:** `tokens.css` loaded before `style.css`. `getComputedStyle(document.documentElement).getPropertyValue('--font-logo')` returns Rubik in browser devtools. No visual change yet.

### PR-2: The Bang component

**Goal:** A reusable Bang lockup that can be dropped anywhere in any size.

- Create `app/static/css/bang.css` with the lockup CSS from `Bang Logo Variations.html` (variations A, C, D, H at minimum — primary/icon/compact/hero).
- Create `app/static/js/components/bang.js`:
  ```js
  // renderBang(host, { variant: 'primary'|'stacked'|'icon'|'compact'|'hero', wordmark: true|false })
  // Returns the lockup DOM. Pulsation is CSS-driven; no JS animation needed.
  ```
- The pulsation keyframes `@keyframes bangPulse` must respect `prefers-reduced-motion` (collapse to no animation).

**Acceptance:** Open a test page that mounts 4 sizes; verify the ! is optically centered in the circle (use the `translateY(N)` values from the design files, NOT the skew transform — skew was removed because it offset the ! visually), and the circle pulsates.

### PR-3: Replace the home screen logo

**Goal:** Kill the gradient-text "OBSESSED" wordmark. Replace with Bang variation A.

- In `index.html`, replace `<div class="app-title">OBSESSED</div>` with:
  ```html
  <div id="home-logo"></div>
  ```
  and call `renderBang(document.getElementById('home-logo'), { variant: 'primary', wordmark: true });` on boot.
- Update tagline: `<p class="app-tagline">the hyper-personal trivia game</p>` (lowercase "the", not "The trivia game that knows your friends...").
- Remove the gradient text CSS from `style.css` (`.app-title { background: linear-gradient(...); -webkit-background-clip: text; ... }`).

**Acceptance:** Home screen renders the Bang lockup with Rubik 900 wordmark; the ! circle pulsates; tagline is correct.

### PR-4: Typography sweep

**Goal:** All headings/questions/buttons use Rubik 900 (or appropriate weight). Body copy uses Nunito.

- In `style.css`:
  - `.app-title`, `.screen-header h2`, `#question-text`, `.winner-display` → `font-family: var(--font-logo); font-weight: 900;`
  - `.btn`, `.answer-btn` → `font-family: var(--font-logo); font-weight: 700;`
  - `.app-tagline`, `.form-group input`, `.form-group textarea`, paragraph text → `font-family: var(--font-body);` (Nunito)
  - `.room-code-display`, `#question-progress`, `.player-entry .player-score`, `.score-val` → `font-family: var(--font-mono);`
- Delete or replace any `font-family: var(--font-main)` references (that was Fredoka One). Use `--font-logo` for headings.

**Acceptance:** No Fredoka anywhere. Open each screen and confirm Rubik renders. Run `[...document.querySelectorAll('*')].map(el => getComputedStyle(el).fontFamily).filter(f => f.includes('Fredoka')).length === 0`.

### PR-5: Bang rack component (scoring indicator)

**Goal:** Replace the dead `.wedge-board` 3x2 grid with a 6-slot bang rack. Slots fill with category color + italic ! when a wedge/round is won.

- Create `app/static/css/bang-rack.css` (or merge into `bang.css`). Use the `.miniSlot` / `.bangSlot` CSS from `Bang Logo Studies.html` and `Obsessed v9 - Bang Refined.html`.
- Create `app/static/js/components/bang-rack.js`:
  ```js
  // BangRack.attach(hostEl, { categories: ['history','entertainment',...] })
  // .fill(category, { burst: true })   // animate the slot
  // .setFills([cat1, cat2, ...])       // bulk set, no anim
  // .clear()
  // .isComplete()                       // → bool
  ```
- In `index.html`, replace `<div id="wedge-board" class="wedge-board">` with `<div id="bang-rack"></div>`. Attach BangRack on screen enter.
- For Phase 1 only: fill one slot per **correct answer** (this is the temporary behavior; Phase 2 changes it to per-round-won).

**Acceptance:** Get a correct answer → a slot in the rack lights up with the category color and an italic !. Six correct answers in a row fills the rack.

### PR-6: Profile-card and form polish

**Goal:** Bring the profile editor up to the new visual standard without rewriting it.

- Restyle `.profile-card` with the new token-driven shadows, radii, spacing (`--radius-lg`, `--shadow-card`, `--space-5`).
- Replace the `⚠ GUEST CONSENT REQUIRED` inline-styled span with a proper `.badge.badge--danger` class.
- Replace the `✓ CONSENT` span with `.badge.badge--success`.
- Form inputs: bump padding to `--space-4`, border-radius to `--radius-md`, focus border to `--pink`. (These mostly already match — verify.)

**Acceptance:** Profile cards look like clean cards, not raw HTML. Form looks like the screenshots in `Obsessed v9 - Bang Refined.html`.

### PR-7: Question / answer screen polish

**Goal:** The game screen looks like the player view in `Obsessed v9 - Bang Refined.html` (`d4__phone` mock).

- Wrap the question in a white card: `background: var(--white); color: var(--text-on-light); border-radius: var(--radius-lg); border-left: 8px solid var(--cat-COLOR); padding: var(--space-5);`
- Category badge above question: small caps, mono, in category color.
- Answer tiles: chunky bottom shadow (`--shadow-tile`), white-on-surface, no border.
- Timer bar: thicker (12px), with category color fill that shifts to `--timer-warn` then `--timer-crit`.
- Show the BangRack at the top of this screen (above the category badge) so the player sees their progress.

**Acceptance:** Side-by-side compare to the mock in `Obsessed v9` — should match within 90%.

### PR-8: Results screen polish

**Goal:** Winner card + final scores look celebratory.

- Winner card: big Bang lockup with the winner's bang count, score, and name.
- Confetti: pure-CSS burst on results screen mount, throttled to `--z-confetti` z-layer, capped at 60 particles, respects reduced-motion.

**Acceptance:** End a game → winner card lands with a pop, confetti fires, scores stagger in.

---

## 5. Phase 1 — file map after merge

```
app/static/
├── index.html                    [MODIFIED — uses bang lockup, new markup]
├── css/
│   ├── tokens.css                [NEW]
│   ├── bang.css                  [NEW]
│   ├── bang-rack.css             [NEW]
│   └── style.css                 [MODIFIED — typography swept; old wedge styles removed]
└── js/
    ├── app.js                    [MODIFIED — attaches BangRack on game screen]
    └── components/
        ├── bang.js               [NEW]
        └── bang-rack.js          [NEW]
```

That's it for Phase 1. Eight PRs, no backend changes. Should land in one sprint.

---

## 6. Phase 2 — rounds + host TV

**Backend (1 PR):**
- `app/routes/games.py`:
  - Add `round_count` to `GameCreate` model (default 3). Game splits its question pool into N rounds of 5 questions, one category per round.
  - On `start_game` and on each round transition, emit `round_start` `{round_num, total_rounds, category, category_label}`.
  - On the last question of each round (after answer reveal), emit `round_end` `{round_num, total_rounds, category, winner: {...}, scores: [...]}`.
  - On every `submit_answer` call, emit `player_answered` `{player_id}` BEFORE the existing `answer_result` reveal.
  - Add `POST /api/games/{room}/abort`.

**Frontend (2 PRs):**
- New screen controller `GameHost` (`app/static/js/screens/game-host.js`) + `app/static/host.html`. Boots from `?room=XK7T2P` or `/host/XK7T2P`. Uses the Bang lockup at hero scale (variation H) and a giant BangRack.
- `GamePlayer` updates: on `round_end`, fill the round-winner's bang slot (instead of filling one per correct answer).
- FastAPI route in `main.py`:
  ```python
  @app.get("/host.html")
  @app.get("/host/{room}")
  async def host_view(room: str = ""):
      return FileResponse("app/static/host.html")
  ```

**Acceptance:** Start a 3-round game on phones; open `/host/<code>` on the TV; see the host view show pre-round card, live question, "X locked in" indicators, then round-end celebration with the winning bang flying into the rack.

---

## 7. Specific gotchas the coding agent will hit

1. **The ! is optically off-center** without `translateY(Npx)` correction. The italic+skew shifts the glyph. Use the exact transform values from `Bang Logo Variations.html` for each variation size (3px for 74px circle, 6px for 120px circle, 7px for 160px+).

2. **The "O" in OBSESSED must be a zero-width ghost** (`<span class="ghost-o">O</span>` with `width: 0; opacity: 0;`) so the wordmark spacing stays correct when the ! takes its place. Don't just delete the O — that throws kerning off.

3. **Letter-spacing is `-0.04em`** on the wordmark. Don't ship at 0.

4. **`prefers-reduced-motion`** must disable: the bang pulsation, the rack slot pop, confetti, screen shake. Use the `@media` block already drafted at the bottom of `tokens.css`.

5. **The repo's existing `.wedge-board` markup in `index.html`** is dead code (the CSS renders empty colored circles). Delete the markup, don't try to migrate it.

6. **Don't touch `app/services/scraper/*`, `app/database.py`, or `app/models.py`** for this work — none of it affects the UI.

7. **The handoff says "Discord was removed because there's no Discord scraper"** — confirmed by reading `app/static/index.html`, which still has a Discord field. The form has BOTH `discord_handle` AND the missing scraper. Leave the field for now (Phase 3 cleanup).

---

## 8. How to hand this off to your coding agent

**Option A — Claude Code (recommended):**

1. `git clone phattbeats/obsessed` locally (if not already).
2. Drop this `CODING-AGENT-HANDOFF.md` into the repo root.
3. Create a `design/` folder in the repo and copy these files in:
   - `LOGO-DECISION.md`
   - `Bang Logo Variations.html`
   - `Bang Logo Studies.html`
   - `Obsessed v9 - Bang Refined.html`
4. Add `design/` to `.gitignore` so it doesn't ship to prod (or commit it — your call; it's useful reference).
5. Open Claude Code in the repo and paste:

   > Read `CODING-AGENT-HANDOFF.md` and the files in `design/`. Implement Phase 1, PR-1 through PR-8, in order. Open each as a separate branch and PR. Stop after PR-8 and report back. Do NOT start Phase 2 until I review Phase 1.

6. Review PRs as they come in.

**Option B — manual:**

1. Same as above (drop the doc + design files).
2. Work through PR-1 through PR-8 yourself, branch by branch.
3. Each PR has explicit acceptance criteria in §4 above — use them.

**Option C — async via GitHub Copilot Workspace / similar:**

1. Open an issue per PR (PR-1 through PR-8). Paste the relevant subsection of §4 as the issue body.
2. Assign each to your agent.
3. Review as they finish.

---

## 9. What to download from this project right now

To get this off the rails and into the repo:

1. `CODING-AGENT-HANDOFF.md` (this file)
2. `LOGO-DECISION.md`
3. `Bang Logo Variations.html`
4. `Bang Logo Studies.html`
5. `Obsessed v9 - Bang Refined.html`

The download card in the chat below this message will bundle them.

---

**Built across passes 1–9. The Bang is locked. Ship it.**

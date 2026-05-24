# Phase 1 — PR Decomposition

Eight PRs, opened in order, each its own branch + GitHub PR. Branches stack (each cut
from the previous). Derived from `CLAUDE_DESIGN_PROMPT.md` §4–§8. Scope notes call out
exactly which files each PR may touch, so reviews stay tight.

---

## PR-1 — Design tokens + CSS architecture foundation
**Branch:** `pha-801/pr-1-foundation` · **Brief:** §4.1, §7 pass 1a, §6
**Files:** `app/static/css/style.css`, `CODING-AGENT-HANDOFF.md`, `design/`

Purely additive — establishes the vocabulary every later PR consumes. No screen should
change appearance yet.

- Extend `:root` with the full §4.1 token set: spacing (base-8), radii, borders,
  elevation/shadows, motion easings + durations, type scale, z-layers. Keep all
  existing color/font tokens untouched.
- Add the global `prefers-reduced-motion: reduce` scaffold (opacity-only fallback hook).
- Add the `.dot-grid-bg` texture utility (SPEC §2) as a reusable background class.
- Add component table-of-contents section headers to `style.css` so the file reads like
  a component index.

**Done when:** tokens resolve, app boots, every existing screen looks identical.

---

## PR-2 — Wedge component + category icons  *(the brand — hard rule)*
**Branch:** `pha-801/pr-2-wedge` · **Brief:** §4.2, §4.5, §5.3, §7 pass 1b
**Files:** `style.css`, new `css/wedge.css` (optional), `index.html` (lobby wedge),
`js/app.js` (wedge render helper)

- Real **SVG pie wedge** (not 6 CSS circles): `.wedge` in `--sm` (48), `--md` (200),
  `--lg` (400), `--inline` (24) sizes. Per-category fillable slices.
- Fill animation: slice fills center-out over ~400ms with `--ease-bounce`; particle
  burst from centroid. Replaces the current `#wedge-board` 3×2 grid on the lobby.
- 6 inline-SVG category icons ("sharpie on whiteboard" style) at 24/32/64px + a legend.

**Done when:** the lobby wedge "looks like the cover of a board-game box" and renders
crisp at all four sizes.

---

## PR-3 — Home cold-open + lobby + confetti
**Branch:** `pha-801/pr-3-home-lobby` · **Brief:** §5.1, §5.3, §4.3, §7 pass 2
**Files:** `index.html`, `style.css`, `js/app.js`

- Home: oversized OBSESSED wordmark, tagline, two huge CTAs (New Game primary / Join
  ghost), dot-grid bg, corner wedge silhouette; returning-visitor mini-card.
- Lobby: 96px JetBrains-Mono room code in a thick card + Copy/QR; `player-bubble`
  avatars popping in on join; Start button pulses at ≥2 players; guest-profile card.
- CSS-keyframe confetti system (60 particles, 6 category colors, ~3s, no canvas/lib).

---

## PR-4 — Game screen (player view)
**Branch:** `pha-801/pr-4-game-player` · **Brief:** §5.4, §4.3, §7 pass 3
**Files:** `index.html`, `style.css`, `js/app.js` (motion hooks only — no WS changes)

- Sticky top bar (name + `wedge--inline` + score pill); animated category badge.
- Question card: slide-up 24px + fade + 0.96→1 scale, `--ease-overshoot`; badge leads,
  question, then answers stagger 50ms.
- `answer-tile` states: default/selected/correct/wrong/disabled, ≥56px tap target.
- Correct: green flash + tile pulse + star-burst + digit-roll score. Wrong: red flash +
  screen shake + correct-answer emphasis. Timer bar color-shifts ok→warn→crit + pulse.

---

## PR-5 — Host / TV view  *(biggest new piece)*
**Branch:** `pha-801/pr-5-host-view` · **Brief:** §5.5, §4.4, §7 pass 4
**Files:** new `app/static/host.html`, `style.css`/`css/`, `js/`, **+ one route in
`app/main.py`** (`GET /host` → `FileResponse(host.html)`)

- Top stripe (wordmark / profile / room code); centered question column at 48px;
  right sidebar player stack ranked with soft rank-change slides; bottom full-width
  timer + 6-icon wedge legend; `wedge--lg` hero on lobby/between-rounds, shrinks to
  corner during a live question.
- Fully keyboard-driven: Space advance, 1–4 highlight, Esc abort. Player WebSocket
  events drive host-side animation (bubble pulses green/red).

---

## PR-6 — Results celebration
**Branch:** `pha-801/pr-6-results` · **Brief:** §5.6, §4.3, §7 pass 5
**Files:** `index.html`, `style.css`, `js/app.js`

- Immediate confetti; centered winner card (name, score, filled wedge) scaling up with
  overshoot; ranked per-player breakdown rows (wedges-by-category, accuracy %,
  fastest-answer count) staggering in 80ms apart; Rematch primary button; optional
  "the one that got you" hardest-question callout. States: standard / perfect / tie.

---

## PR-7 — Profile editor restyle + public-records wiring
**Branch:** `pha-801/pr-7-profile` · **Brief:** §5.2, §6, §7 pass 6
**Files:** `index.html`, `style.css`, `js/app.js`

- Entity-type switcher → chunky 4-tab pill row with emoji icons.
- Grouped thick-bordered cards: Identity → Platforms → **Public Records** → Manual
  Facts. Platform inputs get a leading platform icon + status chip (idle/scraping/
  done/failed). Wire the Public-Records section to the dormant
  news/court/sos/auditor scaffolding (PHA-231/232/233/234) as real fields.
- Post-save question-preview drawer slides up with first 5 generated questions,
  color-coded by category. Submit label swaps Save → Saving… → Saved ✓ without shift.
- Fix the pre-existing `var(--font-heading)` reference in the things-modal (no such
  token — should be `--font-main`).

---

## PR-8 — Polish pass
**Branch:** `pha-801/pr-8-polish` · **Brief:** §4.4, §4.5, §5.8, §5.9, §7 pass 7, §8
**Files:** all `app/static/*` as needed

- Full `prefers-reduced-motion` audit (collapse all motion to opacity-only).
- `:focus-visible` ring (3px `--accent`, 2px offset) on every interactive element;
  never bare `outline: none`.
- WCAG-AA contrast audit (bright category colors get `--bg` foreground, not white).
- Font preload (Fredoka One 400 + Nunito 700); retire remaining long inline `style=`.
- Settings restyle (pulsing status dots, `--danger` on Clear-cache); admin token
  refresh (lower polish, inherit tokens).
- Target Lighthouse a11y ≥ 95 / perf ≥ 85 on `/` and `/host`.

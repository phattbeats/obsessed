# CLAUDE — Obsessed UI Design Brief

> Paste this entire file into a fresh Claude session as the kickoff message. It is a complete brief plus the design-engineering rules of the road. Everything you need to ship a high-end party-game UI is in here or one click away (linked files at the bottom).

---

## 0. Your Role

You are a **senior product designer–engineer**. Game UI is your home turf — you have shipped Jack Box-style party games, you know what Kahoot got right and where Hot Seat copies the homework. You write production CSS like a typographer and motion like an animator.

You are not a "vibe coder." You think in **design tokens, component contracts, and motion choreography**. You make decisions, then defend them in plain English. You never hand back a half-built screen with a TODO. If a decision is genuinely a coin flip, you pick one, ship it, and note the alternative — you do not stop to ask.

You are working inside an existing repo. The backend, database, scrapers, WebSocket protocol, and Python routes are **out of scope**. Touch them only to add a static-file route. If a UI change implies a backend change, write a follow-up note instead.

---

## 1. The Product in One Paragraph

**Obsessed** is a hyper-personal trivia game. Players gather around a TV or laptop and answer trivia about a real person (or place, thing, event) whose social-media exhaust — Reddit comments, tweets, Steam library, Wikipedia entries, public records — has been scraped and turned into question banks. The hook is the unpredictability: nobody knows what the scraper pulled. The mood is *house party on a big screen*: laughing with friends, "wait, they posted **WHAT**?", a little mean, very funny, slightly unhinged. It is **not** a corporate quiz tool.

Two roles, two devices:
- **Host / TV view** — large screen, theatrical. Reveals the question, drives the round.
- **Player / phone view** — small screen, tap-to-answer. Anonymous bigness is the wrong instinct here; this is intimate and fast.

The brand object is the **Trivial Pursuit wedge** — a circle divided into 6 colored pie slices. Filling it is the win condition and the persistent visual identity. Treat it like Spotify treats the album-art rectangle: it shows up everywhere, in every size, and never gets distorted.

---

## 2. The Job

Take the existing functional but **visually undercooked** front-end and rebuild it to match the design language in `SPEC.md` §2 — Jack Box × Trivial Pursuit energy. Cover every screen in the user journey. Build a coherent design system so future screens come cheap. Wire the motion so the game **feels** chaotic and celebratory, not corporate and quiet.

**You are shipping the front-end, not a Figma file.** Real HTML, CSS, and vanilla JS, running against the existing FastAPI app.

---

## 3. Current State (what exists)

Frontend lives entirely in `app/static/`:

```
app/static/
├── index.html          ← single-page, all screens are sibling divs toggled by .active
├── admin.html          ← ops console (cache, games, profiles)
├── css/style.css       ← ~410 lines, tokens already defined in :root
└── js/app.js           ← ~486 lines, vanilla JS, WebSocket client, screen router
```

**What works (do not break):**
- Screen router via `showScreen(name)` in `app.js`.
- WebSocket client and reconnect logic.
- API integration: `/api/profiles`, `/api/games`, `/ws/{room}/{player}`.
- Admin gate via `Authorization: Bearer ${ADMIN_TOKEN}` for `/api/admin/*`.
- Entity-type form (person / place / thing / event) — adapts the scrape fields.

**What's undercooked (your work):**
- **Wedge board** is currently a 3×2 grid of `border-radius: 50%` circles. It must be a real **pie chart with 6 wedges**, fillable per category, animated on fill. This is the brand. Get it right.
- **Home screen** is a stack of buttons. It should look like the cold-open of a game show.
- **Question reveal** is static text dropping into place. It should slide up with overshoot and a category-color sweep.
- **No host/TV view** exists — everything assumes a single device. Build a `/host` route (or `?view=host` flag) with the large-format layout from SPEC.md §3.
- **No motion** — no confetti, no screen shake, no wedge-fill spring, no timer pulse. Spec calls for all of it.
- **No category iconography** — category badges are plain pills. Need 6 inline-SVG icons in the consistent "sharpie on whiteboard" style.
- **Profile editor** is a wall of form inputs. It needs grouping, platform iconography, scrape-status states with personality, and an inline question preview.
- **Results screen** is a list of scores. It should be a full-screen celebration: confetti, winner card scale-up, wedge boards side-by-side.

**Tokens that already exist in `:root`** (extend, don't replace):

```css
--bg, --surface, --surface-elevated, --border, --white, --text-secondary,
--correct, --wrong, --timer-warn, --timer-crit, --accent,
--cat-history, --cat-entertainment, --cat-geography,
--cat-science, --cat-sports, --cat-art,
--font-main (Fredoka One), --font-body (Nunito), --font-mono (JetBrains Mono)
```

---

## 4. Design System Contract

### 4.1 Tokens (extend `:root` — do not introduce a CSS-in-JS layer)

Add the following tokens explicitly. **Every** new style references tokens — no magic numbers anywhere except inside the token definitions and inside `@keyframes` keyframes.

```css
:root {
  /* Existing color, font tokens stay. Add: */

  /* Spacing — base 8 */
  --space-1: 4px;
  --space-2: 8px;
  --space-3: 12px;
  --space-4: 16px;
  --space-5: 24px;
  --space-6: 32px;
  --space-7: 48px;
  --space-8: 64px;

  /* Radii */
  --radius-sm: 8px;
  --radius-md: 12px;   /* buttons */
  --radius-lg: 16px;   /* cards */
  --radius-xl: 24px;
  --radius-pill: 999px;

  /* Borders */
  --stroke-thick: 3px; /* brand */
  --stroke-bold:  8px; /* card left-border accent */

  /* Elevation */
  --shadow-card:   0 8px 32px rgba(0, 0, 0, 0.40);
  --shadow-modal:  0 16px 64px rgba(0, 0, 0, 0.60);
  --shadow-press:  0 2px 8px  rgba(0, 0, 0, 0.30);
  --glow-accent:   0 0 24px  rgba(233, 69, 96, 0.45);

  /* Motion */
  --ease-out:      cubic-bezier(0.16, 1, 0.3, 1);     /* expo-out, nice settle */
  --ease-overshoot: cubic-bezier(0.34, 1.56, 0.64, 1); /* card reveal */
  --ease-bounce:   cubic-bezier(0.68, -0.55, 0.27, 1.55);
  --dur-quick:  100ms;
  --dur-fast:   150ms;
  --dur-base:   200ms;
  --dur-slow:   300ms;
  --dur-reveal: 400ms;

  /* Type scale (from SPEC.md §2 Typography) */
  --fs-display:  72px;  /* app name */
  --fs-h1:       48px;  /* room code, winner */
  --fs-question: 36px;  /* responsive: scale to 48px on host view */
  --fs-h2:       28px;  /* timer */
  --fs-h3:       24px;  /* category label */
  --fs-answer:   22px;
  --fs-body:     16px;
  --fs-caption:  13px;
  --fs-micro:    11px;

  /* Z layers */
  --z-base: 0;
  --z-card: 10;
  --z-modal: 100;
  --z-toast: 200;
  --z-confetti: 300;
}
```

### 4.2 Component naming

Use a flat, **BEM-lite** convention: `block`, `block__element`, `block--modifier`. No utility-class soup. The CSS file should read like a component table of contents.

```
/* ── Wedge board ─────────────────────────────────────── */
.wedge { ... }
.wedge__slice { ... }
.wedge__slice--filled { ... }
.wedge--lg { ... }   /* TV/host scale */
```

Components you must produce (the inventory — do not skip any):

| Component | Variants | Where it appears |
|---|---|---|
| `wedge` | `--sm` (48px), `--md` (200px), `--lg` (400px), `--inline` (24px chip) | Player top bar, lobby, game, results, profile preview |
| `category-icon` | one per category (6 SVGs, 24/32/64 px) | Category badge, scoreboard, wedge legend |
| `category-badge` | one per category color | Question screen, profile cards |
| `btn` | `--primary`, `--ghost`, `--danger`, `--icon` | Everywhere |
| `card` | `--question`, `--profile`, `--player`, `--result` | Game, lobby, results |
| `answer-tile` | default, `--correct`, `--wrong`, `--selected`, `--disabled` | Game screen |
| `timer-bar` | `--ok`, `--warn`, `--crit` (color shifts at thresholds) | Game |
| `score-pill` | with rolling-digit animation | Game, results |
| `player-bubble` | initials avatar in category color | Lobby, host scoreboard |
| `room-code` | display variant + share variant | Lobby |
| `confetti` | particle system, category colors | Results, wedge-complete |
| `toast` | (already exists, restyle) | Global |
| `modal` | (the things-builder + future) | Profile + game setup |
| `screen-header` | back button + title | All non-home screens |
| `dot-grid-bg` | the subtle texture from SPEC §2 | All major screens |

### 4.3 Motion choreography

Motion is not decoration. It is **causality**. Every transition must answer the question "what just happened?"

Use the canonical mappings from `SPEC.md §2 Motion` as the contract. The non-obvious choreography points:

- **Question reveal**: slide up 24px + fade in + scale 0.96→1.0, with `--ease-overshoot`, `--dur-reveal`. The category badge enters first (50ms head start), then the question, then the answers stagger 50ms apart.
- **Correct answer**: full-screen green flash overlay at 0.2 opacity for 150ms; selected tile pulses scale 1.02→1.06→1.02; star-burst SVG explodes from tile center; score counter increments digit-by-digit over 250ms.
- **Wrong answer**: full-screen red flash 0.2 opacity 100ms; **screen shake** (`transform: translate3d(±2px, ±2px, 0)` jitter 3 cycles over 200ms); correct answer fades into bright green-bordered emphasis after wrong reveal.
- **Wedge fill**: the wedge slice fills from center outward — implement as an SVG `<path>` whose `stroke-dasharray` animates, OR a `clip-path: polygon` interpolation, OR a `conic-gradient` mask reveal. Pick one; pick the one that gives the cleanest 400ms spring with `--ease-bounce`. Particle burst from the centroid.
- **Timer pulse**: every second in final 5s, scale 1.0→1.08→1.0 over 200ms. At t=0, full-bar shake + flash.
- **Confetti**: 60 particles, each a 6×16px rounded rect tumbling on a 2D arc with rotation, in the 6 category colors. Lifespan ~3s. CSS keyframes, not JS — keep main thread free. Triggered on wedge complete + game win.

Respect `prefers-reduced-motion: reduce` — collapse all motion to opacity-only transitions, no transforms, no shakes, no confetti.

### 4.4 Accessibility

- **Contrast**: every text-on-color combination must hit WCAG AA (4.5:1 for body, 3:1 for large). Some category colors (yellow `#ffea00`) are bright — when used as a background, the foreground must be `--bg` not `--white`. Verify with the eyedropper, not by vibe.
- **Focus**: every interactive element gets a visible `:focus-visible` ring — 3px solid `--accent`, offset 2px. Never `outline: none` without a replacement.
- **Keyboard**: the host dashboard must be drivable from the keyboard alone — `Space` to advance, `1–4` to highlight answers in preview, `Esc` to abort.
- **Screen reader**: question and timer states announced via `aria-live="polite"`; answer-result via `aria-live="assertive"`. Category icons are decorative (`aria-hidden="true"`) — text label carries meaning.
- **Tap targets**: all answer tiles and game controls ≥ 48×48 px on mobile.

### 4.5 Performance

- Inline critical CSS for first paint **only if** doing so costs nothing in maintainability. Otherwise keep `style.css` linked.
- Preload the two Google Fonts that render above the fold: Fredoka One 400 + Nunito 700.
- All animations use `transform` and `opacity` only — never `width`, `top`, `left` for moving things.
- Confetti particles count cap = 60. SVG, not canvas (no canvas dep is needed).
- The wedge board renders as SVG, not 6 CSS-shaped divs. One file, reusable, scales freely.

---

## 5. Screen-by-Screen Brief

For each screen below: **purpose → must-have visual moves → states → interactions**.

### 5.1 Landing (`#screen-home`)

- **Purpose**: cold-open. Set the tone in 2 seconds.
- **Must-have**: oversized **OBSESSED** wordmark (Fredoka One 72px+, possibly with a gradient stroke or a wedge-board emblem to the right of it). Tagline below. Two huge CTAs — **New Game** (primary, hot pink) and **Join Game** (ghost, white outline). Dot-grid background. A subtle wedge silhouette in the bottom-right corner as art.
- **States**: empty (first visit), returning (show last guest + last winner mini-card).
- **Interactions**: hover scales 1.03 with shadow lift; click presses 0.97.

### 5.2 Profile editor (`#screen-profile`)

- **Purpose**: build the question source — fast, forgiving, with personality.
- **Must-have**: the entity-type switcher becomes a **tab row** (4 chunky pills with emoji icons), not a dropdown. Form sections grouped with thick-bordered cards: **Identity** → **Platforms** → **Public Records** → **Manual Facts**. Each platform input has a leading platform icon and a status chip on the right (`idle`, `scraping`, `done`, `failed`). After save: question preview drawer slides up from the bottom with the first 5 generated questions, color-coded by category.
- **States**: blank form, saved-with-zero-questions, generating, ready-to-play.
- **Interactions**: focus state on inputs lifts the border to `--border`; submit button shows a Fredoka One label that swaps "Save Profile" → "Saving…" → "Saved ✓" without layout shift.

### 5.3 Game setup / lobby (`#screen-lobby`)

- **Purpose**: make waiting fun.
- **Must-have**: room code displayed at 96px in JetBrains Mono inside a thick-bordered card, with a **Copy** and a **QR** button. Player list as `player-bubble` avatars (initials in category color), bouncing in on join. Host sees a **Start Game** primary button that pulses gently when ≥2 players. The selected guest profile shows as a small card up top — avatar + name + question-pool count.
- **States**: 0 players, 1+, host-ready, players-ready.
- **Interactions**: player avatar pops in with scale 0→1.1→1 over 300ms with bounce easing.

### 5.4 Game — player view (mobile)

- **Purpose**: tap to answer, feel the chaos.
- **Layout** (top-to-bottom, full viewport):
  1. **Top bar** (sticky): player name + `wedge--inline` + score pill.
  2. **Category badge** (centered, animates on each new question).
  3. **Question card** (white bg, 8px category-color left border, dark text, slide-up entry).
  4. **Answer grid** 2×2 (or stacked on narrow screens). Each `answer-tile`: thick outline, font-body 700 22px, hover scale 1.02, tap press 0.97, tap-target ≥ 56px tall.
  5. **Timer bar** (bottom, full-width, 12px tall, color-shifting).
- **States**: waiting-for-question, question-active, answered-locked, revealing-correct, revealing-wrong, between-questions.
- **Interactions**: after tap, lock the grid; show subtle "Waiting for reveal…" caption; on reveal, animate correct/wrong as specified in §4.3.

### 5.5 Game — host / TV view (NEW — does not exist today)

- **Purpose**: the centerpiece. This is what gets pointed at the TV.
- **Route**: `/host.html` (separate file is cleaner than a query flag).
- **Layout**:
  - **Top stripe**: OBSESSED wordmark left, profile name + avatar center, room code right.
  - **Main column** (centered, max-width 1000px): category badge → question text at 48px → optional supporting illustration (the source-fact teaser, blurred until reveal).
  - **Sidebar (right, 320px)**: vertical player stack — `player-bubble` + name + score, ordered by current rank. Active rank changes animate with a soft slide.
  - **Bottom**: full-width timer bar (16px tall) + 6 wedge legend icons.
  - **The big wedge** (`wedge--lg`, 400px) lives on the **lobby** TV view and on the **between-rounds** state; during a live question it shrinks to the corner so the question is the hero.
- **Interactions**: keyboard-driven (see §4.4). All player-side WebSocket events also drive host-side animation — e.g., a player answering shows their bubble pulse green/red on the sidebar.

### 5.6 Results (`#screen-results`)

- **Purpose**: catharsis.
- **Must-have**: confetti immediate. Winner card centered with name, final score, wedge board filled. Below: per-player breakdown in ranked rows — each row shows wedges-by-category, accuracy %, fastest-answer count. A **Rematch** primary button at the bottom. Optionally, a "best question" callout — the question with the lowest correct rate, framed as "the one that got you."
- **States**: standard, perfect-game (all 6 wedges), tie-game.
- **Interactions**: row appears stagger 80ms; winner-card scale-up with overshoot.

### 5.7 History / leaderboard (`#screen-history`)

- **Purpose**: bragging rights.
- **Must-have**: split into **Recent games** (cards, scrollable) and **All-time leaderboard** (table). Each game card shows guest avatar, date, winner, podium of top-3, and a tiny wedge mini-board. Leaderboard uses player-bubble avatars with totals.

### 5.8 Settings (`#screen-settings`)

- **Purpose**: ops surface, but should not feel like an admin panel.
- **Must-have**: keep the current grouped cards (App / LiteLLM / Crawl4AI / Game defaults / Entity cache) but restyle them with the new tokens. Status dots become small pulsing indicators. **Danger** buttons (`Clear cache`) get a `--danger` modifier — red outline, requires a `confirm()` already.

### 5.9 Admin (`/admin.html`)

- **Purpose**: power-user ops. Lower polish bar than the player-facing screens. Inherit tokens, but it can stay information-dense.

---

## 6. Technical Constraints

- **Stack**: vanilla HTML / CSS / JS only. No React, Vue, Svelte, Astro, Tailwind, Sass, build step. The existing pattern is single HTML files with `<script>` blocks and `style.css`; preserve it. Splitting per-screen HTML files is **encouraged** when it lowers cognitive load (host view definitely deserves its own file).
- **Module split**: when `app.js` exceeds ~700 lines, split by concern: `ws.js`, `game.js`, `profile.js`, `motion.js`. Use plain `<script>` imports in order; no ES module loader.
- **CSS architecture**: one `style.css` is fine until ~800 lines. Beyond that, split by component into `css/tokens.css`, `css/wedge.css`, `css/game.css`, etc., and `@import` them. **Do not** ship `<style>` blocks scattered across HTML files (the current inline modal styles in `index.html` should be moved into `style.css`).
- **API surface is fixed**: every route in `README.md §API Routes` stays. If you genuinely need a new endpoint, write a `// NEEDS BACKEND:` comment with the proposed contract and surface it in your handoff note — do not invent a fetch URL.
- **WebSocket events** in `app/websocket.py` are fixed: `player_joined`, `game_started`, `new_question`, `answer_result`, `question_advance`, `game_over`. Build the UI around what the server actually emits. If you need a new event, same rule as endpoints.
- **The repo has dormant features** — `news.py`, `court.py`, `sos.py`, `auditor.py` are intentional scaffolding for PHA-231/232/233/234 and need UI **wiring**, not removal. Treat the public-records section in the profile editor as a real feature, not vestigial.
- **Static-file serving**: `app/main.py` mounts `app/static/` at `/static` and serves `index.html` at `/`. To add a new top-level page like `/host`, add a FastAPI route that returns the new HTML file. That is the only Python you should touch.

---

## 7. Build Sequence

Do not try to ship everything in one pass. Sequence:

1. **Pass 1 — Token foundation + wedge component.** Extend `:root` with §4.1 tokens. Build the SVG wedge component in all 4 sizes. Add the 6 SVG category icons. **Hard rule:** by end of pass 1, the wedge board on the lobby screen looks like the cover of a board-game box. Everything else can stay ugly.
2. **Pass 2 — Home + lobby restyle.** Apply the new system to the entry screens. Wire confetti and the player-bubble pop-in.
3. **Pass 3 — Game screen (player view).** Question card slide-up, answer-tile motion, timer color shift, correct/wrong feedback. This is the most-watched surface; spend the time.
4. **Pass 4 — Host / TV view.** New `host.html`. This is the biggest single piece of new work.
5. **Pass 5 — Results.** Confetti, winner card, ranked breakdown.
6. **Pass 6 — Profile editor restyle.** Tabs, platform iconography, status chips, preview drawer.
7. **Pass 7 — Polish.** Reduced-motion pass, focus-visible everywhere, contrast audit, font preload, admin/settings refresh.

After each pass, run the app and **screenshot the changed screens** so the next pass starts from a real baseline.

---

## 8. Acceptance Criteria

You are done with a pass when:

- [ ] No magic numbers in CSS outside `:root` and `@keyframes` keyframes.
- [ ] Every interactive element has hover, focus-visible, active, and disabled states.
- [ ] Every motion respects `prefers-reduced-motion: reduce`.
- [ ] No inline `style="…"` longer than 40 characters in any HTML file. Move it to CSS.
- [ ] Wedge component renders correctly at `--sm`, `--md`, `--lg`, and `--inline`.
- [ ] App boots (`uvicorn app.main:app --port 8000`) and the existing happy path (create profile → start game → answer question → see results) still works end-to-end against the real API.
- [ ] Lighthouse on `/` and `/host`: a11y ≥ 95, performance ≥ 85.

You are **not** done if:
- The page works but feels corporate.
- The wedge looks like a 6-grid of circles.
- The game screen has zero motion.
- Confetti uses canvas or a library.

---

## 9. Inspiration & Anti-patterns

**Look at:**
- Jackbox Party Pack — the title-card energy, the answer-reveal sound design, the host/audience asymmetry. Capture the **visual** equivalent of their sound.
- Kahoot's category color blocks during answer reveal — they own the "saturated block of color is the entire screen" move.
- Trivial Pursuit's original wedge board — the proportions, the segment colors, the gap between segments.
- Sporcle / LearnedLeague for **type-driven** trivia screens, but as a counter-example: they are *too quiet*. We want louder.
- Apple Arcade's marketing pages for hero typography paired with playful illustration.

**Anti-patterns — explicitly avoid:**
- **Glassmorphism / frosted blur.** Wrong era, wrong mood. We are flat-and-thick-bordered, not soft-and-translucent.
- **Neon glow on everything.** One accent glow is brand; ten is a casino.
- **Gradient text on every header.** Reserve gradient text for the wordmark only.
- **Subtle.** The whole reason this UI is being rebuilt is because the current version is too subtle. Bigger, louder, more.
- **Generic SaaS button-with-spinner loading states.** When something is loading, do something **with personality** — a scraping animation that shows little Reddit/Twitter/Wikipedia icons spinning in.
- **Carousel anything.** No carousels.

---

## 10. Your First Move

When you receive this brief, your **first response** should be — in order:

1. A one-paragraph summary of the design north star, in your own words. (Proves you got it.)
2. A short list of **at most 3** clarifying questions, only if genuinely ambiguous. If none, say so and move on.
3. The pass-1 plan in 5–10 bullets, with the **first 3 file diffs** you will write to extend `:root` and stand up the SVG wedge component.

Then start writing code. Do not stop at the plan. Pass 1 should be complete and visually verifiable before you hand the conversation back.

---

## 11. Reference Files (read these now, in order)

1. **`SPEC.md`** — the design language section (§2) is non-negotiable; the rest is context.
2. **`README.md`** — API and WebSocket contract.
3. **`app/static/index.html`** + **`app/static/css/style.css`** + **`app/static/js/app.js`** — current state. Note especially the inline modal styles in `index.html` and the inline `style="…"` attributes that need to be retired.
4. **`app/static/admin.html`** — the lower-polish surface; inherit tokens but lower priority.
5. **`app/main.py`** — to understand static-file mounting and add a `/host` route.
6. **`app/websocket.py`** — to know what events drive the live game UI.
7. **`app/routes/games.py`** — to confirm payload shapes for `new_question`, `answer_result`, `game_over`.

When in doubt, **read the code, not your assumptions about it.**

---

## 12. Boundaries

You may freely:
- Edit anything in `app/static/`.
- Add new HTML files in `app/static/` and add corresponding `GET` routes in `app/main.py` returning `FileResponse`.
- Add font preloads, meta tags, favicons, manifest.json.
- Split CSS and JS into multiple files.

You must not:
- Touch `app/database.py`, `app/models.py`, `app/services/`, or any scraper.
- Modify the WebSocket event names or shapes.
- Change `/api/*` routes or their request/response schemas.
- Introduce a build step, framework, package manager, or `node_modules/`.
- Add tracking, analytics, third-party CDN scripts, or any external JS dependency. (Google Fonts is the only allowed external dep.)
- Remove or "clean up" the dormant route files (`news.py`, `court.py`, `sos.py`, `auditor.py`) — they are scaffolding for PHA-231/232/233/234.

---

**That's the brief. Go.**

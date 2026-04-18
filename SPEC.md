# Obsessed — Hyper-Personal Trivia Platform

## 1. Concept & Vision

Obsessed is a hyper-personal trivia game that scrapes real social media profiles to build question banks about real people. Players compete to answer questions about the guest of honor — someone whose online presence (Reddit comments, tweets, likes, posts) becomes the source material. The unpredictability is the hook: nobody knows what the scraper pulled, from a three-year-old obscure Reddit comment to the specific way someone described their job in a bio.

The tone is sharp, a little mean, and very funny. It's "How well do you actually know your friends?" distilled into a machine that remembers everything they've ever posted.

---

## 2. Design Language

**Aesthetic:** Jack Box Party Pack meets Trivial Pursuit — bold, punchy, party-game energy. Thick outlines, illustrated category icons, saturated blocks of color, big readable type. The visual personality of a game that's fun and slightly unhinged, not corporate.

**Mood:** House party on a big screen. Laughing with friends. Everyone crowded around one device. "Wait, they posted WHAT?"

---

### Color Palette

**Backgrounds & Surfaces**
- Background: `#1a1a2e` (deep navy, not pure black — softer, warmer)
- Surface: `#16213e` (slightly lighter navy card backgrounds)
- Surface elevated: `#0f3460` (modals, dropdowns)
- Border/subtle: `#e94560` (hot pink border accent — brand edge color)
- White: `#ffffff`

**Text**
- Primary: `#ffffff`
- Secondary: `#b8b8d1` (cool muted lavender-grey)
- Dark text on light: `#1a1a2e`

**Feedback Colors**
- Correct: `#00e676` (bright green, saturated)
- Wrong: `#ff1744` (vivid red)
- Timer warning: `#ff9100` (orange)
- Timer critical: `#ff3d00` (deep orange-red)
- Neutral accent: `#ffeb3b` (warm yellow — highlight, spotlight)

**Category Colors** (the core Trivial Pursuit wedge system — these are the brand)
| Category | Color | Hex | Icon |
|---|---|---|---|
| History | Orange | `#ff6d00` | Scroll / calendar / clock |
| Entertainment | Purple | `#d500f9` | Clapperboard / TV |
| Geography | Blue | `#2979ff` | Globe / compass |
| Science | Teal | `#00e676` | Atom / flask |
| Sports | Red | `#ff1744` | Ball / trophy |
| Art & Literature | Yellow | `#ffea00` | Palette / book |

---

### Typography

**Primary Font: Fredoka One** (Google Fonts) — chunky, rounded, bold, playful. Used for:
- App name / logo
- Question text (large, center screen)
- Category names
- Big score numbers
- Button labels

**Secondary Font: Nunito** (Google Fonts) — clean, rounded, highly readable. Used for:
- Body copy, instructions
- Player names
- Answer options
- Timer / small UI text

**Mono: JetBrains Mono** — room codes, technical stats

**Type Scale:**
| Element | Size | Font | Weight |
|---|---|---|---|
| App name | 72px | Fredoka One | 400 |
| Question text | 36–48px | Fredoka One | 400 |
| Category label | 24px | Fredoka One | 400 |
| Score | 64px | Fredoka One | 400 |
| Answer option | 22px | Nunito | 700 |
| Body | 16px | Nunito | 400 |
| Timer | 28px | Fredoka One | 400 |
| Room code | 48px | JetBrains Mono | 700 |

---

### Visual Elements

**Illustrated Category Icons**
Each category has a custom SVG icon in the category color — thick 3px strokes, slightly rounded corners, illustrated/sketch quality. These appear on:
- The scoreboard wedge board
- Category labels on questions
- The "wedge earned" animation
- Profile category stats

Icons are drawn in a consistent hand-crafted style — not pixel-perfect, not clinical. Think sharpie-on-whiteboard energy. Key for brand recognition across all UI surfaces.

**The Wedge Board (Trivial Pursuit Core)**
Persistent element on both host and player views. A circle divided into 6 equal wedges in category colors. When a player earns a wedge in a category, that wedge fills in with the category color (with a satisfying fill animation). First to fill all 6 wins.
- Size: 200px diameter on player view, 400px on host/TV view
- Outline: 3px white stroke
- Empty wedge: `#1a1a2e` fill, category color outline
- Filled wedge: solid category color with subtle radial gradient

**Jack Box Energy Elements**
- **Burst shapes** behind correct answers (starburst / explosion SVG)
- **Confetti particles** on game win (CSS animated, in category colors)
- **Thick outline style** — 3px borders on all cards, buttons, modals
- **Rounded corners** everywhere: `border-radius: 16px` on cards, `12px` on buttons
- **Drop shadows** — not subtle. `0 8px 32px rgba(0,0,0,0.4)` — things float
- **Screen shake** on wrong answer (CSS transform: translate jitter, 200ms)
- **Wiggle animation** on timer when < 5 seconds

**Card Design**
- Question card: white background (`#ffffff`), dark text, thick category-colored left border (8px), slight shadow
- Answer card: category-colored background on hover, scale up 1.02x on hover, press down on click
- Player card: shows name, current wedges mini-board, score

**Background Texture**
- Subtle dot grid pattern on main backgrounds (`radial-gradient` dots at 20% opacity)
- Adds depth without being distracting

**Player Avatars**
- Initials in colored circles (auto-assigned category colors)
- On wedge board: small colored dots around the perimeter

**Host Dashboard (TV View)**
- Large question card center screen
- Live wedge board below question
- Player list sidebar: avatar bubbles with scores, wedge count badges
- Timer bar: full-width, thick (12px), rounded, category-colored fill

**Player Phone View**
- Full-screen question (scrolled to center)
- Wedge board: top of screen, compact (48px), always visible
- Score + player name: top bar
- Answer grid: bottom 60% of screen, large tap targets
- Timer: full-width bar at very bottom

---

### Motion

| Event | Animation |
|---|---|
| Question reveal | Slide up from bottom + fade in, 300ms ease-out, slight overshoot |
| Correct answer | Green flash overlay, burst star SVG, score counter rolls up |
| Wrong answer | Red flash overlay, screen shake (2px jitter, 3 cycles), correct answer highlights |
| Wedge earned | Wedge fills from center outward, 400ms spring, particle burst |
| Timer tick | Pulse scale on each second in final 5s |
| Timer expires | Urgent wiggle, red flash |
| Player joins | Avatar pops in with bounce (scale 0→1.1→1, 300ms) |
| Game win | Confetti rain (CSS keyframes, category colors), winner card scale up |
| Score update | Counter rolls digit by digit, 200ms |
| Answer hover | Scale 1.02x, shadow deepens, 150ms |
| Answer click | Scale 0.97x press, 100ms |
| Card appear | Fade + slight Y translate, staggered 50ms between items |

---

### Spatial System

- Base unit: 8px
- Card padding: 24px
- Section gaps: 32–48px
- Max content width: 960px (centered)
- Border radius standard: 16px
- Border radius buttons: 12px
- Border width: 3px (brand thick outline style)

---

### Logo / App Identity

The wordmark: **"OBSESSED"** in Fredoka One, white, slight letter-spacing. Below it, a small tagline in Nunito: *"How well do you actually know them?"*

No mascot character — the **wedge board** is the visual identity. It appears on every screen. The 6 category colors against the dark navy background is the brand palette.

---

## 3. Layout & Structure

### Pages

**`/` — Landing**
- App name + tagline
- "Host a Game" (creates room) and "Join a Game" (enter room code) buttons
- Recent games list (localStorage)
- About section explaining the concept

**`/host` — Host Dashboard**
- Create/manage game rooms
- See connected players
- Start game, advance questions, see live scoreboard
- Full control over game flow

**`/play/:roomCode` — Player View**
- Join with player name
- See own wedge status, own score
- Current question (with timer)
- Answer on mobile-optimized tap interface

**`/profile` — Profile Manager** (host only)
- Add/edit people profiles
- Enter social handles per platform
- Trigger scrape, see progress
- Preview generated questions
- Re-scrape to update

**`/history` — Game History**
- Past games, winners, scores
- Per-player all-time stats
- Per-profile question quality scores

### Responsive Strategy
- Host view: desktop/tablet-first (mirrored to TV)
- Player view: mobile-first, large tap targets
- Minimal responsive breakpoints: 480px, 768px, 1200px

---

## 4. Features & Interactions

### Profile Management
- Add a person: name, avatar (optional upload or URL)
- Add platform handles: Reddit, Twitter/X, Steam, Discord, Facebook, Instagram, LinkedIn, Mastodon
- "Scrape" button triggers background job (or manual entry)
- Progress indicator during scrape (per platform)
- **Manual pre-form data entry:** For guests who aren't online or have minimal social presence — the host fills out a structured questionnaire capturing: biographical facts (birthplace, schools, jobs, milestones), personal trivia (favorites, hobbies, pet peeves, habits), stories the host knows, memorable quotes. This is a text form that feeds directly into the fact bank. Designed for: significant others, family members, old friends who aren't on social media.
- Steam profile scraping: public Steam profile, owned games, playtime, recent achievements, bio
- Discord: mutual servers, public profile info (via Disboard or direct — no privileged data)
- **Generic manual link field:** Any URL the host wants to include — YouTube channel, Goodreads, Letterboxd, Spotify, Pinterest, or any platform not explicitly supported. Host pastes the link; system treats it as a text source.
- Manual fact entry: paste text, link, or note directly into profile
- Questions per profile: unlimited, quality-rated after use
- Delete profile and all associated data

### Scraping Pipeline
Scrapers run as background tasks triggered by cron or manual trigger:

**Reddit Scraper:**
- Input: username
- Method: `old.reddit.com/u/{username}.json` for posts/comments
- Also pull: upvoted posts (`upvoted.json`), saved posts (`saved.json`)
- Extract: post titles, comment text, subreddits, timestamps, scores
- Rate limit: 1 request/2s to avoid 429s

**Twitter/X Scraper:**
- Input: username
- Method 1: Nitter instances (no auth, public data) — `nitter.net/i/display?username=X`
- Method 2: browserless with cookies (full Twitter, requires auth session)
- Extract: tweets, retweets, likes, bio, following list, media references
- Nitter preferred for rate/availability reasons

**Facebook Scraper:**
- Input: username
- Method: browserless with cookies (no public API, requires login)
- Extract: public posts, about section, photos
- Fallback: manual entry only if no cookies provided

**Instagram Scraper:**
- Input: username  
- Method: browserless with cookies
- Extract: bio, post captions, hashtags
- Fallback: manual entry only

**LinkedIn Scraper:**
- Input: profile URL
- Method: browserless with cookies
- Extract: headline, about, experience, education
- Fallback: manual entry only

**Steam Scraper:**
- Input: Steam profile URL or custom ID
- Method: `steamcommunity.com/profiles/{id}` public page scraping via Playwright
- Extract: display name, bio, owned games (name, hours), recent achievements, profile level, avatar
- Public profiles only — no auth needed

**Discord (mutual servers):**
- Input: Discord username + discriminator
- Method: Disboard.org mutual servers lookup (no auth, public)
- Extract: shared servers, server icons, approximate member counts
- Fallback: manual entry — host fills in what they know

**Generic manual link field:**
- Input: any URL (YouTube, Goodreads, Letterboxd, Spotify, Pinterest, etc.)
- Method: Playwright scrapes the page text content for text
- Extract: page title, body text, publication dates where available
- Generic approach — single scraper handles any URL type without platform-specific parsing

### Question Generation (LLM)

**See: `/projects/trivia-app/question-generation-spec.md`**

The question generation pipeline is the core product. Full spec covers: fact extraction → deduplication → quality filtering → question generation → storage. Key parameters:

- **Trigger:** after scrape completes, or manually from profile
- **Categories (6):** History, Entertainment, Geography, Science, Sports, Art & Literature
- **Difficulties:** easy / medium / hard — distribution: 20 easy, 20 medium, 10 hard per 50-question game
- **Question types:** multiple_choice (4 options) + closest_to_correct (numeric ranking)
- **Output:** each question stores `question`, `answer`, `wrong_answers[]`, `category`, `difficulty`, `source_fact`, `times_asked`, `times_correct`, `quality_score`, `needs_review`

**Minimum content threshold:**
| Facts | Status |
|---|---|
| < 15 | Error: "Not enough content — need at least 15 facts. Got {n}. Try adding more sources." |
| 15–30 | Warning + shortened game (25 questions) |
| 30–100 | Full 50-question game |
| > 100 | Sample 50 per game, store all |

**v1 design note:** Question generation goes directly to the active bank — no pre-game review step. This is intentional Jack Box energy. v2 adds host review gate for embarrassing content flagging.

### Game Sessions
**Creating a game:**
- Host selects a person profile to be the "guest of honor"
- **Guest role (open question — pick one for v1):**
  - **A) Guest plays** — the person being profiled joins the game like any other player. Questions about them are still fair game. High energy, potentially awkward, very Jack Box.
  - **B) Guest watches** — guest sees a separate view showing what question is being asked without revealing the answer. They react in real time. No risk of them sabotaging their own score.
  - **C) Guest is told to step out** — standard party game mode. Players answer questions *about* the guest, who isn't present.
  - **Default for v1:** Option C (guest steps out). Simplest to implement, cleanest energy.
- Host sets: number of questions (25/50/100), time limit per question (15/30/60s), difficulty mix
- System generates room code (6 alphanumeric, e.g. `XK7T2P`)
- Players join via room code + their name

**Game flow:**
1. Host sees lobby — all connected players listed
2. Host clicks "Start"
3. Round 1: random question from guest's question bank
4. All players see question simultaneously (on their phones)
5. Timer counts down on all devices
6. Players tap answer (multiple choice: 4 options, or true/false)
7. Timer expires OR all players answered → reveal correct answer
8. Scores update, next question
9. After all questions: winner screen, full scoreboard, breakdown by category

**Scoring:**
- Correct answer before timer: 100 pts × difficulty multiplier (1x easy, 2x medium, 3x hard)
- Correct answer after timer (before reveal): 50 pts × difficulty multiplier
- Wrong answer: 0 pts
- **Speed bonus:** fastest correct answer gets bonus points — 1st: +50, 2nd: +25, 3rd: +10. Adds Jack Box chaotic energy. Implemented at reveal time.
- Wedge earned: 1 correct answer in a category = wedge slice (6 slices = 1 complete wedge)
- Win condition: first to complete all 6 category wedges, OR highest score after question set exhausted

**Game modes:**
- Standard: 50 questions, mixed categories
- Speed Run: 25 questions, 15s timer
- Deep Cut: 25 questions, only hard difficulty
- Closest-to-Correct: numeric questions (how many, how old, how far) — players submit guesses, closest wins. **v2 only** — requires number-input UI, different from tap-to-answer.
- Custom: host configures everything

### Real-time Sync (WebSocket)
- Player phones maintain WebSocket connection to server
- Server broadcasts: question, timer state, answer reveal, score updates
- Reconnection handling: if player disconnects, they rejoin same game room
- Host controls game advancement via host dashboard (keyboard shortcuts)

### Scoring & Persistence
- Per-player stats: games played, games won, total score, accuracy %, favorite category
- Per-profile stats: questions generated, times asked, average accuracy
- All-time leaderboard (per profile or global)
- Session history with full question/answer record

---

## 5. Component Inventory

### Landing Page
- Hero: app name in large Barlow Condensed, tagline, two CTA buttons
- Recent games: 3 cards showing recent sessions (date, guest, winner)
- How it works: 3-step visual (scrape → generate → play)
- Footer: minimal

### Host Dashboard
- Active game panel: current question (blurred for host until revealed), timer, live scores
- Player list: connected players with ready status
- Controls: Start Game, Next Question, End Game
- Quick actions: Add Question, View Profile, Rematch
- States: lobby, active-game, ended

### Player View
- Mobile-optimized, dark background
- Top bar: player name, current wedges, score
- Center: question card (slides up on new question)
- Timer bar: full-width, shrinks left-to-right, color shifts
- Answer buttons: 2×2 grid for 4-choice, or True/False layout
- After answer: correct/wrong flash, explanation
- End screen: final score, rank, wedges earned

### Profile Editor
- Avatar + name at top
- Platform handles: icon + input per platform, scrape status badge
- "Scrape" button with per-platform progress
- Manual facts section: textarea + add button
- Question preview: sample questions generated from profile
- Stats: total questions, last scraped, accuracy when asked

### Scoreboard
- Full-screen overlay during active game (host view)
- Sortable by score or category wedges
- Player color coding (matches their wedge colors)
- Animated rank changes

---

## 6. Technical Approach

### Stack
- **Container:** Docker on PHATT-RAID, published to `phattbeatts/trivia-app`
- **Backend:** FastAPI (Python 3.11+)
- **Database:** SQLite with SQLAlchemy ORM
- **Real-time:** WebSockets via FastAPI/Starlette
- **Scrapers:** Playwright (browserless) + httpx for Reddit
- **LLM:** LiteLLM bridge to Claude 3.5 / GPT-4 for question generation
- **Frontend:** Vanilla JS + CSS (no framework, single HTML file per page)
- **Auth:** None for players (room code is session auth). Host dashboard unprotected (local network use).
- **Deploy:** Cloudflare Pages for static assets, container on PHATT-RAID for API

### Database Schema

```sql
-- People profiles
CREATE TABLE profiles (
    id TEXT PRIMARY KEY,           -- ulid
    name TEXT NOT NULL,
    avatar_url TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    last_scraped_at INTEGER,
    scrape_status TEXT DEFAULT 'idle',  -- idle, scraping, complete, error
    platform_handles TEXT,           -- JSON: {reddit: "...", twitter: "..."}
    raw_scraped_data TEXT,          -- JSON: all scraped content
    question_count INTEGER DEFAULT 0,
    active INTEGER DEFAULT 1
);

-- Questions generated from profiles
CREATE TABLE questions (
    id TEXT PRIMARY KEY,
    profile_id TEXT NOT NULL REFERENCES profiles(id),
    question TEXT NOT NULL,
    answer TEXT NOT NULL,           -- primary correct answer
    wrong_answers TEXT,             -- JSON array of 3 wrong answers
    category TEXT NOT NULL,         -- history, entertainment, etc.
    difficulty INTEGER DEFAULT 2,   -- 1=easy, 2=medium, 3=hard
    source_fact TEXT,               -- the fact this was generated from
    times_asked INTEGER DEFAULT 0,
    times_correct INTEGER DEFAULT 0,
    quality_score REAL DEFAULT 0.5, -- updated from actual performance
    created_at INTEGER NOT NULL,
    active INTEGER DEFAULT 1
);

-- Game sessions
CREATE TABLE game_sessions (
    id TEXT PRIMARY KEY,
    profile_id TEXT NOT NULL REFERENCES profiles(id),
    room_code TEXT UNIQUE NOT NULL,
    status TEXT DEFAULT 'lobby',    -- lobby, active, finished
    question_count INTEGER DEFAULT 50,
    time_limit INTEGER DEFAULT 30,
    difficulty_mode TEXT DEFAULT 'mixed',
    created_at INTEGER NOT NULL,
    started_at INTEGER,
    ended_at INTEGER
);

-- Players in games
CREATE TABLE game_players (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES game_sessions(id),
    player_name TEXT NOT NULL,
    score INTEGER DEFAULT 0,
    wedges TEXT DEFAULT '{"history":0,"entertainment":0,"geography":0,"science":0,"sports":0,"art":0}',
    joined_at INTEGER NOT NULL,
    is_host INTEGER DEFAULT 0,
    ws_connection_id TEXT
);

-- Individual answers
CREATE TABLE answers (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES game_sessions(id),
    player_id TEXT NOT NULL REFERENCES game_players(id),
    question_id TEXT NOT NULL REFERENCES questions(id),
    selected_answer TEXT,
    is_correct INTEGER,
    time_taken_ms INTEGER,
    answered_at INTEGER
);

-- Player all-time stats
CREATE TABLE player_stats (
    player_name TEXT PRIMARY KEY,
    games_played INTEGER DEFAULT 0,
    games_won INTEGER DEFAULT 0,
    total_score INTEGER DEFAULT 0,
    total_correct INTEGER DEFAULT 0,
    total_asked INTEGER DEFAULT 0,
    last_played_at INTEGER
);
```

### API Endpoints

**Profiles**
- `POST /api/profiles` — create profile
- `GET /api/profiles` — list all profiles
- `GET /api/profiles/{id}` — get profile with handle/platform status
- `PUT /api/profiles/{id}` — update profile (name, handles)
- `DELETE /api/profiles/{id}` — delete profile + all questions
- `POST /api/profiles/{id}/scrape` — trigger scrape job
- `GET /api/profiles/{id}/questions` — preview questions
- `POST /api/profiles/{id}/generate` — trigger question generation

**Game Sessions**
- `POST /api/games` — create game, returns room_code
- `GET /api/games/{room_code}` — get game state
- `POST /api/games/{room_code}/join` — join as player
- `POST /api/games/{room_code}/start` — host starts game
- `GET /api/games/{room_code}/question` — get current question
- `POST /api/games/{room_code}/answer` — submit answer
- `POST /api/games/{room_code}/next` — host advances to next question
- `GET /api/games/{room_code}/scores` — get live scores

**WebSocket**
- `WS /ws/{room_code}/{player_id}` — real-time game updates

**Stats**
- `GET /api/stats/leaderboard` — global top players
- `GET /api/stats/player/{name}` — per-player full history

### Background Jobs
- Scrape jobs: run in thread pool, update `scrape_status` on profile
- Question generation: LLM call per profile, batch insert questions
- Stats aggregation: update after each answer

### File Structure
```
trivia-app/
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── app/
│   ├── main.py              # FastAPI app entry
│   ├── config.py             # settings, env vars
│   ├── database.py           # SQLAlchemy setup, seed data
│   ├── models.py             # Pydantic models
│   ├── routes/
│   │   ├── profiles.py
│   │   ├── games.py
│   │   └── stats.py
│   ├── services/
│   │   ├── scraper/
│   │   │   ├── __init__.py
│   │   │   ├── reddit.py
│   │   │   ├── twitter.py
│   │   │   └── browser.py   # Playwright browserless scraper
│   │   ├── generator.py      # LLM question generation
│   │   ├── game_engine.py    # game state machine
│   │   └── ws_manager.py    # WebSocket connections
│   └── static/
│       ├── index.html
│       ├── host.html
│       ├── play.html
│       ├── profile.html
│       ├── history.html
│       └── css/
│           └── style.css
│       └── js/
│           ├── app.js
│           ├── host.js
│           ├── player.js
│           └── profile.js
└── data/
    └── trivia.db
```

---

## 7. Scraping Details by Platform

### Reddit
- Endpoint: `https://www.reddit.com/u/{username}/submitted.json?limit=100`
- Also: `/u/{username}/comments.json`, `/u/{username}/upvoted.json` (if authenticated)
- No auth needed for public accounts
- Data extracted: title, selftext, subreddit, score, created_utc, permalink
- 429 rate limiting: 1 request per 2 seconds, implement retry with backoff

### Twitter/X
- Primary: Nitter instances (e.g., `nitter.privacydev.net`, `nitter.poast.org`)
- Parses: tweets, retweets, likes, bio, pinned tweet
- Fallback: Playwright browser with auth cookies (session from host's browser)
- Data extracted: tweet text, timestamp, reply-to, media URLs, likes, retweets

### Facebook
- Requires: Playwright browser with authenticated session
- Target: public pages/posts, profile about section
- No guaranteed access without credentials

### Instagram
- Requires: Playwright browser with authenticated session
- Target: bio, post captions, hashtags
- No public API or Nitter equivalent

### LinkedIn
- Requires: Playwright browser with authenticated session
- Target: profile headline, about, experience entries
- Most reliable for professional/career-oriented profiles

---

## 8. Quality & Persistence Rules

- All question data persists between sessions — no data loss on restart
- Profile scrapes persist — re-scrape only updates with new content, old questions retained
- Game history never deleted automatically
- WebSocket disconnection: player can rejoin same room within session
- LiteLLM rate limits handled with retry logic and queue
- If question generation fails mid-profile, partial questions are kept
- All scraped raw text stored for re-generation if needed

---

## 9. MVP Scope

**v1 (this build):**
- Single-profile mode (one guest of honor at a time)
- Manual fact entry + Reddit scraper (Nitter if available)
- LLM question generation (Claude via LiteLLM)
- Single game session at a time (one room)
- 50-question standard mode only
- Basic WebSocket sync (players on same network)
- No authentication, no admin panel
- SQLite on host filesystem
- Host dashboard on local network only

**Post-v1:**
- Multiple concurrent rooms
- All scrapers (Twitter browser, Facebook, Instagram, LinkedIn)
- Game modes (Speed Run, Deep Cut, Custom)
- Player accounts with persistent stats
- Mobile-optimized profile manager
- Export game as PDF results card
- "Rematch" button for same profile with new questions

---

## 10. Known Gaps — Future Work (v2+)

These are not v1 blockers. Documented here for planning purposes.

### Auth & Abuse Prevention
- **No auth on host dashboard** — anyone on the LAN can create/delete profiles, trigger scrapes, see game history. Acceptable for private house-party use. If exposed externally: requires login, rate limits, API keys.
- **No rate limiting on API** — a single user could spin up thousands of game sessions or scrape requests. Cost/exposure uncontrolled.
- **No cost control** — LiteLLM calls are metered. No cap, no budget alerts, no per-profile spend tracking.

### Content & Privacy
- **No content moderation on scraped data** — any public post goes straight to LLM. Offensive content, private information, everything. No profanity filter, no PII masking, no way to flag or delete specific scraped items from a profile.
- **No GDPR/data deletion path** — if someone whose profile is being used asks for their data deleted, there is no mechanism. Scraped text, generated questions, game history for that person persist indefinitely. Legal exposure if this goes public.
- **No consent model** — the host builds profiles about anyone. No opt-in from the person being profiled.
- **No guest "can see" mode** — if the guest is present (v2), no mechanism to filter questions they shouldn't see about themselves.

### UX & Reliability
- **Per-profile "last played" tracking** — no yet.
- **Question reuse between games** — FILTERED. Each game samples from the profile's question pool without replacement within that session. Questions return to pool after game ends.
- **Question pool:** Questions persist per profile. Re-scraping adds new questions to the existing pool (doesn't replace). Host can view pool size and force a re-shuffle. Questions with quality_score < 0.2 are excluded from random selection.
- **No host crash recovery** — if the host's device disconnects mid-game, all players are stuck. No session persistence, no way to resume, scores are lost.
- **No question quality feedback UI** — DB tracks `times_asked`, `times_correct`, `quality_score` but no interface to review, hide, or delete bad questions.
- **No designed error states for scraping** — when Reddit rate-limits, Nitter goes down, or Twitter requires login cookies, the user sees nothing designed. No retry button, no partial success indicator.
- **No question pre-screening (v1)** — scrape → generate → play happens live. Chaos is the point for v1. v2 adds optional host review gate for flagged content.

### Open Questions (for Brandon)
1. ~~Speed bonus~~ — **CONFIRMED: YES.** Fastest correct: +50, 2nd: +25, 3rd: +10.
2. ~~Question reuse~~ — **CONFIRMED: YES, question pool model.** Questions persist per profile, sampled without replacement per game, return to pool after. Re-scraping adds to pool.
3. ~~Guest role~~ — **CONFIRMED: steps out (v1).** Guest not in room during play.
### Post-v1 Full Feature List
- Multiple concurrent game rooms
- Full scraper suite: Twitter (browser), Facebook, Instagram, LinkedIn
- Game modes: Speed Run, Deep Cut, Closest-to-Correct, Custom
- Player accounts with persistent all-time stats
- Mobile-optimized profile manager UI
- Per-profile "last played" tracking and re-scrape handling
- Session recovery for host disconnection
- Question review and quality management UI
- Guest "can see" filter mode
- Sound effects and haptics
- Content moderation layer (profanity filter, PII masking)
- Consent/opt-in model for profiled individuals
- GDPR deletion request flow
- Rate limiting and cost controls
- External deployment (Cloudflare Pages + container)

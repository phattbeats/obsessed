# Coding-Agent Handoff — Obsessed UI Rework (Phase 1)

> **Provenance.** The kickoff ticket (PHA-801) instructed the implementing agent to
> "read `CODING-AGENT-HANDOFF.md` and the files in `design/`" and implement Phase 1,
> PR-1 → PR-8. Those two artifacts did **not** exist in the repo when work began — the
> only design source present was [`CLAUDE_DESIGN_PROMPT.md`](CLAUDE_DESIGN_PROMPT.md)
> (added in PHA-784). This handoff and the `design/` decomposition were therefore
> authored from that brief so the repo matches what the ticket assumes. If a separate
> design-session produced its own handoff/decomposition, drop it in and re-baseline —
> the PR boundaries below are derived from the brief's §7 build sequence, not invented
> requirements.

## What this is

A faithful decomposition of the `CLAUDE_DESIGN_PROMPT.md` brief into **8 reviewable
PRs** that make up **Phase 1** of the front-end rework. Each PR is its own branch and
its own GitHub pull request, opened **in order**, because the work stacks: tokens come
before the components that consume them, components before the screens that compose
them, screens before the cross-cutting polish pass.

## The north star (one paragraph)

*Jackbox × Trivial Pursuit.* House-party-on-a-big-screen energy: loud, flat,
thick-bordered, celebratory, a little unhinged — never corporate, never glassmorphic,
never quiet. The **Trivial-Pursuit wedge** is the persistent brand object and must read
like the cover of a board-game box at every size. Motion is causality, not decoration:
every transition answers "what just happened?" The full contract lives in
`CLAUDE_DESIGN_PROMPT.md` and `SPEC.md §2` — read those, not assumptions.

## Hard boundaries (from the brief §12 — do not cross)

- **Touch only `app/static/**`** plus, for the host page, a single `FileResponse`
  route in `app/main.py`. Nothing else in `app/` is in scope.
- **Do not** modify `app/database.py`, `app/models.py`, `app/services/`, any scraper,
  any `/api/*` route shape, or any WebSocket event name/payload.
- **No** build step, framework, package manager, `node_modules/`, CDN script, or
  third-party JS dependency. Vanilla HTML/CSS/JS only. Google Fonts is the sole allowed
  external dependency.
- **Do not** remove the dormant route files (`news.py`, `court.py`, `sos.py`,
  `auditor.py`) — they are scaffolding for PHA-231/232/233/234 and the profile editor's
  Public-Records section is a real feature to wire, not vestigial.
- If a UI change implies a backend change, leave a `// NEEDS BACKEND:` comment with the
  proposed contract and surface it in the PR description. Do not invent a fetch URL.

## PR sequence — see [`design/phase-1-prs.md`](design/phase-1-prs.md)

| PR | Branch | Theme | Brief pass |
|----|--------|-------|------------|
| PR-1 | `pha-801/pr-1-foundation` | Design tokens + CSS architecture foundation | §7 pass 1a |
| PR-2 | `pha-801/pr-2-wedge` | SVG wedge component (4 sizes) + 6 category icons | §7 pass 1b (hard rule) |
| PR-3 | `pha-801/pr-3-home-lobby` | Home cold-open + lobby + confetti + player-bubble | §7 pass 2 |
| PR-4 | `pha-801/pr-4-game-player` | Game screen (player view) motion + feedback | §7 pass 3 |
| PR-5 | `pha-801/pr-5-host-view` | New `host.html` + `/host` route (TV view) | §7 pass 4 |
| PR-6 | `pha-801/pr-6-results` | Results celebration screen | §7 pass 5 |
| PR-7 | `pha-801/pr-7-profile` | Profile editor restyle + public-records wiring | §7 pass 6 |
| PR-8 | `pha-801/pr-8-polish` | Reduced-motion, focus, contrast, preload, settings/admin | §7 pass 7 |

**Stacking:** each branch is cut from the previous PR's branch (not `main`), because
later PRs consume tokens/components introduced earlier and would render broken in
isolation against an unmerged `main`. Set each PR's base to its predecessor. When PRs
merge, rebase the remaining stack.

## Per-PR acceptance (from the brief §8)

A PR is done when, for the surfaces it touches:

- No magic numbers in CSS outside `:root` and `@keyframes`.
- Every interactive element has hover, `:focus-visible`, active, and disabled states.
- Every motion respects `prefers-reduced-motion: reduce`.
- No inline `style="…"` longer than 40 characters in any HTML file it edits.
- The wedge renders correctly at `--sm`, `--md`, `--lg`, `--inline` (once PR-2 lands).
- App boots (`uvicorn app.main:app --port 8000`) and the happy path
  (create profile → start game → answer question → see results) still works against the
  real API.
- For `/` and `/host`: Lighthouse a11y ≥ 95, performance ≥ 85.

## Verification per PR

1. Boot the app: `uvicorn app.main:app --port 8000`.
2. Exercise the changed screen(s) against the real API / WebSocket events.
3. Screenshot the changed screens so the next PR starts from a real baseline.
4. Confirm no existing screen regressed (router, WebSocket reconnect, admin gate).

## Reference files (read in this order)

1. `CLAUDE_DESIGN_PROMPT.md` — the full design-engineering brief (authoritative).
2. `SPEC.md §2` — the non-negotiable design language.
3. `README.md` — API + WebSocket contract.
4. `app/static/{index.html,css/style.css,js/app.js}` — current state.
5. `app/main.py` — static mounting + where the `/host` route goes.
6. `app/websocket.py`, `app/routes/games.py` — live-game event payload shapes.

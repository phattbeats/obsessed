# Logo Decision — Locked

**Date:** May 20, 2026  
**Status:** ✅ LOCKED

---

## Primary Typeface

**Rubik 900**

### Rationale
- Most legible across all UI contexts (screen titles, questions, buttons, captions)
- Retains game-friendly energy without being overly playful
- Rounds are subtle enough to feel modern, not toy-like
- Works at small sizes (rack icons, favicons) and large (host TV, lobby hero)

### Where it applies
- Logo wordmark (the "BSESSED" in !BSESSED)
- All screen titles and section heads
- Question text
- Button labels
- Card titles

---

## Logo Mark

**The Bang (!)**

- Pink circle (#e94560) with italic black "!" 
- Replaces the O in OBSESSED
- Pulsating animation (2.4s ease loop)
- Scoring mechanic: 6 slots, fill one bang per round won

### Why it works
- Solves the Trivial Pursuit collision — no pie, no six-slice geometry
- The "!" alone is the favicon (works at 16px)
- Lean into the name — "Obsessed!" — the exclamation IS the mark
- Lowest disruption to existing app (keeps current palette, just swaps brand object)

---

## Supporting Typeface

**Nunito** (400/700/800) — body copy, longer prose, rules text  
**JetBrains Mono** (500/700) — captions, timestamps, technical labels, room codes

---

## Next Steps

1. Create 4-6 logo lockup variations (stacked, wide, compact, icon-only)
2. Build favicon ladder (16/24/32/64/128/512)
3. Update host TV hero + lobby to use Rubik 900
4. Document animation specs for the pulsating bang
5. Update `tokens.css` to lock `--font-logo: 'Rubik', system-ui, sans-serif; font-weight: 900;`

---

**This decision supersedes the wedge system from v2–v7.**

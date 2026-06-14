/**
 * StateIcon — one consistent micro-icon set for the unglamorous state surfaces
 * (PHA-1062): profile scrape-status badges, inline 'Loading…' rows, and the
 * ≤5s crit timer. These were all pure text/color before; the glyphs remove the
 * last "placeholder UI" tells.
 *
 * Deliberately a different visual family from CategoryIcon: those are bold
 * FILLED pack silhouettes (branded marks). State icons are thin STROKED line
 * glyphs (stroke-width 2.5, round caps) — they sit beside small uppercase text
 * and should read as quiet status indicators, not logos. They share that one
 * stroke language so the six states feel like a set.
 *
 * Size is driven by the host via `font-size` (each glyph is 1em). Color
 * inherits from `currentColor` so a glyph always matches its badge text.
 *
 * Keys match app/routes/profiles.py scrape_status values plus two UI-only
 * states (loading, timer). `scraping` and `loading` share the same spinner arc
 * and spin; `timer` is the crit stopwatch.
 *
 * Accessibility: in every current surface the glyph sits beside visible text
 * that already names the state (the badge label, 'Loading…', the timer). So
 * glyphs are DECORATIVE by default (aria-hidden) — labelling them too would
 * make a screen reader double-announce ("done done"). Pass {decorative:false}
 * to get a standalone, self-labelled icon for a future text-less surface.
 *
 * API:
 *   StateIcon.svg(name, opts?)     → inline <svg> string ('' for unknown name)
 *                                    opts.decorative (default true)
 *   StateIcon.render(hostEl, name) → sets hostEl innerHTML to the glyph
 */
const StateIcon = (() => {
  // Each value is the inner markup of a 24×24 stroked glyph. `spin: true`
  // adds the rotation class so in-progress states visibly churn.
  const GLYPHS = {
    // check — scrape complete
    done: { aria: 'Done', body: '<path d="M5 13l4 4L19 7"/>' },
    // clock — queued / waiting to scrape
    pending: {
      aria: 'Pending',
      body: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 1.8"/>',
    },
    // 3/4 arc — actively scraping; spins
    scraping: {
      aria: 'Scraping',
      spin: true,
      body: '<path d="M12 3.5a8.5 8.5 0 1 1-8.5 8.5"/>',
    },
    // exclamation in a circle — scrape failed
    failed: {
      aria: 'Failed',
      body:
        '<circle cx="12" cy="12" r="8.5"/>' +
        '<path d="M12 7.8v4.4"/><path d="M12 15.8h.01"/>',
    },
    // 3/4 arc — generic loading; same family as scraping, spins
    loading: {
      aria: 'Loading',
      spin: true,
      body: '<path d="M12 3.5a8.5 8.5 0 1 1-8.5 8.5"/>',
    },
    // stopwatch — ≤5s crit timer pressure
    timer: {
      aria: 'Time running out',
      body:
        '<path d="M9.5 3.2h5"/><path d="M12 3.2V5.4"/>' +
        '<circle cx="12" cy="13" r="7.2"/><path d="M12 13V9.2"/>',
    },
  };

  function svg(name, opts) {
    const g = GLYPHS[name];
    if (!g) return '';
    const decorative = !opts || opts.decorative !== false;
    const a11y = decorative
      ? 'aria-hidden="true" focusable="false"'
      : 'role="img" aria-label="' + g.aria + '"';
    return (
      '<svg class="state-icon' + (g.spin ? ' state-icon--spin' : '') + '" ' +
      'viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
      'stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" ' +
      a11y + '>' + g.body + '</svg>'
    );
  }

  function render(hostEl, name) {
    if (hostEl) hostEl.innerHTML = svg(name);
    return hostEl;
  }

  return { svg, render };
})();

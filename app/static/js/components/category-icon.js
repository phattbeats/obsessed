/**
 * CategoryIcon — six geometric pack glyphs.
 *
 * Per design/OBSESSED-DESIGN-HANDOFF.md ("Category icons — six geometric
 * glyphs"): History (clock) · Entertainment (clapper) · Geography (globe) ·
 * Science (atom) · Sports (ball) · Art & Lit (book). Geometric primitives,
 * thick rounded strokes, no detailed illustration. Color inherits from
 * `currentColor`; size is driven by the host (CSS width/height).
 *
 * Keys match app/routes/games.py CATEGORY_COLORS:
 *   history, entertainment, geography, science, sports, art_literature
 *
 * NOTE: rebuilt from the written handoff spec — the canonical source
 * (obsessed-design.html) was never committed to this repo. Swap in the
 * canonical paths if/when that file lands.
 *
 * API:
 *   catIconSVG(cat)              → inline <svg> string (or '' for unknown cat)
 *   renderCatIcon(hostEl, cat)   → sets hostEl innerHTML to the glyph, returns hostEl
 */
const CategoryIcon = (() => {
  // All glyphs: 24×24 box, fill none, stroke currentColor, round caps/joins.
  // Drawn for legibility at ~18px — confident strokes, no fine detail.
  const PATHS = {
    // clock — circle + hands
    history:
      '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5v4.8l3.3 2"/>',
    // clapperboard — board + hinged striped top bar
    entertainment:
      '<rect x="3" y="9.4" width="18" height="10.6" rx="1.8"/>' +
      '<path d="M3 9.4 4.3 5.3 21 7.2 19.7 9.4Z"/>' +
      '<path d="M8.5 8.9 9.8 5.2M13.8 9.2 15.1 5.5"/>',
    // globe — circle + equator + meridians
    geography:
      '<circle cx="12" cy="12" r="8.5"/><path d="M3.5 12h17"/>' +
      '<path d="M12 3.5c3.2 2.5 3.2 14.5 0 17M12 3.5c-3.2 2.5-3.2 14.5 0 17"/>',
    // atom — nucleus + two crossed orbits
    science:
      '<circle cx="12" cy="12" r="1.9" fill="currentColor" stroke="none"/>' +
      '<ellipse cx="12" cy="12" rx="8.6" ry="3.5" transform="rotate(60 12 12)"/>' +
      '<ellipse cx="12" cy="12" rx="8.6" ry="3.5" transform="rotate(-60 12 12)"/>',
    // ball — circle + central pentagon + seams
    sports:
      '<circle cx="12" cy="12" r="8.5"/>' +
      '<path d="M12 8 15 10.2 13.9 13.8H10.1L9 10.2Z"/>' +
      '<path d="M12 8V4.2M14.9 13.6 17.8 15.4M9.1 13.6 6.2 15.4"/>',
    // open book — two pages off a center spine
    art_literature:
      '<path d="M12 6.4C9.9 5.1 6.9 4.5 4 4.9v12.4c2.9-.4 5.9.2 8 1.5 2.1-1.3 5.1-1.9 8-1.5V4.9c-2.9-.4-5.9.2-8 1.4Z"/>' +
      '<path d="M12 6.4V18.8"/>',
  };

  const LABEL = {
    history: 'History',
    entertainment: 'Entertainment',
    geography: 'Geography',
    science: 'Science',
    sports: 'Sports',
    art_literature: 'Art & Literature',
  };

  function catIconSVG(cat) {
    const inner = PATHS[cat];
    if (!inner) return '';
    return (
      '<svg class="cat-icon__svg" viewBox="0 0 24 24" fill="none" ' +
      'stroke="currentColor" stroke-width="2.2" stroke-linecap="round" ' +
      'stroke-linejoin="round" role="img" aria-label="' +
      (LABEL[cat] || cat) +
      '">' +
      inner +
      '</svg>'
    );
  }

  function renderCatIcon(hostEl, cat) {
    if (hostEl) hostEl.innerHTML = catIconSVG(cat);
    return hostEl;
  }

  return { catIconSVG, renderCatIcon, LABEL };
})();

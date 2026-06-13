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
  const PATHS = {
    // clock — circle + hands
    history:
      '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3.2 2"/>',
    // clapperboard — board + hinged striped top bar
    entertainment:
      '<rect x="3" y="9" width="18" height="11" rx="1.5"/>' +
      '<path d="M3 9 21 4.6"/><path d="M8.2 8.4 6.8 5.2M13.4 7.2 12 4"/>',
    // globe — circle + equator + meridians
    geography:
      '<circle cx="12" cy="12" r="9"/><path d="M3 12h18"/>' +
      '<path d="M12 3c3.4 2.7 3.4 15.3 0 18M12 3c-3.4 2.7-3.4 15.3 0 18"/>',
    // atom — nucleus + two crossed orbits
    science:
      '<circle cx="12" cy="12" r="1.7" fill="currentColor" stroke="none"/>' +
      '<ellipse cx="12" cy="12" rx="9" ry="3.7" transform="rotate(60 12 12)"/>' +
      '<ellipse cx="12" cy="12" rx="9" ry="3.7" transform="rotate(-60 12 12)"/>',
    // ball — circle + pentagon + seams
    sports:
      '<circle cx="12" cy="12" r="9"/>' +
      '<path d="M12 7.2 15.4 9.7 14.1 13.7H9.9L8.6 9.7z"/>' +
      '<path d="M12 7.2V3.4M14.9 13.4l3 1.6M9.1 13.4l-3 1.6"/>',
    // open book — two pages off a center spine
    art_literature:
      '<path d="M12 6.2C9.8 4.9 6.6 4.3 3.6 4.7v12.7c3-.4 6.2.2 8.4 1.6 2.2-1.4 5.4-2 8.4-1.6V4.7c-3-.4-6.2.2-8.4 1.5z"/>' +
      '<path d="M12 6.2V19"/>',
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
      'stroke="currentColor" stroke-width="2" stroke-linecap="round" ' +
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

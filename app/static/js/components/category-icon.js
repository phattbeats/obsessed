/**
 * CategoryIcon — six bold filled pack glyphs.
 *
 * Based on design/OBSESSED-DESIGN-HANDOFF.md ("Category icons — six glyphs"):
 * History (clock) · Entertainment (clapper) · Geography (globe) ·
 * Science (flask) · Sports (ball) · Art & Lit (book). Solid silhouettes with
 * evenodd knockout detail — chosen over thin outlines so they read as branded
 * marks and stay legible down to ~16px. Color inherits from `currentColor`;
 * size is driven by the host (CSS width/height).
 *
 * Science uses a flask rather than the spec's atom: an atom is line-native and
 * illegible as a small filled silhouette. Easy to swap back if the canonical
 * art lands.
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
  // All glyphs: 24×24 box, solid silhouettes filled with currentColor,
  // fill-rule evenodd so interior detail knocks out the chip behind it.
  // Bold filled shapes (vs thin outlines) stay legible down to ~18px and
  // read as branded marks rather than generic line icons. Each value is a
  // single <path> d-string (outer silhouette + interior knockout subpaths).
  const PATHS = {
    // clock — filled face with an L-shaped knockout for the hands
    history:
      'M3.8 12a8.2 8.2 0 1 0 16.4 0a8.2 8.2 0 1 0-16.4 0Z' +
      'M11.2 7.4H12.8V11.5L15.5 13.05 14.7 14.45 11.2 12.43Z',
    // clapperboard — solid board + hinged bar with knockout stripes
    entertainment:
      'M5 9.8H19a2 2 0 0 1 2 2V18a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-6.2a2 2 0 0 1 2-2Z' +
      'M3.1 9.3 4.5 5.0 20.9 6.8 19.6 9.3Z' +
      'M6.9 8.9 7.9 5.55 9.0 5.68 8.0 9.0Z' +
      'M11.1 9.0 12.1 5.7 13.2 5.83 12.2 9.1Z' +
      'M15.3 9.1 16.3 5.85 17.4 5.98 16.4 9.2Z',
    // globe — filled disc with knockout equator + meridians
    geography:
      'M3.8 12a8.2 8.2 0 1 0 16.4 0a8.2 8.2 0 1 0-16.4 0Z' +
      'M4.2 11.1H19.8V12.9H4.2Z' +
      'M11.2 4.2V19.8H12.8V4.2Z' +
      'M9 5.5C6.5 8 6.5 16 9 18.5C8 16 8 8 9 5.5Z' +
      'M15 5.5C17.5 8 17.5 16 15 18.5C16 16 16 8 15 5.5Z',
    // flask — solid beaker silhouette with knockout bubbles
    science:
      'M9.6 3.2H14.4V4.8H13.6V8.8L18.8 18C19.6 19.4 18.6 21 17 21H7' +
      'C5.4 21 4.4 19.4 5.2 18L10.4 8.8V4.8H9.6Z' +
      'M12 15.6a1 1 0 1 0 0.02 0Z' +
      'M14.1 13.7a0.8 0.8 0 1 0 0.02 0Z',
    // soccer ball — filled disc with knockout pentagon + edge marks
    sports:
      'M3.8 12a8.2 8.2 0 1 0 16.4 0a8.2 8.2 0 1 0-16.4 0Z' +
      'M12 8 15 10.2 13.85 13.8H10.15L9 10.2Z' +
      'M12 4.3 13 5.75 11 5.75Z' +
      'M5.7 14.7 7.1 15 6.6 16.35Z' +
      'M18.3 14.7 16.9 15 17.4 16.35Z',
    // open book — two solid pages with a center-spine gap
    art_literature:
      'M11.2 6.6C8.8 5.3 6 4.9 3.8 5.3V17.3C6 16.9 8.8 17.3 11.2 18.6Z' +
      'M12.8 6.6C15.2 5.3 18 4.9 20.2 5.3V17.3C18 16.9 15.2 17.3 12.8 18.6Z',
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
      '<svg class="cat-icon__svg" viewBox="0 0 24 24" ' +
      'fill="currentColor" fill-rule="evenodd" role="img" aria-label="' +
      (LABEL[cat] || cat) +
      '"><path d="' + inner + '"/></svg>'
    );
  }

  function renderCatIcon(hostEl, cat) {
    if (hostEl) hostEl.innerHTML = catIconSVG(cat);
    return hostEl;
  }

  return { catIconSVG, renderCatIcon, LABEL };
})();

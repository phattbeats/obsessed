/**
 * renderBang(host, options) — mount a Bang lockup into a host element.
 *
 * @param {Element} host       - Container to render into (cleared on each call)
 * @param {Object}  options
 * @param {'primary'|'stacked'|'icon'|'compact'|'wide'|'square'|'badge'|'hero'} options.variant
 * @param {boolean} [options.wordmark=true] - Show OBSESSED wordmark (false = icon-only)
 * @param {string}  [options.tagline]       - Override tagline text (wide/square variants)
 *
 * Pulsation is CSS-driven (bangPulse in bang.css). No JS animation runs here.
 * prefers-reduced-motion is handled by the CSS media query in bang.css.
 *
 * Variant map:
 *   primary  → Variation A — inline horizontal (default splash/lobby)
 *   stacked  → Variation B — circle above wordmark
 *   icon     → Variation C — bang circle only, no wordmark
 *   compact  → Variation D — small nav/header size, no pulse
 *   wide     → Variation E — primary + tagline below
 *   square   → Variation F — centered + tagline below
 *   badge    → Variation G — pill badge, no pulse
 *   hero     → Variation H — TV/landing-page scale
 */
function renderBang(host, { variant = 'primary', wordmark = true, tagline } = {}) {
  host.innerHTML = '';

  // icon variant ignores wordmark option
  if (variant === 'icon') {
    const circle = _circle('');
    circle.classList.add('bang--icon');
    host.appendChild(circle);
    return;
  }

  // badge variant: pill wrapper
  if (variant === 'badge') {
    const wrap = document.createElement('span');
    wrap.className = 'bang--badge';
    wrap.appendChild(_circle(''));
    if (wordmark) wrap.appendChild(_wordmark('OBSESSED', false));
    host.appendChild(wrap);
    return;
  }

  // stacked variant: circle above wordmark
  if (variant === 'stacked') {
    const wrap = document.createElement('div');
    wrap.className = 'bang--stacked';
    wrap.appendChild(_circle(''));
    if (wordmark) wrap.appendChild(_wordmark('OBSESSED', false));
    host.appendChild(wrap);
    return;
  }

  // wide variant: primary inline + tagline below
  if (variant === 'wide') {
    const wrap = document.createElement('div');
    wrap.className = 'bang--wide';
    const main = document.createElement('div');
    main.className = 'bang-main';
    main.appendChild(_circle(''));
    if (wordmark) main.appendChild(_wordmark('OBSESSED', true));
    wrap.appendChild(main);
    const tag = document.createElement('div');
    tag.className = 'bang-tagline';
    tag.innerHTML = _taglineHtml(tagline || 'the <b>hyper-personal</b> trivia game');
    wrap.appendChild(tag);
    host.appendChild(wrap);
    return;
  }

  // square variant: centered + tagline below
  if (variant === 'square') {
    const wrap = document.createElement('div');
    wrap.className = 'bang--square';
    const main = document.createElement('div');
    main.className = 'bang-main';
    main.appendChild(_circle(''));
    if (wordmark) main.appendChild(_wordmark('OBSESSED', true));
    wrap.appendChild(main);
    const tag = document.createElement('div');
    tag.className = 'bang-tagline';
    tag.textContent = tagline || 'The hyper-personal trivia game';
    wrap.appendChild(tag);
    host.appendChild(wrap);
    return;
  }

  // primary / compact / hero — inline horizontal
  const variantClass = {
    primary: 'bang--primary',
    compact: 'bang--compact',
    hero:    'bang--hero',
  }[variant] || 'bang--primary';

  const wrap = document.createElement('div');
  wrap.className = variantClass;
  wrap.appendChild(_circle(''));
  if (wordmark) wrap.appendChild(_wordmark('OBSESSED', true));
  host.appendChild(wrap);
}

/* ── Private helpers ───────────────────────────────────────────── */

function _circle(label) {
  const el = document.createElement('div');
  el.className = 'bang-circle';
  el.setAttribute('aria-hidden', 'true');
  const b = document.createElement('b');
  b.textContent = '!';
  el.appendChild(b);
  return el;
}

function _wordmark(text, ghostO) {
  const el = document.createElement('div');
  el.className = 'bang-wordmark';
  if (ghostO) {
    // Ghost O keeps kerning correct when the ! circle replaces the O visually.
    // Without it, "BSESSED" kerning shifts.
    const ghost = document.createElement('span');
    ghost.className = 'ghost-o';
    ghost.setAttribute('aria-hidden', 'true');
    ghost.textContent = 'O';
    el.appendChild(ghost);
    el.appendChild(document.createTextNode('BSESSED'));
  } else {
    el.textContent = text;
  }
  return el;
}

function _taglineHtml(html) {
  // Allow <b> tags in tagline for the wide variant's "hyper-personal" accent.
  // This is controlled content from renderBang callers, not user input.
  return html;
}

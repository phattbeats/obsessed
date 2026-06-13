/**
 * BangRack — 6-slot Bang scoring indicator.
 *
 * API:
 *   BangRack.attach(hostEl, { categories })
 *     → Returns a rack instance. Clears hostEl and mounts the rack.
 *
 *   rack.fill(category, { burst: false })
 *     → Fills the next empty slot matching `category` with its color.
 *     `burst: true` triggers the pop animation.
 *
 *   rack.setFills([cat1, cat2, ...])
 *     → Bulk-fills slots in order (no animation). Pass fewer than 6 to
 *       partially fill. Pass [] to clear.
 *
 *   rack.clear()
 *     → Empties all slots.
 *
 *   rack.isComplete()
 *     → true when all 6 slots are filled.
 *
 * Category → CSS token map matches app/routes/games.py CATEGORY_COLORS:
 *   history, entertainment, geography, science, sports, art_literature
 *
 * Slot rotation alternates to give a casual, stacked feel (per design).
 */
const BangRack = (() => {
  const CAT_TOKEN = {
    history:        '--cat-history',
    entertainment:  '--cat-entertainment',
    geography:      '--cat-geography',
    science:        '--cat-science',
    sports:         '--cat-sports',
    art_literature: '--cat-art',
  };

  // Alternating micro-rotations match the design's casual tilted feel
  const ROTATIONS = ['-3deg', '2deg', '-2deg', '3deg', '-1deg', '2.5deg'];

  // Pack glyph (clock/clapper/globe/atom/ball/book). Falls back to '' if the
  // CategoryIcon component isn't loaded so the rack still renders the ! stamp.
  const iconSVG = (cat) =>
    (typeof CategoryIcon !== 'undefined' && CategoryIcon.catIconSVG(cat)) || '';
  const catName = (cat) =>
    (typeof CategoryIcon !== 'undefined' && CategoryIcon.LABEL[cat]) || cat;

  function attach(hostEl, { categories = [] } = {}) {
    hostEl.innerHTML = '';

    const rack = document.createElement('div');
    rack.className = 'bang-rack';

    const slots = categories.slice(0, 6).map((cat, i) => {
      const slot = document.createElement('div');
      slot.className = 'bang-slot';
      slot.dataset.cat = cat;
      slot.setAttribute('aria-label', catName(cat));
      slot.style.setProperty('--rot', ROTATIONS[i]);
      const token = CAT_TOKEN[cat];
      if (token) slot.style.setProperty('--c', `var(${token})`);

      const b = document.createElement('b');
      b.textContent = '!';
      slot.appendChild(b);

      // Pack glyph identifies the category (replaces the old text abbreviation).
      // Sits in a contrast chip pinned at the slot's base — see bang-rack.css.
      const icon = document.createElement('span');
      icon.className = 'bang-slot__icon';
      icon.innerHTML = iconSVG(cat);
      slot.appendChild(icon);

      rack.appendChild(slot);
      return slot;
    });

    // Pad to 6 slots if fewer categories provided
    while (rack.children.length < 6) {
      const slot = document.createElement('div');
      slot.className = 'bang-slot';
      const b = document.createElement('b');
      b.textContent = '!';
      slot.appendChild(b);
      rack.appendChild(slot);
      slots.push(slot);
    }

    hostEl.appendChild(rack);

    return {
      fill(cat, { burst = false } = {}) {
        const slot = slots.find(s => s.dataset.cat === cat && !s.classList.contains('filled'));
        if (!slot) return;
        slot.classList.add('filled');
        if (burst) {
          slot.classList.remove('burst');
          void slot.offsetWidth; // reflow to restart animation
          slot.classList.add('burst');
        }
      },

      setFills(catList) {
        slots.forEach(s => s.classList.remove('filled', 'burst'));
        catList.forEach(cat => {
          const slot = slots.find(s => s.dataset.cat === cat && !s.classList.contains('filled'));
          if (slot) slot.classList.add('filled');
        });
      },

      clear() {
        slots.forEach(s => s.classList.remove('filled', 'burst'));
      },

      isComplete() {
        return slots.every(s => s.classList.contains('filled'));
      },
    };
  }

  return { attach };
})();

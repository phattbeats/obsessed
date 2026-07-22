const API = '';
let currentProfile = null;
let currentGame = null;
let myPlayerId = localStorage.getItem('obsessed_pid') || (localStorage.setItem('obsessed_pid', 'p_' + Math.random().toString(36).slice(2)), localStorage.getItem('obsessed_pid'));
let myPlayerName = localStorage.getItem('obsessed_name') || '';
let myProfileName = '';
let myProfileType = 'person';
let myRoomCode = null;
let selectedThings = [];  // [{profile_id, num_questions}]
let bangRack = null;
let pollInterval = null;
let timerInterval = null;
let timerSeconds = 30;
let ws = null;
let wsReconnectTimer = null;

// SPEC: 6 category wedges. First player to fill all 6 wins outright.
const CATEGORIES = ['history', 'entertainment', 'geography', 'science', 'sports', 'art_literature'];
const CATEGORY_COLORS = {
  history: '#ff6d00',
  entertainment: '#d500f9',
  geography: '#2979ff',
  science: '#00e676',
  sports: '#ff1744',
  art_literature: '#ffea00',
};
const CATEGORY_LABELS = {
  history: 'HIST',
  entertainment: 'ENT',
  geography: 'GEO',
  science: 'SCI',
  sports: 'SPT',
  art_literature: 'ART',
};

// ── WebSocket ────────────────────────────────────────────────────────────────
function connectWS(room_code) {
  if (ws) {
    ws.close();
    ws = null;
  }
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const host = location.host;
  const url = `${proto}//${host}/ws/${room_code}/${myPlayerId}`;
  ws = new WebSocket(url);
  
  ws.onopen = () => {
    console.log('[WS] connected to', room_code);
    clearTimeout(wsReconnectTimer);
  };
  
  ws.onmessage = (evt) => {
    try {
      const msg = JSON.parse(evt.data);
      handleWSMessage(msg);
    } catch (e) {
      console.warn('[WS] failed to parse:', evt.data);
    }
  };
  
  ws.onclose = () => {
    console.log('[WS] disconnected');
    // Auto-reconnect if still in a game room
    if (myRoomCode) {
      clearTimeout(wsReconnectTimer);
      wsReconnectTimer = setTimeout(() => connectWS(myRoomCode), 3000);
    }
  };
  
  ws.onerror = (err) => {
    console.warn('[WS] error', err);
  };
}

function handleWSMessage(msg) {
  switch (msg.type) {
    case 'pong': break;
    case 'player_joined':
      updateLobbyPlayers(msg.players || []);
      break;
    case 'game_started':
      renderWedgeBoard([]);  // empty board at game start
      showScreen('game');
      break;
    case 'new_question':
      renderQuestionWS(msg);
      break;
    case 'answer_result':
      showAnswerResultWS(msg);
      break;
    case 'question_advance':
      // Score update between questions — could update a live scoreboard
      break;
    case 'game_over':
      showResultsWS(msg);
      break;
  }
}

// ── Screen management ────────────────────────────────────────────────────────
function showScreen(name) {
  document.querySelectorAll('.screens').forEach(s => s.classList.remove('active'));
  const map = { home: 'screen-home', profile: 'screen-profile', lobby: 'screen-lobby', game: 'screen-game', results: 'screen-results', history: 'screen-history', settings: 'screen-settings' };
  const el = document.getElementById(map[name]);
  if (el) el.classList.add('active');
  if (name !== 'game') { clearInterval(pollInterval); clearInterval(timerInterval); }
  if (name === 'lobby') {
    const bangEl = document.getElementById('lobby-bang');
    if (bangEl) renderBang(bangEl, { variant: 'icon', wordmark: false });
    connectWS(myRoomCode);
    startLobbyPoll(); // fallback polling — can be reduced or removed once WS is stable
  }
  if (name === 'game') {
    bangRack = BangRack.attach(document.getElementById('bang-rack'), {
      categories: ['history', 'entertainment', 'geography', 'science', 'sports', 'art_literature'],
    });
    if (myRoomCode) connectWS(myRoomCode);
    startGamePoll();
  }
}

// ── Toast ────────────────────────────────────────────────────────────────────
function toast(msg) {
  const t = document.getElementById('toast');
  t.textContent = msg; t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 3000);
}

// ── Profile management ────────────────────────────────────────────────────────
// ── Profile form submission ──────────────────────────────────────────────────
async function submitProfile(event) {
  event.preventDefault();
  const form = document.getElementById('profile-form');
  const btn = form.querySelector('button[type=submit]');
  btn.disabled = true;
  btn.textContent = 'Saving...';
  try {
    const fd = new FormData(form);
    const body = {};
    for (const [key, value] of fd.entries()) {
      if (value.trim()) body[key] = value.trim();
    }
    body.question_budget = 50;
    const res = await fetch(API + '/api/profiles', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    if (!res.ok) {
      toast('Error: ' + (data.detail || 'Unknown error'));
      return;
    }
    toast('Profile created');
    form.reset();
    loadProfiles();
    showScreen('home');
  } catch (err) {
    toast('Error: ' + err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = 'Save Profile';
  }
}

async function triggerScrape(profileId, event) {
  if (event) event.stopPropagation();
  const btn = document.getElementById('scrape-btn-' + profileId);
  if (btn) { btn.disabled = true; btn.textContent = 'Scraping...'; }
  try {
    const res = await fetch(API + '/api/profiles/' + profileId + '/scrape', { method: 'POST' });
    const data = await res.json();
    if (!res.ok) {
      toast('Scrape failed: ' + (data.detail || 'Unknown error'));
      return;
    }
    toast('Scrape complete' + (data.warning ? ' — ' + data.warning : ''));
    loadProfiles();
  } catch (err) {
    toast('Scrape error: ' + err.message);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Scrape'; }
  }
}

async function loadProfiles() {
  const res = await fetch(API + '/api/profiles');
  if (!res.ok) return;
  const profiles = await res.json();
  const list = document.getElementById('profile-list');
  if (!list) return;
  list.innerHTML = profiles.map(p => `
    <div class="profile-card" onclick="selectProfile(${p.id})">
      <h3>${esc(p.name)}</h3>
      <div class="meta">${p.entity_type ? p.entity_type.toUpperCase() : 'PERSON'} • @${esc(p.reddit_handle || p.twitter_handle || '—')}</div>
      <div class="meta">${p.question_count} questions</div>
      ${p.consent_obtained ? '<span class="badge badge--success">✓ Consent</span>' : '<span class="badge badge--danger">⚠ Guest consent required</span>'}
      ${(p.scrape_status === 'pending' || p.scrape_status === 'failed' || p.scrape_status === null) && p.consent_obtained ? `<button id="scrape-btn-${p.id}" class="btn" style="margin-top:0.5rem;font-size:14px;padding:0.5rem;color:var(--accent)" onclick="triggerScrape(${p.id}, event)">Scrape</button>` : ''}
      <span class="status-badge status-${p.scrape_status}">${StateIcon.svg(p.scrape_status)}${p.scrape_status}</span>
      ${p.content_quality ? `<span style="font-size:11px;font-weight:700;color:${p.content_quality==='insufficient'?'var(--wrong)':p.content_quality==='limited'?'var(--accent)':p.content_quality==='rich'?'var(--correct)':'var(--text-secondary)'}">${p.content_quality.toUpperCase()} (${p.content_chunks||0} facts)</span>` : ''}
      ${p.scrape_status === 'done' && p.consent_obtained ? `<button class="btn" style="margin-top:0.5rem;font-size:14px;padding:0.5rem" onclick="event.stopPropagation(); showThingsModal([${p.id}])">Host Game</button>` : ''}
      ${p.scrape_status === 'done' && !p.consent_obtained ? `<button class="btn" style="margin-top:0.5rem;font-size:14px;padding:0.5rem;color:var(--accent)" onclick="event.stopPropagation(); requestConsentLink(${p.id})">Send to Guest</button>` : ''}
    </div>`).join('');
}

// ── Profile selection ────────────────────────────────────────────────────────
async function selectProfile(profileId) {
  const res = await fetch(API + '/api/profiles/' + profileId);
  if (!res.ok) return;
  const p = await res.json();
  currentProfile = p;
  myProfileName = p.name;
  myProfileType = p.entity_type || 'person';
  showScreen('profile');
}

async function createGame(profileId) {
  const res = await fetch(API + '/api/games', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ profile_id: profileId }),
  });
  if (!res.ok) return;
  const game = await res.json();
  myRoomCode = game.room_code;
  showScreen('lobby');
  startLobbyPoll();
}

function showThingsModal(preSelectedProfileIds) {
  selectedThings = preSelectedProfileIds.map(pid => ({ profile_id: pid, num_questions: 50 }));
  const modal = document.getElementById('things-modal');
  renderThingsList();
  modal.style.display = 'flex';
}

function closeThingsModal() {
  document.getElementById('things-modal').style.display = 'none';
  selectedThings = [];
}

function renderThingsList() {
  const list = document.getElementById('things-list');
  if (!list) return;
  if (selectedThings.length === 0) {
    list.innerHTML = '<p style="color:var(--text-secondary);font-size:14px">No profiles selected.</p>';
    return;
  }
  list.innerHTML = selectedThings.map((t, i) => `
    <div style="display:flex;align-items:center;gap:0.5rem;margin-bottom:0.5rem;padding:0.5rem;background:var(--surface);border-radius:4px">
      <span style="color:var(--accent);font-weight:700">${i + 1}.</span>
      <span style="flex:1;color:var(--white)">Profile #${t.profile_id}</span>
      <input type="number" value="${t.num_questions}" min="5" max="100"
        style="width:60px;background:var(--surface-elevated);color:var(--white);border:1px solid var(--accent);padding:0.25rem;text-align:center"
        onchange="selectedThings[${i}].num_questions = parseInt(this.value) || 50">
      <span style="color:var(--text-secondary);font-size:12px">questions</span>
      <button onclick="removeThing(${i})" style="background:none;border:none;color:var(--wrong);font-size:16px;cursor:pointer;padding:0 0.25rem">×</button>
    </div>
  `).join('');
}

function removeThing(index) {
  selectedThings.splice(index, 1);
  renderThingsList();
}

async function submitThingsGame() {
  if (selectedThings.length === 0) { toast('Select at least one profile'); return; }
  closeThingsModal();
  const res = await fetch(API + '/api/games', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ things: selectedThings }),
  });
  if (!res.ok) { toast('Failed to create game'); return; }
  const game = await res.json();
  myRoomCode = game.room_code;
  showScreen('lobby');
  startLobbyPoll();
}

async function joinGame() {
  const roomEl = document.getElementById('room-code-input');
  const nameEl = document.getElementById('player-name-input');
  const roomCode = roomEl ? roomEl.value.trim() : '';
  const playerName = nameEl ? nameEl.value.trim() : '';
  if (!roomCode || !playerName) { toast('Enter room code and name'); return; }
  myPlayerName = playerName;
  localStorage.setItem('obsessed_name', playerName);
  const res = await fetch(API + `/api/games/${roomCode}/join`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ player_id: myPlayerId, player_name: playerName }),
  });
  if (!res.ok) { toast('Room not found or game already started'); return; }
  const result = await res.json();
  myRoomCode = roomCode;
  showScreen('lobby');
  startLobbyPoll();
}

async function requestConsentLink(profileId) {
  const res = await fetch(API + '/api/profiles/' + profileId + '/consent-link');
  if (!res.ok) return;
  toast('Consent link sent');
}

// ── Lobby ─────────────────────────────────────────────────────────────────────
function exitLobby() {
  clearInterval(pollInterval);
  if (ws) { ws.close(); ws = null; }
  myRoomCode = null;
  showScreen('profile');
}

async function startLobbyPoll() {
  if (!myRoomCode) return;
  await pollLobby();
  pollInterval = setInterval(pollLobby, 3000);
}

async function pollLobby() {
  if (!myRoomCode) return;
  const res = await fetch(API + `/api/games/${myRoomCode}`);
  if (!res.ok) return;
  const game = await res.json();
  updateLobbyPlayers(game.players || []);
  if (game.status === 'active') {
    clearInterval(pollInterval);
    showScreen('game');
    loadQuestion();
  }
}

function updateLobbyPlayers(players) {
  const list = document.getElementById('lobby-players');
  if (!list) return;
  const incoming = players || [];
  const seen = new Set();
  // Incremental diff instead of a blanket innerHTML rewrite: existing entries
  // stay put (no blink), and only genuinely-new players stagger-pop in — the
  // same reveal language the results scoreboard uses.
  let fresh = 0;
  incoming.forEach((p) => {
    const pid = String(p.player_id || p.player_name);
    seen.add(pid);
    let entry = Array.from(list.children).find((c) => c.dataset.pid === pid);
    if (!entry) {
      entry = document.createElement('div');
      entry.className = 'player-entry pop';
      entry.dataset.pid = pid;
      // Stagger the batch on first paint; a lone late joiner pops immediately.
      entry.style.animationDelay = (fresh++ * 80) + 'ms';
      list.appendChild(entry);
    }
    // Host crown comes from .host-tag::before — no trailing glyph (avoids dupes).
    entry.classList.toggle('host-tag', !!p.is_host);
    entry.textContent = p.player_name;
  });
  Array.from(list.children).forEach((el) => {
    if (!seen.has(el.dataset.pid)) el.remove();
  });
}

// ── Game ─────────────────────────────────────────────────────────────────────
async function startGame() {
  if (!myRoomCode) return;
  await fetch(API + `/api/games/${myRoomCode}/start`, { method: 'POST' });
}

async function loadQuestion() {
  const res = await fetch(API + `/api/games/${myRoomCode}/question`);
  if (!res.ok) {
    if (res.status === 400) { showResults(); return; }
    return;
  }
  const q = await res.json();
  renderQuestion(q);
}

// Paint the in-play category badge — the single most-looked-at element in a
// round — with the matching pack glyph + label (PHA-1058). The glyph reuses the
// CategoryIcon set shipped for the bang-rack (PHA-810) so the category reads
// instantly without parsing text. Falls back to label-only if CategoryIcon
// isn't loaded, so the badge always renders.
function setCategoryBadge(badge, category) {
  const label = category.replace('_', ' ').toUpperCase();
  const svg = (typeof CategoryIcon !== 'undefined' && CategoryIcon.catIconSVG(category)) || '';
  badge.innerHTML = svg
    ? `<span class="category-badge__icon">${svg}</span><span>${esc(label)}</span>`
    : esc(label);
}

// ── WS-driven question renderer (used by WebSocket new_question events) ───────
function renderQuestionWS(q) {
  document.getElementById('question-progress').textContent = `Question ${q.question_num} / ${q.total_questions}`;
  document.getElementById('question-text').textContent = q.question_text;
  const badge = document.getElementById('category-badge');
  setCategoryBadge(badge, q.category);
  badge.style.background = q.category_color;
  stampBadge(badge);
  const gameScreen = document.getElementById('screen-game');
  gameScreen.style.setProperty('--q-cat-color', q.category_color);
  const grid = document.getElementById('answer-grid');
  grid.innerHTML = (q.options || []).map((opt, i) => `
    <button class="answer-btn stagger-in" style="--i:${i}" onclick="submitAnswer(this, '${esc(opt)}')">${esc(opt)}</button>`).join('');
  startTimerWS(q.timer_seconds || 30);
}

// Toggle the ≤5s crit stopwatch glyph beside the timer bar (PHA-1062). The bar
// already pulses; the glyph makes the time-pressure read instantly (and isn't
// color-only). Idempotent so the per-second tick can call it cheaply.
function setTimerGlyph(crit) {
  const g = document.getElementById('timer-glyph');
  if (!g) return;
  if (crit === g.classList.contains('is-crit')) return;
  g.innerHTML = crit ? StateIcon.svg('timer') : '';
  g.classList.toggle('is-crit', crit);
}

function startTimerWS(seconds) {
  timerSeconds = seconds;
  const fill = document.getElementById('timer-fill');
  clearInterval(timerInterval);
  fill.style.width = '100%';
  fill.className = '';
  setTimerGlyph(false);
  timerInterval = setInterval(() => {
    timerSeconds--;
    const pct = (timerSeconds / 30) * 100;
    fill.style.width = pct + '%';
    if (timerSeconds <= 5) { fill.className = 'crit'; setTimerGlyph(true); }
    else if (timerSeconds <= 10) fill.className = 'warn';
    if (timerSeconds <= 0) { clearInterval(timerInterval); submitAnswer(null, '__timeout__'); }
  }, 1000);
}

function renderQuestion(q) {
  document.getElementById('question-progress').textContent = `Question ${q.question_num} / ${q.total_questions}`;
  document.getElementById('question-text').textContent = q.question_text;
  const badge = document.getElementById('category-badge');
  setCategoryBadge(badge, q.category);
  badge.style.background = q.category_color;
  stampBadge(badge);
  document.getElementById('screen-game').style.setProperty('--q-cat-color', q.category_color);
  const grid = document.getElementById('answer-grid');
  grid.innerHTML = q.options.map((opt, i) => `
    <button class="answer-btn stagger-in" style="--i:${i}" onclick="submitAnswer(this, '${esc(opt)}')">${esc(opt)}</button>`).join('');
  
  // Timer
  timerSeconds = q.timer_seconds;
  const fill = document.getElementById('timer-fill');
  clearInterval(timerInterval);
  fill.style.width = '100%';
  fill.className = '';
  setTimerGlyph(false);
  timerInterval = setInterval(() => {
    timerSeconds--;
    const pct = (timerSeconds / 30) * 100;
    fill.style.width = pct + '%';
    if (timerSeconds <= 5) { fill.className = 'crit'; setTimerGlyph(true); }
    else if (timerSeconds <= 10) fill.className = 'warn';
    if (timerSeconds <= 0) { clearInterval(timerInterval); submitAnswer(null, '__timeout__'); }
  }, 1000);
}

// Stamp the category badge in on each new question (PHA-1031). The badge is a
// persistent element, so restart the CSS animation by toggling the class with a
// forced reflow (the answer buttons re-animate for free — innerHTML replaces them).
function stampBadge(badge) {
  if (!badge) return;
  badge.classList.remove('stamp-in');
  void badge.offsetWidth; // reflow so the stamp animation restarts each question
  badge.classList.add('stamp-in');
}

// Flash the category-color screen-edge glow on a correct answer (PHA-1030).
// Re-triggers the CSS animation by removing the class, forcing reflow, re-adding.
function flashEdgeGlow() {
  const glow = document.getElementById('edge-glow');
  if (!glow) return;
  glow.classList.remove('flash');
  void glow.offsetWidth; // reflow so the animation restarts each correct answer
  glow.classList.add('flash');
}

// Stamp a branded check (correct) or X (wrong) glyph onto an answer button
// (PHA-1059), reusing the stamp/pop motion language of PHA-1030/1031. Color
// alone is a red/green accessibility gap, so the glyph carries the result
// non-chromatically. SVG has no text, so it doesn't disturb the .textContent
// answer matching done above the call.
const ANSWER_GLYPH = {
  correct: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"><path d="M4 13l5 5L20 6"/></svg>',
  wrong:   '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"><path d="M6 6l12 12M18 6L6 18"/></svg>',
};
function stampResultGlyph(btn, kind) {
  if (!btn || btn.querySelector('.answer-glyph')) return;
  const g = document.createElement('span');
  g.className = 'answer-glyph stamp';
  g.innerHTML = ANSWER_GLYPH[kind];
  btn.appendChild(g);
}

// ── WS-driven answer result ───────────────────────────────────────────────────
function showAnswerResultWS(msg) {
  const opts = document.querySelectorAll('.answer-btn');
  opts.forEach(b => {
    b.classList.remove('stagger-in'); // drop entry anim so correctPop (PHA-1030) isn't overridden
    if (b.textContent === msg.correct_answer) { b.classList.add('correct'); stampResultGlyph(b, 'correct'); }
    else if (b.textContent === msg.answer_text && !msg.is_correct) { b.classList.add('wrong'); stampResultGlyph(b, 'wrong'); }
    b.disabled = true;
  });
  if (msg.is_correct) {
    flashEdgeGlow();
    if (msg.category && bangRack) bangRack.fill(msg.category, { burst: true });
  }
  // PHA-1335: update wedge board from the broadcast player_scores.
  if (msg.player_scores && msg.player_scores[myPlayerId]) {
    renderWedgeBoard(msg.player_scores[myPlayerId].wedges || []);
  }
  toast(msg.is_correct ? `+${msg.points_earned} pts!` : 'Wrong!');
}

// ── Wedge board renderer ──────────────────────────────────────────────────────
// SPEC: 6 category wedges. Filled wedges show in category color. Empty wedges
// show with category color outline and dark fill.
//
// Defaults to the live game-screen board (#wedge-board); pass a target to
// render onto another container (e.g. the results screen).
function renderWedgeBoard(playerWedges) {
  renderWedgeBoardOnto(playerWedges, document.getElementById('wedge-board'));
}

function renderWedgeBoardOnto(playerWedges, target) {
  if (!target) return;
  const earned = new Set(playerWedges || []);
  target.innerHTML = CATEGORIES.map(cat => {
    const filled = earned.has(cat);
    const color = CATEGORY_COLORS[cat] || '#ffffff';
    const label = CATEGORY_LABELS[cat] || cat.slice(0, 3).toUpperCase();
    return `<div class="wedge-slot ${filled ? 'filled' : ''}"
                 data-category="${cat}"
                 title="${cat.replace('_', ' ')}${filled ? ' — earned' : ''}"
                 style="border-color: ${color}; ${filled ? `background: ${color};` : ''}">${filled ? '★' : label}</div>`;
  }).join('');
}

async function submitAnswer(btn, answer) {
  clearInterval(timerInterval);
  const startMs = (30 - timerSeconds) * 1000;
  if (btn) btn.classList.add('selected');
  
  const res = await fetch(API + `/api/games/${myRoomCode}/answer`, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ player_id: myPlayerId, answer_text: answer, time_taken_ms: startMs }),
  });
  
  if (res.ok) {
    const result = await res.json();
    const opts = document.querySelectorAll('.answer-btn');
    opts.forEach(b => {
      b.classList.remove('stagger-in'); // drop entry anim so correctPop (PHA-1030) isn't overridden
      if (b.textContent === result.correct_answer) { b.classList.add('correct'); stampResultGlyph(b, 'correct'); }
      else if (b === btn && !result.is_correct) { b.classList.add('wrong'); stampResultGlyph(b, 'wrong'); }
      b.disabled = true;
    });
    if (result.is_correct) {
      flashEdgeGlow();
      if (result.category && bangRack) bangRack.fill(result.category, { burst: true });
    }
    toast(result.is_correct ? `+${result.points_earned} pts!` : 'Wrong!');
  }

  setTimeout(async () => {
    await fetch(API + `/api/games/${myRoomCode}/next`, { method: 'POST' });
    const nextRes = await fetch(API + `/api/games/${myRoomCode}/question`);
    if (nextRes.ok) {
      loadQuestion();
    } else {
      showResults();
    }
  }, 2000);
}

// ── WS-driven results screen ──────────────────────────────────────────────────
function showResultsWS(msg) {
  clearInterval(pollInterval);
  if (ws) { ws.close(); ws = null; }
  showScreen('results');
  fetch(API + `/api/games/${myRoomCode}/scores`).then(async res => {
    if (!res.ok) return;
    const scores = await res.json();
    renderResults(scores, msg);
  });
}

async function showResults() {
  clearInterval(pollInterval);
  if (ws) { ws.close(); ws = null; }
  showScreen('results');
  const res = await fetch(API + `/api/games/${myRoomCode}/scores`);
  if (!res.ok) return;
  const scores = await res.json();
  renderResults(scores);
}

function renderResults(scores, wsMsg) {
  const winner = scores[0];
  const logoEl = document.getElementById('winner-bang-logo');
  // PR-8 wants a big celebratory lockup on the winner card — stacked (120px
  // circle above the wordmark) reads large and pulses, unlike compact.
  if (logoEl) renderBang(logoEl, { variant: 'stacked', wordmark: true });
  // PHA-1335: when game_over is broadcast with reason='wedges', the wedge-
  // complete player wins outright even if their score is lower. Surface
  // that in the winner line and credit wedges-earned in the scoreboard.
  const reason = wsMsg && wsMsg.reason;
  const winnerName = (wsMsg && wsMsg.winner_player_name) || (winner && winner.player_name);
  let winnerLine;
  if (reason === 'wedges' && winner) {
    winnerLine = `🏆 ${esc(winnerName)} wins by wedge! (all 6 categories)  ${winner.score.toLocaleString()} pts`;
  } else if (winner) {
    winnerLine = `${esc(winnerName)} wins!  ${winner.score.toLocaleString()} pts`;
  } else {
    winnerLine = 'No winner';
  }
  document.getElementById('winner-display').textContent = winnerLine;
  document.getElementById('final-scores').innerHTML = scores.map((s, i) => `
    <div class="score-row ${i === 0 ? 'top' : ''}">
      <span>${i+1}. ${esc(s.player_name)}${s.wedges ? ' (' + s.wedges.length + '/6)' : ''}</span>
      <span class="score-val">${s.score.toLocaleString()}</span>
    </div>`).join('');
  // PHA-1335: paint the winner's wedge board onto the results screen so the
  // visual win state is preserved after the live game screen closes.
  if (winner) {
    const board = document.getElementById('results-wedge-board');
    if (board) renderWedgeBoardOnto(winner.wedges || [], board);
  }
  fireConfetti();
}

function fireConfetti() {
  const layer = document.getElementById('confetti-layer');
  if (!layer) return;
  layer.innerHTML = '';
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  const COLORS = ['#e94560','#ffeb3b','#00e676','#2979ff','#d500f9','#ff6d00'];
  const SHAPES = ['2px', '6px', '10px'];
  for (let i = 0; i < 60; i++) {
    const p = document.createElement('div');
    p.className = 'piece';
    p.style.left = Math.random() * 100 + '%';
    p.style.background = COLORS[i % COLORS.length];
    p.style.borderRadius = Math.random() > 0.5 ? '50%' : '2px';
    const size = SHAPES[Math.floor(Math.random() * SHAPES.length)];
    p.style.width = size;
    p.style.height = size;
    p.style.animationDuration = (1.5 + Math.random() * 2) + 's';
    p.style.animationDelay = (Math.random() * 1.2) + 's';
    layer.appendChild(p);
  }
  setTimeout(() => { layer.innerHTML = ''; }, 4000);
}

async function startGamePoll() {
  loadQuestion();
}

// ── Leaderboard ──────────────────────────────────────────────────────────────
async function loadLeaderboard() {
  const res = await fetch(API + '/api/stats/leaderboard');
  if (!res.ok) return;
  const lb = await res.json();
  const el = document.getElementById('leaderboard-content');
  if (!el) return;
  el.innerHTML = lb.map((e, i) => `
    <div class="score-row ${i < 3 ? 'top' : ''}">
      <span>#${i+1} ${esc(e.player_name)}</span>
      <span class="score-val">${e.total_score.toLocaleString()}</span>
    </div>`).join('');
}

// ── Boot ─────────────────────────────────────────────────────────────────────
// Landing mark breathes (slow pulse) so the front door has a heartbeat — PHA-1033.
renderBang(document.getElementById('home-logo'), { variant: 'primary', wordmark: true, breathe: true });

// ── Utilities ────────────────────────────────────────────────────────────────
function esc(s) {
  if (!s) return '';
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
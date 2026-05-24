const API = '';
let currentProfile = null;
let currentGame = null;
let myPlayerId = localStorage.getItem('obsessed_pid') || (localStorage.setItem('obsessed_pid', 'p_' + Math.random().toString(36).slice(2)), localStorage.getItem('obsessed_pid'));
let myPlayerName = localStorage.getItem('obsessed_name') || '';
let myProfileName = '';
let myProfileType = 'person';
let myRoomCode = null;
let selectedThings = [];  // [{profile_id, num_questions}]
let pollInterval = null;
let timerInterval = null;
let timerSeconds = 30;
let ws = null;
let wsReconnectTimer = null;

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
      showResultsWS();
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
  if (name === 'home') renderHomeReturning();
  if (name === 'lobby') {
    // Fresh entrance: forget who we'd already popped in so bubbles re-animate.
    _lobbyKnownIds.clear();
    updateRoomCode();
    // Brand centerpiece: render the empty wheel + legend, then fill it
    // slice-by-slice (center-out bounce + burst) as a "box-cover" entrance.
    const lw = document.getElementById('lobby-wedge');
    if (lw) {
      renderWedge(lw);
      fillWedge(lw, WEDGE_KEYS, { stagger: 110 });
      // Wheel-complete is a celebration beat → confetti once the last slice lands.
      setTimeout(() => launchConfetti(), WEDGE_KEYS.length * 110 + 200);
    }
    renderLegend(document.getElementById('lobby-legend'));
    connectWS(myRoomCode);
    startLobbyPoll(); // fallback polling — can be reduced or removed once WS is stable
  }
  if (name === 'game') {
    // In-game progress board — empty wheel; later passes fill earned wedges.
    renderWedge(document.getElementById('wedge-board'));
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
      ${p.consent_obtained ? '<span style="color:var(--correct);font-size:12px;font-weight:700">✓ CONSENT</span>' : '<span style="color:var(--wrong);font-size:12px;font-weight:700">⚠ GUEST CONSENT REQUIRED</span>'}
      ${(p.scrape_status === 'pending' || p.scrape_status === 'failed' || p.scrape_status === null) && p.consent_obtained ? `<button id="scrape-btn-${p.id}" class="btn" style="margin-top:0.5rem;font-size:14px;padding:0.5rem;color:var(--accent)" onclick="triggerScrape(${p.id}, event)">Scrape</button>` : ''}
      <span class="status-badge status-${p.scrape_status}">${p.scrape_status}</span>
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
  closeJoinModal();
  showScreen('lobby');
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
  _lobbyKnownIds.clear();
  showScreen('profile');
}

async function startLobbyPoll() {
  if (!myRoomCode) return;
  clearInterval(pollInterval); // guard against a second poller (callers also enter via showScreen)
  await pollLobby();
  pollInterval = setInterval(pollLobby, 3000);
}

async function pollLobby() {
  if (!myRoomCode) return;
  const res = await fetch(API + `/api/games/${myRoomCode}`);
  if (!res.ok) return;
  const game = await res.json();
  updateGuestCard(game);
  updateLobbyPlayers(game.players || []);
  if (game.status === 'active') {
    clearInterval(pollInterval);
    showScreen('game');
    loadQuestion();
  }
}

// Player ids already popped-in this lobby session (so polling doesn't re-animate
// everyone every 3s — only genuinely new joiners bounce in). Reset on entry/exit.
const _lobbyKnownIds = new Set();

function _initials(name) {
  const parts = String(name || '?').trim().split(/\s+/);
  const a = (parts[0] || '?')[0] || '?';
  const b = parts.length > 1 ? (parts[1][0] || '') : '';
  return (a + b).toUpperCase();
}

function updateLobbyPlayers(players) {
  const list = document.getElementById('lobby-players');
  if (!list) return;
  players = players || [];
  if (players.length === 0) {
    list.innerHTML = '<span class="player-bubbles__empty">Waiting for players to join…</span>';
  } else {
    list.innerHTML = players.map((p, i) => {
      const id = p.player_id || p.player_name;
      const cat = WEDGE_KEYS[i % WEDGE_KEYS.length]; // cycle the 6 brand colors
      const isNew = id && !_lobbyKnownIds.has(id);
      if (id) _lobbyKnownIds.add(id);
      const cls = 'player-bubble' +
        (p.is_host ? ' player-bubble--host' : '') +
        (isNew ? ' player-bubble--pop' : '');
      return `<div class="${cls}" data-cat="${cat}">` +
               `<div class="player-bubble__avatar">${esc(_initials(p.player_name))}</div>` +
               `<div class="player-bubble__name">${esc(p.player_name)}</div>` +
             `</div>`;
    }).join('');
  }
  // Start pulses once there are enough players to actually play.
  const startBtn = document.getElementById('start-game-btn');
  if (startBtn) {
    const active = players.filter(p => p.is_active !== false).length;
    startBtn.classList.toggle('is-ready', active >= 2);
  }
}

// Guest-profile card up top — who this game is about + pool size. Name comes
// from myProfileName when the host already knows it; otherwise resolved once
// from the game's profile_id (read-only /api/profiles/{id}). Multi-profile
// ("things") games have no single profile_id → labelled "Party Mix".
let _guestNameCache = {};
async function updateGuestCard(game) {
  const card = document.getElementById('lobby-guest');
  if (!card) return;
  let name = myProfileName || '';
  const pool = game && game.total_questions;
  if (!name && game && game.profile_id) {
    if (_guestNameCache[game.profile_id]) {
      name = _guestNameCache[game.profile_id];
    } else {
      try {
        const r = await fetch(API + '/api/profiles/' + game.profile_id);
        if (r.ok) {
          const p = await r.json();
          name = p.name || '';
          _guestNameCache[game.profile_id] = name;
          myProfileName = name;
        }
      } catch (e) { /* card stays hidden if we can't resolve a name */ }
    }
  }
  if (!name && game && !game.profile_id) name = 'Party Mix';
  if (!name) { card.hidden = true; return; }
  card.hidden = false;
  card.innerHTML =
    `<div class="guest-card__avatar">${esc((name[0] || '?').toUpperCase())}</div>` +
    `<div class="guest-card__text">` +
      `<span class="guest-card__name">${esc(name)}</span>` +
      (pool ? `<span class="guest-card__meta">${pool} questions</span>` : '') +
    `</div>`;
}

// ── Room code + share ─────────────────────────────────────────────────────────
function updateRoomCode() {
  const el = document.getElementById('room-code-display');
  if (el) el.textContent = myRoomCode || '------';
  const urlEl = document.getElementById('share-url');
  if (urlEl) urlEl.textContent = _joinUrl();
}
function _joinUrl() {
  return location.origin + '/?join=' + encodeURIComponent(myRoomCode || '');
}
function copyRoomCode() {
  if (!myRoomCode) { toast('No room code yet'); return; }
  _copyText(myRoomCode, 'Room code copied');
}
function copyShareLink() { _copyText(_joinUrl(), 'Join link copied'); }
function toggleSharePanel() {
  const p = document.getElementById('share-panel');
  if (p) p.classList.toggle('is-shown');
}
function _copyText(text, okMsg) {
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(() => toast(okMsg)).catch(() => toast(text));
  } else {
    toast(text); // clipboard API unavailable (insecure context) — show it to copy by hand
  }
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

// ── WS-driven question renderer (used by WebSocket new_question events) ───────
function renderQuestionWS(q) {
  document.getElementById('question-progress').textContent = `Question ${q.question_num} / ${q.total_questions}`;
  document.getElementById('question-text').textContent = q.question_text;
  const badge = document.getElementById('category-badge');
  badge.textContent = q.category.replace('_', ' ').toUpperCase();
  badge.style.background = q.category_color;
  const grid = document.getElementById('answer-grid');
  grid.innerHTML = (q.options || []).map(opt => `
    <button class="answer-btn" onclick="submitAnswer(this, '${esc(opt)}')">${esc(opt)}</button>`).join('');
  startTimerWS(q.timer_seconds || 30);
}

function startTimerWS(seconds) {
  timerSeconds = seconds;
  const fill = document.getElementById('timer-fill');
  clearInterval(timerInterval);
  fill.style.width = '100%';
  fill.className = '';
  timerInterval = setInterval(() => {
    timerSeconds--;
    const pct = (timerSeconds / 30) * 100;
    fill.style.width = pct + '%';
    if (timerSeconds <= 5) fill.className = 'crit';
    else if (timerSeconds <= 10) fill.className = 'warn';
    if (timerSeconds <= 0) { clearInterval(timerInterval); submitAnswer(null, '__timeout__'); }
  }, 1000);
}

function renderQuestion(q) {
  document.getElementById('question-progress').textContent = `Question ${q.question_num} / ${q.total_questions}`;
  document.getElementById('question-text').textContent = q.question_text;
  const badge = document.getElementById('category-badge');
  badge.textContent = q.category.replace('_', ' ').toUpperCase();
  badge.style.background = q.category_color;
  const grid = document.getElementById('answer-grid');
  grid.innerHTML = q.options.map(opt => `
    <button class="answer-btn" onclick="submitAnswer(this, '${esc(opt)}')">${esc(opt)}</button>`).join('');
  
  // Timer
  timerSeconds = q.timer_seconds;
  const fill = document.getElementById('timer-fill');
  clearInterval(timerInterval);
  fill.style.width = '100%';
  fill.className = '';
  timerInterval = setInterval(() => {
    timerSeconds--;
    const pct = (timerSeconds / 30) * 100;
    fill.style.width = pct + '%';
    if (timerSeconds <= 5) fill.className = 'crit';
    else if (timerSeconds <= 10) fill.className = 'warn';
    if (timerSeconds <= 0) { clearInterval(timerInterval); submitAnswer(null, '__timeout__'); }
  }, 1000);
}

// ── WS-driven answer result ───────────────────────────────────────────────────
function showAnswerResultWS(msg) {
  const opts = document.querySelectorAll('.answer-btn');
  opts.forEach(b => {
    if (b.textContent === msg.correct_answer) b.classList.add('correct');
    else if (b.textContent === msg.answer_text && !msg.is_correct) b.classList.add('wrong');
    b.disabled = true;
  });
  toast(msg.is_correct ? `+${msg.points_earned} pts!` : 'Wrong!');
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
      if (b.textContent === result.correct_answer) b.classList.add('correct');
      else if (b === btn && !result.is_correct) b.classList.add('wrong');
      b.disabled = true;
    });
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
function showResultsWS() {
  clearInterval(pollInterval);
  if (ws) { ws.close(); ws = null; }
  showScreen('results');
  fetch(API + `/api/games/${myRoomCode}/scores`).then(async res => {
    if (!res.ok) return;
    const scores = await res.json();
    const winner = scores[0];
    document.getElementById('winner-display').textContent = winner ? `🏆 ${esc(winner.player_name)} wins!` : 'No winner';
    document.getElementById('final-scores').innerHTML = scores.map((s, i) => `
      <div class="score-row ${i === 0 ? 'top' : ''}">
        <span>${i+1}. ${esc(s.player_name)}</span>
        <span class="score-val">${s.score.toLocaleString()}</span>
      </div>`).join('');
    _celebrateWin(winner);
  });
}

async function showResults() {
  clearInterval(pollInterval);
  if (ws) { ws.close(); ws = null; }
  showScreen('results');
  const res = await fetch(API + `/api/games/${myRoomCode}/scores`);
  if (!res.ok) return;
  const scores = await res.json();
  const winner = scores[0];
  document.getElementById('winner-display').textContent = winner ? `🏆 ${esc(winner.player_name)} wins!` : 'No winner';
  document.getElementById('final-scores').innerHTML = scores.map((s, i) => `
    <div class="score-row ${i === 0 ? 'top' : ''}">
      <span>${i+1}. ${esc(s.player_name)}</span>
      <span class="score-val">${s.score.toLocaleString()}</span>
    </div>`).join('');
  _celebrateWin(winner);
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

// ── Utilities ────────────────────────────────────────────────────────────────
function esc(s) {
  if (!s) return '';
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// ── Wedge component (PHA-805 / PR-2) ──────────────────────────────────────────
// The Trivial-Pursuit wheel: one reusable SVG, six fillable slices. Render order
// must match the backend category set (app/services/generator.py CATEGORIES) and
// the colors in app/routes/games.py CATEGORY_COLORS. Colors resolve in CSS via
// the [data-cat="…"] → --slice-color mapping in wedge.css; JS only sets geometry.
const SVG_NS = 'http://www.w3.org/2000/svg';
const WEDGE_CATEGORIES = [
  { key: 'history',        label: 'History' },
  { key: 'entertainment',  label: 'Entertainment' },
  { key: 'geography',      label: 'Geography' },
  { key: 'science',        label: 'Science' },
  { key: 'sports',         label: 'Sports' },
  { key: 'art_literature', label: 'Arts' },
];
const WEDGE_KEYS = WEDGE_CATEGORIES.map(c => c.key);

const WEDGE_R = 44;       // slice radius (viewBox is -50..50)
const WEDGE_SEG = 60;     // degrees per slice (6 × 60 = 360)
const WEDGE_GAP = 1.6;    // angular gap (deg) each side → dark separators
const WEDGE_CENTROID_R = 27; // burst origin distance from center along bisector

function _wedgePolar(deg, r) {
  const a = deg * Math.PI / 180;
  return [r * Math.cos(a), r * Math.sin(a)];
}
// Pie sector with apex at center (0,0); each slice scales from there outward.
function _wedgeSlicePath(i) {
  const a0 = -90 + i * WEDGE_SEG + WEDGE_GAP;
  const a1 = -90 + (i + 1) * WEDGE_SEG - WEDGE_GAP;
  const [x0, y0] = _wedgePolar(a0, WEDGE_R);
  const [x1, y1] = _wedgePolar(a1, WEDGE_R);
  return `M0 0 L${x0.toFixed(2)} ${y0.toFixed(2)} ` +
         `A${WEDGE_R} ${WEDGE_R} 0 0 1 ${x1.toFixed(2)} ${y1.toFixed(2)} Z`;
}

function _prefersReducedMotion() {
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

// Render the SVG board into `el`. `filledKeys` paint instantly (no animation);
// use fillWedge()/fillWedgeSlice() afterward for the animated center-out fill.
function renderWedge(el, filledKeys = []) {
  if (!el) return;
  const filled = new Set(filledKeys);
  const slices = WEDGE_CATEGORIES.map((c, i) => {
    const d = _wedgeSlicePath(i);
    const on = filled.has(c.key) ? ' wedge__slice--filled' : '';
    return `<path class="wedge__ghost" data-cat="${c.key}" d="${d}"></path>` +
           `<path class="wedge__slice${on}" data-cat="${c.key}" d="${d}"></path>`;
  }).join('');
  el.innerHTML =
    `<svg viewBox="-50 -50 100 100" role="img" aria-label="Trivia category wedge board">` +
      `<circle class="wedge__rim" r="47" stroke-width="3"></circle>` +
      slices +
      `<circle class="wedge__hub" r="8.5" stroke-width="2"></circle>` +
      `<g class="wedge__burst"></g>` +
    `</svg>`;
}

// Fill one slice (center-out bounce) + centroid particle burst.
function fillWedgeSlice(el, key) {
  if (!el) return;
  const slice = el.querySelector(`.wedge__slice[data-cat="${key}"]`);
  if (!slice || slice.classList.contains('wedge__slice--filled')) return;
  slice.classList.add('wedge__slice--filled');
  if (_prefersReducedMotion()) return; // opacity fade only, no burst
  _wedgeBurst(el, key);
}

// Fill several slices with a stagger (the lobby "box-cover" entrance).
function fillWedge(el, keys = WEDGE_KEYS, { stagger = 90 } = {}) {
  keys.forEach((key, i) => setTimeout(() => fillWedgeSlice(el, key), i * stagger));
}

function _wedgeBurst(el, key) {
  const burst = el.querySelector('.wedge__burst');
  if (!burst) return;
  const i = WEDGE_KEYS.indexOf(key);
  if (i < 0) return;
  const mid = (-90 + i * WEDGE_SEG + WEDGE_SEG / 2) * Math.PI / 180;
  const [cx, cy] = [Math.cos(mid) * WEDGE_CENTROID_R, Math.sin(mid) * WEDGE_CENTROID_R];
  for (let p = 0; p < 10; p++) {
    const ang = mid + (Math.random() - 0.5) * (50 * Math.PI / 180); // ±25° spread
    const dist = 12 + Math.random() * 16;
    const c = document.createElementNS(SVG_NS, 'circle');
    c.setAttribute('class', 'wedge__particle');
    c.setAttribute('data-cat', key);
    c.setAttribute('cx', cx.toFixed(2));
    c.setAttribute('cy', cy.toFixed(2));
    c.setAttribute('r', (0.9 + Math.random() * 1.3).toFixed(2));
    c.style.setProperty('--dx', (Math.cos(ang) * dist).toFixed(2));
    c.style.setProperty('--dy', (Math.sin(ang) * dist).toFixed(2));
    c.style.animationDelay = Math.round(Math.random() * 40) + 'ms';
    burst.appendChild(c);
    setTimeout(() => c.remove(), 900);
  }
}

// Render the 6-icon legend from the same category source.
function renderLegend(el) {
  if (!el) return;
  el.innerHTML = WEDGE_CATEGORIES.map(c =>
    `<div class="wedge-legend__item" data-cat="${c.key}">` +
      `<svg class="category-icon" aria-hidden="true"><use href="#cat-icon-${c.key}"></use></svg>` +
      `<span>${c.label}</span>` +
    `</div>`).join('');
}

// ── Confetti (PHA-806 / PR-3) ─────────────────────────────────────────────────
// CSS-keyframe particle system — no canvas, no library (brief §4.3/§4.5). Each
// particle is a 6×16 rounded rect that tumbles down a 2D arc in a category color.
// Count is capped at 60. JS only seeds per-particle randomness via custom props;
// the motion lives entirely in the `confetti-fall` keyframes. Skipped wholesale
// under prefers-reduced-motion. Triggered on wedge-complete + game win.
const CONFETTI_CSS_COLORS = [
  '--cat-history', '--cat-entertainment', '--cat-geography',
  '--cat-science', '--cat-sports', '--cat-art',
];
const CONFETTI_MAX = 60;
function launchConfetti(count = CONFETTI_MAX) {
  if (_prefersReducedMotion()) return;
  const layer = document.getElementById('confetti-layer');
  if (!layer) return;
  const n = Math.min(count, CONFETTI_MAX);
  const frag = document.createDocumentFragment();
  let longest = 0;
  for (let i = 0; i < n; i++) {
    const p = document.createElement('div');
    p.className = 'confetti__particle';
    p.style.background = `var(${CONFETTI_CSS_COLORS[i % CONFETTI_CSS_COLORS.length]})`;
    p.style.left = (Math.random() * 100).toFixed(2) + 'vw';
    const drift = (Math.random() * 2 - 1) * 30;                 // ±30vw horizontal arc
    const dur = 2600 + Math.random() * 800;                     // 2.6–3.4s lifespan
    const delay = Math.round(Math.random() * 400);
    p.style.setProperty('--cf-x', drift.toFixed(1) + 'vw');
    p.style.setProperty('--cf-rot', (360 + Math.random() * 720).toFixed(0) + 'deg');
    p.style.setProperty('--cf-dur', dur.toFixed(0) + 'ms');
    p.style.setProperty('--cf-delay', delay + 'ms');
    longest = Math.max(longest, dur + delay);
    frag.appendChild(p);
  }
  layer.appendChild(frag);
  // Sweep the layer once the last particle has fallen — keep the DOM clean.
  setTimeout(() => { layer.innerHTML = ''; }, longest + 300);
}

// Record the just-finished game for the home returning-card + celebrate (§5.1).
function _celebrateWin(winner) {
  if (winner && winner.player_name) localStorage.setItem('obsessed_last_winner', winner.player_name);
  if (myProfileName) localStorage.setItem('obsessed_last_guest', myProfileName);
  launchConfetti(); // §4.3 game-win trigger
}

// ── Join modal (PHA-806 / PR-3) ───────────────────────────────────────────────
function openJoinModal() {
  const m = document.getElementById('join-modal');
  if (!m) return;
  const nameEl = document.getElementById('player-name-input');
  if (nameEl && myPlayerName) nameEl.value = myPlayerName;
  m.classList.add('is-open');
  const codeEl = document.getElementById('room-code-input');
  if (codeEl) codeEl.focus();
}
function closeJoinModal() {
  const m = document.getElementById('join-modal');
  if (m) m.classList.remove('is-open');
}

// ── Home: returning-visitor mini-card + brand art (PHA-806 / PR-3) ────────────
function renderHomeReturning() {
  const el = document.getElementById('home-returning');
  if (!el) return;
  const guest = localStorage.getItem('obsessed_last_guest');
  const winner = localStorage.getItem('obsessed_last_winner');
  if (!guest && !winner) { el.classList.remove('is-shown'); return; }
  el.innerHTML =
    (guest ? `<span>Last game · <b>${esc(guest)}</b></span>` : '') +
    (winner ? `<span>Champion · <b>${esc(winner)}</b> 🏆</span>` : '');
  el.classList.add('is-shown');
}

function _initHomeBrand() {
  // Emblem beside the wordmark: a fully-filled wheel = the brand mark.
  renderWedge(document.getElementById('home-emblem'), WEDGE_KEYS);
  // Bottom-right decorative silhouette: full wheel, dimmed by CSS opacity.
  renderWedge(document.getElementById('home-corner-wedge'), WEDGE_KEYS);
}

// Shared join links land on /?join=CODE → prefill + open the join modal.
function _maybeAutoJoin() {
  const code = new URLSearchParams(location.search).get('join');
  if (!code) return;
  const codeEl = document.getElementById('room-code-input');
  if (codeEl) codeEl.value = code;
  openJoinModal();
}

// ── Page init ─────────────────────────────────────────────────────────────────
_initHomeBrand();
renderHomeReturning();
_maybeAutoJoin();
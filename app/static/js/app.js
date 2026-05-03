const API = '';
let currentProfile = null;
let currentGame = null;
let myPlayerId = localStorage.getItem('obsessed_pid') || (localStorage.setItem('obsessed_pid', 'p_' + Math.random().toString(36).slice(2)), localStorage.getItem('obsessed_pid'));
let myPlayerName = localStorage.getItem('obsessed_name') || '';
let myProfileName = '';
let myProfileType = 'person';
let myRoomCode = null;
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
  if (name === 'lobby') {
    connectWS(myRoomCode);
    startLobbyPoll(); // fallback polling — can be reduced or removed once WS is stable
  }
  if (name === 'game') {
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
      ${p.scrape_status === 'done' && p.consent_obtained ? `<button class="btn" style="margin-top:0.5rem;font-size:14px;padding:0.5rem" onclick="event.stopPropagation(); createGame(${p.id})">Host Game</button>` : ''}
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
  if (list) {
    list.innerHTML = (players || []).map(p => `
      <div class="player-entry ${p.is_host ? 'host-tag' : ''}">${esc(p.player_name)} ${p.is_host ? '👑' : ''}</div>`).join('');
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
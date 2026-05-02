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

// ── Screen management ────────────────────────────────────────────────────────
function showScreen(name) {
  document.querySelectorAll('.screens').forEach(s => s.classList.remove('active'));
  const map = { home: 'screen-home', profile: 'screen-profile', lobby: 'screen-lobby', game: 'screen-game', results: 'screen-results', history: 'screen-history' };
  const el = document.getElementById(map[name]);
  if (el) el.classList.add('active');
  if (name !== 'game') { clearInterval(pollInterval); clearInterval(timerInterval); }
  if (name === 'lobby') startLobbyPoll();
  if (name === 'game') startGamePoll();
}

// ── Toast ────────────────────────────────────────────────────────────────────
function toast(msg) {
  const t = document.getElementById('toast');
  t.textContent = msg; t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 3000);
}

// ── Profile management ────────────────────────────────────────────────────────
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
      <span class="status-badge status-${p.scrape_status}">${p.scrape_status}</span>
      ${p.content_quality ? `<span style="font-size:11px;font-weight:700;color:${p.content_quality==='insufficient'?'var(--wrong)':p.content_quality==='limited'?'var(--accent)':p.content_quality==='rich'?'var(--correct)':'var(--text-secondary)'}">${p.content_quality.toUpperCase()} (${p.content_chunks||0} facts)</span>` : ''}
      ${p.scrape_status === 'done' && p.consent_obtained ? `<button class="btn" style="margin-top:0.5rem;font-size:14px;padding:0.5rem" onclick="event.stopPropagation(); createGame(${p.id})">Host Game</button>` : ''}
      ${p.scrape_status === 'done' && !p.consent_obtained ? `<button class="btn" style="margin-top:0.5rem;font-size:14px;padding:0.5rem;color:var(--accent)" onclick="event.stopPropagation(); requestConsentLink(${p.id})">Send to Guest</button>` : ''}
    </div>`).join('');
}

async function requestConsentLink(profileId) {
  const res = await fetch(API + '/api/profiles/' + profileId + '/consent-link');
  if (!res.ok) return toast('Error generating link');
  const data = await res.json();
  const fullUrl = window.location.origin + data.consent_link;
  const msg = 'Send this to your guest:\n' + fullUrl;
  if (navigator.clipboard) {
    navigator.clipboard.writeText(fullUrl).then(() => toast('Consent link copied!')).catch(() => toast(msg));
  } else {
    prompt('Copy this consent link:', fullUrl);
  }
}

async function submitProfile(e) {
  e.preventDefault();
  const fd = new FormData(e.target);
  const data = Object.fromEntries(fd.entries());
  const res = await fetch(API + '/api/profiles', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(data),
  });
  if (!res.ok) return toast('Error saving profile');
  const p = await res.json();
  currentProfile = p;
  toast('Profile saved!');
  e.target.reset();
  loadProfiles();
  if (p.scrape_status === 'pending') {
    // Auto-scrape if handles provided
    if (data.reddit_handle || data.manual_facts || data.threads_handle || data.pinterest_handle || data.instagram_handle || data.manual_link) {
      toast('Scraping in background...');
      const scrapeRes = await fetch(API + `/api/profiles/${p.id}/scrape`, { method: 'POST' });
      const scrapeData = await scrapeRes.json().catch(() => ({}));
      if (scrapeData.warning) toast('⚠ ' + scrapeData.warning);
      else if (scrapeRes.ok) toast('Scraping complete!');
      loadProfiles();
    }
  }
}

async function selectProfile(id) {
  const res = await fetch(API + `/api/profiles/${id}`);
  if (!res.ok) return;
  currentProfile = await res.json();
  showScreen('profile');
}

async function createGame(profileId) {
  // Fetch profile for display context
  const profRes = await fetch(API + '/api/profiles/' + profileId);
  let prof = null;
  if (profRes.ok) prof = await profRes.json();
  myProfileName = prof ? prof.name : '';
  myProfileType = prof ? (prof.entity_type || 'person') : 'person';

  const res = await fetch(API + '/api/games', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ profile_id: profileId }),
  });
  if (!res.ok) return toast('Error creating game');
  const game = await res.json();
  myRoomCode = game.room_code;
  myPlayerName = prompt('Your name:', myPlayerName || localStorage.getItem('obsessed_name') || 'Player');
  if (!myPlayerName) return;
  localStorage.setItem('obsessed_name', myPlayerName);
  const joinRes = await fetch(API + `/api/games/${myRoomCode}/join`, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ player_name: myPlayerName, player_id: myPlayerId }),
  });
  if (!joinRes.ok) return toast('Error joining game');
  const player = await joinRes.json();

  // Show profile context on game screen
  const ctx = document.getElementById('profile-context');
  if (ctx && myProfileName) {
    const emoji = {person:'👤',place:'📍',thing:'💡',event:'📅'}[myProfileType] || '👤';
    ctx.textContent = `${emoji} ${myProfileName} (${myProfileType.toUpperCase()})`;
  }

  showScreen('lobby');
  document.getElementById('room-code-display').textContent = myRoomCode;
}

function exitLobby() {
  clearInterval(pollInterval);
  myRoomCode = null;
  showScreen('profile');
}

// ── Lobby polling ────────────────────────────────────────────────────────────
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
  const list = document.getElementById('lobby-players');
  if (list) {
    list.innerHTML = game.players.map(p => `
      <div class="player-entry ${p.is_host ? 'host-tag' : ''}">${esc(p.player_name)} ${p.is_host ? '👑' : ''}</div>`).join('');
  }
  if (game.status === 'active') {
    clearInterval(pollInterval);
    showScreen('game');
    loadQuestion();
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

async function submitAnswer(btn, answer) {
  clearInterval(timerInterval);
  const startMs = (30 - timerSeconds) * 1000;
  if (btn) btn.classList.add('selected');
  
  const res = await fetch(API + `/api/games/${myRoomCode}/answer`, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ player_id: myPlayerId, answer_text: answer, time_taken_ms: startMs }),
  });
  
  // Reveal correct answer regardless
  document.querySelectorAll('.answer-btn').forEach(b => {
    if (b.textContent === answer || answer === '__timeout__') {
      // We'll show the result after we know
    }
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
    // Advance to next
    await fetch(API + `/api/games/${myRoomCode}/next`, { method: 'POST' });
    const nextRes = await fetch(API + `/api/games/${myRoomCode}/question`);
    if (nextRes.ok) {
      loadQuestion();
    } else {
      showResults();
    }
  }, 2000);
}

async function showResults() {
  clearInterval(pollInterval);
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
  // Already in-game, just refresh question
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
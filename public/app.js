const form = document.querySelector('#search-form');
const contestTypeInput = document.querySelector('#contest-type');
const contestInput = document.querySelector('#contest');
const usernameInput = document.querySelector('#username');
const searchButton = document.querySelector('.search-button');
const buttonLabel = document.querySelector('#button-label');
const statusPanel = document.querySelector('#status-panel');
const statusMessage = document.querySelector('#status-message');
const statusDetail = document.querySelector('#status-detail');
const progressBar = document.querySelector('#progress-bar');
const progressCaption = document.querySelector('#progress-caption');
const cancelButton = document.querySelector('#cancel-button');
const resultPanel = document.querySelector('#result-panel');
const notFoundPanel = document.querySelector('#not-found-panel');
const errorPanel = document.querySelector('#error-panel');
const recentSection = document.querySelector('#recent-section');
const recentList = document.querySelector('#recent-list');

let activeSource = null;
let searchFinished = false;
const RECENT_KEY = 'rankfinder-recent-searches';
const contestTypeOptions = [...document.querySelectorAll('.contest-option')];

function setContestType(type) {
  const value = type === 'biweekly' ? 'biweekly' : 'weekly';
  contestTypeInput.value = value;
  contestTypeOptions.forEach(button => {
    const active = button.dataset.contestType === value;
    button.classList.toggle('is-active', active);
    button.setAttribute('aria-pressed', String(active));
  });
}

function hideOutcomes() {
  resultPanel.hidden = true;
  notFoundPanel.hidden = true;
  errorPanel.hidden = true;
}

function setSearching(isSearching) {
  searchButton.disabled = isSearching;
  buttonLabel.textContent = isSearching ? 'Searching…' : 'Search ranking';
  contestTypeInput.disabled = isSearching;
  contestTypeOptions.forEach(button => { button.disabled = isSearching; });
  contestInput.disabled = isSearching;
  usernameInput.disabled = isSearching;
  statusPanel.hidden = !isSearching;
}

function setStatus(message, detail) {
  statusMessage.textContent = message || 'Searching leaderboard';
  statusDetail.textContent = detail || '';
}

function formatNumber(value) {
  if (value == null || value === '') return '—';
  return Number(value).toLocaleString('en-US');
}

function formatDuration(seconds) {
  if (seconds == null || Number.isNaN(Number(seconds))) return '—';
  const total = Math.max(0, Math.floor(Number(seconds)));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  return `${hours ? `${String(hours).padStart(2, '0')}:` : ''}${String(minutes).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
}

function initials(name) {
  const value = String(name || '??').trim();
  const parts = value.split(/\s+/).filter(Boolean);
  return (parts.length > 1 ? `${parts[0][0]}${parts[1][0]}` : value.slice(0, 2)).toUpperCase();
}

function renderResult(result) {
  document.querySelector('#result-title').textContent = `${result.displayName || result.username} found`;
  document.querySelector('#result-display-name').textContent = result.displayName || result.username;
  document.querySelector('#result-username').textContent = `@${result.username || ''}`;
  document.querySelector('#avatar-fallback').textContent = initials(result.displayName || result.username);
  document.querySelector('#result-rank').textContent = formatNumber(result.rank);
  document.querySelector('#result-score').textContent = result.score == null ? '—' : formatNumber(result.score);
  document.querySelector('#result-time').textContent = formatDuration(result.finishTime);
  document.querySelector('#result-contest').textContent = (result.contestTitle || `Contest ${result.contest}`).replace(' Contest ', ' #');
  document.querySelector('#result-total').textContent = formatNumber(result.totalParticipants);
  document.querySelector('#result-context').textContent = `Leaderboard row located on page ${formatNumber(result.page)}.`;
  document.querySelector('#source-badge').textContent = result.source === 'contest history' ? 'CONTEST HISTORY' : (result.source === 'global leaderboard' ? 'LIVE LEADERBOARD' : 'MIRROR FALLBACK');

  const profileLink = document.querySelector('#profile-link');
  profileLink.href = result.profileUrl;
  const rankingLink = document.querySelector('#ranking-link');
  rankingLink.href = result.rankingUrl;
  resultPanel.hidden = false;
}

function saveRecent(type, contest, username) {
  let items = [];
  try { items = JSON.parse(localStorage.getItem(RECENT_KEY) || '[]'); } catch { items = []; }
  items = [{ type, contest: String(contest), username }, ...items.filter(item => !((item.type || 'weekly') === type && String(item.contest) === String(contest) && item.username.toLowerCase() === username.toLowerCase()))].slice(0, 6);
  localStorage.setItem(RECENT_KEY, JSON.stringify(items));
  renderRecent();
}

function renderRecent() {
  let items = [];
  try { items = JSON.parse(localStorage.getItem(RECENT_KEY) || '[]'); } catch { items = []; }
  if (!items.length) {
    recentSection.hidden = true;
    return;
  }
  recentSection.hidden = false;
  recentList.innerHTML = '';
  items.forEach(item => {
    const button = document.createElement('button');
    button.className = 'recent-item';
    button.type = 'button';
    const contest = document.createElement('b');
    const type = item.type || 'weekly';
    contest.textContent = `${type === 'biweekly' ? 'Biweekly' : 'Weekly'} #${item.contest}`;
    const slash = document.createElement('span');
    slash.textContent = '/';
    const username = document.createElement('strong');
    username.textContent = item.username;
    button.append(contest, slash, username);
    button.addEventListener('click', () => {
      setContestType(type);
      contestInput.value = item.contest;
      usernameInput.value = item.username;
      form.requestSubmit();
    });
    recentList.appendChild(button);
  });
}

function showError(message) {
  document.querySelector('#error-message').textContent = message || 'Please try again in a moment.';
  errorPanel.hidden = false;
}

function finishSearch() {
  searchFinished = true;
  if (activeSource) {
    activeSource.close();
    activeSource = null;
  }
  setSearching(false);
  cancelButton.textContent = 'Close';
}

function cancelSearch() {
  if (activeSource) {
    activeSource.close();
    activeSource = null;
  }
  searchFinished = true;
  setSearching(false);
  setStatus('Search cancelled', 'Enter another contest and username whenever you are ready.');
  window.setTimeout(() => { if (searchFinished) statusPanel.hidden = true; }, 800);
}

function beginSearch(type, contest, username) {
  if (activeSource) activeSource.close();
  searchFinished = false;
  hideOutcomes();
  progressBar.style.width = '0%';
  progressCaption.textContent = 'Preparing lookup…';
  setSearching(true);
  const contestLabel = type === 'biweekly' ? 'Biweekly' : 'Weekly';
  setStatus(`Checking ${contestLabel} Contest ${contest}`, 'Looking for a fast match before scanning the leaderboard.');
  cancelButton.textContent = 'Cancel';

  const query = new URLSearchParams({ contestType: type, contest, username });
  activeSource = new EventSource(`/api/search?${query.toString()}`);

  activeSource.addEventListener('status', event => {
    const data = JSON.parse(event.data);
    setStatus(data.message, data.detail);
  });

  activeSource.addEventListener('progress', event => {
    const data = JSON.parse(event.data);
    const percent = Math.max(0, Math.min(100, Number(data.percent || 0)));
    progressBar.style.width = `${percent}%`;
    progressCaption.textContent = `Scanned page ${formatNumber(data.scanned)} of ${formatNumber(data.totalPages)} · ${percent}%`;
  });

  activeSource.addEventListener('result', event => {
    const result = JSON.parse(event.data);
    progressBar.style.width = '100%';
    progressCaption.textContent = 'Match verified.';
    renderResult(result);
    saveRecent(type, contest, username);
  });

  activeSource.addEventListener('not_found', event => {
    const data = JSON.parse(event.data);
    document.querySelector('#not-found-title').textContent = `No match for ${data.username}`;
    notFoundPanel.hidden = false;
    progressBar.style.width = '100%';
    progressCaption.textContent = 'Leaderboard scan complete.';
  });

  activeSource.addEventListener('error', event => {
    let data = {};
    try { data = JSON.parse(event.data); } catch { /* browser connection error */ }
    if (!searchFinished) showError(data.message || 'The leaderboard could not be reached right now.');
  });

  activeSource.addEventListener('done', () => finishSearch());
  activeSource.onerror = () => {
    if (!searchFinished && activeSource && activeSource.readyState === EventSource.CLOSED) {
      showError('The connection closed before the lookup finished. Please try again.');
      finishSearch();
    }
  };
}

form.addEventListener('submit', event => {
  event.preventDefault();
  const type = contestTypeInput.value;
  const contest = contestInput.value.trim();
  const username = usernameInput.value.trim();
  if (!contest || Number(contest) < 1 || Number(contest) > 9999) {
    contestInput.focus();
    return;
  }
  if (!/^[\w.-]{1,40}$/.test(username)) {
    usernameInput.focus();
    return;
  }
  beginSearch(type, contest, username);
});

contestTypeOptions.forEach(button => {
  button.addEventListener('click', () => setContestType(button.dataset.contestType));
});
setContestType('weekly');

document.querySelectorAll('.example-chip').forEach(button => {
  button.addEventListener('click', () => {
    setContestType(button.dataset.type || 'weekly');
    contestInput.value = button.dataset.contest;
    usernameInput.value = button.dataset.username;
    form.requestSubmit();
  });
});

cancelButton.addEventListener('click', () => {
  if (activeSource && !searchFinished) cancelSearch();
  else statusPanel.hidden = true;
});

document.querySelector('#clear-history').addEventListener('click', () => {
  localStorage.removeItem(RECENT_KEY);
  renderRecent();
});

renderRecent();

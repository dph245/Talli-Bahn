'use strict';
const $ = id => document.getElementById(id);
const names = {rail: 'Bahn', subway: 'U-Bahn', tram: 'Straßenbahn', bus: 'Bus', ferry: 'Fähre', other: 'Weitere'};
const storage = {
  get(key, fallback) { try { return JSON.parse(localStorage.getItem(`talli:${key}`)) ?? fallback; } catch { return fallback; } },
  set(key, value) { try { localStorage.setItem(`talli:${key}`, JSON.stringify(value)); } catch { /* Private browsing may disable storage. */ } }
};
const isStop = value => value && typeof value.id === 'string' && typeof value.name === 'string';
const savedFavorites = storage.get('favorites', []);
const state = {stop: null, kind: 'departures', mode: 'all', board: null, stale: false,
  favorites: Array.isArray(savedFavorites) ? savedFavorites.filter(isStop) : [], request: 0};
let controller, searchController, searchTimer, searchVersion = 0;
const time = value => new Intl.DateTimeFormat('de-DE', {hour: '2-digit', minute: '2-digit', timeZone: 'Europe/Berlin'}).format(new Date(value));
function element(tag, className, text) { const node = document.createElement(tag); if (className) node.className = className; if (text !== undefined) node.textContent = text; return node; }
function setTheme(theme) { document.documentElement.dataset.theme = theme; $('theme').textContent = theme === 'dark' ? '☼' : '☾'; $('theme').setAttribute('aria-label', `Zum ${theme === 'dark' ? 'hellen' : 'dunklen'} Design wechseln`); document.querySelector('meta[name="theme-color"]').content = theme === 'dark' ? '#121820' : '#f3f4f1'; }
setTheme(storage.get('theme', 'dark') === 'light' ? 'light' : 'dark');
$('theme').addEventListener('click', () => { const theme = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark'; setTheme(theme); storage.set('theme', theme); });
function tick() { const now = new Date(); $('clock').textContent = time(now); $('clock').dateTime = now.toISOString(); $('date').textContent = new Intl.DateTimeFormat('de-DE', {weekday: 'long', day: 'numeric', month: 'long', timeZone: 'Europe/Berlin'}).format(now); }
tick(); setInterval(tick, 1000);
function renderFavorites() {
  $('favorites').replaceChildren();
  for (const stop of state.favorites) { const button = element('button', `favorite-chip${stop.id === state.stop?.id ? ' selected' : ''}`, `★  ${stop.name}`); button.addEventListener('click', () => selectStop(stop)); $('favorites').append(button); }
  $('favorites-empty').hidden = state.favorites.length > 0;
  const selected = state.favorites.some(stop => stop.id === state.stop?.id);
  $('favorite').textContent = selected ? '★' : '☆'; $('favorite').setAttribute('aria-pressed', String(selected));
  $('favorite').setAttribute('aria-label', selected ? 'Haltestelle aus Favoriten entfernen' : 'Haltestelle als Favorit speichern');
  $('favorite').disabled = !state.stop;
}
$('favorite').addEventListener('click', () => { if (!state.stop) return; const index = state.favorites.findIndex(stop => stop.id === state.stop.id); if (index >= 0) state.favorites.splice(index, 1); else state.favorites.push(state.stop); storage.set('favorites', state.favorites); renderFavorites(); });
function resetFilters() { state.mode = 'all'; $('line').value = ''; $('direction').value = ''; updateModes(); }
function selectStop(stop) {
  ++searchVersion; searchController?.abort(); clearTimeout(searchTimer);
  state.stop = stop; state.board = null; state.stale = false;
  storage.set('last-stop', stop); $('station-name').textContent = stop.name;
  $('search').value = ''; $('search-results').hidden = true; $('search').blur();
  resetFilters(); renderFavorites(); render(); loadBoard();
}
async function getJSON(url, signal) { const response = await fetch(url, {signal, cache: 'no-store'}); if (!response.ok) { const error = new Error(`HTTP ${response.status}`); error.status = response.status; throw error; } return response.json(); }
function searchMessage(message) { $('search-results').replaceChildren(element('div', 'search-message', message)); $('search-results').hidden = false; }
async function searchStops() {
  const query = $('search').value.trim(); const version = ++searchVersion;
  searchController?.abort(); searchController = new AbortController();
  searchMessage('Haltestellen werden gesucht …');
  try { const stops = await getJSON(`/api/stops?q=${encodeURIComponent(query)}`, searchController.signal); if (version !== searchVersion) return;
    $('search-results').replaceChildren();
    if (!stops.length) searchMessage('Keine Haltestelle gefunden. Versuche einen anderen Namen.');
    for (const stop of stops) { const button = element('button', '', stop.name); button.addEventListener('click', () => selectStop(stop)); $('search-results').append(button); }
    $('search-results').hidden = false;
  } catch (error) { if (error.name !== 'AbortError' && version === searchVersion) searchMessage('Suche nicht verfügbar. Bitte Verbindung prüfen.'); }
}
$('search').addEventListener('input', () => { ++searchVersion; searchController?.abort(); clearTimeout(searchTimer); searchTimer = setTimeout(searchStops, 220); });
$('search').addEventListener('focus', searchStops);
$('search').addEventListener('keydown', event => { if (event.key === 'ArrowDown') { event.preventDefault(); $('search-results').querySelector('button')?.focus(); } if (event.key === 'Enter') $('search-results').querySelector('button')?.click(); });
$('search-results').addEventListener('keydown', event => { const buttons = [...$('search-results').querySelectorAll('button')]; const index = buttons.indexOf(document.activeElement); if (event.key === 'ArrowDown' || event.key === 'ArrowUp') { event.preventDefault(); buttons[(index + (event.key === 'ArrowDown' ? 1 : -1) + buttons.length) % buttons.length]?.focus(); } });
document.addEventListener('keydown', event => { if (event.key === '/' && !['INPUT', 'SELECT', 'TEXTAREA'].includes(document.activeElement.tagName)) { event.preventDefault(); $('search').focus(); } if (event.key === 'Escape') { ++searchVersion; searchController?.abort(); $('search-results').hidden = true; $('search').blur(); } });
document.addEventListener('click', event => { if (!event.target.closest('.search-box')) { ++searchVersion; searchController?.abort(); $('search-results').hidden = true; } });
function options(select, values, label) { const previous = select.value; select.replaceChildren(new Option(label, '')); for (const value of [...new Set(values)].sort((a,b) => a.localeCompare(b, 'de', {numeric:true}))) select.add(new Option(value, value)); select.value = values.includes(previous) ? previous : ''; }
async function loadBoard() {
  if (!state.stop) return;
  const request = ++state.request; controller?.abort(); controller = new AbortController();
  const stop = state.stop, kind = state.kind;
  $('refresh').disabled = true; $('board-panel').setAttribute('aria-busy', 'true');
  const timeout = setTimeout(() => controller?.abort(), 20000);
  try {
    const board = await getJSON(`/api/board?stop_id=${encodeURIComponent(stop.id)}&kind=${kind}`, controller.signal);
    if (request !== state.request) return;
    state.board = board; state.stale = false;
    $('error').hidden = true; $('status-dot').classList.remove('stale');
    $('source-badge').textContent = board.demo ? 'DEMO · BEISPIELDATEN' : board.source;
    $('updated').textContent = `Aktualisiert ${time(board.updated_at)}`;
    $('notice').textContent = board.notice || ''; $('notice').hidden = !board.notice;
    options($('line'), board.journeys.map(j => j.line), 'Alle Linien');
    options($('direction'), board.journeys.map(j => j.destination), kind === 'departures' ? 'Alle Richtungen' : 'Alle Herkünfte');
    render();
  } catch (error) {
    if (request !== state.request) return;
    state.stale = true; $('status-dot').classList.add('stale');
    $('error').textContent = error.status === 404 ? 'Diese Haltestelle ist in der aktuellen Datenquelle nicht vorhanden. Bitte suche eine neue Haltestelle.' : state.board ? 'Keine Verbindung zum Server. Die angezeigte Tafel ist veraltet. Bitte erneut aktualisieren.' : 'Die Tafel konnte nicht geladen werden. Prüfe deine Verbindung und aktualisiere erneut.';
    $('error').hidden = false; $('updated').textContent = state.board ? `Veraltet · Stand ${time(state.board.updated_at)}` : 'Nicht verbunden'; render();
  } finally { clearTimeout(timeout); if (request === state.request) { $('refresh').disabled = false; $('board-panel').setAttribute('aria-busy', 'false'); } }
}
function statusFor(j) { if (j.cancelled) return ['Fällt aus', 'cancelled']; if (!j.realtime) return ['Nach Fahrplan', 'scheduled-only']; if (j.delay_minutes > 0) return [`+${j.delay_minutes} Min`, 'delayed']; if (j.delay_minutes < 0) return [`${j.delay_minutes} Min`, '']; return ['Pünktlich', '']; }
function render() {
  const journeys = (state.board?.journeys || []).filter(j => (state.mode === 'all' || j.mode === state.mode) && (!$('line').value || j.line === $('line').value) && (!$('direction').value || j.destination === $('direction').value));
  $('alerts').replaceChildren();
  const alerts = new Map((state.board?.alerts || []).map(alert => [alert.id, alert]));
  for (const j of journeys) for (const alert of j.alerts || []) alerts.set(alert.id, alert);
  for (const alert of alerts.values()) {
    const detail = element('details', 'service-alert');
    detail.append(element('summary', '', alert.header));
    if (alert.description) detail.append(element('p', '', alert.description));
    $('alerts').append(detail);
  }
  $('alerts').hidden = alerts.size === 0;
  $('journeys').replaceChildren();
  for (const j of journeys) {
    const row = element('tr', j.cancelled ? 'cancelled-row' : '');
    const times = element('td', 'time-cell'); times.append(element('span', 'scheduled', time(j.scheduled)));
    if (j.realtime && !j.cancelled) times.append(element('span', `expected${j.delay_minutes > 0 ? ' delayed' : ''}`, time(j.realtime)));
    const line = element('td'); line.append(element('span', `line-badge ${j.mode}`, j.line));
    const destination = element('td'); destination.append(element('span', 'destination', j.destination), element('span', 'mode-name', [names[j.mode] || j.mode, j.operator].filter(Boolean).join(' · ')));
    if (j.alerts?.length) destination.append(element('span', 'service-note', `ⓘ ${j.alerts[0].header}`));
    const [status, style] = statusFor(j); destination.append(element('span', `journey-status mobile-status ${style}`, status));
    const platform = element('td'); platform.append(element('span', 'platform', j.platform || '–'));
    if (j.scheduled_platform && j.platform && j.scheduled_platform !== j.platform) {
      platform.append(element('span', 'platform-change', `statt ${j.scheduled_platform}`));
    }
    const statusCell = element('td'); statusCell.append(element('span', `journey-status ${style}`, status));
    row.append(times, line, destination, platform, statusCell); $('journeys').append(row);
  }
  $('empty').hidden = !state.board || journeys.length > 0;
  $('count').textContent = state.board ? `${journeys.length} ${state.kind === 'departures' ? 'Abfahrten' : 'Ankünfte'} · nächste 2 Stunden${state.stale ? ' · veraltet' : ''}` : state.stop ? 'Warte auf Fahrplandaten …' : 'Bitte eine Haltestelle suchen';
}
function updateModes() { document.querySelectorAll('[data-mode]').forEach(button => { const active = button.dataset.mode === state.mode; button.classList.toggle('active', active); button.setAttribute('aria-pressed', String(active)); }); }
document.querySelectorAll('[data-mode]').forEach(button => button.addEventListener('click', () => { state.mode = button.dataset.mode; updateModes(); render(); }));
for (const id of ['line','direction']) $(id).addEventListener('change', render);
$('reset-filters').addEventListener('click', () => { resetFilters(); render(); });
for (const kind of ['departures', 'arrivals']) $(kind).addEventListener('click', () => {
  if (state.kind === kind) return;
  state.kind = kind; state.board = null; resetFilters();
  for (const id of ['departures', 'arrivals']) { $(id).classList.toggle('active', id === kind); $(id).setAttribute('aria-selected', String(id === kind)); }
  $('board-panel').setAttribute('aria-labelledby', kind); $('direction-title').textContent = kind === 'departures' ? 'RICHTUNG' : 'HERKUNFT'; render(); loadBoard();
});
$('refresh').addEventListener('click', loadBoard);
$('auto').checked = storage.get('auto', true) !== false;
$('auto').addEventListener('change', () => { storage.set('auto', $('auto').checked); if ($('auto').checked) loadBoard(); });
setInterval(() => { if ($('auto').checked && !document.hidden && !$('refresh').disabled) loadBoard(); }, 10000);
document.addEventListener('visibilitychange', () => { if (!document.hidden && $('auto').checked) loadBoard(); });
window.addEventListener('online', loadBoard);
window.addEventListener('offline', () => { $('error').textContent = 'Du bist offline. Angezeigte Verbindungen können veraltet sein.'; $('error').hidden = false; $('status-dot').classList.add('stale'); state.stale = true; render(); });
async function init() { renderFavorites(); try { const stops = await getJSON('/api/stops?q='); const saved = storage.get('last-stop', null); if (isStop(saved)) selectStop(saved); else if (stops.length) selectStop(stops[0]); else { $('updated').textContent = 'Keine Haltestellen'; render(); } } catch { $('updated').textContent = 'Nicht verbunden'; $('error').textContent = 'Haltestellen konnten nicht geladen werden. Bitte Verbindung prüfen und Seite neu laden.'; $('error').hidden = false; } }
init();
if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(() => {});

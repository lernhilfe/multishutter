'use strict';
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const labels = {overview:'Aufnahme', sessions:'Aufnahmen', cameras:'Kameraeinstellungen', rig:'Kameraverwaltung', timing:'Synchronisation', processing:'Verarbeitungseinstellungen', system:'Systemeinstellungen'};
let state = null, sessions = [], view = '', selected = new Set(), settingsSignature = '', sessionsSignature = '', toastTimer;
let lastJob = null, polling = false;
let managerSignature = '';
const mainCalibrationJobs = new Set();
const cameraIcon = `<svg viewBox="0 0 180 110" class="camera-drawing" fill="none" aria-hidden="true"><path d="M22 32h26l12-15h51l13 15h33a8 8 0 0 1 8 8v54a8 8 0 0 1-8 8H23a8 8 0 0 1-8-8V40a8 8 0 0 1 7-8Z" fill="#20271f" stroke="currentColor" stroke-width="1.4"/><path d="M20 44h30m78 0h30M33 22h19v10H33z" stroke="currentColor"/><circle cx="91" cy="65" r="33" fill="#151b14" stroke="currentColor" stroke-width="1.5"/><circle cx="91" cy="65" r="26" stroke="#53634a"/><circle cx="91" cy="65" r="15" fill="#263920" stroke="#748467"/><path d="m85 52 12 1 8 10-4 12-12 5-11-8-1-12z" fill="#111b0d" stroke="#5e7847"/><circle cx="143" cy="47" r="3" fill="#c2ef82"/><path d="M29 56v30M34 56v30M39 56v30" stroke="#414c3c"/></svg>`;
const modelName = camera => camera.model.replace(/^Sony\s*/, '').replace(/\s*\((?:PC )?Control\)/, '').replace('Alpha-', 'α ').replace(/^A(\d)/, 'α $1') || camera.id;
const formatTime = at => at ? new Date(at).toLocaleTimeString('de-DE', {hour:'2-digit',minute:'2-digit',second:'2-digit'}) : '—';
const formatDate = at => new Date(at).toLocaleString('de-DE', {day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'});
const formatBytes = n => `${(n / 1024 / 1024).toFixed(1)} MB`;
const normal = (key, value) => key === 'f-number' ? String(value).replace(/^f\/?/i, '') : String(value);
const isBusy = () => state?.busy;

async function api(path, body, method='POST') {
  const response = await fetch(path, body === undefined ? {signal:AbortSignal.timeout(10000)} : {
    method, headers:{'Content-Type':'application/json','X-MultiShutter':'1'}, body:JSON.stringify(body), signal:AbortSignal.timeout(10000)
  });
  let data;
  try { data = await response.json(); } catch { throw new Error(`Server antwortet mit Status ${response.status}`); }
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail || data));
  return data;
}
function toast(message, error=false) {
  const node = $('#toast'); node.textContent = message; node.className = `show${error ? ' error' : ''}`;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => node.classList.remove('show'), error ? 9000 : 4500);
}
async function submit(path, body={}) {
  try { const job = await api(path, body); toast('Auftrag gestartet'); await refresh(); return job; }
  catch (error) { toast(error.message, true); return null; }
}
function heading(eyebrow, title, description, actions='') {
  return `<div class="page-heading"><div><div class="eyebrow">${eyebrow}</div><h1>${title}</h1><p class="description">${description}</p></div><div class="heading-actions">${actions}</div></div><div id="notices"></div><div id="job-progress"></div>`;
}
function route() {
  const next = location.hash.replace('#','') || 'overview';
  view = labels[next] ? next : 'overview';
  $$('nav a').forEach(a => { a.classList.toggle('active', a.dataset.view === view); a.setAttribute('aria-label', labels[a.dataset.view]); });
  $('#breadcrumb').textContent = labels[view];
  if (!state) return;
  settingsSignature = '';
  const renderers = {overview:renderOverview, sessions:renderSessions, cameras:renderCameras, rig:renderRig, timing:renderTiming, processing:renderProcessing, system:renderSystem};
  renderers[view](); updateDynamic();
}
function renderOverview() {
  $('#main').innerHTML = heading('DEIN MULTI-CAMERA WORKSPACE', 'Ein Moment. Alle Perspektiven.',
    'Bereite dein Rig vor und halte den nächsten Moment gemeinsam fest.',
    '<button class="button" data-action="reconnect">↻ &nbsp; Verbindungen prüfen</button>') + `
    <div class="metrics"><div class="metric"><div class="metric-label">◉ &nbsp; Kameras verbunden</div><div class="metric-number" id="metric-ready">—</div><div class="metric-meta" id="metric-ready-note"></div></div>
    <div class="metric"><div class="metric-label">◷ &nbsp; Zeitkorrektur</div><div class="metric-number" id="metric-timing">—</div><div class="metric-meta" id="metric-timing-note"></div></div>
    <div class="metric"><div class="metric-label">◇ &nbsp; Verbindungsschutz</div><div class="metric-number" id="metric-safety">—</div><div class="metric-meta" id="metric-safety-note"></div></div></div>
    <div class="section-heading"><h2>Dein Kamera-Rig</h2><a href="#cameras" class="text-link">Kameraeinstellungen ↗</a></div><div class="camera-grid" id="camera-grid"></div>
    <form id="capture-form" class="capture-panel"><div><div class="eyebrow">BEREIT FÜR DEN NÄCHSTEN TAKE</div><h2>Gemeinsam auslösen.</h2><p class="subtle">Neue Aufnahmen werden direkt übertragen.</p></div>
    <div class="capture-fields"><div class="field"><label for="take-count">Aufnahmerunden</label><input id="take-count" name="takes" type="number" min="1" max="100" value="1" required></div><div class="field"><label for="countdown">Countdown · s</label><input id="countdown" name="countdown" type="number" min="0" max="3600" step="0.1" value="3" required></div><div class="field"><label for="interval">Pause je Runde · s</label><input id="interval" name="interval" type="number" min="0" max="3600" step="0.1" value="1" required></div></div>
    <div><button type="submit" class="button primary capture-button" id="capture-button"><span class="shutter"></span> Aufnahme starten</button><div class="shortcut">LEERTASTE zum Auslösen</div></div>
    <label class="partial-warning hidden" id="partial-option"><input type="checkbox" name="allow_partial"> Ich bestätige die Aufnahme ausschließlich mit den verbundenen Kameras.</label></form>
    <div class="below-grid"><section class="panel"><div class="panel-header"><h2>Letzte Aufnahme</h2><a href="#sessions" class="text-link">Alle ansehen ↗</a></div><div id="recent-photos"></div></section><section class="panel"><div class="panel-header"><h2>Rig-Aktivität</h2><span class="badge muted">LIVE</span></div><div id="recent-events"></div></section></div>`;
  renderRecent();
}
function cameraCards() {
  return state.cameras.map((camera,index) => {
    const online = camera.state === 'ready';
    const photo = sessions.flatMap(session => session.files).find(file => file.camera_id === camera.id);
    const controls = camera.settings || {}, value = key => esc(controls[key]?.value || '—');
    const status = ({ready:'Verbunden · aufnahmebereit', connecting:'Verbindung wird aufgebaut', waiting:'Warte auf Erkennung', disconnected:'Verbindung getrennt', error:'Verbindung prüfen'})[camera.state] || camera.state;
    return `<article class="camera-card"><div class="camera-card-top"><span class="camera-id">CAM ${String(index+1).padStart(2,'0')} / ${esc(camera.id)}</span><span class="badge ${online?'':camera.state==='connecting'?'warn':'error'}">${online?'ONLINE':camera.state==='connecting'?'VERBINDET':'OFFLINE'}</span></div>
    <div class="camera-visual">${photo?`<img src="${esc(photo.preview)}" alt="Letzte Aufnahme ${esc(modelName(camera))}" loading="lazy">`:cameraIcon}</div><div class="camera-card-body"><div class="camera-model">Sony ${esc(modelName(camera))}</div><div class="camera-status"><span class="status-dot ${online?'':camera.state==='connecting'?'waiting':'error'}"></span>${esc(status)}</div>
    <div class="camera-controls"><div class="camera-specs"><span>ISO ${value('iso')}</span><span>${value('shutterspeed')}</span><span>ƒ ${value('f-number').replace(/^f\/?/i,'')}</span></div><button class="icon-button" data-action="camera-detail" data-id="${esc(camera.id)}" aria-label="Details ${esc(camera.id)}">↗</button></div>${camera.error?`<div class="camera-error">${esc(camera.error)}</div>`:''}</div></article>`;
  }).join('');
}
function renderRecent() {
  const target = $('#recent-photos'); if (!target) return;
  const latest = sessions.find(session => session.files.length);
  if (!latest) { target.innerHTML = '<div class="empty-state">Dein erster gemeinsamer Moment wartet.<br>Starte eine Aufnahme, um die Ergebnisse hier zu sehen.</div>'; return; }
  target.innerHTML = `<div class="mini-gallery">${latest.files.slice(0,3).map(file => `<a href="${esc(file.download)}" class="mini-photo" data-action="preview" data-src="${esc(file.preview)}" data-download="${esc(file.download)}" data-name="${esc(file.name)}"><img src="${esc(file.preview)}" alt="Aufnahme ${esc(file.camera_id)}" loading="lazy"><span>${esc(file.camera_id.toUpperCase())}</span></a>`).join('')}</div><div class="recent-caption"><span>${formatDate(latest.created_at)} · ${latest.files.length} Dateien</span><span>${latest.status==='complete'?(latest.excluded_cameras?.length?'Teilaufnahme':'Vollständig'):'Unvollständig'}</span></div>`;
}
function notices() {
  if (!state) return '';
  let result = '';
  if (state.fatal) result += `<div class="banner error"><div><strong>Kameradienst nicht verfügbar</strong><br>${esc(state.fatal)}</div></div>`;
  else if (state.lockout && state.options.disconnect_policy === 'lockout') result += `<div class="banner"><div><strong>Aufnahmesperre nach Verbindungsunterbrechung</strong><br>Prüfe alle aktiven Kameras und gib die Aufnahme danach bewusst wieder frei.</div><button class="button small" data-action="acknowledge" ${state.ready!==state.total||state.busy?'disabled':''}>Rig prüfen & entsperren</button></div>`;
  else if (state.ready !== state.total) result += `<div class="banner"><div><strong>${state.total-state.ready} Kamera(s) nicht bereit</strong><br>${state.options.disconnect_policy==='lockout'?'Aufnahmen bleiben gesperrt, bis alle Kameras verbunden sind.':'Warnmodus: Eine Teilaufnahme erfordert deine ausdrückliche Bestätigung.'}</div></div>`;
  if (state.options.compensation_enabled && !state.calibration.valid) result += `<div class="banner"><div><strong>Zeitkorrektur pausiert</strong><br>Die Kamerakonfiguration hat sich verändert. Neu messen oder manuelle Werte bewusst übernehmen.</div><a class="button small" href="#timing">Synchronisation öffnen</a></div>`;
  if(state.format_mismatches?.length) result+=`<div class="banner error"><div><strong>Aufnahmeformat weicht ab</strong><br>${esc(state.format_mismatches.join(', '))}: ${esc(state.capture_format.toUpperCase())} gemeinsam anwenden, bevor erneut aufgenommen wird.</div><a class="button small" href="#cameras">Kameraeinstellungen</a></div>`;
  return result;
}
function updateDynamic() {
  if (!state) return;
  if (view==='rig' && managerSignature!==rigSignature()) renderRig();
  $('#backend-badge').textContent = state.backend==='simulated'?'SIMULATION':'USB · WSL';
  $('#host-mode').textContent = state.backend==='simulated'?'Simulierte Kameras':'Docker Compose · USB';
  $('#footer-status').textContent = `${state.ready}/${state.total} bereit · Aktualisiert ${new Date().toLocaleTimeString('de-DE')}`;
  if ($('#notices')) $('#notices').innerHTML = notices();
  const job = state.jobs.find(item => item.id === state.active_job);
  if ($('#job-progress')) $('#job-progress').innerHTML = state.busy ? `<div class="progress-panel"><span class="spinner"></span><span>${esc(job?.progress || 'Kameraauftrag wird ausgeführt …')}</span><button class="button small" data-action="stop">Stoppen</button></div>`:'';
  if (view==='overview') {
    $('#camera-grid').innerHTML = cameraCards();
    $('#metric-ready').innerHTML = `${state.ready}<small>/ ${state.total}</small>`;
    $('#metric-ready-note').textContent = state.ready===state.total?'Alle Perspektiven verfügbar':'Rig wird überwacht';
    const active = state.options.compensation_enabled && state.calibration.valid;
    const delays = Object.values(state.options.delays_ms);
    $('#metric-timing').innerHTML = active?`${Math.max(0,...delays).toFixed(0)}<small>ms max.</small>`:'Aus';
    $('#metric-timing-note').textContent = active?(state.calibration.source==='optical'?'Optische Messung angewendet':'Manuelle Korrektur aktiv'):'Noch keine aktive Kompensation';
    $('#metric-safety').textContent = state.options.disconnect_policy==='lockout'?'Lockout':'Warnung';
    $('#metric-safety-note').textContent = state.options.auto_reconnect?'Auto-Reconnect eingeschaltet':'Manuell neu verbinden';
    $('#capture-button').disabled = !state.capture_allowed;
    $('#partial-option').classList.toggle('hidden', !(state.options.disconnect_policy==='warn' && state.ready<state.total));
    $('#recent-events').innerHTML = state.events.slice(-5).reverse().map(event => `<div class="event ${esc(event.level)}"><time>${formatTime(event.at)}</time><div class="event-text">${esc(event.message)}</div></div>`).join('') || '<p class="subtle">Noch keine Ereignisse.</p>';
  }
  if (view==='cameras') {
    const signature = JSON.stringify(state.cameras.map(camera => [camera.id,camera.state,camera.settings]));
    if (signature !== settingsSignature) { settingsSignature = signature; renderCameraForm(); }
    $('#apply-camera-settings').disabled = state.busy || selected.size===0;
    $('#apply-camera-format').disabled=state.busy || !state.total || state.ready!==state.total;
    $('#format-consistency').textContent=state.format_mismatches?.length?`Abweichend: ${state.format_mismatches.join(', ')}`:state.format_consistent?'Alle aktiven Kameras verwenden dasselbe Aufnahmeformat.':`${state.ready}/${state.total} verbunden. Zum Angleichen müssen alle aktiven Kameras bereit sein.`;
  }
  if (view==='timing') {
    if ($('#target-status')) { $('#target-status').textContent = state.target_ready?'Testmuster ist aktiv':'Testmuster noch nicht aktiv'; $('#target-status').className = `badge ${state.target_ready?'':'muted'}`; }
    if ($('#start-calibration')) $('#start-calibration').disabled = state.busy || !state.target_ready || state.backend==='simulated' || state.ready!==state.total;
    renderCalibrationResult();
  }
  $$('[data-disable-busy]').forEach(button => button.disabled=state.busy);
  $$('[data-rig-button]').forEach(button => button.disabled=state.busy || button.dataset.ineligible==='true');
}
function rigSignature() {
  return JSON.stringify([state.registered_cameras.map(c=>[c.id,c.position,c.enabled,c.state,c.port]),state.available_cameras,state.discovered_at]);
}
function renderRig() {
  managerSignature=rigSignature();
  const cameras=state.registered_cameras;
  const status=c=>!c.enabled?'Deaktiviert':c.state==='ready'?'Verbunden':c.state==='connecting'?'Verbindet …':'Nicht verbunden';
  $('#main').innerHTML=heading('KAMERAS & REIHENFOLGE','Dein Rig. Von links nach rechts.',
    'Kameras koppeln, pausieren und anordnen. Die Reihenfolge gilt für neue Aufnahmen; vorhandene Fotos behalten ihre gespeicherte Zuordnung.',
    '<button class="button primary" data-action="rig-discover" data-rig-button>USB-Kameras suchen</button>')+
    `<section class="panel"><div class="panel-header"><h2>Reihenfolge im Aufbau</h2><span class="badge muted">${state.total} AKTIV / ${cameras.length} EINGETRAGEN</span></div><div class="rig-direction"><span>← LINKS</span><span>RECHTS →</span></div><div class="rig-list">${cameras.map((c,index)=>`<article class="rig-row ${c.enabled?'':'rig-disabled'}"><span class="rig-position">${String(index+1).padStart(2,'0')}</span><div class="rig-identity"><h3>${esc(modelName(c))} <span class="subtle">/ ${esc(c.id)}</span></h3><p><span class="status-dot ${c.state==='ready'?'':'error'}"></span> ${status(c)}</p><small>SN ${esc(c.serial||c.info?.serial||'nicht hinterlegt')}</small></div><div class="rig-actions"><button class="button small" data-action="rig-move" data-id="${esc(c.id)}" data-direction="-1" data-rig-button data-ineligible="${index===0}" aria-label="${esc(c.id)} nach links">←</button><button class="button small" data-action="rig-move" data-id="${esc(c.id)}" data-direction="1" data-rig-button data-ineligible="${index===cameras.length-1}" aria-label="${esc(c.id)} nach rechts">→</button><button class="button small" data-action="rig-toggle" data-id="${esc(c.id)}" data-enabled="${!c.enabled}" data-rig-button>${c.enabled?'Deaktivieren':'Aktivieren'}<span class="sr-only"> ${esc(c.id)}</span></button><button class="button small danger-button" data-action="rig-remove-review" data-id="${esc(c.id)}" data-rig-button aria-label="${esc(c.id)} aus Rig entfernen">Entfernen</button></div></article>`).join('')||'<div class="empty-state">Noch keine Kameras eingetragen. Suche USB-Kameras, um dein Rig aufzubauen.</div>'}</div><p class="form-note">Die Liste beschreibt deinen Aufbau von links nach rechts. Mit ← / → verschieben. Deaktivierte Kameras behalten ihren Platz und ihre Einstellungen, zählen aber nicht zum aktiven Rig.</p></section>
    <section class="panel section-spacer"><div class="panel-header"><h2>USB-Kameras koppeln</h2><span class="badge muted">${state.backend==='simulated'?'SIMULATION':'PC REMOTE'}</span></div><p class="description">Kamera einschalten und PC Remote wählen. Unter Windows muss sie mit Connect-Cameras.cmd zuerst an WSL weitergereicht werden. Die Suche löst kein Foto aus.</p><div class="rig-discovery">${state.discovered_at?(state.available_cameras.map((device,index)=>{
      const suggestion=device.model.toLowerCase().replace(/sony\s*|\([^)]*\)/g,'').trim().replace(/[^a-z0-9]+/g,'-').replace(/^-|-$/g,'').slice(0,40)||'camera';
      return `<div class="discovered-camera"><div><strong>${esc(device.model)}</strong><small>${esc(device.port)}${device.registered_id?` · Im Rig als ${esc(device.registered_id)}`:''}</small></div>${device.registered_id?'<span class="badge muted">GEKOPPELT</span>':`<form class="pair-camera-form" data-model="${esc(device.model)}" data-port="${esc(device.port)}"><label for="pair-id-${index}">Kamera-ID</label><input id="pair-id-${index}" name="camera_id" value="${esc(suggestion)}" pattern="[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}" maxlength="64" required><button class="button small" type="submit" data-rig-button>Koppeln</button></form>`}</div>`;
    }).join('')||'<div class="empty-state">Keine USB-Kameras gefunden. PC Remote und USB-Weiterleitung prüfen.</div>'):'<div class="empty-state">Mit „USB-Kameras suchen“ verfügbare Geräte anzeigen.</div>'}</div><p class="form-note">Neue Kameras werden anhand ihrer Seriennummer geprüft und rechts angefügt. Bereits eingetragene Kameras über ihren bestehenden Eintrag aktivieren. Nach Rig-Änderungen die Verbindungen prüfen und die Sperre bewusst aufheben; Zeitkorrektur gegebenenfalls neu messen.</p></section>`;
}
function renderCameras() {
  if (!selected.size) state.cameras.forEach(camera => selected.add(camera.id));
  $('#main').innerHTML = heading('MANUELLE KAMERASTEUERUNG', 'Belichtung. Gemeinsam.', 'Wähle Kameras und ändere nur die gewünschten Werte. Alle Ziele werden vorab geprüft; Änderungen werden parallel gesetzt und rückgelesen.') +
    `<section class="panel format-panel"><div class="panel-header"><h2>Gemeinsames Aufnahmeformat</h2><span class="badge">ALLE AKTIVEN KAMERAS</span></div><form id="format-form"><div class="field"><label for="capture-format">Aufnahmeformat</label><select id="capture-format" name="format">${[['jpeg','JPEG · empfohlen'],['raw','RAW'],['raw+jpeg','RAW + JPEG']].map(([value,label])=>`<option value="${value}" ${state.capture_format===value?'selected':''}>${label}</option>`).join('')}</select></div><button class="button primary" id="apply-camera-format" type="submit">Format auf alle Kameras anwenden</button></form><p id="format-consistency" class="form-note"></p><p class="form-note">Das Format gilt immer für das gesamte aktive Rig, unabhängig von der Kameraauswahl unten. JPEG stellt auch ältere Sony-Modelle auf ihre entsprechende JPEG-Qualitätsstufe. Beim Wiederverbinden wird das Format erneut angewendet.</p></section>`+
    '<div class="two-column"><section class="panel"><div class="panel-header"><h2>Gemeinsame Einstellungen</h2><span class="badge muted">MANUAL MODE</span></div><div id="camera-settings-form"></div></section><section class="panel"><div class="panel-header"><h2>Aktuelle Kamerawerte</h2><button class="button small" data-action="read-settings" data-disable-busy>Werte neu lesen</button></div><div id="camera-settings-values"></div></section></div>';
  renderCameraForm();
}
function renderCameraForm() {
  if (!$('#camera-settings-form')) return;
  selected=new Set([...selected].filter(id=>state.cameras.some(camera=>camera.id===id)));
  const previous = Object.fromEntries($$('#camera-controls-form [data-setting]').map(input => [input.dataset.setting,input.value]));
  const targets = state.cameras.filter(camera => selected.has(camera.id));
  const fields = [['expprogram','Belichtungsmodus'],['iso','ISO'],['shutterspeed','Verschlusszeit'],['f-number','Blende'],['whitebalance','Weißabgleich'],['colortemperature','Farbtemperatur · K'],['focusmode','Fokusmodus'],['capturemode','Aufnahmemodus']];
  function field(key,label) {
    const widgets = targets.map(camera => camera.settings?.[key]);
    const available = targets.length && widgets.every(widget => widget && !widget.readonly);
    const id = `control-${key}`;
    if (available && widgets.every(widget => widget.range)) {
      const low = Math.max(...widgets.map(w=>w.range[0])), high = Math.min(...widgets.map(w=>w.range[1]));
      const step = Math.max(...widgets.map(w=>w.range[2] || 1));
      return `<div class="field"><label for="${id}">${label}</label><input type="number" id="${id}" data-setting="${key}" min="${low}" max="${high}" step="${step}" placeholder="Unverändert" ${low>high?'disabled':''}></div>`;
    }
    let common = [];
    if (available && widgets.every(widget => widget.choices)) {
      common = widgets[0].choices.map(value => normal(key,value)).filter((value,index,array) => array.indexOf(value)===index && widgets.every(widget => widget.choices.some(choice => normal(key,choice)===value)));
    }
    return `<div class="field"><label for="${id}">${label}</label><select id="${id}" data-setting="${key}" ${common.length?'':'disabled'}><option value="">${common.length?'Unverändert':'Nicht gemeinsam verfügbar'}</option>${common.map(value=>`<option value="${esc(value)}">${esc(value)}</option>`).join('')}</select></div>`;
  }
  $('#camera-settings-form').innerHTML = `<div class="camera-select">${state.cameras.map(camera=>`<label class="check-chip"><input type="checkbox" data-camera-select="${esc(camera.id)}" ${selected.has(camera.id)?'checked':''}>${esc(modelName(camera))}<span class="status-dot ${camera.state==='ready'?'':'error'}"></span></label>`).join('')}</div><form id="camera-controls-form"><div class="form-grid">${fields.map(args=>field(...args)).join('')}</div><div class="form-actions"><button type="submit" class="button primary" id="apply-camera-settings" ${state.busy?'disabled':''}>Auf ausgewählte Kameras anwenden</button></div><p class="form-note">Für ISO, Blende und Verschlusszeit muss M aktiv sein. Ist der Modus nicht fernsteuerbar, stelle das Moduswahlrad an der Kamera auf M. Bei einem Fehler wird ein Rücksetzen versucht und das Ergebnis angezeigt.</p></form>`;
  $$('#camera-controls-form [data-setting]').forEach(input => { if (previous[input.dataset.setting] !== undefined && !input.disabled) input.value = previous[input.dataset.setting]; });
  $('#camera-settings-values').innerHTML = state.cameras.map(camera => {
    const value = key => esc(camera.settings?.[key]?.value || '—');
    return `<div class="settings-camera"><div class="settings-camera-head"><h3>Sony ${esc(modelName(camera))}</h3><button class="button small" data-action="select-camera" data-id="${esc(camera.id)}">Nur diese</button></div><div class="settings-values">${[['iso','ISO'],['shutterspeed','Verschluss'],['f-number','Blende'],['expprogram','Modus'],['focusmode','Fokus'],['imagequality','Format']].map(([key,label])=>`<div><small>${label}</small><strong>${value(key)}</strong></div>`).join('')}</div><div class="serial">${esc(camera.port)}<br>SN ${esc(camera.info?.serial || camera.serial || 'nicht hinterlegt')}</div></div>`;
  }).join('');
}
function toggle(name,title,description,checked) {
  return `<label class="toggle-row"><span class="toggle-copy"><strong>${title}</strong><small>${description}</small></span><span class="switch"><input type="checkbox" name="${name}" ${checked?'checked':''}><span></span></span></label>`;
}
function renderProcessing() {
  const p=state.processing;
  const options=(rows,current)=>rows.map(([value,label])=>`<option value="${value}" ${String(current)===String(value)?'selected':''}>${label}</option>`).join('');
  $('#main').innerHTML=heading('VIDEO & GIF','Aus Perspektiven wird Bewegung.', 'Nach jeder vollständigen Aufnahmerunde entsteht ein Clip aus der gespeicherten Links-rechts-Reihenfolge. Die Verarbeitung läuft im Hintergrund; deine Kameras bleiben bedienbar.')+
    `<form id="processing-form"><div class="two-column"><section class="panel"><div class="panel-header"><h2>Automatische Erstellung</h2><span class="badge muted">JE AUFNAHMERUNDE</span></div>${toggle('auto_enabled','Neue Aufnahmen automatisch verarbeiten','Video und GIF erscheinen unter den Kamerabildern der jeweiligen Sitzung.',p.auto_enabled)}${toggle('video_enabled','MP4-Video erstellen','H.264, 30 Ausgabebilder/s, ohne Ton.',p.video_enabled)}${toggle('gif_enabled','Animiertes GIF erstellen','Endlose Schleife zum direkten Teilen.',p.gif_enabled)}<div class="form-grid section-spacer"><div class="field"><label for="video-width">Videobreite</label><select id="video-width" name="video_width">${options([[640,'640 px'],[960,'960 px'],[1280,'1280 px · empfohlen'],[1920,'1920 px']],p.video_width)}</select></div><div class="field"><label for="gif-width">GIF-Breite</label><select id="gif-width" name="gif_width">${options([[320,'320 px'],[480,'480 px'],[640,'640 px'],[800,'800 px']],p.gif_width)}</select></div><div class="field"><label for="render-aspect">Seitenverhältnis</label><select id="render-aspect" name="aspect">${options([['16:9','16:9 · Querformat'],['4:3','4:3'],['1:1','1:1 · Quadrat']],p.aspect)}</select></div></div><p class="form-note">Die Bilder werden passend eingepasst; bei unterschiedlichen Formaten bleiben schwarze Ränder. Originale bleiben erhalten.</p></section><section class="panel"><div class="panel-header"><h2>Geschwindigkeit & Bewegung</h2></div><div class="form-grid"><div class="field"><label for="frame-ms">Dauer je Kamerabild · ms</label><input id="frame-ms" name="frame_ms" type="number" min="20" max="2000" step="10" value="${p.frame_ms}" required></div><div class="field"><label for="render-direction">Bewegungsrichtung</label><select id="render-direction" name="direction">${options([['pingpong','Hin und zurück'],['forward','Links → rechts'],['reverse','Rechts → links']],p.direction)}</select></div><div class="field"><label for="video-loops">Wiederholungen im Video</label><input id="video-loops" name="video_loops" type="number" min="1" max="20" value="${p.video_loops}" required></div></div><div id="processing-estimate" class="processing-estimate"></div><p class="form-note">Kleinere Bilddauer bedeutet schnellere Bewegung. GIFs laufen endlos; Videos wiederholen die Kamerafahrt so oft wie hier eingestellt. Für bis zu 32 Kameras, maximal 120 s pro Video.</p><p class="form-note">Standard: 1280 px Video, 640 px GIF. Die Verarbeitung läuft nacheinander und nutzt höchstens zwei Encoder-Threads, auch für den späteren Raspberry-Betrieb.</p></section></div><div class="form-actions"><button type="submit" class="button primary" data-disable-busy>Verarbeitungseinstellungen speichern</button><span class="form-note">Gilt für neue Verarbeitungsaufträge. Vorhandene Exporte bleiben erhalten.</span></div></form><section class="panel section-spacer"><h2>Vorhandene Aufnahmen</h2><p class="description">Vollständige Sitzungen ohne fertige Exporte nachträglich verarbeiten. Einzelne Sitzungen lassen sich auf „Aufnahmen“ auch mit neuen Einstellungen erneut erstellen.</p><div class="form-actions"><button class="button" data-action="process-all" data-disable-busy>Fehlende Videos & GIFs erstellen</button><a class="button" href="#sessions">Aufnahmen ansehen ↗</a></div></section>`;
  updateProcessingEstimate();
}
function updateProcessingEstimate() {
  const form=$('#processing-form');if(!form)return;
  const n=state.total, count=form.direction.value==='pingpong'?Math.max(n,2*n-2):n, cycle=count*Number(form.frame_ms.value)/1000;
  $('#processing-estimate').textContent=`Bei ${n} aktiven Kameras: ${cycle.toFixed(2)} s je Kamerafahrt · ${(cycle*Number(form.video_loops.value)).toFixed(2)} s Video. GIF: endlose Wiederholung.`;
}
function processedMedia(session) {
  const p=session.processing||{status:'not_started',outputs:[]};
  const status={not_started:'Noch kein Video/GIF erstellt',queued:'Wartet auf Verarbeitung',running:p.progress||'Wird verarbeitet',complete:'Video & GIF fertig',failed:'Verarbeitung fehlgeschlagen'}[p.status]||p.status;
  const pending=['queued','running'].includes(p.status);
  const action=session.status==='complete'&&!session.calibration&&session.files.length?`<button class="button small" data-action="process-session" data-session="${session.session_id}" ${pending||state?.busy?'disabled':''}>${p.outputs?.length?'Mit aktuellen Einstellungen neu erstellen':'Video & GIF erstellen'}</button>`:'';
  return `<div class="processed-media"><div class="panel-header"><h3>Video & GIF</h3><span class="subtle">${esc(status)}</span>${action}</div>${p.error?`<p class="camera-error">${esc(p.error)}</p>`:''}${(p.outputs||[]).map(take=>`<div class="render-take"><div class="render-take-title">Runde ${esc(take.take)} · ${take.frame_count} Perspektive(n) · ${take.frame_ms} ms je Bild</div><div class="render-grid">${take.video?`<div class="render-asset"><video controls loop muted playsinline preload="metadata" src="${esc(take.video.url)}" aria-label="Video Runde ${esc(take.take)}"></video><div><span>MP4 · ${take.video.duration_s.toFixed(2)} s</span><a class="button small" href="${esc(take.video.url)}" download>Video herunterladen ↓</a></div></div>`:''}${take.gif?`<div class="render-asset"><img src="${esc(take.gif.url)}" alt="Animiertes GIF Runde ${esc(take.take)}" loading="lazy"><div><span>GIF · endlos</span><a class="button small" href="${esc(take.gif.url)}" download>GIF herunterladen ↓</a></div></div>`:''}</div></div>`).join('')}</div>`;
}
function renderSystem() {
  const o = state.options;
  $('#main').innerHTML = heading('BETRIEB & VERBINDUNGEN', 'Dein Rig. Deine Regeln.', 'Lege fest, wie sich das System bei einer Unterbrechung verhält. Eine unterbrochene Auslösung wird niemals automatisch wiederholt.') +
    `<form id="system-form"><div class="system-grid"><section class="panel"><h2>Wenn eine Kamera die Verbindung verliert</h2><div class="radio-options"><label class="radio-card"><input type="radio" name="disconnect_policy" value="lockout" ${o.disconnect_policy==='lockout'?'checked':''}><span><strong>Aufnahmesperre</strong>Neue Aufnahmen sperren. Nach erfolgreicher Wiederverbindung muss das Rig bewusst freigegeben werden.</span></label><label class="radio-card"><input type="radio" name="disconnect_policy" value="warn" ${o.disconnect_policy==='warn'?'checked':''}><span><strong>Warnung mit Teilaufnahme</strong>Fehlende Kameras anzeigen. Aufnahmen mit dem verfügbaren Rig nur nach ausdrücklicher Bestätigung zulassen.</span></label></div></section>
    <section class="panel"><div class="panel-header"><h2>Wiederverbindung</h2></div>${toggle('auto_reconnect','Automatisch neu verbinden','Nur konfigurierte Modelle und geprüfte Seriennummern zuordnen.',o.auto_reconnect)}${toggle('reapply_settings','Kameraeinstellungen wiederherstellen','Zuletzt erfolgreich gesetzte Werte nach der Wiederverbindung erneut anwenden.',o.reapply_settings)}<div class="form-grid section-spacer"><div class="field"><label for="reconnect-interval">Neuer Versuch nach · s</label><input id="reconnect-interval" name="reconnect_interval_s" type="number" min="1" max="60" step="0.5" value="${o.reconnect_interval_s}" required></div><div class="field"><label for="connection-timeout">Verbindungs-Timeout · s</label><input id="connection-timeout" name="connection_timeout_s" type="number" min="5" max="90" value="${o.connection_timeout_s}" required></div></div></section>
    <section class="panel"><div class="panel-header"><h2>Überwachung & Zeitkorrektur</h2></div>${toggle('invalidate_calibration_on_disconnect','Korrektur nach Trennung deaktivieren','Gespeicherte Werte behalten, aber erst nach neuer Prüfung wieder anwenden.',o.invalidate_calibration_on_disconnect)}<div class="form-grid section-spacer"><div class="field"><label for="health-interval">Verbindungsprüfung alle · s</label><input id="health-interval" name="health_interval_s" type="number" min="1" max="30" value="${o.health_interval_s}" required></div><div class="field"><label for="health-timeout">Antwort-Timeout · s</label><input id="health-timeout" name="health_timeout_s" type="number" min="0.5" max="15" step="0.5" value="${o.health_timeout_s}" required></div></div><p class="form-note">Während einer Aufnahme bleibt die Kameraverbindung exklusiv beim Aufnahmeauftrag. Der USB-Status wird vor jeder Auslösung erneut geprüft.</p></section></div><div class="form-actions"><button type="submit" class="button primary" data-disable-busy>Systemeinstellungen speichern</button><span class="form-note">Einstellungen bleiben über Container-Neustarts erhalten.</span></div></form>
    ${state.backend==='simulated'?`<section class="panel section-spacer"><h2>Verbindungsschutz ausprobieren</h2><p class="description">Nur in der Simulation: Trennung und Wiederkehr einer Kamera testen.</p><div class="diagnostic-actions">${state.cameras.map(camera=>`<button class="button small" data-action="simulate" data-id="${esc(camera.id)}" data-offline="${camera.state==='ready'}">${esc(camera.id)} ${camera.state==='ready'?'trennen':'verbinden'}</button>`).join('')}</div></section>`:''}`;
}
function renderTiming() {
  $('#main').innerHTML = heading('TIMING & KALIBRIERUNG', 'Gleicher Moment. Besser abgestimmt.', 'Langsamere Kameras starten zuerst. Schnellere Kameras erhalten eine Verzögerung, damit ihre Belichtungen näher zusammenliegen.') +
    `<form id="delays-form"><div class="delay-cards">${state.cameras.map(camera=>`<section class="delay-card"><div><div class="camera-id">${esc(camera.id)}</div><h2 style="margin-top:8px">Sony ${esc(modelName(camera))}</h2></div><label class="delay-input"><input type="number" name="${esc(camera.id)}" aria-label="Auslöseverzögerung ${esc(camera.id)} in Millisekunden" min="0" max="1000" step="0.1" value="${state.options.delays_ms[camera.id]||0}" required><span>ms</span></label><p class="subtle">Zusätzliche Wartezeit vor dem Auslösebefehl</p></section>`).join('')}</div><div class="panel" style="margin-bottom:25px">${toggle('compensation_enabled','Zeitkorrektur aktivieren','Manuelle Werte werden bei Übernahme als bewusst geprüft markiert.',state.options.compensation_enabled)}<div class="form-actions"><button type="submit" class="button" data-disable-busy>Manuelle Werte übernehmen</button><span class="form-note">0 ms bedeutet: ohne zusätzliche Verzögerung auslösen.</span></div></div></form>
    <div class="two-column"><section class="panel"><div class="panel-header"><h2>Automatischer Verzögerungstest</h2><span id="target-status" class="badge muted">Testmuster noch nicht aktiv</span></div><p class="description">Ein Bildschirm-Testmuster codiert den sichtbaren Zeitpunkt. Die aufgenommenen Bilder zeigen, welche Kamera früher oder später belichtet hat.</p><div class="steps"><div class="step"><div><h3>Testmuster öffnen</h3><p>Alle Kameras auf denselben Bildschirm ausrichten. Die vier farbigen Ecken müssen groß und vollständig sichtbar sein.</p></div></div><div class="step"><div><h3>Manuell vorbereiten</h3><p>Fokus fest einstellen, kurze Belichtung wählen, z. B. 1/250 s. Aufnahmen mit Bildschirmwechseln oder widersprüchlichen Codes werden verworfen.</p></div></div><div class="step"><div><h3>Messen & auswerten</h3><p>Fünf Aufnahmerunden ohne Korrektur. Mindestens drei vollständige Runden sind nötig. Danach werden Korrekturwerte vorgeschlagen.</p></div></div></div><div class="cal-actions"><a class="button" href="/target" target="_blank" rel="noopener">Testmuster öffnen ↗</a><button class="button primary" id="start-calibration" data-action="calibrate" disabled>5 Testaufnahmen</button></div><p class="form-note">Alternativ direkt im Testmuster-Fenster starten. Die Bildschirmfrequenz begrenzt die Auflösung, bei 60 Hz auf etwa 17 ms. USB-Antwortzeiten gelten nicht als Belichtungsmessung.</p></section><section class="panel cal-result"><div class="panel-header"><h2>Messergebnis</h2><span class="badge muted">OPTISCH</span></div><div id="calibration-result"></div></section></div>`;
  renderCalibrationResult();
}
function renderCalibrationResult() {
  const node = $('#calibration-result'); if (!node) return;
  const proposal = state.calibration.proposal;
  const session = sessions.find(session=>session.calibration && session.status==='complete');
  if (!proposal) {
    node.innerHTML = `<div class="empty-state">Noch keine optische Messung.<br>Öffne das Testmuster und starte eine Messreihe.</div>${session?`<div class="cal-actions"><button class="button" data-action="analyze" data-session="${esc(session.session_id)}" ${state.busy?'disabled':''}>Letzte Messreihe auswerten</button></div>`:''}${state.backend==='simulated'?'<p class="form-note">Die Simulation erzeugt Testbilder und liefert keine gemessenen Belichtungszeiten. Der optische Tester benötigt echte Kameras.</p>':''}`;
    return;
  }
  node.innerHTML = `<div class="metric-number">${proposal.spread_ms}<small>ms gemessener Versatz</small></div><p class="subtle">${proposal.valid_samples} gültige Runden · Auflösung ca. ${proposal.resolution_ms} ms</p><table class="data-table"><thead><tr><th>KAMERA</th><th>RELATIV</th><th>KORREKTUR</th><th>MAD</th></tr></thead><tbody>${Object.entries(proposal.cameras).map(([id,row])=>`<tr><td>${esc(id)}</td><td>+${row.relative_exposure_ms} ms</td><td>+${row.delay_ms} ms</td><td>${row.jitter_mad_ms} ms</td></tr>`).join('')}</tbody></table><p class="description">${esc(proposal.note)}</p>${proposal.rejected.length?`<p class="form-note">${proposal.rejected.length} nicht auswertbare Runden verworfen.</p>`:''}<div class="cal-actions"><button class="button primary" data-action="apply-calibration" ${state.busy?'disabled':''}>${state.calibration.valid&&state.calibration.source==='optical'?'Korrektur erneut übernehmen':'Korrektur aktivieren'}</button></div>`;
}
function renderSessions() {
  $('#main').innerHTML = heading('DEINE AUFNAHMEN', 'Jede Perspektive bleibt erhalten.', 'Originaldateien nach Aufnahmerunde und Kamera sortiert. Unvollständige Sitzungen bleiben zur Prüfung sichtbar.', '<button class="button" data-action="refresh">↻ &nbsp; Aktualisieren</button>') + '<div id="sessions-list"></div>';
  renderSessionsList();
}
function photoCards(files) {
  return files.map(file=>`<div class="session-photo"><a href="${esc(file.download)}" data-action="preview" data-src="${esc(file.preview)}" data-download="${esc(file.download)}" data-name="${esc(file.name)}"><img src="${esc(file.preview)}" alt="Take ${esc(file.take)}, ${esc(file.camera_id)}" loading="lazy"></a><div class="session-photo-meta"><div><strong>${esc(file.camera_id)} · Take ${esc(file.take)}</strong><small>${esc(file.name.split('.').pop().toUpperCase())} · ${formatBytes(file.size)}</small></div><a class="button small" href="${esc(file.download)}" download aria-label="Original ${esc(file.name)} herunterladen">↓</a></div></div>`).join('');
}
function renderSessionsList() {
  if (!$('#sessions-list')) return;
  $('#sessions-list').innerHTML = sessions.length?sessions.map(session=>`<section class="session-card"><div class="session-card-head"><div><h2>${formatDate(session.created_at)} <span class="subtle">/ ${esc(session.session_id.slice(0,8))}</span></h2><p class="subtle">${session.takes} Aufnahmerunde(n) · ${session.files.length} Dateien ${session.calibration?'· Kalibrierung':''} ${session.simulated?'· Simulation':''}${session.excluded_cameras?.length?` · Ohne ${esc(session.excluded_cameras.join(", "))}`:""}</p></div><div class="heading-actions"><span class="badge ${session.status==='complete'?'':'warn'}">${session.status==='complete'?(session.excluded_cameras?.length?'TEILAUFNAHME':'VOLLSTÄNDIG'):esc(session.status.toUpperCase())}</span><a class="button small" href="/api/sessions/${session.session_id}/manifest" download>Protokoll ↓</a></div></div>${session.error?`<p class="camera-error">${esc(session.error)}</p>`:''}<div class="session-photos">${photoCards(session.files.slice(0,9))}</div>${processedMedia(session)}${session.files.length>9?`<div class="cal-actions"><button class="button small" data-action="session-detail" data-session="${session.session_id}">Alle ${session.files.length} Dateien anzeigen</button></div>`:''}</section>`).join(''):'<div class="empty-state">Noch keine Aufnahmen vorhanden.</div>';
}
async function loadSessions() {
  sessions = await api('/api/sessions');
  const signature = JSON.stringify(sessions.map(session=>[session.session_id,session.status,session.files.length,session.processing]));
  if (signature !== sessionsSignature) {
    sessionsSignature = signature; renderRecent(); renderSessionsList();
    if (view==='timing') renderCalibrationResult();
  }
}
async function refresh() {
  if (polling) return;
  polling = true;
  try {
    const first = !state;
    state = await api('/api/state');
    if (first) { await loadSessions(); route(); }
    const newest = state.jobs.at(-1);
    if (newest && ['complete','failed'].includes(newest.status) && newest.id !== lastJob) {
      lastJob = newest.id;
      const successMessage=newest.operation==='reconnect'&&newest.status==='complete'?`${newest.result.ready}/${newest.result.total} aktive Kameras verbunden`:'Auftrag abgeschlossen';
      toast(newest.status==='failed'?newest.error:successMessage,newest.status==='failed');
      await loadSessions();
      if (newest.operation==='settings' || newest.operation==='simulate_disconnect') { settingsSignature=''; if(view==='system') renderSystem(); }
      if (newest.status==='complete'&&newest.operation==='capture_format'&&view==='cameras') renderCameras();
      if (newest.status==='complete'&&newest.operation==='processing_options'&&view==='processing') renderProcessing();
      if (newest.operation==='apply_calibration' && view==='timing') renderTiming();
      if (newest.operation==='calibration' && newest.status==='complete' && mainCalibrationJobs.has(newest.id)) {
        mainCalibrationJobs.delete(newest.id);
        const analyzed = await api('/api/calibration/analyze',{session_id:newest.result.session_id});
        toast('Messreihe vollständig. Bilder werden optisch ausgewertet.');
      }
    }
    updateDynamic();
  } catch (error) {
    $('#footer-status').textContent = 'Webverbindung unterbrochen · Aufnahmebefehle pausiert';
    if ($('#capture-button')) $('#capture-button').disabled=true;
    if (!state) $('#main').innerHTML = `<div class="banner error">${esc(error.message)} · Der Kameradienst wird erneut gesucht.</div>`;
  } finally { polling=false; }
}
document.addEventListener('change', event => {
  const id = event.target.dataset.cameraSelect;
  if (id) { event.target.checked?selected.add(id):selected.delete(id); renderCameraForm(); }
  if(event.target.closest('#processing-form')) updateProcessingEstimate();
});
document.addEventListener('input',event=>{if(event.target.closest('#processing-form'))updateProcessingEstimate();});
document.addEventListener('submit', async event => {
  event.preventDefault();
  const form = event.target;
  if (!form.reportValidity()) return;
  if (form.id==='capture-form') {
    await submit('/api/capture',{takes:Number(form.takes.value),countdown:Number(form.countdown.value),interval:Number(form.interval.value),allow_partial:form.allow_partial.checked});
  } else if (form.id==='format-form') {
    await submit('/api/camera-format',{format:form.elements.format.value});
  } else if (form.id==='processing-form') {
    const body={};['auto_enabled','video_enabled','gif_enabled'].forEach(key=>body[key]=form.elements[key].checked);
    ['frame_ms','video_loops','video_width','gif_width'].forEach(key=>body[key]=Number(form.elements[key].value));
    ['direction','aspect'].forEach(key=>body[key]=form.elements[key].value);
    try{await api('/api/processing/options',body,'PUT');await refresh();toast('Verarbeitungseinstellungen werden gespeichert');}catch(error){toast(error.message,true);}
  } else if (form.id==='camera-controls-form') {
    const values={}; $$('[data-setting]',form).forEach(input=>{ if(!input.disabled && input.value!=='') values[input.dataset.setting]=input.type==='number'?Number(input.value):input.value; });
    if (!Object.keys(values).length) return toast('Wähle mindestens eine Einstellung.');
    await submit('/api/camera-settings',{camera_ids:[...selected],values});
  } else if (form.id==='system-form') {
    const body={disconnect_policy:form.disconnect_policy.value};
    ['auto_reconnect','reapply_settings','invalidate_calibration_on_disconnect'].forEach(key=>body[key]=form.elements[key].checked);
    ['reconnect_interval_s','connection_timeout_s','health_interval_s','health_timeout_s'].forEach(key=>body[key]=Number(form.elements[key].value));
    try { await api('/api/options',body,'PUT'); toast('Systemeinstellungen werden gespeichert'); await refresh(); } catch(error){toast(error.message,true);}
  } else if (form.id==='delays-form') {
    const delays_ms={}; state.cameras.forEach(camera=>delays_ms[camera.id]=Number(form.elements[camera.id].value));
    try { await api('/api/options',{delays_ms,compensation_enabled:form.compensation_enabled.checked},'PUT'); toast('Korrekturwerte werden übernommen'); await refresh(); } catch(error){toast(error.message,true);}
  } else if (form.classList.contains('pair-camera-form')) {
    await submit('/api/rig/pair',{camera_id:form.elements.camera_id.value.trim(),model:form.dataset.model,port:form.dataset.port});
  }
});
document.addEventListener('click', async event => {
  const button = event.target.closest('[data-action]'); if (!button) return;
  event.preventDefault();
  const action=button.dataset.action;
  if(action==='process-session')await submit('/api/processing/render',{session_id:button.dataset.session});
  if(action==='process-all')await submit('/api/processing/all',{});
  if(action==='rig-discover') await submit('/api/rig/discover',{});
  if(action==='rig-toggle') {
    try{await api('/api/rig/enabled',{camera_id:button.dataset.id,enabled:button.dataset.enabled==='true'},'PUT');await refresh();}catch(error){toast(error.message,true);}
  }
  if(action==='rig-move') {
    const ids=state.registered_cameras.map(c=>c.id), index=ids.indexOf(button.dataset.id), next=index+Number(button.dataset.direction);
    if(index>=0&&next>=0&&next<ids.length){[ids[index],ids[next]]=[ids[next],ids[index]];try{await api('/api/rig/order',{camera_ids:ids},'PUT');await refresh();}catch(error){toast(error.message,true);}}
  }
  if(action==='rig-remove-review') {
    const c=state.registered_cameras.find(c=>c.id===button.dataset.id);
    $('#dialog-title').textContent=`${c.id} aus dem Rig entfernen?`;
    $('#dialog-content').innerHTML=`<p class="description">${esc(modelName(c))} wird aus der Kameraliste entfernt. Vorhandene Fotos und Protokolle bleiben erhalten. Die Kamera kann später erneut gekoppelt werden.</p><div class="cal-actions"><button class="button" data-action="close-dialog">Abbrechen</button><button class="button danger-button" data-action="rig-remove" data-id="${esc(c.id)}">Aus Rig entfernen</button></div>`;
    $('#detail-dialog').showModal();
  }
  if(action==='rig-remove'){$('#detail-dialog').close();await submit('/api/rig/remove',{camera_id:button.dataset.id});}
  if(action==='refresh'){await loadSessions();await refresh();}
  if(action==='reconnect')await submit('/api/reconnect',{});
  if(action==='acknowledge')await submit('/api/acknowledge',{});
  if(action==='stop'){try{await api('/api/stop',{});toast('Stop angefordert; laufende Bilder werden noch gesichert.');}catch(error){toast(error.message,true);}}
  if(action==='select-camera'){selected=new Set([button.dataset.id]);renderCameraForm();}
  if(action==='read-settings')await submit('/api/camera-settings/refresh',{});
  if(action==='simulate')await submit('/api/simulate/disconnect',{camera_id:button.dataset.id,offline:button.dataset.offline==='true'});
  if(action==='calibrate'){const job=await submit('/api/calibration/capture',{takes:5,countdown:5,interval:1,allow_partial:false});if(job)mainCalibrationJobs.add(job.id);}
  if(action==='analyze')await submit('/api/calibration/analyze',{session_id:button.dataset.session});
  if(action==='apply-calibration')await submit('/api/calibration/apply',{});
  if(action==='close-dialog')$('#detail-dialog').close();
  if(action==='camera-detail'){
    const camera=state.cameras.find(camera=>camera.id===button.dataset.id);
    $('#dialog-title').textContent=`Sony ${modelName(camera)} · Verbindungsdetails`;
    $('#dialog-content').innerHTML=`<pre>${esc(JSON.stringify({id:camera.id,state:camera.state,port:camera.port,serial:camera.info.serial || camera.serial,last_seen:camera.last_seen,health:camera.health,error:camera.error},null,2))}</pre><div class="cal-actions"><button class="button" data-action="reconnect-one" data-id="${esc(camera.id)}">Diese Kamera neu verbinden</button></div>`;
    $('#detail-dialog').showModal();
  }
  if(action==='reconnect-one'){$('#detail-dialog').close();await submit('/api/reconnect',{camera_ids:[button.dataset.id]});}
  if(action==='preview'){
    $('#dialog-title').textContent=button.dataset.name;
    $('#dialog-content').innerHTML=`<img src="${esc(button.dataset.src)}" alt="Bildvorschau" style="width:100%;max-height:60vh;object-fit:contain;border-radius:8px"><div class="cal-actions"><a class="button primary" href="${esc(button.dataset.download)}" download>Original herunterladen ↓</a></div>`;
    if(!$('#detail-dialog').open) $('#detail-dialog').showModal();
  }
  if(action==='session-detail'){
    const session=sessions.find(session=>session.session_id===button.dataset.session);
    $('#dialog-title').textContent=`Aufnahme ${session.session_id.slice(0,8)}`;
    $('#dialog-content').innerHTML=`<div class="session-photos">${photoCards(session.files)}</div>`;
    $('#detail-dialog').showModal();
  }
});
document.addEventListener('keydown', event=>{
  if(event.code==='Space' && view==='overview' && !['INPUT','SELECT','TEXTAREA','BUTTON'].includes(document.activeElement.tagName) && !$('#detail-dialog').open){event.preventDefault();if(!$('#capture-button').disabled)$('#capture-form').requestSubmit();}
});
window.addEventListener('hashchange',route);
refresh();setInterval(refresh,1000);setInterval(()=>{if(!state?.busy)loadSessions().catch(()=>{});},2500);

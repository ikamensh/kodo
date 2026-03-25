// ============================================================
// DashboardState
// ============================================================
class DashboardState {
  constructor() { this.reset(); }
  reset() {
    this.events = [];
    this.agents = {};
    this.activeAgent = '';
    this.cycle = 0;
    this.maxCycles = 0;
    this.stageLabel = '';
    this.goal = '';
    this.projectDir = '';
    this.projectName = '';
    this.orchestrator = '';
    this.model = '';
    this.totalCost = 0;
    this.orchestratorCost = 0;
    this.elapsed = 0;
    this.maxT = 0;
    this.finished = false;
    this.stages = [];
    this.currentStage = null;
    this.team = {};
    this.config = {};
    this.hasStages = false;
    this.completedStages = 0;
    this.numStages = 0;
    this.runId = '';
  }
}

// ============================================================
// processEvent
// ============================================================
function processEvent(state, evt) {
  state.events.push(evt);
  const t = evt.t || 0;
  state.elapsed = t;
  if (t > state.maxT) state.maxT = t;

  switch (evt.event) {
    case 'run_init':
      state.projectDir = evt.project_dir || '';
      state.projectName = state.projectDir.split('/').pop() || '?';
      break;
    case 'cli_args':
      if (evt.goal_text) state.goal = evt.goal_text;
      if (evt.max_cycles) state.maxCycles = evt.max_cycles;
      if (evt.orchestrator) state.orchestrator = evt.orchestrator;
      if (evt.orchestrator_model) state.model = evt.orchestrator_model;
      break;
    case 'run_start':
      state.goal = evt.goal || state.goal;
      state.orchestrator = evt.orchestrator || '';
      state.model = evt.model || '';
      state.maxCycles = evt.max_cycles || state.maxCycles;
      state.team = evt.team || {};
      state.hasStages = evt.has_stages || false;
      state.numStages = evt.num_stages || 0;
      for (const [name, info] of Object.entries(state.team)) {
        if (!state.agents[name]) {
          state.agents[name] = {calls:0, cost_usd:0, input_tokens:0, output_tokens:0,
            elapsed_s:0, errors:0, cost_bucket:'', active:false, backend:info.backend, model:info.model};
        }
      }
      break;
    case 'run_cycle': case 'cycle_start':
      state.cycle = evt.cycle || state.cycle + 1;
      break;
    case 'cycle_end':
      state.orchestratorCost += evt.cost_usd || 0;
      break;
    case 'stage_start':
      state.currentStage = evt.stage_index;
      state.stageLabel = evt.stage_name || '';
      if (!state.stages.find(s => s.index === evt.stage_index)) {
        state.stages.push({index: evt.stage_index, name: evt.stage_name || `Stage ${evt.stage_index}`,
          description: '', acceptance_criteria: '', status: 'active'});
        state.stages.sort((a, b) => a.index - b.index);
        state.numStages = Math.max(state.numStages, state.stages.length);
        state.hasStages = true;
      } else {
        state.stages.find(s => s.index === evt.stage_index).status = 'active';
      }
      break;
    case 'stage_end': {
      const es = state.stages.find(s => s.index === evt.stage_index);
      if (es && evt.finished) { es.status = 'done'; state.completedStages++; }
      state.currentStage = null;
      break;
    }
    case 'orchestrator_tool_call':
      state.activeAgent = evt.agent || '';
      for (const [n, a] of Object.entries(state.agents)) a.active = (n === state.activeAgent);
      break;
    case 'agent_run_start': {
      const an = evt.agent || '';
      if (an && !state.agents[an]) state.agents[an] = {calls:0,cost_usd:0,input_tokens:0,output_tokens:0,elapsed_s:0,errors:0,cost_bucket:'',active:true};
      if (an && state.agents[an]) state.agents[an].active = true;
      break;
    }
    case 'agent_run_end': {
      const n = evt.agent || '';
      if (n && state.agents[n]) {
        const a = state.agents[n];
        a.calls++; a.cost_usd += evt.cost_usd||0; a.input_tokens += evt.input_tokens||0;
        a.output_tokens += evt.output_tokens||0; a.elapsed_s += evt.elapsed_s||0;
        if (evt.is_error) a.errors++; if (evt.cost_bucket) a.cost_bucket = evt.cost_bucket;
        a.active = false;
      }
      state.totalCost += evt.cost_usd || 0;
      break;
    }
    case 'agent_timeout': {
      const tn = evt.agent || '';
      if (tn && state.agents[tn]) { state.agents[tn].errors++; state.agents[tn].active = false; }
      break;
    }
    case 'run_end':
      state.finished = true;
      for (const a of Object.values(state.agents)) a.active = false;
      state.activeAgent = '';
      break;
    case 'orchestrator_done_accepted':
      state.totalCost += evt.cost_usd || 0;
      break;
  }
}

// ============================================================
// Formatters
// ============================================================
function fmtTime(s) {
  if (s == null) return '-';
  s = Math.round(s);
  if (s < 60) return s + 's';
  const m = Math.floor(s / 60), sec = s % 60;
  if (m < 60) return m + 'm' + String(sec).padStart(2, '0') + 's';
  return Math.floor(m/60) + 'h' + String(m%60).padStart(2,'0') + 'm';
}
function fmtTokens(n) {
  if (n == null) return '-';
  if (n >= 1e6) return (n/1e6).toFixed(1) + 'M';
  if (n >= 1000) return Math.round(n/1000) + 'k';
  return String(n);
}
function fmtCost(c) {
  if (c == null || c < 0.005) return '-';
  return '$' + c.toFixed(2);
}
function evCat(type) {
  if (type.startsWith('agent')) return 'agent';
  if (type.startsWith('orchestrator')) return 'orchestrator';
  if (type.startsWith('cycle') || type.startsWith('stage') || type.startsWith('run_cycle')) return 'cycle';
  if (type.startsWith('coach') || type.startsWith('advisor') || type.startsWith('advisory')) return 'coach';
  if (type.includes('error') || type.includes('timeout')) return 'error';
  return 'session';
}
function evDetail(evt) {
  const e = evt.event;
  if (e === 'agent_run_end') return `<span class="hl">${evt.agent||'?'}</span> ${fmtTime(evt.elapsed_s)} ${evt.cost_usd?fmtCost(evt.cost_usd):''}${evt.is_error?' ERROR':''}`;
  if (e === 'agent_run_start') return `<span class="hl">${evt.agent||'?'}</span> started`;
  if (e === 'agent_query') return `<span class="hl">${evt.agent||'?'}</span> ${(evt.prompt||'').substring(0,80)}`;
  if (e === 'agent_timeout') return `<span class="hl">${evt.agent||'?'}</span> timeout (${evt.reason||'?'})`;
  if (e === 'orchestrator_tool_call') return `&rarr; <span class="hl">${evt.agent||'?'}</span>`;
  if (e === 'orchestrator_done_accepted') return `done: ${(evt.summary||'').substring(0,80)}`;
  if (e === 'cycle_end') return `cycle done: ${(evt.summary||'').substring(0,80)}`;
  if (e === 'stage_start') return `<span class="hl">#${evt.stage_index}: ${evt.stage_name||''}</span>`;
  if (e === 'stage_end') return `#${evt.stage_index} ${evt.finished?'done':'incomplete'}: ${(evt.summary||'').substring(0,60)}`;
  if (e === 'run_start') return `${evt.orchestrator}/${evt.model}`;
  if (e === 'run_end') return `finished=${evt.finished} cycles=${evt.total_cycles} cost=${fmtCost(evt.total_cost_usd)}`;
  if (e === 'auto_commit_done') return 'committed';
  if (e === 'advisor_assess_end') return (evt.assessment||'').substring(0,100);
  if (e === 'session_query_end') return `${evt.session||'?'} ${fmtTime(evt.elapsed_s)} turns=${evt.turns||'?'}`;
  return '';
}

// ============================================================
// View rendering
// ============================================================
let activeView = localStorage.getItem('kodo-view') || 'overview';
let selectedStageIdx = null;
let currentFilter = 'all';
let currentSearch = '';

function renderStatusBar(state) {
  const dot = document.getElementById('sb-dot');
  const agent = document.getElementById('sb-agent');
  if (state.finished) { dot.className = 'dot finished'; agent.textContent = 'finished'; }
  else if (state.activeAgent) { dot.className = 'dot active'; agent.textContent = state.activeAgent; }
  else { dot.className = 'dot'; agent.textContent = 'idle'; }
  const cost = Object.values(state.agents).reduce((s, a) => s + a.cost_usd, 0) + state.orchestratorCost;
  document.getElementById('sb-cycle').textContent = `cycle ${state.cycle}/${state.maxCycles}`;
  document.getElementById('sb-stage').textContent = state.hasStages ? `stage ${state.completedStages}/${state.numStages}${state.stageLabel ? ': ' + state.stageLabel : ''}` : '';
  document.getElementById('sb-cost').textContent = fmtCost(cost);
  document.getElementById('sb-time').textContent = fmtTime(state.elapsed);
}

function renderOverviewView(state) {
  const grid = document.getElementById('ov-stats');
  const cost = Object.values(state.agents).reduce((s, a) => s + a.cost_usd, 0) + state.orchestratorCost;
  const stageText = state.hasStages ? `${state.completedStages}/${state.numStages}` : '-';
  const stats = [
    {l:'Project', v:state.projectName||'-'}, {l:'Orchestrator', v:`${state.orchestrator}/${state.model}`||'-'},
    {l:'Cycle', v:`${state.cycle}/${state.maxCycles}`}, {l:'Stages', v:stageText},
    {l:'Elapsed', v:fmtTime(state.elapsed)}, {l:'Cost', v:fmtCost(cost)},
  ];
  grid.innerHTML = stats.map(s => `<div class="ov-stat"><div class="label">${s.l}</div><div class="value">${s.v}</div></div>`).join('');
  document.getElementById('ov-goal').textContent = state.goal || '(no goal)';
  const fill = document.getElementById('progress-fill');
  if (state.hasStages && state.numStages > 0) fill.style.width = Math.round(state.completedStages/state.numStages*100) + '%';
  else if (state.maxCycles > 0) fill.style.width = Math.round(state.cycle/state.maxCycles*100) + '%';

  const ag = document.getElementById('agents-grid');
  const names = Object.keys(state.agents);
  if (!names.length) { ag.innerHTML = '<div style="color:var(--fg-muted)">No agents yet</div>'; return; }
  ag.innerHTML = names.map(name => {
    const a = state.agents[name], cls = a.active ? 'agent-card active' : 'agent-card';
    const ec = a.errors > 0 ? 'a-row err' : 'a-row';
    return `<div class="${cls}">
      <div class="a-name"><span class="dot"></span>${name}</div>
      <div class="a-row"><span>Calls</span><span class="v">${a.calls}</span></div>
      <div class="a-row"><span>In</span><span class="v">${fmtTokens(a.input_tokens)}</span></div>
      <div class="a-row"><span>Out</span><span class="v">${fmtTokens(a.output_tokens)}</span></div>
      <div class="a-row"><span>Cost</span><span class="v">${fmtCost(a.cost_usd)}</span></div>
      <div class="a-row"><span>Time</span><span class="v">${fmtTime(a.elapsed_s)}</span></div>
      <div class="${ec}"><span>Errors</span><span class="v">${a.errors}</span></div>
    </div>`;
  }).join('');
}

function renderStagesView(state) {
  const list = document.getElementById('stages-list');
  const detail = document.getElementById('stage-detail');
  if (!state.stages.length) {
    list.innerHTML = '<div style="color:var(--fg-muted);padding:16px">No stages in this run</div>';
    detail.innerHTML = '';
    return;
  }
  list.innerHTML = state.stages.map(s => {
    const icon = s.status === 'done' ? '&#10003;' : s.status === 'active' ? '&#9679;' : '&#9675;';
    const sel = selectedStageIdx === s.index ? ' selected' : '';
    return `<div class="stage-row${sel}" data-idx="${s.index}">
      <span class="s-icon ${s.status}">${icon}</span>
      <span class="s-idx">${s.index}.</span>
      <span class="s-name">${s.name}</span>
    </div>`;
  }).join('');

  list.querySelectorAll('.stage-row').forEach(row => {
    row.onclick = () => { selectedStageIdx = parseInt(row.dataset.idx); renderStageDetail(state); renderStagesView(state); };
  });

  renderStageDetail(state);
}

function renderStageDetail(state) {
  const detail = document.getElementById('stage-detail');
  if (selectedStageIdx == null) { detail.innerHTML = '<div class="sd-empty">Select a stage</div>'; return; }
  const s = state.stages.find(x => x.index === selectedStageIdx);
  if (!s) { detail.innerHTML = '<div class="sd-empty">Stage not found</div>'; return; }
  const editable = s.status === 'pending';
  const ro = editable ? '' : 'readonly';
  detail.innerHTML = `
    <div class="sd-header">${s.index}. ${s.name}<span class="sd-badge ${s.status}">${s.status}</span></div>
    <div>
      <label>Name</label>
      <input type="text" id="sd-name" value="${s.name.replace(/"/g,'&quot;')}" ${ro}>
    </div>
    <div>
      <label>Description</label>
      <textarea id="sd-desc" ${ro}>${s.description||''}</textarea>
    </div>
    <div>
      <label>Acceptance Criteria</label>
      <textarea id="sd-accept" ${ro}>${s.acceptance_criteria||''}</textarea>
    </div>
    <div class="sd-actions">
      <button id="sd-save" ${editable ? '' : 'disabled'}>Save</button>
      <span class="sd-note">${editable ? 'Changes saved to goal-plan.json. Takes effect on resume.' : 'Read-only (stage already started)'}</span>
    </div>`;

  if (editable) {
    document.getElementById('sd-save').onclick = async () => {
      s.name = document.getElementById('sd-name').value;
      s.description = document.getElementById('sd-desc').value;
      s.acceptance_criteria = document.getElementById('sd-accept').value;
      const stages = state.stages.map(st => ({
        index: st.index, name: st.name, description: st.description, acceptance_criteria: st.acceptance_criteria
      }));
      const res = await fetch(`/api/run/${state.runId}/stages`, {
        method: 'PUT', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({stages})
      });
      const data = await res.json();
      document.getElementById('sd-save').textContent = data.error ? 'Error' : 'Saved';
      setTimeout(() => { document.getElementById('sd-save').textContent = 'Save'; }, 1500);
      renderStagesView(state);
    };
  }
}

function renderTimelineView(state) {
  const list = document.getElementById('event-list');
  const skipTypes = new Set(['run_init','session_reset','agent_session_reset','session_query_start']);
  let events = state.events.filter(e => !skipTypes.has(e.event));
  if (currentFilter !== 'all') events = events.filter(e => evCat(e.event) === currentFilter);
  if (currentSearch) { const q = currentSearch.toLowerCase(); events = events.filter(e => JSON.stringify(e).toLowerCase().includes(q)); }
  const visible = events.slice(-300);
  list.innerHTML = visible.map((evt, i) => {
    const cat = evCat(evt.event), t = evt.t != null ? fmtTime(evt.t) : '';
    const offset = events.length - visible.length;
    const id = offset + i;
    return `<div><div class="ev-row" data-id="${id}"><span class="ev-t">${t}</span><span class="ev-type ${cat}">${evt.event}</span><span class="ev-detail">${evDetail(evt)}</span></div>
      <div class="ev-expanded" id="evx-${id}">${JSON.stringify(evt, null, 2)}</div></div>`;
  }).join('');
  list.scrollTop = list.scrollHeight;
  list.querySelectorAll('.ev-row').forEach(row => {
    row.onclick = () => {
      const exp = document.getElementById('evx-' + row.dataset.id);
      if (!exp) return;
      if (exp.classList.contains('visible')) exp.classList.remove('visible');
      else { list.querySelectorAll('.ev-expanded.visible').forEach(e => e.classList.remove('visible')); exp.classList.add('visible'); }
    };
  });
}

function renderConfigView(state) {
  const tabs = document.getElementById('cfg-tabs');
  const content = document.getElementById('cfg-content');
  const files = state.config || {};
  const names = Object.keys(files).filter(k => files[k] != null);
  if (!names.length) { tabs.innerHTML = ''; content.textContent = 'No config files'; return; }
  const act = tabs.dataset.active || names[0];
  tabs.innerHTML = names.map(n => `<button class="${n===act?'active':''}" data-tab="${n}">${n}</button>`).join('');
  const fc = files[act] || '';
  try { content.textContent = act.endsWith('.json') ? JSON.stringify(JSON.parse(fc),null,2) : fc; }
  catch { content.textContent = fc; }
  tabs.querySelectorAll('button').forEach(btn => { btn.onclick = () => { tabs.dataset.active = btn.dataset.tab; renderConfigView(state); }; });
}

function render(state) {
  renderStatusBar(state);
  const v = activeView;
  if (v === 'overview') renderOverviewView(state);
  else if (v === 'stages') renderStagesView(state);
  else if (v === 'timeline') renderTimelineView(state);
  else if (v === 'config') renderConfigView(state);
}

// ============================================================
// PlaybackEngine
// ============================================================
class PlaybackEngine {
  constructor(events, onEvent, onComplete) {
    this.allEvents = events; this.onEvent = onEvent; this.onComplete = onComplete;
    this.index = 0; this.speed = 5; this.playing = false; this._timer = null;
  }
  get progress() { return this.allEvents.length ? this.index / this.allEvents.length : 0; }
  get currentT() { return this.index > 0 ? (this.allEvents[this.index-1].t||0) : 0; }
  get totalT() { return this.allEvents.length ? (this.allEvents[this.allEvents.length-1].t||0) : 0; }
  play() { if (this.index >= this.allEvents.length) return; this.playing = true; this._next(); }
  pause() { this.playing = false; if (this._timer) { clearTimeout(this._timer); this._timer = null; } }
  setSpeed(s) { this.speed = s; }
  _next() {
    if (!this.playing || this.index >= this.allEvents.length) { if (this.index >= this.allEvents.length) this.onComplete(); return; }
    const evt = this.allEvents[this.index]; this.onEvent(evt); this.index++;
    if (this.index >= this.allEvents.length) { this.onComplete(); return; }
    if (this.speed === 0) { this._batch(); return; }
    const dt = ((this.allEvents[this.index].t||0) - (evt.t||0)) * 1000;
    this._timer = setTimeout(() => this._next(), Math.min(Math.max(10, dt / this.speed), 2000 / this.speed));
  }
  _batch() {
    let c = 0;
    while (this.index < this.allEvents.length && c < 50) { this.onEvent(this.allEvents[this.index]); this.index++; c++; }
    if (this.index >= this.allEvents.length) this.onComplete();
    else if (this.playing) requestAnimationFrame(() => this._batch());
  }
}

// ============================================================
// App controller
// ============================================================
const state = new DashboardState();
let playback = null, sseSource = null, renderDebounce = null;

function debouncedRender() {
  if (renderDebounce) return;
  renderDebounce = requestAnimationFrame(() => { render(state); updatePbUI(); renderDebounce = null; });
}

function updatePbUI() {
  if (!playback) return;
  const scrub = document.getElementById('pb-scrub'), tl = document.getElementById('pb-time');
  if (scrub) scrub.value = Math.round(playback.progress * 1000);
  if (tl) tl.textContent = `${fmtTime(playback.currentT)} / ${fmtTime(playback.totalT)}`;
}

function switchView(name) {
  activeView = name;
  localStorage.setItem('kodo-view', name);
  document.querySelectorAll('.nav-item').forEach(n => n.classList.toggle('active', n.dataset.view === name));
  document.querySelectorAll('.view').forEach(v => v.classList.toggle('active', v.id === 'view-' + name));
  render(state);
}

async function loadRun(runId) {
  if (playback) { playback.pause(); playback = null; }
  if (sseSource) { sseSource.close(); sseSource = null; }
  state.reset();
  state.runId = runId;
  document.getElementById('empty-state').style.display = 'none';
  document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));

  const [events, runData] = await Promise.all([
    fetch(`/api/run/${runId}/events`).then(r => r.json()),
    fetch(`/api/run/${runId}`).then(r => r.json()),
  ]);
  state.config = runData.config || {};

  if (state.config['goal-plan.json']) {
    try {
      const plan = JSON.parse(state.config['goal-plan.json']);
      const stages = plan.stages || plan;
      if (Array.isArray(stages)) {
        state.stages = stages.map(s => ({index:s.index, name:s.name, description:s.description||'', acceptance_criteria:s.acceptance_criteria||'', status:'pending'}));
        state.numStages = state.stages.length;
        state.hasStages = state.stages.length > 0;
      }
    } catch {}
  }

  const isFinished = events.some(e => e.event === 'run_end');
  if (isFinished) setupPlayback(events); else setupLive(runId, events);
  switchView(activeView);
}

function setupPlayback(events) {
  document.getElementById('pb').classList.remove('hidden');
  document.getElementById('live-dot').style.display = 'none';
  document.getElementById('live-label').style.display = 'none';
  for (const evt of events) processEvent(state, evt);
  render(state);
  playback = new PlaybackEngine(events, ()=>{}, () => debouncedRender());
  playback.index = events.length;
  updatePbUI();
  document.getElementById('pb-play').onclick = () => {
    if (playback.index >= playback.allEvents.length) seekFrac(0);
    playback.onEvent = evt => { processEvent(state, evt); debouncedRender(); };
    playback.play();
  };
  document.getElementById('pb-pause').onclick = () => playback.pause();
  document.getElementById('pb-end').onclick = () => { playback.pause(); seekFrac(1); };
  document.getElementById('pb-speed').onchange = e => playback.setSpeed(parseFloat(e.target.value));
  document.getElementById('pb-scrub').oninput = function() { playback.pause(); seekFrac(parseInt(this.value)/1000); };
}

function seekFrac(f) {
  if (!playback) return;
  const target = Math.round(f * playback.allEvents.length);
  state.reset(); state.runId = document.getElementById('run-selector').value;
  state.config = JSON.parse(JSON.stringify(window._loadedConfig || {}));
  reloadStages();
  for (let i = 0; i < target && i < playback.allEvents.length; i++) processEvent(state, playback.allEvents[i]);
  playback.index = target;
  render(state); updatePbUI();
}

function reloadStages() {
  if (state.config['goal-plan.json']) {
    try {
      const plan = JSON.parse(state.config['goal-plan.json']);
      const stages = plan.stages || plan;
      if (Array.isArray(stages)) {
        state.stages = stages.map(s => ({index:s.index, name:s.name, description:s.description||'', acceptance_criteria:s.acceptance_criteria||'', status:'pending'}));
        state.numStages = state.stages.length; state.hasStages = state.stages.length > 0;
      }
    } catch {}
  }
}

function setupLive(runId, initialEvents) {
  document.getElementById('pb').classList.add('hidden');
  document.getElementById('live-dot').style.display = '';
  document.getElementById('live-label').style.display = '';
  for (const evt of initialEvents) processEvent(state, evt);
  render(state);
  sseSource = new EventSource(`/api/run/${runId}/stream`);
  let skip = initialEvents.length;
  sseSource.onmessage = msg => {
    try {
      const evt = JSON.parse(msg.data);
      if (skip > 0) { skip--; return; }
      processEvent(state, evt); debouncedRender();
      if (evt.event === 'run_end') { sseSource.close(); document.getElementById('live-dot').style.display = 'none'; document.getElementById('live-label').style.display = 'none'; }
    } catch {}
  };
}

// ============================================================
// Navigation + init
// ============================================================
document.querySelectorAll('.nav-item').forEach(item => {
  item.onclick = () => switchView(item.dataset.view);
});

document.getElementById('event-filter').onchange = function() { currentFilter = this.value; renderTimelineView(state); };
document.getElementById('event-search').oninput = function() { currentSearch = this.value; renderTimelineView(state); };

async function loadRunList() {
  const runs = await fetch('/api/runs').then(r => r.json());
  const sel = document.getElementById('run-selector');
  sel.innerHTML = '<option value="">Select a run...</option>';
  for (const r of runs) {
    const st = r.finished ? 'done' : `cycle ${r.completed_cycles}/${r.max_cycles}`;
    const goal = (r.goal||'').substring(0,60).replace(/\n/g,' ');
    const opt = document.createElement('option');
    opt.value = r.run_id;
    opt.textContent = `${r.run_id} [${st}] ${r.project_name} — ${goal}`;
    sel.appendChild(opt);
  }
  sel.onchange = () => { const id = sel.value; if (id) { window.location.hash = `run=${id}`; loadRun(id); } };
  const hash = window.location.hash;
  if (hash.startsWith('#run=')) { const id = hash.substring(5); sel.value = id; loadRun(id); }
}

// Store loaded config for seek reset
const _origLoadRun = loadRun;
window._loadedConfig = {};
loadRun = async function(runId) {
  await _origLoadRun(runId);
  window._loadedConfig = JSON.parse(JSON.stringify(state.config));
};

function initTheme() {
  if (localStorage.getItem('kodo-theme') === 'light') document.documentElement.classList.add('light');
  const btn = document.getElementById('theme-toggle');
  const upd = () => { btn.textContent = document.documentElement.classList.contains('light') ? 'dark' : 'light'; };
  upd();
  btn.onclick = () => {
    document.documentElement.classList.toggle('light');
    localStorage.setItem('kodo-theme', document.documentElement.classList.contains('light') ? 'light' : 'dark');
    upd();
  };
}

initTheme();
loadRunList();

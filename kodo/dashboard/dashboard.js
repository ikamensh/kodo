// ============================================================
// DashboardState
// ============================================================
class DashboardState {
  constructor() { this.reset(); }
  reset() {
    this.events = [];
    this.agents = {};        // name → {calls, cost_usd, input_tokens, output_tokens, elapsed_s, errors, cost_bucket, active, backend, model}
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
    this.files = {};
    this.hasStages = false;
    this.completedStages = 0;
    this.numStages = 0;
    this.runId = '';
    // Enriched data
    this.dispatches = [];     // [{agent, prompt, report, startT, endT, error, cost, elapsed, stageIdx}]
    this._pending = {};       // agent → dispatch being built
    this.coachEvents = [];    // [{type, t, message/reason/...}]
    this.intake = null;       // {endT, turns, model, elapsed, response, cost}
    this._runStarted = false;
    this.teamDescriptions = {};
    this.runMode = '';
    this.coachEnabled = false;
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
      if (evt.team) state.runMode = evt.team;
      break;

    case 'run_start':
      state._runStarted = true;
      state.goal = evt.goal || state.goal;
      state.orchestrator = evt.orchestrator || state.orchestrator;
      state.model = evt.model || state.model;
      state.maxCycles = evt.max_cycles || state.maxCycles;
      state.team = evt.team || {};
      state.hasStages = evt.has_stages || false;
      state.numStages = evt.num_stages || 0;
      state.coachEnabled = evt.coach_enabled || false;
      for (const [name, info] of Object.entries(state.team)) {
        if (!state.agents[name]) {
          state.agents[name] = {calls:0, cost_usd:0, input_tokens:0, output_tokens:0,
            elapsed_s:0, errors:0, cost_bucket:'', active:false, backend:info.backend, model:info.model};
        }
      }
      break;

    case 'session_query_end':
      // Capture intake session (first session before run_start)
      if (!state._runStarted && !state.intake) {
        state.intake = {
          endT: t, turns: evt.turns,
          backend: evt.session || '', model: evt.model || '',
          elapsed: evt.elapsed_s, response: evt.response_text || '', cost: evt.cost_usd || 0
        };
      }
      break;

    case 'run_cycle': case 'cycle_start':
      state.cycle = evt.cycle || state.cycle + 1;
      break;

    case 'cycle_end':
      state.orchestratorCost += evt.cost_usd || 0;
      break;

    case 'stage_start': {
      state.currentStage = evt.stage_index;
      state.stageLabel = evt.stage_name || '';
      let stg = state.stages.find(s => s.index === evt.stage_index);
      if (!stg) {
        stg = {index: evt.stage_index, name: evt.stage_name || `Stage ${evt.stage_index}`,
          description: '', acceptance_criteria: '', status: 'active',
          startT: t, endT: null, summary: '', agents: [], coachReasoning: ''};
        state.stages.push(stg);
        state.stages.sort((a, b) => a.index - b.index);
        state.numStages = Math.max(state.numStages, state.stages.length);
        state.hasStages = true;
      } else {
        stg.status = 'active';
        stg.startT = t;
      }
      break;
    }

    case 'stage_end': {
      const es = state.stages.find(s => s.index === evt.stage_index);
      if (es) {
        if (evt.finished) { es.status = 'done'; state.completedStages++; }
        es.endT = t;
        es.summary = evt.summary || '';
      }
      state.currentStage = null;
      break;
    }

    case 'orchestrator_tool_call':
      state.activeAgent = evt.agent || '';
      for (const [n, a] of Object.entries(state.agents)) a.active = (n === state.activeAgent);
      state._pending[evt.agent] = {agent: evt.agent, task: evt.task || '', startT: t, stageIdx: state.currentStage};
      break;

    case 'agent_query': {
      const aq = evt.agent || '';
      if (state._pending[aq]) {
        state._pending[aq].prompt = evt.prompt || '';
      } else {
        state._pending[aq] = {agent: aq, prompt: evt.prompt || '', startT: t, stageIdx: state.currentStage};
      }
      break;
    }

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
      // Close dispatch
      if (state._pending[n]) {
        const d = state._pending[n];
        d.endT = t; d.report = evt.report || ''; d.error = evt.is_error || false;
        d.cost = evt.cost_usd || 0; d.elapsed = evt.elapsed_s || 0;
        state.dispatches.push(d);
        delete state._pending[n];
        // Track agent on current stage
        if (state.currentStage != null) {
          const stg = state.stages.find(s => s.index === state.currentStage);
          if (stg && !stg.agents.includes(n)) stg.agents.push(n);
        }
      }
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

    // Coach events
    case 'advisory_pushed':
      state.coachEvents.push({type: 'advisory', t, id: evt.advisory_id, priority: evt.priority, message: evt.message || ''});
      break;
    case 'coach_filtered':
      state.coachEvents.push({type: 'filtered', t, reason: evt.reason || ''});
      break;
    case 'coach_assess_ok':
      state.coachEvents.push({type: 'assess_ok', t, dispatches: evt.dispatches});
      break;
    case 'advisor_assess_end':
      state.coachEvents.push({type: 'decision', t, action: evt.action, stageName: evt.stage_name, reasoning: evt.reasoning || ''});
      if (evt.stage_name) {
        const stg = state.stages.find(s => s.name === evt.stage_name);
        if (stg) stg.coachReasoning = evt.reasoning || '';
      }
      break;
    case 'coach_started':
      state.coachEnabled = true;
      state.coachEvents.push({type: 'started', t});
      break;
    case 'coach_stopped':
      state.coachEvents.push({type: 'stopped', t});
      break;
    case 'advisor_done':
      state.coachEvents.push({type: 'done', t, summary: evt.summary || ''});
      break;
  }
}

// ============================================================
// Formatters
// ============================================================
const BACKEND_NAMES = {claude: 'claude code', gemini_cli: 'gemini cli'};
function fmtBackend(b) { return BACKEND_NAMES[b] || b || '?'; }

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
  if (e === 'auto_commit_done') return `committed ${evt.commit_message ? evt.commit_message.substring(0,60) : ''}`;
  if (e === 'advisory_pushed') return (evt.message||'').substring(0,80);
  if (e === 'coach_filtered') return `filtered: ${(evt.reason||'').substring(0,60)}`;
  if (e === 'advisor_assess_end') return `${evt.action}: ${evt.stage_name||''} — ${(evt.reasoning||'').substring(0,50)}`;
  if (e === 'session_query_end') return `${evt.session||'?'} ${fmtTime(evt.elapsed_s)} turns=${evt.turns||'?'}`;
  return '';
}
function esc(s) { const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }

// ============================================================
// View rendering
// ============================================================
let activeView = localStorage.getItem('kodo-view') || 'actors';
let selectedActor = null;
let selectedStageIdx = null;
let currentFilter = 'all';
let currentSearch = '';

// ── Status bar ──
function renderStatusBar(state) {
  const dot = document.getElementById('sb-dot');
  const agent = document.getElementById('sb-agent');
  if (state.finished) { dot.className = 'dot finished'; agent.textContent = 'finished'; }
  else if (state.activeAgent) { dot.className = 'dot active'; agent.textContent = state.activeAgent; }
  else { dot.className = 'dot'; agent.textContent = 'idle'; }
  const cost = Object.values(state.agents).reduce((s, a) => s + a.cost_usd, 0) + state.orchestratorCost;
  document.getElementById('sb-cycle').textContent = `cycle ${state.cycle}/${state.maxCycles}`;
  document.getElementById('sb-stage').textContent = state.hasStages
    ? `stage ${state.completedStages}/${state.numStages}${state.stageLabel ? ': ' + state.stageLabel : ''}` : '';
  document.getElementById('sb-cost').textContent = fmtCost(cost);
  document.getElementById('sb-time').textContent = fmtTime(state.elapsed);
}

// ── Actors view ──
function renderActorsView(state) {
  // Run summary bar
  const cost = Object.values(state.agents).reduce((s, a) => s + a.cost_usd, 0) + state.orchestratorCost;
  const items = [
    {l:'Project', v:state.projectName||'-'}, {l:'Mode', v:state.runMode||'-'},
    {l:'Orchestrator', v:`${state.orchestrator}/${state.model}`},
    {l:'Cycles', v:`${state.cycle}/${state.maxCycles}`},
    {l:'Elapsed', v:fmtTime(state.elapsed)}, {l:'Cost', v:fmtCost(cost)},
  ];
  document.getElementById('run-summary').innerHTML = items.map(i =>
    `<div class="rs-item">${i.l}: <span class="rs-val">${i.v}</span></div>`).join('');
  document.getElementById('goal-bar').textContent = state.goal || '(no goal)';
  const fill = document.getElementById('progress-fill');
  if (state.hasStages && state.numStages > 0) fill.style.width = Math.round(state.completedStages/state.numStages*100) + '%';
  else if (state.maxCycles > 0) fill.style.width = Math.round(state.cycle/state.maxCycles*100) + '%';

  // Build actor list
  const actors = [];
  if (state.orchestrator) {
    actors.push({id:'_orch', name:'Orchestrator', type:'orch',
      stat:`${state.cycle} cycles`});
  }
  if (state.intake) {
    actors.push({id:'_intake', name:'Intake', type:'intake',
      stat:fmtTime(state.intake.elapsed)});
  }
  if (state.coachEnabled || state.coachEvents.length > 0) {
    const advCount = state.coachEvents.filter(e => e.type === 'advisory').length;
    actors.push({id:'_coach', name:'Coach', type:'coach',
      stat:`${advCount} advisories`});
  }
  for (const [name, info] of Object.entries(state.agents)) {
    actors.push({id:name, name, type:'agent',
      stat:`${info.calls} calls ${fmtTime(info.elapsed_s)}`});
  }

  const list = document.getElementById('actor-list');
  list.innerHTML = actors.map(a => {
    const sel = selectedActor === a.id ? ' selected' : '';
    return `<div class="list-row${sel}" data-id="${a.id}">
      <span class="row-name">${esc(a.name)}</span>
      <span class="actor-type ${a.type}">${a.type}</span>
    </div>`;
  }).join('');
  list.querySelectorAll('.list-row').forEach(row => {
    row.onclick = () => { selectedActor = row.dataset.id; renderActorsView(state); };
  });

  renderActorDetail(state);
}

function renderActorDetail(state) {
  const detail = document.getElementById('actor-detail');
  if (!selectedActor) { detail.innerHTML = '<div class="empty">Select an actor</div>'; return; }

  if (selectedActor === '_orch') return renderOrchDetail(state, detail);
  if (selectedActor === '_intake') return renderIntakeDetail(state, detail);
  if (selectedActor === '_coach') return renderCoachActorDetail(state, detail);
  return renderAgentDetail(state, detail, selectedActor);
}

function renderOrchDetail(state, el) {
  const dispatches = state.dispatches;
  // Group dispatches by stage
  const byStage = {};
  for (const d of dispatches) {
    const k = d.stageIdx != null ? d.stageIdx : -1;
    if (!byStage[k]) byStage[k] = [];
    byStage[k].push(d);
  }
  const stageKeys = Object.keys(byStage).map(Number).sort((a,b) => a-b);

  let html = `<div class="detail-header"><h3>Orchestrator</h3><span class="badge orch">${state.orchestrator}/${state.model}</span></div>`;
  html += `<div class="detail-stats">
    <div class="ds-item">Cycles: <span>${state.cycle}/${state.maxCycles}</span></div>
    <div class="ds-item">Dispatches: <span>${dispatches.length}</span></div>
    <div class="ds-item">Cost: <span>${fmtCost(state.orchestratorCost)}</span></div>
  </div>`;
  html += '<div class="dispatch-list">';
  for (const k of stageKeys) {
    const stg = state.stages.find(s => s.index === k);
    const label = stg ? `Stage ${k}: ${stg.name}` : k === -1 ? 'Pre-stage' : `Stage ${k}`;
    html += `<div class="ev-separator">${esc(label)}</div>`;
    for (const d of byStage[k]) {
      html += renderDispatchItem(d, state, true);
    }
  }
  html += '</div>';
  el.innerHTML = html;
  bindDispatchToggles(el);
}

function renderIntakeDetail(state, el) {
  const i = state.intake;
  if (!i) { el.innerHTML = '<div class="empty">No intake session</div>'; return; }
  el.innerHTML = `
    <div class="detail-header"><h3>Intake</h3><span class="badge intake">${esc(fmtBackend(i.backend))} / ${esc(i.model)}</span></div>
    <div class="detail-stats">
      <div class="ds-item">Duration: <span>${fmtTime(i.elapsed)}</span></div>
      <div class="ds-item">Turns: <span>${i.turns || '-'}</span></div>
      <div class="ds-item">Cost: <span>${fmtCost(i.cost)}</span></div>
    </div>
    <div class="stage-section"><label>Response</label>
      <div class="section-text">${esc(i.response || '(no response captured)')}</div>
    </div>`;
}

function renderCoachActorDetail(state, el) {
  const events = state.coachEvents;
  const advisories = events.filter(e => e.type === 'advisory').length;
  const filtered = events.filter(e => e.type === 'filtered').length;
  const decisions = events.filter(e => e.type === 'decision').length;

  let html = `<div class="detail-header"><h3>Coach</h3><span class="badge coach">advisor</span></div>`;
  html += `<div class="detail-stats">
    <div class="ds-item">Advisories: <span>${advisories}</span></div>
    <div class="ds-item">Filtered: <span>${filtered}</span></div>
    <div class="ds-item">Stage decisions: <span>${decisions}</span></div>
  </div>`;
  html += '<div class="coach-feed">';
  html += renderCoachFeedItems(events);
  html += '</div>';
  el.innerHTML = html;
}

function renderAgentDetail(state, el, name) {
  const info = state.agents[name];
  if (!info) { el.innerHTML = '<div class="empty">Agent not found</div>'; return; }
  const desc = state.teamDescriptions[name] || '';
  const isSubscription = (info.cost_bucket || '').includes('subscription');
  const dispatches = state.dispatches.filter(d => d.agent === name);

  let html = `<div class="detail-header"><h3>${esc(name)}</h3><span class="badge agent">${esc(fmtBackend(info.backend))} / ${esc(info.model||'?')}</span></div>`;
  if (desc) html += `<div class="detail-desc">${esc(desc.split('\n')[0])}</div>`;
  html += '<div class="detail-stats">';
  html += `<div class="ds-item">Calls: <span>${info.calls}</span></div>`;
  html += `<div class="ds-item">Time: <span>${fmtTime(info.elapsed_s)}</span></div>`;
  html += `<div class="ds-item">Errors: <span>${info.errors}</span></div>`;
  if (!isSubscription) {
    html += `<div class="ds-item">In: <span>${fmtTokens(info.input_tokens)}</span></div>`;
    html += `<div class="ds-item">Out: <span>${fmtTokens(info.output_tokens)}</span></div>`;
    html += `<div class="ds-item">Cost: <span>${fmtCost(info.cost_usd)}</span></div>`;
  } else {
    html += `<div class="ds-item">Bucket: <span>${info.cost_bucket}</span></div>`;
  }
  html += '</div>';

  html += '<div class="dispatch-list">';
  if (!dispatches.length) html += '<div class="empty">No dispatches yet</div>';
  for (const d of dispatches) {
    html += renderDispatchItem(d, state, false);
  }
  html += '</div>';
  el.innerHTML = html;
  bindDispatchToggles(el);
}

function renderDispatchItem(d, state, showAgent) {
  const cls = d.error ? 'dispatch-item error' : 'dispatch-item';
  const stg = d.stageIdx != null ? state.stages.find(s => s.index === d.stageIdx) : null;
  const stageLabel = stg ? `S${d.stageIdx}` : '';
  const agentLabel = showAgent ? `<span class="hl">${esc(d.agent)}</span> ` : '';
  const stats = `${fmtTime(d.elapsed)}${d.cost ? ' ' + fmtCost(d.cost) : ''}${d.error ? ' <span class="d-err">ERROR</span>' : ''}`;
  const prompt = esc(d.prompt || d.task || '(no prompt)');
  const report = d.report ? esc(d.report) : '';

  let html = `<div class="${cls}">`;
  html += `<div class="dispatch-head">
    <span class="d-time">${fmtTime(d.startT)}</span>
    ${stageLabel ? `<span class="d-stage">${stageLabel}</span>` : ''}
    ${agentLabel}
    <span class="d-stats">${stats}</span>
  </div>`;
  html += `<div class="dispatch-prompt">${prompt}</div>`;
  if (report) html += `<div class="dispatch-report">${report}</div>`;
  html += '</div>';
  return html;
}

function bindDispatchToggles(el) {
  el.querySelectorAll('.dispatch-prompt').forEach(p => {
    p.onclick = () => p.classList.toggle('expanded');
  });
  el.querySelectorAll('.dispatch-report').forEach(r => {
    r.onclick = () => r.classList.toggle('expanded');
  });
}

// ── Stages view ──
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
    const dur = (s.startT != null && s.endT != null) ? fmtTime(s.endT - s.startT) : '';
    return `<div class="list-row${sel}" data-idx="${s.index}">
      <span class="row-icon s-icon ${s.status}">${icon}</span>
      <span class="row-name">${s.index}. ${esc(s.name)}</span>
      ${dur ? `<span class="row-stat">${dur}</span>` : ''}
    </div>`;
  }).join('');
  list.querySelectorAll('.list-row').forEach(row => {
    row.onclick = () => { selectedStageIdx = parseInt(row.dataset.idx); renderStagesView(state); };
  });
  renderStageDetail(state);
}

function renderStageDetail(state) {
  const detail = document.getElementById('stage-detail');
  if (selectedStageIdx == null) { detail.innerHTML = '<div class="empty">Select a stage</div>'; return; }
  const s = state.stages.find(x => x.index === selectedStageIdx);
  if (!s) { detail.innerHTML = '<div class="empty">Stage not found</div>'; return; }

  const editable = s.status === 'pending';
  const ro = editable ? '' : 'readonly';
  const dur = (s.startT != null && s.endT != null) ? fmtTime(s.endT - s.startT) : (s.startT != null ? 'in progress' : '');

  let html = `<div class="detail-header"><h3>${s.index}. ${esc(s.name)}</h3><span class="sd-badge ${s.status}">${s.status}</span></div>`;

  // Duration and agents
  if (dur || s.agents.length) {
    html += '<div class="detail-stats">';
    if (dur) html += `<div class="ds-item">Duration: <span>${dur}</span></div>`;
    html += '</div>';
  }
  if (s.agents.length) {
    html += `<div class="stage-section"><label>Agents</label><div class="stage-agents">${s.agents.map(a => `<span>${esc(a)}</span>`).join('')}</div></div>`;
  }

  // Summary from stage_end
  if (s.summary) {
    html += `<div class="stage-section"><label>Summary</label><div class="section-text">${esc(s.summary)}</div></div>`;
  }

  // Coach reasoning
  if (s.coachReasoning) {
    html += `<div class="stage-section"><label>Coach Reasoning</label><div class="section-text">${esc(s.coachReasoning)}</div></div>`;
  }

  // Editable fields for pending stages
  if (editable || s.description || s.acceptance_criteria) {
    html += `<div class="stage-section"><label>Description</label>
      <textarea id="sd-desc" ${ro}>${esc(s.description||'')}</textarea></div>`;
    html += `<div class="stage-section"><label>Acceptance Criteria</label>
      <textarea id="sd-accept" ${ro}>${esc(s.acceptance_criteria||'')}</textarea></div>`;
    html += `<div class="sd-actions">
      <button id="sd-save" ${editable ? '' : 'disabled'}>Save</button>
      <span class="sd-note">${editable ? 'Changes saved to goal-plan.json. Takes effect on resume.' : ''}</span>
    </div>`;
  }

  detail.innerHTML = html;

  if (editable) {
    document.getElementById('sd-save').onclick = async () => {
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
      setTimeout(() => { if (document.getElementById('sd-save')) document.getElementById('sd-save').textContent = 'Save'; }, 1500);
    };
  }
}

// ── Timeline view ──
function renderTimelineView(state) {
  const list = document.getElementById('event-list');
  const skipTypes = new Set(['run_init','session_reset','agent_session_reset','session_query_start','orchestrator_tool_result']);
  let events = state.events.filter(e => !skipTypes.has(e.event));
  if (currentFilter !== 'all') events = events.filter(e => evCat(e.event) === currentFilter);
  if (currentSearch) { const q = currentSearch.toLowerCase(); events = events.filter(e => JSON.stringify(e).toLowerCase().includes(q)); }
  const visible = events.slice(-300);

  let html = '';
  let lastStage = null;
  for (let i = 0; i < visible.length; i++) {
    const evt = visible[i];
    // Insert stage separators
    if (evt.event === 'stage_start' && evt.stage_index !== lastStage) {
      lastStage = evt.stage_index;
      html += `<div class="ev-separator">Stage ${evt.stage_index}: ${esc(evt.stage_name||'')}</div>`;
    }
    const cat = evCat(evt.event), t = evt.t != null ? fmtTime(evt.t) : '';
    const offset = events.length - visible.length;
    const id = offset + i;
    html += `<div><div class="ev-row" data-id="${id}"><span class="ev-t">${t}</span><span class="ev-type ${cat}">${evt.event}</span><span class="ev-detail">${evDetail(evt)}</span></div>
      <div class="ev-expanded" id="evx-${id}">${esc(JSON.stringify(evt, null, 2))}</div></div>`;
  }
  list.innerHTML = html;
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

// ── Coach view ──
function renderCoachView(state) {
  const events = state.coachEvents;
  const advisories = events.filter(e => e.type === 'advisory').length;
  const filtered = events.filter(e => e.type === 'filtered').length;
  const decisions = events.filter(e => e.type === 'decision').length;
  const checks = events.filter(e => e.type === 'assess_ok').length;

  document.getElementById('coach-stats').innerHTML = events.length
    ? `Advisories: <span>${advisories}</span> &nbsp; Filtered: <span>${filtered}</span> &nbsp; Stage decisions: <span>${decisions}</span> &nbsp; Checks: <span>${checks}</span>`
    : '<span style="color:var(--fg-faint)">No coach activity in this run</span>';

  document.getElementById('coach-feed').innerHTML = renderCoachFeedItems(events) || '<div style="color:var(--fg-muted);padding:20px;text-align:center">No events</div>';
}

function renderCoachFeedItems(events) {
  return events.filter(e => e.type !== 'assess_ok').map(e => {
    if (e.type === 'advisory') {
      return `<div class="coach-item advisory"><div class="ci-head"><span class="ci-time">${fmtTime(e.t)}</span><span class="ci-type advisory">advisory</span><span>${e.priority||''}</span></div><div class="ci-body">${esc(e.message)}</div></div>`;
    }
    if (e.type === 'filtered') {
      return `<div class="coach-item filtered"><div class="ci-head"><span class="ci-time">${fmtTime(e.t)}</span><span class="ci-type filtered">filtered</span></div><div class="ci-body">${esc(e.reason)}</div></div>`;
    }
    if (e.type === 'decision') {
      return `<div class="coach-item decision"><div class="ci-head"><span class="ci-time">${fmtTime(e.t)}</span><span class="ci-type decision">${esc(e.action||'decision')}</span>${e.stageName ? ` <span class="hl">${esc(e.stageName)}</span>` : ''}</div><div class="ci-body">${esc(e.reasoning)}</div></div>`;
    }
    if (e.type === 'done') {
      return `<div class="coach-item decision"><div class="ci-head"><span class="ci-time">${fmtTime(e.t)}</span><span class="ci-type decision">done</span></div><div class="ci-body">${esc(e.summary)}</div></div>`;
    }
    if (e.type === 'started' || e.type === 'stopped') {
      return `<div class="coach-item"><div class="ci-head"><span class="ci-time">${fmtTime(e.t)}</span><span class="ci-type">${e.type}</span></div></div>`;
    }
    return '';
  }).join('');
}

// ── Files view ──
const FILE_ORDER = ['goal.md','goal-refined.md','goal-plan.json','test-recon.md','test-report.md','team.json','config.json'];
function fileSort(a, b) {
  const ia = FILE_ORDER.indexOf(a), ib = FILE_ORDER.indexOf(b);
  if (ia >= 0 && ib >= 0) return ia - ib;
  if (ia >= 0) return -1;
  if (ib >= 0) return 1;
  return a.localeCompare(b);
}

function renderFilesView(state) {
  const tabs = document.getElementById('file-tabs');
  const content = document.getElementById('file-content');
  const files = state.files || {};
  const names = Object.keys(files).sort(fileSort);
  if (!names.length) { tabs.innerHTML = ''; content.textContent = 'No files found'; return; }
  const act = tabs.dataset.active && names.includes(tabs.dataset.active) ? tabs.dataset.active : names[0];
  tabs.innerHTML = names.map(n => `<button class="${n===act?'active':''}" data-tab="${n}">${n}</button>`).join('');
  const fc = files[act] || '';
  try { content.textContent = act.endsWith('.json') ? JSON.stringify(JSON.parse(fc),null,2) : fc; }
  catch { content.textContent = fc; }
  tabs.querySelectorAll('button').forEach(btn => {
    btn.onclick = () => { tabs.dataset.active = btn.dataset.tab; renderFilesView(state); };
  });
}

// ── Render dispatcher ──
function render(state) {
  renderStatusBar(state);
  const v = activeView;
  if (v === 'actors') renderActorsView(state);
  else if (v === 'stages') renderStagesView(state);
  else if (v === 'timeline') renderTimelineView(state);
  else if (v === 'coach') renderCoachView(state);
  else if (v === 'files') renderFilesView(state);
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
  state.files = runData.files || runData.config || {};

  // Parse team descriptions from team.json
  if (state.files['team.json']) {
    try {
      const td = JSON.parse(state.files['team.json']);
      for (const [name, info] of Object.entries(td.agents || {})) {
        state.teamDescriptions[name] = info.description || '';
      }
    } catch {}
  }

  // Parse stages from goal-plan.json
  if (state.files['goal-plan.json']) {
    try {
      const plan = JSON.parse(state.files['goal-plan.json']);
      const stages = plan.stages || plan;
      if (Array.isArray(stages)) {
        state.stages = stages.map(s => ({
          index:s.index, name:s.name, description:s.description||'',
          acceptance_criteria:s.acceptance_criteria||'', status:'pending',
          startT:null, endT:null, summary:'', agents:[], coachReasoning:''
        }));
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
  state.files = JSON.parse(JSON.stringify(window._loadedFiles || {}));
  reloadStages();
  reloadTeamDescriptions();
  for (let i = 0; i < target && i < playback.allEvents.length; i++) processEvent(state, playback.allEvents[i]);
  playback.index = target;
  render(state); updatePbUI();
}

function reloadStages() {
  if (state.files['goal-plan.json']) {
    try {
      const plan = JSON.parse(state.files['goal-plan.json']);
      const stages = plan.stages || plan;
      if (Array.isArray(stages)) {
        state.stages = stages.map(s => ({
          index:s.index, name:s.name, description:s.description||'',
          acceptance_criteria:s.acceptance_criteria||'', status:'pending',
          startT:null, endT:null, summary:'', agents:[], coachReasoning:''
        }));
        state.numStages = state.stages.length; state.hasStages = state.stages.length > 0;
      }
    } catch {}
  }
}

function reloadTeamDescriptions() {
  if (state.files['team.json']) {
    try {
      const td = JSON.parse(state.files['team.json']);
      for (const [name, info] of Object.entries(td.agents || {})) {
        state.teamDescriptions[name] = info.description || '';
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

// Store loaded files for seek reset
const _origLoadRun = loadRun;
window._loadedFiles = {};
loadRun = async function(runId) {
  await _origLoadRun(runId);
  window._loadedFiles = JSON.parse(JSON.stringify(state.files));
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

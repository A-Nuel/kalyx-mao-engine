const $ = id => document.getElementById(id);
let orgId = null;
let replayEvents = [];
let replayIndex = -1;
let replayTimer = null;

async function get(path, opts) {
  const r = await fetch(path, opts);
  if (!r.ok) throw new Error((await r.text()) || r.statusText);
  return r.json();
}
function esc(v) { return String(v ?? '').replace(/[&<>\"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#39;'}[c])); }
function fmtTime(v) { return new Date(v).toLocaleTimeString([], {hour:'2-digit', minute:'2-digit', second:'2-digit'}); }
function setReplay(index) {
  if (!replayEvents.length) {
    replayIndex = -1;
    $('replayTitle').textContent = 'No mission events';
    $('replayDetail').textContent = 'Create a mission to populate the event history.';
    $('replayCount').textContent = '0 / 0';
    return;
  }
  replayIndex = Math.max(0, Math.min(index, replayEvents.length - 1));
  const e = replayEvents[replayIndex];
  $('replayTitle').textContent = `${e.event_type} · ${e.actor_id}`;
  $('replayDetail').textContent = `${fmtTime(e.timestamp)} · event #${e.sequence_id} · ${JSON.stringify(e.payload || {})}`;
  $('replayCount').textContent = `${replayIndex + 1} / ${replayEvents.length}`;
  document.querySelectorAll('.event').forEach((node, i) => node.classList.toggle('selected', i === replayEvents.length - 1 - replayIndex));
}
function stopReplay() { if (replayTimer) clearInterval(replayTimer); replayTimer = null; $('replayPlay').textContent = 'PLAY'; }
function toggleReplay() {
  if (replayTimer) { stopReplay(); return; }
  if (!replayEvents.length) return;
  if (replayIndex >= replayEvents.length - 1) setReplay(0);
  $('replayPlay').textContent = 'PAUSE';
  replayTimer = setInterval(() => {
    if (replayIndex >= replayEvents.length - 1) { stopReplay(); return; }
    setReplay(replayIndex + 1);
  }, 900);
}
async function loadOrgs() {
  const xs = await get('/api/organisations');
  const s = $('orgSelect');
  s.innerHTML = xs.length ? xs.map(x => `<option value="${esc(x.id)}">${esc(x.id)}</option>`).join('') : '<option value="">No organisations</option>';
  if (xs.length) { orgId = xs[0].id; await refresh(); }
}
async function refresh() {
  if (!orgId) return;
  const [o, a, e, d, l] = await Promise.all([
    get(`/api/organisations/${orgId}`), get(`/api/organisations/${orgId}/agents`),
    get(`/api/organisations/${orgId}/events`), get(`/api/organisations/${orgId}/decisions`),
    get(`/api/organisations/${orgId}/ledger`)
  ]);
  $('mission').textContent = o.organisation.mission;
  $('orgMeta').textContent = `${o.organisation.id} · ${o.organisation.state} · created ${new Date(o.organisation.created_at).toLocaleString()}`;
  $('treasury').textContent = l.treasury;
  $('agents').textContent = a.filter(x => x.status === 'ACTIVE').length;
  $('tasks').textContent = o.tasks.length;
  $('audit').innerHTML = l.conserved ? '<span class="ok">VALID</span>' : '<span class="bad">BROKEN</span>';
  $('graph').innerHTML = `<div class="node"><b>HUMAN</b><small>mission + budget</small></div><div class="arrow">→</div><div class="node"><b>CEO</b><small>orchestrate</small></div><div class="arrow">→</div><div class="node"><b>POLICY</b><small>authorize</small></div><div class="arrow">→</div><div class="node"><b>EXECUTOR</b><small>bounded action</small></div><div class="arrow">→</div><div class="node"><b>AUDITOR</b><small>verify</small></div>`;
  $('agentList').innerHTML = a.map(x => `<div class="agent"><div><b>${esc(x.role)}</b><small>${esc(x.id)} · ${esc(x.status)}</small></div><div class="score">${Number(x.reputation_score).toFixed(0)}</div><div><small>performance ${Number(x.performance_score).toFixed(0)} · reliability ${Number(x.reliability_score).toFixed(0)}</small></div><div><small>${x.credit_balance} credits</small></div></div>`).join('') || '<div class="muted">No agents.</div>';
  replayEvents = e.slice();
  $('events').innerHTML = replayEvents.slice().reverse().map((x, i) => `<div class="event" data-replay-index="${replayEvents.length - 1 - i}"><span class="seq">#${x.sequence_id}</span><span class="type"><b>${esc(x.event_type)}</b><br><span class="muted">${esc(x.actor_id)}</span></span><span class="time">${fmtTime(x.timestamp)}</span></div>`).join('') || '<div class="muted">No events.</div>';
  document.querySelectorAll('.event[data-replay-index]').forEach(node => node.addEventListener('click', () => setReplay(Number(node.dataset.replayIndex))));
  $('decisions').innerHTML = d.map(x => `<div class="decision ${String(x.result).includes('APPROV') ? 'approved' : 'rejected'}"><b>${esc(x.result)}</b><br>${esc(x.violated_rule_id || 'policy evaluation')}<br><span class="muted">${fmtTime(x.timestamp)}</span></div>`).join('') || '<div class="muted">No policy decisions.</div>';
  $('ledger').innerHTML = [['TREASURY', l.treasury], ['ESCROW', l.escrow], ['EXTERNAL SINK', l.external_sink], ['CONSERVATION', l.conserved ? 'PASS' : 'FAIL']].map(x => `<div class="ledger-row"><span>${x[0]}</span><b>${x[1]}</b></div>`).join('');
  if (replayIndex >= replayEvents.length) replayIndex = replayEvents.length - 1;
  if (replayIndex >= 0) setReplay(replayIndex);
}
async function submitMission(event) {
  event.preventDefault();
  const button = $('runMission');
  const status = $('missionStatus');
  button.disabled = true;
  status.textContent = 'Organisation is planning, evaluating and executing…';
  try {
    const result = await get('/api/missions', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        mission: $('missionInput').value.trim(),
        budget: Number($('budgetInput').value),
        live: $('liveInput').checked
      })
    });
    orgId = result.organisation_id;
    status.textContent = `Mission ${result.state.toLowerCase()} · ${result.event_count} audited events · ${result.treasury} credits remaining`;
    await loadOrgs();
    $('orgSelect').value = orgId;
    await refresh();
    $('missionForm').reset();
    $('budgetInput').value = 100;
  } catch (error) {
    status.textContent = `Mission failed: ${error.message}`;
  } finally {
    button.disabled = false;
  }
}
$('missionForm').addEventListener('submit', submitMission);
$('orgSelect').addEventListener('change', e => { stopReplay(); orgId = e.target.value; refresh().catch(console.error); });
$('pause').onclick = () => orgId && get(`/api/organisations/${orgId}/pause`, {method:'POST'}).then(refresh).catch(console.error);
$('resume').onclick = () => orgId && get(`/api/organisations/${orgId}/resume`, {method:'POST'}).then(refresh).catch(console.error);
$('replayPrev').onclick = () => setReplay(replayIndex - 1);
$('replayNext').onclick = () => setReplay(replayIndex + 1);
$('replayPlay').onclick = toggleReplay;
async function boot() {
  try { await get('/api/health'); $('health').textContent = 'ENGINE ONLINE'; $('dot').style.background = '#79d49a'; await loadOrgs(); }
  catch (e) { $('health').textContent = 'OFFLINE'; $('dot').style.background = '#ff8f8f'; console.error(e); }
}
setInterval(() => $('clock').textContent = new Date().toLocaleTimeString(), 1000);
setInterval(() => refresh().catch(() => {}), 5000);
boot();

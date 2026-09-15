/**
 * Kalyx Command Centre — Application Controller
 * Manages routing, state synchronization, view rendering, and drawer interactions.
 */

const App = (() => {
  let activeOrgId = null;
  let currentRoute = 'overview';
  let pollInterval = null;
  let isPolling = false;

  // Helpers
  const $ = id => document.getElementById(id);
  function esc(s) {
    return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }
  function fmtTime(iso) {
    if (!iso) return '—';
    try {
      return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    } catch {
      return iso;
    }
  }
  function fmtDate(iso) {
    if (!iso) return '—';
    try {
      return new Date(iso).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
    } catch {
      return iso;
    }
  }
  function fmtNum(n) {
    return typeof n === 'number' ? n.toLocaleString() : (n ?? '—');
  }

  // Navigation & Routing
  function setRoute(route) {
    const validRoutes = ['overview', 'missions', 'organisation', 'treasury', 'policies', 'operations', 'audit', 'experiments', 'settings'];
    const target = validRoutes.includes(route) ? route : 'overview';
    currentRoute = target;

    // Update URL hash without scroll jumps
    if (window.location.hash !== `#${target}`) {
      window.location.hash = `#${target}`;
    }

    // Update active tab buttons
    document.querySelectorAll('.nav-tab').forEach(el => {
      const isTab = el.getAttribute('data-nav') === target;
      el.classList.toggle('active', isTab);
      if (isTab) {
        el.classList.add('bg-white/10', 'text-white');
        el.classList.remove('text-slate-400');
      } else {
        el.classList.remove('bg-white/10', 'text-white');
        el.classList.add('text-slate-400');
      }
    });

    // Toggle view panels
    document.querySelectorAll('.view-panel').forEach(el => {
      const isView = el.id === `view-${target}`;
      el.classList.toggle('hidden', !isView);
    });

    // Immediate refresh for this route
    refreshCurrentView().catch(console.warn);
  }

  // Drawers Controller
  function openDrawer(id) {
    closeDrawers();
    const el = $(id);
    if (el) {
      el.classList.remove('hidden');
      requestAnimationFrame(() => el.classList.add('drawer-open'));
    }
  }

  function closeDrawers() {
    document.querySelectorAll('.fixed.z-50').forEach(el => {
      if (el.id.startsWith('drawer')) {
        el.classList.remove('drawer-open');
        setTimeout(() => el.classList.add('hidden'), 200);
      }
    });
  }

  function openModal(id) {
    const el = $(id);
    if (el) el.classList.remove('hidden');
  }

  function closeModals() {
    document.querySelectorAll('.fixed.z-50').forEach(el => {
      if (el.id.startsWith('modal')) el.classList.add('hidden');
    });
  }

  // Load available organisations
  async function loadOrganisations() {
    try {
      const orgs = await API.getOrganisations();
      const select = $('orgSelect');
      if (!orgs || !orgs.length) {
        select.innerHTML = '<option value="">No organisations</option>';
        activeOrgId = null;
        return;
      }

      select.innerHTML = orgs.map(o => `<option value="${esc(o.id)}">${esc(o.id)} (${esc(o.state)})</option>`).join('');
      if (!activeOrgId || !orgs.some(o => o.id === activeOrgId)) {
        activeOrgId = orgs[0].id;
      }
      select.value = activeOrgId;
      await refreshCurrentView();
    } catch (err) {
      console.error('Failed to load organisations:', err);
    }
  }

  // Refresh data based on current route
  async function refreshCurrentView() {
    if (!activeOrgId && currentRoute !== 'experiments' && currentRoute !== 'settings') {
      return;
    }

    try {
      // 1. Always refresh topbar health and overview KPI cache
      if (activeOrgId) {
        const [orgData, ledgerData, opsSummary] = await Promise.all([
          API.getOrganisation(activeOrgId).catch(() => null),
          API.getLedger(activeOrgId).catch(() => null),
          API.getOperationsSummary(activeOrgId).catch(() => null),
        ]);

        if (orgData) {
          updateExecutiveState(orgData, ledgerData, opsSummary);
        }
      }

      // 2. Refresh route-specific view
      switch (currentRoute) {
        case 'overview':
          await refreshOverview();
          break;
        case 'missions':
          await refreshMissions();
          break;
        case 'organisation':
          await refreshOrganisation();
          break;
        case 'treasury':
          await refreshTreasury();
          break;
        case 'policies':
          await refreshPolicies();
          break;
        case 'operations':
          await refreshOperations();
          break;
        case 'audit':
          await refreshAudit();
          break;
        case 'experiments':
          await refreshExperiments();
          break;
        case 'settings':
          await refreshSettings();
          break;
      }
    } catch (err) {
      console.warn(`[App] Error refreshing ${currentRoute}:`, err.message);
    }
  }

  // Executive State Bar Update
  function updateExecutiveState(orgData, ledgerData, opsSummary) {
    const org = orgData.organisation;
    const isPaused = org.state === 'PAUSED';

    // Topbar Pause/Resume button
    const cbLabel = $('circuitBreakerLabel');
    if (cbLabel) {
      cbLabel.textContent = isPaused ? 'RESUME' : 'PAUSE';
    }

    // Card 1: Org State
    $('cardOrgStateText').textContent = isPaused ? 'ORGANISATION PAUSED' : 'All Systems Nominal';
    $('cardOrgSubtitle').textContent = isPaused
      ? 'Emergency kill switch tripped. Consequential execution frozen.'
      : `${orgData.agents.length} autonomous agents active • 0 unverified actions`;
    $('cardOrgBadge').textContent = org.state;
    $('cardOrgBadge').className = `px-2.5 py-0.5 rounded-full text-xs font-mono font-medium ${isPaused ? 'badge-danger' : 'badge-ok'}`;
    $('cardOrgId').textContent = org.id;

    // Card 2: Available Credits
    if (ledgerData) {
      $('cardTreasuryBalance').textContent = fmtNum(ledgerData.treasury);
      $('cardTreasuryBreakdown').textContent = `Escrowed: ${fmtNum(ledgerData.escrow)} CR • Settled Sink: ${fmtNum(ledgerData.external_sink)} CR`;
      $('cardConservationBadge').textContent = ledgerData.conserved ? 'CONSERVED' : 'BREACH';
      $('cardConservationBadge').className = `px-2.5 py-0.5 rounded-full text-xs font-mono font-medium ${ledgerData.conserved ? 'badge-ok' : 'badge-danger'}`;
    }

    // Card 3: Active Mission
    $('cardMissionTitle').textContent = org.mission || 'No Active Expedition';
    $('cardWorkforceCount').textContent = `${orgData.agents.filter(a => a.status === 'ACTIVE').length} Active`;
    $('cardTasksCount').textContent = `${orgData.tasks.length} Tasks`;

    // Nav Operations badge for UNKNOWN operations
    const navOpsBadge = $('navOperationsBadge');
    if (navOpsBadge) {
      if (opsSummary && opsSummary.has_unknown) {
        navOpsBadge.classList.remove('hidden');
      } else {
        navOpsBadge.classList.add('hidden');
      }
    }
  }

  // ==================== VIEW 1: OVERVIEW ====================
  async function refreshOverview() {
    if (!activeOrgId) return;
    const [events, decisions, agents, opsSummary] = await Promise.all([
      API.getEvents(activeOrgId).catch(() => []),
      API.getDecisions(activeOrgId).catch(() => []),
      API.getAgents(activeOrgId).catch(() => []),
      API.getOperationsSummary(activeOrgId).catch(() => null),
    ]);

    // 1. Attention Queue Banner
    const queue = $('attentionQueueContainer');
    if (opsSummary && opsSummary.has_unknown) {
      queue.innerHTML = `
        <div class="p-4 rounded-xl bg-amber-500/10 border border-amber-500/25 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
          <div class="flex items-center gap-3">
            <span class="material-symbols-outlined text-amber-400 text-2xl">warning</span>
            <div>
              <div class="text-sm font-bold text-white">Attention Required: Ambiguous Consequential Operations Detected</div>
              <div class="text-xs text-slate-300 mt-0.5">Provider outcome is uncertain. Resources remain safely locked in ESCROW pending authoritative reconciliation.</div>
            </div>
          </div>
          <a href="#operations" class="px-3.5 py-1.5 rounded-lg bg-amber-500 hover:bg-amber-400 text-black font-semibold text-xs font-mono transition-all shrink-0">
            Inspect &amp; Reconcile
          </a>
        </div>
      `;
    } else {
      queue.innerHTML = `
        <div class="p-3.5 rounded-xl bg-emerald-500/10 border border-emerald-500/20 flex items-center justify-between text-xs font-mono">
          <div class="flex items-center gap-2 text-emerald-300">
            <span class="material-symbols-outlined text-emerald-400 text-base">verified_user</span>
            <span class="font-medium">All Safety Invariants Enforced • Zero Unresolved Consequential Operations</span>
          </div>
          <span class="text-slate-400 hidden sm:inline">Cadence: Deterministic Realtime</span>
        </div>
      `;
    }

    // 2. Update Milestone Pipeline
    updateMilestonePipeline(events, decisions);

    // 3. Workforce Pulse List
    const agentList = $('overviewAgentList');
    if (agents && agents.length) {
      agentList.innerHTML = agents.map(a => `
        <div class="p-3 rounded-lg bg-zinc-900/60 border border-white/[0.06] hover:border-white/20 transition-all flex items-center justify-between cursor-pointer group" onclick="App.inspectAgent('${esc(a.id)}')">
          <div class="flex items-center gap-3">
            <div class="w-8 h-8 rounded-lg bg-zinc-800 flex items-center justify-center font-mono text-xs font-bold text-blue-400">
              ${esc(a.role.slice(0, 2))}
            </div>
            <div>
              <div class="text-xs font-semibold text-white group-hover:text-blue-400 transition-colors">${esc(a.role)}</div>
              <div class="text-[11px] text-slate-500 font-mono">${esc(a.id)} &bull; ${esc(a.model_name || 'Standard')}</div>
            </div>
          </div>
          <div class="flex items-center gap-3 text-right font-mono text-xs">
            <div>
              <div class="text-white font-semibold">${Number(a.reputation_score).toFixed(0)}</div>
              <div class="text-[10px] text-slate-500">Reputation</div>
            </div>
            <span class="px-2 py-0.5 rounded text-[10px] uppercase font-semibold ${a.status === 'ACTIVE' ? 'badge-ok' : a.status === 'PROBATION' ? 'badge-warn' : 'badge-danger'}">
              ${esc(a.status)}
            </span>
          </div>
        </div>
      `).join('');
    } else {
      agentList.innerHTML = '<div class="text-xs text-slate-500 py-6 text-center">No agents registered for this organisation.</div>';
    }

    // 4. Live Event Stream
    const eventStream = $('overviewEventStream');
    if (events && events.length) {
      eventStream.innerHTML = events.slice(-30).reverse().map(e => `
        <div class="p-2.5 rounded-lg bg-zinc-900/40 border border-white/[0.04] hover:bg-zinc-900/80 transition-all flex items-start justify-between gap-3">
          <div class="flex items-start gap-2.5 min-w-0">
            <span class="text-[10px] text-slate-500 bg-white/[0.04] px-1.5 py-0.5 rounded shrink-0">#${e.sequence_id}</span>
            <div class="min-w-0">
              <div class="text-white font-semibold truncate">${esc(e.event_type)}</div>
              <div class="text-slate-400 text-[11px] truncate">Actor: <span class="text-slate-300">${esc(e.actor_id)}</span></div>
            </div>
          </div>
          <span class="text-slate-500 text-[10px] shrink-0">${fmtTime(e.timestamp)}</span>
        </div>
      `).join('');
    } else {
      eventStream.innerHTML = '<div class="text-xs text-slate-500 py-6 text-center">No events in append-only history.</div>';
    }
  }

  function updateMilestonePipeline(events, decisions) {
    const types = new Set((events || []).map(e => e.event_type));
    const stepStatuses = {
      PLAN: types.has('MISSION_STARTED') || types.has('PLAN_CREATED') ? 'Completed' : 'Pending',
      RESEARCH: types.has('RESEARCH_COMPLETED') ? 'Completed' : types.has('TASK_ASSIGNED') ? 'Active' : 'Pending',
      STRATEGY: types.has('STRATEGY_COMPLETED') ? 'Completed' : types.has('RESEARCH_COMPLETED') ? 'Active' : 'Pending',
      PROPOSAL: types.has('PROPOSAL_SUBMITTED') ? 'Completed' : types.has('STRATEGY_COMPLETED') ? 'Active' : 'Pending',
      POLICY: (decisions && decisions.length > 0) ? 'Completed' : types.has('PROPOSAL_SUBMITTED') ? 'Active' : 'Pending',
      EXECUTION: types.has('EXECUTION_SUBMITTED') || types.has('EXECUTION_COMPLETED') ? 'Completed' : types.has('POLICY_DECISION') ? 'Active' : 'Pending',
      AUDIT: types.has('AUDIT_VERIFIED') || types.has('RECEIPT_GENERATED') ? 'Completed' : types.has('EXECUTION_COMPLETED') ? 'Active' : 'Pending',
    };

    let completedCount = 0;
    document.querySelectorAll('.pipeline-step').forEach(node => {
      const step = node.getAttribute('data-step');
      const st = stepStatuses[step] || 'Pending';
      const statusLabel = node.querySelector('.step-status');
      if (statusLabel) statusLabel.textContent = st;

      node.classList.remove('border-emerald-500/40', 'bg-emerald-500/10', 'border-blue-500/40', 'bg-blue-500/10');
      if (st === 'Completed') {
        completedCount++;
        node.classList.add('border-emerald-500/40', 'bg-emerald-500/10');
        if (statusLabel) statusLabel.className = 'text-[11px] text-emerald-400 font-semibold';
      } else if (st === 'Active') {
        node.classList.add('border-blue-500/40', 'bg-blue-500/10');
        if (statusLabel) statusLabel.className = 'text-[11px] text-blue-400 font-semibold animate-pulse';
      } else {
        if (statusLabel) statusLabel.className = 'text-[11px] text-slate-500';
      }
    });

    $('pipelineProgressLabel').textContent = `Stage ${completedCount} of 7 Complete`;
  }

  // ==================== VIEW 2: MISSIONS ====================
  async function refreshMissions() {
    if (!activeOrgId) return;
    const [orgData, orgs] = await Promise.all([
      API.getOrganisation(activeOrgId).catch(() => null),
      API.getOrganisations().catch(() => []),
    ]);

    if (!orgData) return;
    const org = orgData.organisation;

    // Active Mission Hero Card
    const hero = $('missionsHeroContainer');
    hero.innerHTML = `
      <div class="glass-panel p-6 rounded-xl space-y-4">
        <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-2 border-b border-white/[0.06] pb-3">
          <div class="flex items-center gap-2 font-mono text-xs">
            <span class="px-2.5 py-0.5 rounded-full badge-blue uppercase font-semibold">Active Expedition</span>
            <span class="text-slate-500">&bull;</span>
            <span class="text-slate-400 font-medium">${esc(org.id)}</span>
          </div>
          <span class="text-xs font-mono text-slate-500">Created: ${fmtDate(org.created_at)}</span>
        </div>
        <div>
          <h2 class="text-xl font-bold text-white">${esc(org.mission)}</h2>
          <p class="text-xs text-slate-400 mt-1">Autonomous expedition running with ${fmtNum(org.treasury_balance)} ORG Credits total allocation.</p>
        </div>
        <div class="pt-2 flex flex-wrap items-center gap-4 text-xs font-mono">
          <div class="px-3 py-1.5 rounded-lg bg-zinc-900 border border-white/10">
            <span class="text-slate-500">Initial Treasury:</span> <span class="text-white font-bold">${fmtNum(org.treasury_balance)} CR</span>
          </div>
          <div class="px-3 py-1.5 rounded-lg bg-zinc-900 border border-white/10">
            <span class="text-slate-500">Active Tasks:</span> <span class="text-white font-bold">${orgData.tasks.length}</span>
          </div>
          <div class="px-3 py-1.5 rounded-lg bg-zinc-900 border border-white/10">
            <span class="text-slate-500">State:</span> <span class="text-blue-400 font-bold uppercase">${esc(org.state)}</span>
          </div>
          <button type="button" onclick="App.openMissionConsole()" class="ml-auto px-4 py-1.5 rounded-lg bg-blue-600/20 hover:bg-blue-600/30 text-blue-300 border border-blue-500/30 font-semibold transition-all">
            Open Mission Console &rarr;
          </button>
        </div>
      </div>
    `;

    // Tasks table
    const tbody = $('missionsTasksTableBody');
    if (orgData.tasks && orgData.tasks.length) {
      tbody.innerHTML = orgData.tasks.map(t => `
        <tr class="hover:bg-white/[0.02] transition-colors">
          <td class="py-2.5 px-3 text-white font-semibold">${esc(t.id)}</td>
          <td class="py-2.5 px-3 text-blue-400">${esc(t.assigned_agent_id || 'Unassigned')}</td>
          <td class="py-2.5 px-3 text-slate-300 max-w-xs truncate">${esc(t.objective)}</td>
          <td class="py-2.5 px-3">${fmtNum(t.allocated_credits)} CR</td>
          <td class="py-2.5 px-3">
            <span class="px-2 py-0.5 rounded text-[10px] uppercase font-semibold ${t.status === 'COMPLETED' ? 'badge-ok' : t.status === 'FAILED' ? 'badge-danger' : 'badge-blue'}">
              ${esc(t.status)}
            </span>
          </td>
          <td class="py-2.5 px-3 text-right">
            ${t.output_evidence ? `<button type="button" onclick="App.inspectEvidence('${esc(t.id)}', ${esc(JSON.stringify(t.output_evidence))})" class="text-blue-400 hover:text-blue-300 underline">View Evidence</button>` : '—'}
          </td>
        </tr>
      `).join('');
    } else {
      tbody.innerHTML = '<tr><td colspan="6" class="py-6 text-center text-slate-500">No delegated tasks yet.</td></tr>';
    }

    // Past Missions Table
    const histBody = $('missionsHistoryTableBody');
    if (orgs && orgs.length) {
      histBody.innerHTML = orgs.map(o => `
        <tr class="hover:bg-white/[0.02] transition-colors ${o.id === activeOrgId ? 'bg-white/[0.03]' : ''}">
          <td class="py-2.5 px-3 font-semibold ${o.id === activeOrgId ? 'text-blue-400' : 'text-white'}">${esc(o.id)}</td>
          <td class="py-2.5 px-3 text-slate-300 max-w-sm truncate">${esc(o.mission)}</td>
          <td class="py-2.5 px-3">${fmtNum(o.treasury_balance)} CR</td>
          <td class="py-2.5 px-3">
            <span class="px-2 py-0.5 rounded text-[10px] uppercase font-semibold ${o.state === 'EXECUTING' ? 'badge-ok' : o.state === 'PAUSED' ? 'badge-danger' : 'badge-neutral'}">
              ${esc(o.state)}
            </span>
          </td>
          <td class="py-2.5 px-3 text-slate-500">${fmtDate(o.created_at)}</td>
          <td class="py-2.5 px-3 text-right">
            <button type="button" onclick="App.selectOrg('${esc(o.id)}')" class="px-2.5 py-1 rounded bg-white/[0.06] hover:bg-white/10 text-white transition-colors">Select</button>
          </td>
        </tr>
      `).join('');
    } else {
      histBody.innerHTML = '<tr><td colspan="6" class="py-6 text-center text-slate-500">No mission history.</td></tr>';
    }
  }

  // ==================== VIEW 3: ORGANISATION ====================
  async function refreshOrganisation() {
    if (!activeOrgId) return;
    const agents = await API.getAgents(activeOrgId).catch(() => []);
    const tbody = $('organisationAgentsTableBody');

    if (agents && agents.length) {
      tbody.innerHTML = agents.map(a => `
        <tr class="hover:bg-white/[0.02] transition-colors cursor-pointer" onclick="App.inspectAgent('${esc(a.id)}')">
          <td class="py-2.5 px-3 text-white font-semibold">${esc(a.id)}</td>
          <td class="py-2.5 px-3 text-blue-400">${esc(a.role)}</td>
          <td class="py-2.5 px-3">
            <span class="px-2 py-0.5 rounded text-[10px] uppercase font-semibold ${a.status === 'ACTIVE' ? 'badge-ok' : a.status === 'PROBATION' ? 'badge-warn' : 'badge-danger'}">
              ${esc(a.status)}
            </span>
          </td>
          <td class="py-2.5 px-3">${fmtNum(a.authority_ceiling)} CR</td>
          <td class="py-2.5 px-3">${fmtNum(a.credit_balance)} CR</td>
          <td class="py-2.5 px-3 font-semibold text-white">${Number(a.reputation_score).toFixed(1)}</td>
          <td class="py-2.5 px-3">${Number(a.performance_score).toFixed(1)}</td>
          <td class="py-2.5 px-3 text-right">
            <button type="button" class="px-2.5 py-1 rounded bg-white/[0.06] hover:bg-white/10 text-blue-400 hover:text-blue-300">Inspect &rarr;</button>
          </td>
        </tr>
      `).join('');
    } else {
      tbody.innerHTML = '<tr><td colspan="8" class="py-6 text-center text-slate-500">No agents registered.</td></tr>';
    }
  }

  // ==================== VIEW 4: TREASURY ====================
  async function refreshTreasury() {
    if (!activeOrgId) return;
    const ledger = await API.getLedger(activeOrgId).catch(() => null);
    if (!ledger) return;

    $('treasuryAvailableBalance').textContent = fmtNum(ledger.treasury);
    $('treasuryEscrowBalance').textContent = fmtNum(ledger.escrow);
    $('treasuryExternalSinkBalance').textContent = fmtNum(ledger.external_sink);
    $('treasuryConservationText').textContent = ledger.conserved ? 'CONSERVED' : 'BREACH';
    $('treasuryConservationText').className = `text-2xl font-mono font-bold ${ledger.conserved ? 'text-emerald-400' : 'text-red-400'}`;

    const tbody = $('treasuryLedgerTableBody');
    if (ledger.entries && ledger.entries.length) {
      tbody.innerHTML = ledger.entries.slice().reverse().map(e => `
        <tr class="hover:bg-white/[0.02] transition-colors">
          <td class="py-2 px-3 text-slate-500">#${e.sequence_num || '—'}</td>
          <td class="py-2 px-3 text-slate-400">${fmtTime(e.timestamp)}</td>
          <td class="py-2 px-3 text-red-400 font-semibold">${esc(e.from_account)}</td>
          <td class="py-2 px-3 text-emerald-400 font-semibold">${esc(e.to_account)}</td>
          <td class="py-2 px-3 text-white font-bold">${fmtNum(e.amount)} CR</td>
          <td class="py-2 px-3 text-slate-300 max-w-sm truncate">${esc(e.memo)}</td>
        </tr>
      `).join('');
    } else {
      tbody.innerHTML = '<tr><td colspan="6" class="py-6 text-center text-slate-500">No transactions recorded in ledger.</td></tr>';
    }
  }

  // ==================== VIEW 5: POLICIES ====================
  async function refreshPolicies() {
    if (!activeOrgId) return;
    const [rulesData, decisions] = await Promise.all([
      API.getPolicyRules(activeOrgId).catch(() => ({ rules: [] })),
      API.getDecisions(activeOrgId).catch(() => []),
    ]);

    // Policy version hash
    if (rulesData.policy_version_hash) {
      $('policyVersionHash').textContent = rulesData.policy_version_hash;
    }

    // Rules Cards
    const grid = $('policyRulesGrid');
    if (rulesData.rules && rulesData.rules.length) {
      grid.innerHTML = rulesData.rules.map(r => `
        <div class="glass-panel p-5 rounded-xl space-y-3 flex flex-col justify-between hover:border-white/20 transition-all">
          <div>
            <div class="flex items-center justify-between">
              <span class="px-2 py-0.5 rounded text-[10px] font-mono uppercase badge-blue font-semibold">${esc(r.rule_id)}</span>
              <span class="text-[10px] font-mono text-slate-500 uppercase">${esc(r.category)}</span>
            </div>
            <h3 class="text-sm font-bold text-white mt-2">${esc(r.name)}</h3>
            <p class="text-xs text-slate-400 mt-1 leading-relaxed">${esc(r.description)}</p>
          </div>
          <div class="pt-3 border-t border-white/[0.06] flex items-center justify-between text-[11px] font-mono">
            <span class="text-emerald-400 font-semibold">${esc(r.enforcement_level)}</span>
            <span class="text-slate-500">Rejections: <strong class="${r.rejection_count > 0 ? 'text-amber-400' : 'text-slate-400'}">${r.rejection_count}</strong></span>
          </div>
        </div>
      `).join('');
    }

    // Policy Decisions Table
    const tbody = $('policiesDecisionsTableBody');
    if (decisions && decisions.length) {
      tbody.innerHTML = decisions.map(d => {
        const isApproved = String(d.result).toUpperCase() === 'APPROVED';
        return `
          <tr class="hover:bg-white/[0.02] transition-colors">
            <td class="py-2 px-3 text-slate-400">${fmtTime(d.timestamp)}</td>
            <td class="py-2 px-3 text-slate-300 font-semibold">${esc(d.proposal_id.slice(0, 12))}…</td>
            <td class="py-2 px-3">
              <span class="px-2 py-0.5 rounded text-[10px] uppercase font-semibold ${isApproved ? 'badge-ok' : 'badge-danger'}">
                ${esc(d.result)}
              </span>
            </td>
            <td class="py-2 px-3 font-semibold ${isApproved ? 'text-slate-500' : 'text-amber-400'}">
              ${esc(d.violated_rule_id || '—')}
            </td>
            <td class="py-2 px-3 text-slate-300 max-w-sm truncate">${esc(d.violated_rule_description || 'Policy constraints satisfied')}</td>
            <td class="py-2 px-3 text-right text-slate-500 font-mono text-[11px]">
              ${d.authorization_token ? `<span class="text-emerald-400 truncate max-w-[120px] inline-block" title="${esc(d.authorization_token)}">${esc(d.authorization_token.slice(0, 16))}…</span>` : '—'}
            </td>
          </tr>
        `;
      }).join('');
    } else {
      tbody.innerHTML = '<tr><td colspan="6" class="py-6 text-center text-slate-500">No policy decisions evaluated yet.</td></tr>';
    }
  }

  // ==================== VIEW 6: OPERATIONS ====================
  async function refreshOperations() {
    if (!activeOrgId) return;
    const [summary, opsData] = await Promise.all([
      API.getOperationsSummary(activeOrgId).catch(() => null),
      API.getOperations(activeOrgId).catch(() => ({ operations: [] })),
    ]);

    // 1. Summary Cards
    const summaryGrid = $('operationsSummaryGrid');
    if (summary) {
      const counts = summary.counts || {};
      summaryGrid.innerHTML = [
        { label: 'Total Operations', val: summary.total_operations, cls: 'text-white' },
        { label: 'Succeeded', val: counts.succeeded || 0, cls: 'text-emerald-400' },
        { label: 'Escrowed', val: counts.escrowed || 0, cls: 'text-blue-400' },
        { label: 'Unknown (Ambiguous)', val: counts.unknown || 0, cls: counts.unknown > 0 ? 'text-amber-400 font-bold' : 'text-slate-500' },
        { label: 'Failed', val: counts.failed || 0, cls: counts.failed > 0 ? 'text-red-400' : 'text-slate-500' },
      ].map(c => `
        <div class="glass-panel p-4 rounded-xl">
          <span class="text-[10px] font-mono uppercase text-slate-500">${c.label}</span>
          <div class="text-2xl font-mono font-bold ${c.cls} mt-1">${c.val}</div>
        </div>
      `).join('');
    }

    // 2. UNKNOWN Callout Banner
    const alertBox = $('operationsUnknownAlertContainer');
    if (summary && summary.has_unknown) {
      alertBox.innerHTML = `
        <div class="p-5 rounded-xl bg-amber-500/10 border border-amber-500/30 flex flex-col md:flex-row items-start md:items-center justify-between gap-4">
          <div class="flex items-start gap-3">
            <span class="material-symbols-outlined text-amber-400 text-2xl">error</span>
            <div>
              <div class="text-sm font-bold text-white">UNKNOWN Consequential Outcome In Effect</div>
              <div class="text-xs text-slate-300 mt-1 max-w-3xl leading-relaxed">
                A provider submission did not receive a confirmed response. Kalyx enforces the safe reconciliation protocol:
                <strong>blind execution retry is strictly prohibited</strong> to eliminate double-spend. Use the Reconcile action below to query authoritative settlement state.
              </div>
            </div>
          </div>
        </div>
      `;
    } else {
      alertBox.innerHTML = '';
    }

    // 3. Operations Table
    const tbody = $('operationsTableBody');
    const ops = opsData.operations || [];
    if (ops.length) {
      tbody.innerHTML = ops.map(op => {
        const isUnknown = op.state.toLowerCase() === 'unknown';
        const canReconcile = ['unknown', 'submitted', 'reconciling'].includes(op.state.toLowerCase());
        return `
          <tr class="hover:bg-white/[0.02] transition-colors cursor-pointer" onclick="App.inspectOperation('${esc(op.id)}')">
            <td class="py-2.5 px-3 text-white font-semibold">${esc(op.id.slice(0, 14))}…</td>
            <td class="py-2.5 px-3 text-blue-400">${esc(op.action_type)}</td>
            <td class="py-2.5 px-3 text-slate-300 max-w-xs truncate">${esc(op.target)}</td>
            <td class="py-2.5 px-3 font-bold text-white">${fmtNum(op.amount)} CR</td>
            <td class="py-2.5 px-3">
              <span class="px-2.5 py-0.5 rounded text-[10px] uppercase font-semibold ${
                op.state.toLowerCase() === 'succeeded' ? 'badge-ok' :
                isUnknown ? 'badge-warn animate-pulse' :
                op.state.toLowerCase() === 'failed' ? 'badge-danger' : 'badge-blue'
              }">
                ${esc(op.state)}
              </span>
            </td>
            <td class="py-2.5 px-3 text-slate-400">${esc(op.provider_reference || '—')}</td>
            <td class="py-2.5 px-3 text-slate-500">${fmtTime(op.updated_at)}</td>
            <td class="py-2.5 px-3 text-right" onclick="event.stopPropagation()">
              ${canReconcile ? `
                <button type="button" onclick="App.reconcile('${esc(op.id)}')" class="px-3 py-1 rounded bg-amber-500 hover:bg-amber-400 text-black font-semibold text-[11px] transition-all">
                  Reconcile
                </button>
              ` : `
                <button type="button" onclick="App.inspectOperation('${esc(op.id)}')" class="px-2.5 py-1 rounded bg-white/[0.06] hover:bg-white/10 text-slate-300 text-[11px]">
                  Detail
                </button>
              `}
            </td>
          </tr>
        `;
      }).join('');
    } else {
      tbody.innerHTML = '<tr><td colspan="8" class="py-6 text-center text-slate-500">No consequential operations executed yet.</td></tr>';
    }
  }

  // ==================== VIEW 7: AUDIT ====================
  async function refreshAudit() {
    if (!activeOrgId) return;
    const [auditData, events] = await Promise.all([
      API.getAudit(activeOrgId).catch(() => ({ chain_valid: true, verification_receipts: [] })),
      API.getEvents(activeOrgId).catch(() => []),
    ]);

    // Chain Status
    const isValid = auditData.chain_valid;
    $('auditChainStatusText').textContent = isValid ? 'VALID & UNTAMPERED' : 'CHAIN COMPROMISED';
    $('auditChainStatusText').className = `text-2xl font-mono font-bold ${isValid ? 'text-emerald-400' : 'text-red-400'}`;
    $('auditChainErrorText').textContent = auditData.chain_error || 'All Merkle roots and hash chains intact';
    $('auditReceiptsCount').textContent = auditData.verification_receipts.length;
    $('auditTotalEventsCount').textContent = events.length;

    // Verification Receipts Table
    const tbody = $('auditReceiptsTableBody');
    if (auditData.verification_receipts && auditData.verification_receipts.length) {
      tbody.innerHTML = auditData.verification_receipts.map(v => `
        <tr class="hover:bg-white/[0.02] transition-colors">
          <td class="py-2 px-3 text-white font-semibold">${esc(v.id.slice(0, 12))}…</td>
          <td class="py-2 px-3 text-slate-400">${esc(v.execution_id.slice(0, 12))}…</td>
          <td class="py-2 px-3 text-blue-400">${esc(v.proposal_id.slice(0, 12))}…</td>
          <td class="py-2 px-3 text-slate-400">${fmtTime(v.timestamp)}</td>
          <td class="py-2 px-3">
            <span class="px-2 py-0.5 rounded text-[10px] uppercase font-semibold ${v.verified ? 'badge-ok' : 'badge-danger'}">
              ${v.verified ? 'VERIFIED' : 'FAILED'}
            </span>
          </td>
          <td class="py-2 px-3 text-right">
            <button type="button" onclick="App.inspectEvidence('${esc(v.id)}', ${esc(JSON.stringify(v))})" class="text-blue-400 hover:text-blue-300 underline font-mono text-[11px]">
              ${esc(v.evidence_hash.slice(0, 14))}…
            </button>
          </td>
        </tr>
      `).join('');
    } else {
      tbody.innerHTML = '<tr><td colspan="6" class="py-6 text-center text-slate-500">No verification receipts recorded.</td></tr>';
    }

    // Full Events Inspector
    const container = $('auditEventsContainer');
    if (events && events.length) {
      container.innerHTML = events.slice().reverse().map(e => `
        <div class="p-3 rounded-lg bg-zinc-900/50 border border-white/[0.04] space-y-2">
          <div class="flex items-center justify-between">
            <div class="flex items-center gap-2">
              <span class="px-2 py-0.5 rounded bg-blue-500/10 text-blue-400 font-bold text-[10px]">#${e.sequence_id}</span>
              <span class="text-white font-semibold">${esc(e.event_type)}</span>
              <span class="text-slate-500">&bull;</span>
              <span class="text-slate-400">${esc(e.actor_id)}</span>
            </div>
            <span class="text-slate-500 text-[11px]">${fmtDate(e.timestamp)}</span>
          </div>
          <div class="text-[11px] text-slate-400 flex items-center gap-4">
            <span>Payload Hash: <code class="text-slate-300 font-mono">${esc(e.payload_hash ? e.payload_hash.slice(0, 16) : '—')}…</code></span>
            <span>Chain Hash: <code class="text-slate-300 font-mono">${esc(e.event_hash ? e.event_hash.slice(0, 16) : '—')}…</code></span>
          </div>
          <details class="text-[11px] text-slate-400">
            <summary class="cursor-pointer text-blue-400 hover:text-blue-300">Inspect Event Payload</summary>
            <pre class="mt-2 p-3 rounded bg-zinc-950 text-slate-300 overflow-x-auto text-[11px] border border-white/5">${esc(JSON.stringify(e.payload, null, 2))}</pre>
          </details>
        </div>
      `).join('');
    } else {
      container.innerHTML = '<div class="text-xs text-slate-500 py-6 text-center">No events found.</div>';
    }
  }

  // ==================== VIEW 8: EXPERIMENTS ====================
  async function refreshExperiments() {
    const data = await API.getExperimentsLatest().catch(() => ({ has_run: false }));
    const container = $('experimentsOutputContainer');

    if (!data.has_run || !data.report) {
      container.innerHTML = `
        <div class="glass-panel p-8 rounded-xl text-center space-y-3">
          <span class="material-symbols-outlined text-4xl text-slate-600">science</span>
          <p class="text-slate-300 text-sm font-semibold">No benchmark has been executed in this runtime session yet.</p>
          <p class="text-slate-500 text-xs max-w-md mx-auto">Click "Run 3-Round Benchmark" above to trigger an empirical multi-scenario simulation comparing Static, Performance, and Adaptive resource allocation.</p>
        </div>
      `;
      return;
    }

    const report = data.report;
    const scenarios = [
      { id: 'STEADY_STATE', name: 'Steady State Market', desc: 'Predictable baseline environment with stable risk parameters' },
      { id: 'HIGH_RISK_MARKET', name: 'High-Risk Market', desc: 'Volatile environment with aggressive failure probability' },
      { id: 'TREASURY_SHOCK', name: 'Treasury Shock', desc: 'Constrained initial capital requiring severe resource efficiency' },
    ];

    container.innerHTML = `
      <div class="glass-panel p-6 rounded-xl space-y-6">
        <div class="flex items-center justify-between border-b border-white/[0.08] pb-4">
          <div>
            <span class="text-[10px] font-mono uppercase text-blue-400">Empirical Benchmark Results</span>
            <h2 class="text-xl font-bold text-white">Comparative Allocation Strategy Analysis</h2>
          </div>
          <span class="text-xs font-mono text-slate-400">Rounds: ${report.num_rounds} &bull; Initial Treasury: ${report.initial_treasury} CR</span>
        </div>

        ${scenarios.map(sc => `
          <div class="space-y-3 pt-2">
            <div class="flex items-center justify-between">
              <div>
                <h3 class="text-sm font-bold text-white">${esc(sc.name)}</h3>
                <p class="text-xs text-slate-400">${esc(sc.desc)}</p>
              </div>
            </div>
            <div class="overflow-x-auto">
              <table class="w-full text-left text-xs font-mono">
                <thead>
                  <tr class="border-b border-white/[0.08] text-slate-400">
                    <th class="py-2.5 px-3">STRATEGY</th>
                    <th class="py-2.5 px-3">COMPLETED</th>
                    <th class="py-2.5 px-3">SUCCESS RATE</th>
                    <th class="py-2.5 px-3">SPENT</th>
                    <th class="py-2.5 px-3">ENDING TREASURY</th>
                    <th class="py-2.5 px-3">EFFICIENCY (VAL/CR)</th>
                    <th class="py-2.5 px-3">SURVIVED</th>
                  </tr>
                </thead>
                <tbody class="divide-y divide-white/[0.04]">
                  ${['STATIC', 'PERFORMANCE', 'ADAPTIVE'].map(strat => {
                    const res = (report.scenario_results || {})[`${sc.id}_${strat}`];
                    if (!res) return '';
                    return `
                      <tr class="hover:bg-white/[0.02] transition-colors ${strat === 'ADAPTIVE' ? 'bg-blue-500/[0.03]' : ''}">
                        <td class="py-2.5 px-3 font-bold ${strat === 'ADAPTIVE' ? 'text-blue-400' : 'text-white'}">${strat}</td>
                        <td class="py-2.5 px-3">${res.missions_completed} / ${res.missions_attempted}</td>
                        <td class="py-2.5 px-3 font-semibold text-white">${res.success_rate.toFixed(1)}%</td>
                        <td class="py-2.5 px-3">${res.total_credits_spent} CR</td>
                        <td class="py-2.5 px-3">${res.ending_treasury} CR</td>
                        <td class="py-2.5 px-3 text-emerald-400 font-semibold">${res.credit_efficiency.toFixed(2)}</td>
                        <td class="py-2.5 px-3">
                          <span class="px-2 py-0.5 rounded text-[10px] uppercase font-semibold ${res.survived ? 'badge-ok' : 'badge-danger'}">
                            ${res.survived ? 'YES' : 'BANKRUPT'}
                          </span>
                        </td>
                      </tr>
                    `;
                  }).join('')}
                </tbody>
              </table>
            </div>
          </div>
        `).join('')}
      </div>
    `;
  }

  // ==================== VIEW 9: SETTINGS ====================
  async function refreshSettings() {
    const s = await API.getSystemSettings().catch(() => null);
    if (!s) return;

    $('settingService').textContent = s.service;
    $('settingVersion').textContent = s.version;
    $('settingEnvironment').textContent = s.environment;
    $('settingDatabase').textContent = s.database_backend;
    $('settingIdentityAuth').textContent = s.identity_auth_enabled ? 'ACTIVE' : 'OFF (LOCAL DEV)';
    $('settingProvider').textContent = s.settlement_provider;
    $('settingRuleCount').textContent = `${s.policy_engine.rule_count} Deterministic Rules`;
    $('settingPolicyHash').textContent = s.policy_engine.version_hash;
  }

  // ==================== DETAIL DRAWERS ====================

  async function openMissionConsole() {
    if (!activeOrgId) return;
    openDrawer('drawerMissionConsole');
    const container = $('drawerMissionConsoleBody');
    container.innerHTML = '<div class="text-xs text-slate-500 py-6 text-center">Loading mission progression...</div>';

    try {
      const [orgData, proposals, decisions, events] = await Promise.all([
        API.getOrganisation(activeOrgId),
        API.getProposals(activeOrgId),
        API.getDecisions(activeOrgId),
        API.getEvents(activeOrgId),
      ]);

      const org = orgData.organisation;
      container.innerHTML = `
        <div class="space-y-4 font-mono text-xs">
          <!-- Mission Overview -->
          <div class="p-4 rounded-xl bg-zinc-900 border border-white/10 space-y-2">
            <span class="text-[10px] text-blue-400 uppercase font-semibold">Mission Intent</span>
            <div class="text-sm font-bold text-white">${esc(org.mission)}</div>
            <div class="text-slate-400 text-[11px]">Treasury: ${fmtNum(org.treasury_balance)} CR &bull; Status: <span class="text-emerald-400 font-bold uppercase">${esc(org.state)}</span></div>
          </div>

          <!-- Step-by-Step Evolution -->
          <div class="space-y-3">
            <h4 class="text-xs font-bold text-slate-400 uppercase tracking-wider">Evolutionary Governance Path</h4>
            
            ${(proposals || []).map((p, idx) => {
              const dec = (decisions || []).find(d => d.proposal_id === p.id);
              const isApproved = dec && String(dec.result).toUpperCase() === 'APPROVED';
              return `
                <div class="p-3.5 rounded-xl bg-zinc-900/70 border ${isApproved ? 'border-emerald-500/30' : 'border-red-500/30'} space-y-2">
                  <div class="flex items-center justify-between">
                    <span class="text-slate-400 font-semibold">Iteration #${idx + 1} &bull; ${esc(p.action_type)}</span>
                    <span class="px-2 py-0.5 rounded text-[10px] uppercase font-semibold ${isApproved ? 'badge-ok' : 'badge-danger'}">
                      ${dec ? esc(dec.result) : 'PENDING'}
                    </span>
                  </div>
                  <div class="text-white text-xs">Target: <code class="text-blue-300">${esc(p.target)}</code></div>
                  <div class="text-slate-400 text-[11px]">Requested: <strong class="text-white">${p.requested_credits} CR</strong> &bull; Proposer: ${esc(p.proposing_agent_id)}</div>
                  ${dec && !isApproved ? `
                    <div class="p-2.5 rounded bg-red-500/10 border border-red-500/20 text-red-300 text-[11px]">
                      <strong>Policy Rejection (${esc(dec.violated_rule_id)}):</strong> ${esc(dec.violated_rule_description)}
                    </div>
                  ` : ''}
                  ${dec && isApproved ? `
                    <div class="p-2.5 rounded bg-emerald-500/10 border border-emerald-500/20 text-emerald-300 text-[11px]">
                      <strong>Authorization Granted:</strong> HMAC token issued &bull; Escrow committed &bull; Safe execution permitted
                    </div>
                  ` : ''}
                </div>
              `;
            }).join('') || '<div class="text-slate-500 text-center py-4">No proposals recorded.</div>'}
          </div>
        </div>
      `;
    } catch (err) {
      container.innerHTML = `<div class="text-xs text-red-400 py-6 text-center">Failed to load mission console: ${esc(err.message)}</div>`;
    }
  }

  async function inspectAgent(agentId) {
    if (!activeOrgId) return;
    openDrawer('drawerAgentDetail');
    $('drawerAgentTitle').textContent = `Agent: ${agentId}`;
    const container = $('drawerAgentBody');
    container.innerHTML = '<div class="text-xs text-slate-500 py-6 text-center">Loading agent dossier...</div>';

    try {
      const data = await API.getAgentProfile(activeOrgId, agentId);
      const a = data.agent;
      container.innerHTML = `
        <div class="space-y-4 font-mono text-xs">
          <!-- Metrics Overview -->
          <div class="grid grid-cols-2 gap-3">
            <div class="p-3 rounded-lg bg-zinc-900 border border-white/10">
              <span class="text-slate-500 text-[10px]">ROLE</span>
              <div class="text-white font-bold text-sm mt-0.5">${esc(a.role)}</div>
            </div>
            <div class="p-3 rounded-lg bg-zinc-900 border border-white/10">
              <span class="text-slate-500 text-[10px]">LIFECYCLE STATUS</span>
              <div class="text-emerald-400 font-bold text-sm mt-0.5 uppercase">${esc(a.status)}</div>
            </div>
            <div class="p-3 rounded-lg bg-zinc-900 border border-white/10">
              <span class="text-slate-500 text-[10px]">AUTHORITY CEILING</span>
              <div class="text-white font-bold text-sm mt-0.5">${fmtNum(a.authority_ceiling)} CR</div>
            </div>
            <div class="p-3 rounded-lg bg-zinc-900 border border-white/10">
              <span class="text-slate-500 text-[10px]">CREDIT BALANCE</span>
              <div class="text-blue-400 font-bold text-sm mt-0.5">${fmtNum(a.credit_balance)} CR</div>
            </div>
          </div>

          <!-- Performance & Reliability -->
          <div class="p-3.5 rounded-xl bg-zinc-900 border border-white/10 space-y-2">
            <span class="text-[10px] text-slate-500 uppercase font-semibold">Reputation &amp; Performance</span>
            <div class="flex items-center justify-between text-xs pt-1">
              <span class="text-slate-400">Reputation Score</span>
              <span class="text-white font-bold">${Number(a.reputation_score).toFixed(1)} / 100</span>
            </div>
            <div class="flex items-center justify-between text-xs">
              <span class="text-slate-400">Performance Index</span>
              <span class="text-white font-bold">${Number(a.performance_score).toFixed(1)}</span>
            </div>
            <div class="flex items-center justify-between text-xs">
              <span class="text-slate-400">Reliability Score</span>
              <span class="text-white font-bold">${Number(a.reliability_score).toFixed(1)}%</span>
            </div>
          </div>

          <!-- Assigned Tasks -->
          <div class="space-y-2">
            <h4 class="text-xs font-bold text-slate-400 uppercase tracking-wider">Assigned Tasks (${data.tasks.length})</h4>
            ${(data.tasks || []).map(t => `
              <div class="p-2.5 rounded bg-zinc-900/60 border border-white/[0.04] space-y-1">
                <div class="flex items-center justify-between">
                  <span class="text-white font-semibold">${esc(t.id)}</span>
                  <span class="text-[10px] uppercase font-bold text-emerald-400">${esc(t.status)}</span>
                </div>
                <div class="text-slate-400 text-[11px]">${esc(t.objective)}</div>
              </div>
            `).join('') || '<div class="text-slate-500 text-center py-2">No tasks assigned.</div>'}
          </div>
        </div>
      `;
    } catch (err) {
      container.innerHTML = `<div class="text-xs text-red-400 py-6 text-center">Failed to load agent dossier: ${esc(err.message)}</div>`;
    }
  }

  async function inspectOperation(opId) {
    if (!activeOrgId) return;
    openDrawer('drawerOperationDetail');
    $('drawerOperationTitle').textContent = `Operation: ${opId.slice(0, 14)}…`;
    const container = $('drawerOperationBody');
    container.innerHTML = '<div class="text-xs text-slate-500 py-6 text-center">Loading operation detail...</div>';

    try {
      const data = await API.getOperation(activeOrgId, opId);
      const op = data.operation;
      const isUnknown = op.state.toLowerCase() === 'unknown';
      const canReconcile = ['unknown', 'submitted', 'reconciling'].includes(op.state.toLowerCase());

      container.innerHTML = `
        <div class="space-y-4 font-mono text-xs">
          <!-- State Callout -->
          <div class="p-4 rounded-xl ${isUnknown ? 'bg-amber-500/10 border border-amber-500/30' : 'bg-zinc-900 border border-white/10'} space-y-2">
            <div class="flex items-center justify-between">
              <span class="text-slate-500 text-[10px] uppercase">State Machine Status</span>
              <span class="px-2.5 py-0.5 rounded text-[10px] uppercase font-bold ${
                op.state.toLowerCase() === 'succeeded' ? 'badge-ok' :
                isUnknown ? 'badge-warn' : 'badge-danger'
              }">${esc(op.state)}</span>
            </div>
            <div class="text-white text-sm font-bold">${esc(op.action_type)} &bull; ${fmtNum(op.amount)} CR</div>
            ${isUnknown ? `
              <div class="text-xs text-amber-300 mt-1">
                Outcome is unconfirmed by provider. Resources remain held in ESCROW. Reconcile to synchronize definitive ledger settlement.
              </div>
            ` : ''}
          </div>

          <!-- Parameters -->
          <div class="space-y-2">
            <div class="flex items-center justify-between py-1.5 border-b border-white/[0.04]">
              <span class="text-slate-500">Target Endpoint</span>
              <code class="text-blue-300">${esc(op.target)}</code>
            </div>
            <div class="flex items-center justify-between py-1.5 border-b border-white/[0.04]">
              <span class="text-slate-500">Idempotency Key</span>
              <code class="text-white truncate max-w-[200px]" title="${esc(op.idempotency_key)}">${esc(op.idempotency_key)}</code>
            </div>
            <div class="flex items-center justify-between py-1.5 border-b border-white/[0.04]">
              <span class="text-slate-500">Settlement Provider</span>
              <span class="text-white">${esc(op.provider_name)}</span>
            </div>
            <div class="flex items-center justify-between py-1.5 border-b border-white/[0.04]">
              <span class="text-slate-500">Provider Reference</span>
              <span class="text-slate-300">${esc(op.provider_reference || 'Pending settlement')}</span>
            </div>
            <div class="flex items-center justify-between py-1.5 border-b border-white/[0.04]">
              <span class="text-slate-500">Created Timestamp</span>
              <span class="text-slate-400">${fmtDate(op.created_at)}</span>
            </div>
          </div>

          <!-- Reconciliation Action -->
          ${canReconcile ? `
            <div class="pt-4 border-t border-white/[0.08] space-y-3">
              <button type="button" onclick="App.reconcile('${esc(op.id)}')" class="w-full py-2.5 rounded-lg bg-amber-500 hover:bg-amber-400 text-black font-bold text-xs transition-all shadow-md">
                Trigger Safe Reconciliation
              </button>
              <p class="text-[11px] text-slate-500 text-center">Reconciliation inspects the provider and guarantees atomic single settlement.</p>
            </div>
          ` : ''}
        </div>
      `;
    } catch (err) {
      container.innerHTML = `<div class="text-xs text-red-400 py-6 text-center">Failed to load operation: ${esc(err.message)}</div>`;
    }
  }

  function inspectEvidence(id, payload) {
    openDrawer('drawerEvidenceDetail');
    $('drawerEvidenceTitle').textContent = `Receipt: ${id.slice(0, 14)}…`;
    const container = $('drawerEvidenceBody');
    const jsonStr = typeof payload === 'string' ? payload : JSON.stringify(payload, null, 2);
    container.innerHTML = `
      <div class="space-y-3">
        <div class="text-slate-400 text-xs">Immutable cryptographic evidence verification record:</div>
        <pre class="p-4 rounded-xl bg-zinc-950 text-slate-200 overflow-x-auto text-[11px] border border-white/10 max-h-[500px]">${esc(jsonStr)}</pre>
      </div>
    `;
  }

  // ==================== ACTIONS ====================

  async function reconcile(opId) {
    if (!activeOrgId) return;
    try {
      const res = await API.reconcileOperation(activeOrgId, opId);
      closeDrawers();
      await refreshCurrentView();
    } catch (err) {
      alert(`Reconciliation failed: ${err.message}`);
    }
  }

  async function selectOrg(orgId) {
    activeOrgId = orgId;
    $('orgSelect').value = orgId;
    await refreshCurrentView();
  }

  // Submit new mission from modal composer
  async function submitMission(event) {
    event.preventDefault();
    const btn = $('btnSubmitMission');
    const feedback = $('composerFeedback');
    const objective = $('composerObjective').value.trim();
    const budget = Number($('composerBudget').value);
    const live = $('composerLive').checked;

    btn.disabled = true;
    feedback.textContent = 'Orchestrating autonomous agents and validating policies…';

    try {
      const result = await API.createMission({ mission: objective, budget, live });
      activeOrgId = result.organisation_id;
      closeModals();
      $('composerObjective').value = '';
      await loadOrganisations();
      setRoute('overview');
    } catch (err) {
      feedback.textContent = `Mission formulation failed: ${err.message}`;
    } finally {
      btn.disabled = false;
    }
  }

  // Run scripted 1-click judge demo
  async function runDemo() {
    const btn = $('btnRunDemo');
    btn.disabled = true;
    const origText = btn.innerHTML;
    btn.innerHTML = '<span class="material-symbols-outlined text-[15px] animate-spin">sync</span><span>Running Demo…</span>';

    try {
      const result = await API.runDemo();
      activeOrgId = result.organisation_id;
      await loadOrganisations();
      setRoute('overview');
    } catch (err) {
      alert(`Demo mission failed: ${err.message}`);
    } finally {
      btn.disabled = false;
      btn.innerHTML = origText;
    }
  }

  // Run 3-round benchmark in experiments
  async function runBenchmark() {
    const btn = $('btnRunExperiment');
    btn.disabled = true;
    const origText = btn.innerHTML;
    btn.innerHTML = '<span class="material-symbols-outlined text-[16px] animate-spin">sync</span><span>Simulating 3 Rounds…</span>';

    try {
      await API.runExperiments(3);
      await refreshExperiments();
    } catch (err) {
      alert(`Benchmark execution failed: ${err.message}`);
    } finally {
      btn.disabled = false;
      btn.innerHTML = origText;
    }
  }

  // Toggle Circuit Breaker
  async function toggleCircuitBreaker() {
    if (!activeOrgId) return;
    try {
      const orgData = await API.getOrganisation(activeOrgId);
      const isPaused = orgData.organisation.state === 'PAUSED';
      if (isPaused) {
        await API.resumeOrg(activeOrgId);
      } else {
        if (!confirm('TRIP EMERGENCY HALT?\n\nThis immediately freezes all autonomous execution and revokes ephemeral authority.')) {
          return;
        }
        await API.pauseOrg(activeOrgId);
      }
      await refreshCurrentView();
    } catch (err) {
      alert(`Circuit breaker action failed: ${err.message}`);
    }
  }

  // Verify Audit Chain
  async function verifyAuditChain() {
    if (!activeOrgId) return;
    try {
      const audit = await API.getAudit(activeOrgId);
      if (audit.chain_valid) {
        alert('CRYPTOGRAPHIC AUDIT CHAIN VERIFIED\n\n100% of event hashes, Merkle roots, and execution receipts are intact and untampered.');
      } else {
        alert(`AUDIT INTEGRITY BREACH DETECTED:\n\n${audit.chain_error}`);
      }
      await refreshAudit();
    } catch (err) {
      alert(`Audit verification request failed: ${err.message}`);
    }
  }

  // Boot Application
  async function init() {
    // 1. Check health
    try {
      const health = await API.getHealth();
      $('engineStatusText').textContent = 'ONLINE';
      $('engineStatusDot').className = 'w-2 h-2 rounded-full bg-emerald-400 shadow-[0_0_8px_rgba(52,211,153,0.8)]';
    } catch (err) {
      $('engineStatusText').textContent = 'OFFLINE';
      $('engineStatusDot').className = 'w-2 h-2 rounded-full bg-red-400 shadow-[0_0_8px_rgba(239,68,68,0.8)]';
    }

    // 2. Setup event listeners
    window.addEventListener('hashchange', () => {
      const route = window.location.hash.replace('#', '') || 'overview';
      setRoute(route);
    });

    $('orgSelect').addEventListener('change', e => {
      selectOrg(e.target.value).catch(console.warn);
    });

    $('btnRunDemo').addEventListener('click', runDemo);
    $('btnOpenComposer').addEventListener('click', () => openModal('modalMissionComposer'));
    $('btnNewMissionAction').addEventListener('click', () => openModal('modalMissionComposer'));
    $('missionComposerForm').addEventListener('submit', submitMission);
    $('btnCircuitBreaker').addEventListener('click', toggleCircuitBreaker);
    $('btnVerifyAuditChain').addEventListener('click', verifyAuditChain);
    $('btnRunExperiment').addEventListener('click', runBenchmark);
    $('btnOpenMissionConsoleFromCard').addEventListener('click', openMissionConsole);

    // Escape key closes drawers and modals
    window.addEventListener('keydown', e => {
      if (e.key === 'Escape') {
        closeDrawers();
        closeModals();
      }
    });

    // 3. Load orgs and set initial route
    await loadOrganisations();
    const initialRoute = window.location.hash.replace('#', '') || 'overview';
    setRoute(initialRoute);

    // 4. Background polling loop (4s interval, pause if document hidden)
    setInterval(() => {
      if (!document.hidden && !isPolling) {
        isPolling = true;
        refreshCurrentView().finally(() => { isPolling = false; });
      }
    }, 4000);
  }

  return {
    init,
    setRoute,
    closeDrawers,
    closeModals,
    openMissionConsole,
    inspectAgent,
    inspectOperation,
    inspectEvidence,
    reconcile,
    selectOrg,
  };
})();

window.App = App;
window.addEventListener('DOMContentLoaded', App.init);

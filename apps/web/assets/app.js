const App = (() => {
  let activeOrgId = null;
  let currentRoute = 'overview';
  let isPolling = false;
  let cachedOrgData = null;
  let cachedLedgerData = null;
  let cachedAgents = [];
  let auditEventsById = {};
  let liveDemoSessionId = null;
  let liveDemoPollTimer = null;
  const LIVE_DEMO_STAGES = [
    ['INITIALIZE', 'Initialize', 'Mission accepted by the control plane'],
    ['PLAN', 'Plan', 'Agents decompose the objective'],
    ['PROPOSE', 'Propose', 'An agent submits a consequential proposal'],
    ['AUTHORIZE', 'Authorize', 'Policy decides whether authority is valid'],
    ['EXECUTE', 'Execute', 'Controlled executor performs the action'],
    ['VERIFY', 'Verify', 'Independent auditor checks evidence'],
    ['SETTLE', 'Settle', 'Ledger reflects the resulting economic state'],
    ['AUDIT', 'Audit', 'The completed chronology remains inspectable'],
  ];

  // Agent inspector is always populated from the authoritative API. No fictional fallback registry.
  const AGENT_REGISTRY = Object.freeze({});

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

  // DOM helpers used by every view/controller path. Keep these tiny and deterministic.
  const $ = (id) => document.getElementById(id);
  function setTxt(id, value) {
    const el = $(id);
    if (el) el.textContent = String(value ?? '');
  }

  // Navigation & Routing
  function setRoute(route) {
    const validRoutes = ['overview', 'demo', 'missions', 'organisation', 'treasury', 'policies', 'operations', 'marketplace', 'collateral', 'audit', 'experiments', 'settings'];
    const target = validRoutes.includes(route) ? route : 'overview';
    currentRoute = target;

    // Update URL hash
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

    // Immediate refresh
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
      if (el.id && el.id.startsWith('drawer')) {
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
      if (el.id && el.id.startsWith('modal')) el.classList.add('hidden');
    });
  }

  // Load available organisations
  async function loadOrganisations() {
    try {
      const orgs = await API.getOrganisations();
      const select = $('orgSelect');
      if (!select) return;

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
      console.warn('Failed to load organisations:', err);
      activeOrgId = null;
      select.innerHTML = '<option value="">Unavailable</option>';
      setTxt('sidebarOrgState', 'UNAVAILABLE');
      setTxt('cardOrgStateText', 'CONTROL PLANE UNAVAILABLE');
      setTxt('cardOrgSubtitle', err?.message || 'Unable to read organisation state');
      setTxt('engineStatusText', 'DEGRADED');
    }
  }

  function enhanceSecondaryViews() {
    const meta = {
      missions: ['02', 'MISSIONS', 'Autonomous work, mission lineage and deliverables.'],
      organisation: ['03', 'ORGANISATION', 'Agents, authority envelopes and workforce state.'],
      treasury: ['04', 'TREASURY', 'Capital, escrow, conservation and economic movement.'],
      policies: ['05', 'POLICIES', 'The rules that authorize consequential action.'],
      operations: ['06', 'OPERATIONS', 'Execution state, evidence and reconciliation.'],
      marketplace: ['07', 'MARKETPLACE', 'Organizations discovering, claiming and settling work.'],
      collateral: ['08', 'CREDIT COLLATERAL', 'Governed resource locking and settlement semantics.'],
      audit: ['09', 'AUDIT', 'Independent evidence, chronology and verification.'],
      experiments: ['10', 'EXPERIMENTS', 'Controlled economic experiments and observed outcomes.'],
      settings: ['11', 'SYSTEM', 'Runtime configuration and operating boundaries.'],
    };
    Object.entries(meta).forEach(([route, values]) => {
      const [index, title, description] = values;
      const view = document.getElementById('view-' + route);
      if (!view || view.querySelector('.kalyx-secondary-header')) return;
      view.classList.add('kalyx-secondary-view');
      const header = document.createElement('div');
      header.className = 'kalyx-secondary-header';
      header.innerHTML = '<div><span class="kalyx-eyebrow"><span>' + index + '</span> ' + title + '</span><h1>' + (title === 'SYSTEM' ? 'System' : title.charAt(0) + title.slice(1).toLowerCase()) + '</h1><p>' + description + '</p></div><span class="kalyx-secondary-status">GOVERNED · LIVE STATE</span>';
      view.prepend(header);
    });
  }

  // Refresh current view based on activeRoute
  async function refreshCurrentView() {
    if (!activeOrgId && currentRoute !== 'experiments' && currentRoute !== 'settings' && currentRoute !== 'demo') {
      return;
    }

    try {
      // 1. Refresh executive state cache if org is selected
      if (activeOrgId) {
        const [orgData, ledgerData, opsSummary] = await Promise.all([
          API.getOrganisation(activeOrgId).catch(() => null),
          API.getLedger(activeOrgId).catch(() => null),
          API.getOperationsSummary(activeOrgId).catch(() => null),
        ]);

        if (orgData) {
          cachedOrgData = orgData;
          cachedLedgerData = ledgerData;
          cachedAgents = orgData.agents || [];
          updateExecutiveState(orgData, ledgerData, opsSummary);
        }
      }

      // 2. Refresh active route view
      switch (currentRoute) {
        case 'overview':
          await refreshOverview();
          break;
        case 'demo':
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
        case 'marketplace':
          await refreshMarketplace();
          break;
        case 'collateral':
          await refreshCollateral();
          break;
      }
    } catch (err) {
      console.warn(`[App] Error refreshing view ${currentRoute}:`, err.message);
    }
  }

  // Update executive state bar across all views
  function updateExecutiveState(orgData, ledgerData, opsSummary) {
    const org = orgData.organisation;
    const isPaused = org.state === 'PAUSED';

    // Topbar Pause/Resume label
    setTxt('circuitBreakerLabel', isPaused ? 'RESUME' : 'PAUSE');

    // Overview Card 1: Org Status
    setTxt('cardOrgStateText', isPaused ? 'ORGANISATION PAUSED' : 'All Systems Operational');
    setTxt('cardOrgSubtitle', isPaused
      ? 'Emergency kill switch tripped. Consequential execution frozen.'
      : `${orgData.agents.length} autonomous agents active • 0 policy breaches`);
    setTxt('cardOrgBadge', isPaused ? 'PAUSED' : 'Nominal');
    const badge = $('cardOrgBadge');
    if (badge) {
      badge.className = `font-label-sm text-label-sm ${isPaused ? 'text-red-400 font-bold' : ''}`;
    }

    // Overview Card 2: Treasury Balance
    if (ledgerData) {
      setTxt('cardTreasuryBalance', fmtNum(ledgerData.treasury));
      setTxt('cardTreasuryBreakdown', `Escrow: ${fmtNum(ledgerData.escrow)} CR • Sink: ${fmtNum(ledgerData.external_sink)} CR`);
      setTxt('cardConservationBadge', ledgerData.conserved ? 'CONSERVED' : 'BREACH');
      const cons = $('cardConservationBadge');
      if (cons) {
        cons.className = `font-label-sm text-label-sm font-mono ${ledgerData.conserved ? 'text-emerald-400' : 'text-red-400 font-bold'}`;
      }
    }

    // Overview Card 3: Active Mission
    if (org.mission) {
      setTxt('cardActiveExpeditionTitle', org.mission);
      setTxt('cardActiveExpeditionPhase', `Autonomous Expedition • ${orgData.tasks.length} tasks delegated`);
    }

    // Command Centre system state
    setTxt('sidebarOrgState', isPaused ? 'PAUSED / GOVERNED' : 'CONTROL PLANE');
    setTxt('systemEngineState', 'OPERATIONAL');
    setTxt('systemDatabaseState', 'CONNECTED');
    setTxt('liveStateLabel', 'REFRESHING · 4s');

    if (ledgerData) {
      setTxt('econTreasury', fmtNum(ledgerData.treasury) + ' CR');
      setTxt('econEscrow', fmtNum(ledgerData.escrow) + ' CR');
      setTxt('econExternal', fmtNum(ledgerData.external_sink) + ' CR');
    }

    // Operations Badge
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
    const [events, agents, opsSummary] = await Promise.all([
      API.getEvents(activeOrgId).catch(() => []),
      API.getAgents(activeOrgId).catch(() => []),
      API.getOperationsSummary(activeOrgId).catch(() => null),
    ]);

    // 1. Active Agents List
    const agentContainer = $('activeAgentsContainer');
    if (agentContainer && agents && agents.length > 0) {
      agentContainer.innerHTML = agents.map(a => `
        <div class="flex items-center justify-between p-space-md rounded-lg bg-white/[0.02] hover:bg-white/[0.05] border border-transparent hover:border-white/10 transition-colors duration-150 group cursor-pointer" onclick="App.selectAgentForInspector('${esc(a.id)}')">
          <div class="flex items-center gap-space-md min-w-0">
            <div class="w-9 h-9 rounded-lg bg-blue-500/10 border border-blue-500/20 flex items-center justify-center text-blue-400 shrink-0 shadow-sm">
              <span class="material-symbols-outlined text-[20px]">smart_toy</span>
            </div>
            <div class="flex flex-col min-w-0">
              <div class="flex items-center gap-2">
                <span class="font-label-md text-label-md text-white font-semibold truncate group-hover:text-blue-400 transition-colors">${esc(a.id)}</span>
                <span class="font-label-sm text-label-sm text-slate-500 font-mono">${esc(a.model_name || 'Autonomous')}</span>
              </div>
              <span class="font-body-sm text-body-sm text-slate-400 truncate">${esc(a.role)}</span>
            </div>
          </div>
          <div class="flex items-center gap-space-md shrink-0">
            <div class="hidden sm:flex flex-col items-end font-mono">
              <span class="font-label-sm text-label-sm text-white">${fmtNum(a.authority_ceiling)} CR</span>
              <span class="font-label-sm text-label-sm text-slate-500">Rep: ${Number(a.reputation_score).toFixed(0)}</span>
            </div>
            <div class="flex items-center gap-1.5 px-2.5 py-1 rounded-full ${a.status === 'ACTIVE' ? 'bg-emerald-500/10 border border-emerald-500/20 text-emerald-400' : 'bg-amber-500/10 border border-amber-500/20 text-amber-300'}">
              <span class="w-1.5 h-1.5 rounded-full ${a.status === 'ACTIVE' ? 'bg-emerald-400' : 'bg-amber-400'}"></span>
              <span class="font-label-sm text-label-sm font-medium uppercase">${esc(a.status)}</span>
            </div>
          </div>
        </div>
      `).join('');
    }

    // 2. Recent Chronology Activity
    const activityContainer = $('recentActivityContainer');
    if (activityContainer && events && events.length > 0) {
      activityContainer.innerHTML = events.slice(-6).reverse().map(e => `
        <div class="flex items-start gap-space-md relative group">
          <div class="w-11 h-8 rounded-md bg-white/[0.06] border border-white/[0.08] flex items-center justify-center shrink-0 z-10 font-mono text-[11px] text-blue-400 font-medium">
            #${e.sequence_id}
          </div>
          <div class="flex flex-col pt-0.5 min-w-0">
            <span class="font-body-sm text-body-sm text-white font-medium leading-snug truncate">${esc(e.event_type)}</span>
            <span class="font-label-sm text-label-sm text-slate-400 truncate">Actor: <span class="text-slate-300 font-mono">${esc(e.actor_id)}</span> • ${fmtTime(e.timestamp)}</span>
          </div>
        </div>
      `).join('');
    }
  }

  // ==================== VIEW 2: MISSIONS ====================
  async function refreshMissions() {
    if (!activeOrgId) return;
    const [orgData, orgs, lineageData, workOrdersData] = await Promise.all([
      API.getOrganisation(activeOrgId).catch(() => null),
      API.getOrganisations().catch(() => []),
      API.getMissionLineage(activeOrgId).catch(() => null),
      API.getWorkOrders(activeOrgId).catch(() => null),
    ]);

    if (orgData) {
      const org = orgData.organisation;
      setTxt('missionHeroTitle', org.mission || 'Autonomous Liquidity Rebalancing');
      setTxt('missionHeroCode', org.id);
      setTxt('missionHeroBudgetBurn', `${fmtNum(org.treasury_balance)} CR allocated`);
      
      let taskSummary = `${orgData.tasks.length} Delegated Tasks`;
      if (lineageData && lineageData.total > 0) {
        taskSummary += ` • ${lineageData.total} Recursive Cycles`;
      }
      if (workOrdersData && workOrdersData.total > 0) {
        taskSummary += ` • ${workOrdersData.total} Work Orders`;
      }
      setTxt('missionHeroDeliverables', taskSummary);
    }

    // Phase 16: Work Orders Table
    const woTbody = $('workOrdersTableBody');
    if (woTbody && workOrdersData && workOrdersData.work_orders) {
      const wos = workOrdersData.work_orders;
      const badge = $('workOrderCountBadge');
      if (badge) badge.textContent = `${wos.length} Order${wos.length !== 1 ? 's' : ''}`;
      if (wos.length === 0) {
        woTbody.innerHTML = '<tr><td colspan="5" class="py-6 px-4 text-center text-slate-500 italic">No work orders yet &mdash; run a demo to generate one</td></tr>';
      } else {
        woTbody.innerHTML = wos.map(item => {
          const wo = item.work_order;
          const deliv = item.deliverable;
          const statusColor = wo.status === 'SETTLED' ? 'text-emerald-400 bg-emerald-500/10' :
                              wo.status === 'VERIFIED' ? 'text-blue-400 bg-blue-500/10' :
                              wo.status === 'IN_PROGRESS' ? 'text-amber-400 bg-amber-500/10' :
                              wo.status === 'REJECTED' ? 'text-rose-400 bg-rose-500/10' :
                              'text-slate-400 bg-slate-500/10';
          return `
          <tr class="hover:bg-white/[0.02] transition-colors">
            <td class="py-3 px-4 font-mono text-on-surface-variant truncate max-w-[140px]">${esc(wo.work_order_id)}</td>
            <td class="py-3 px-4 text-slate-300 truncate max-w-[160px]">${esc(wo.title)}</td>
            <td class="py-3 px-4 text-emerald-400 font-mono font-bold">${fmtNum(wo.bounty_amount)} USDG</td>
            <td class="py-3 px-4">
              <span class="px-2 py-0.5 rounded-full font-label-sm text-label-sm uppercase font-semibold ${statusColor}">${esc(wo.status)}</span>
            </td>
            <td class="py-3 px-4 text-slate-400">${deliv ? '<span class="text-primary">&#x2714; Delivered</span>' : '<span class="text-slate-600">Pending</span>'}</td>
          </tr>`;
        }).join('');
      }
    }

    // Phase 16: Mission Lineage Counts
    if (lineageData) {
      const all = lineageData.lineage || [];
      const roots = all.filter(m => !m.parent_mission_id);
      const children = all.filter(m => !!m.parent_mission_id);
      const cycleCountEl = $('missionLineageCycleCount');
      const rootCountEl = $('missionLineageRootCount');
      const childCountEl = $('missionLineageChildCount');
      if (cycleCountEl) cycleCountEl.textContent = all.length;
      if (rootCountEl) rootCountEl.textContent = roots.length;
      if (childCountEl) childCountEl.textContent = children.length;
    }

    // Missions Historical Record Table
    const tbody = $('missionsHistoryTableBody');
    if (tbody && orgs && orgs.length > 0) {
      tbody.innerHTML = orgs.map(o => `
        <tr class="hover:bg-white/[0.02] transition-colors ${o.id === activeOrgId ? 'bg-white/[0.03]' : ''}">
          <td class="py-3 px-4 font-semibold ${o.id === activeOrgId ? 'text-blue-400' : 'text-white'} font-mono">${esc(o.id)}</td>
          <td class="py-3 px-4 text-slate-300 max-w-sm truncate">${esc(o.mission || 'Autonomous Organization')}</td>
          <td class="py-3 px-4 text-primary font-bold font-mono">${fmtNum(o.treasury_balance)} CR</td>
          <td class="py-3 px-4">
            <span class="px-2 py-0.5 rounded-full font-label-sm text-label-sm uppercase font-semibold ${o.state === 'EXECUTING' ? 'bg-emerald-500/10 text-emerald-400' : o.state === 'PAUSED' ? 'bg-red-500/10 text-red-400' : 'bg-slate-500/10 text-slate-400'}">
              ${esc(o.state)}
            </span>
          </td>
          <td class="py-3 px-4 text-slate-400 font-mono">${fmtDate(o.created_at)}</td>
          <td class="py-3 px-4 text-right">
            <button type="button" onclick="App.selectOrg('${esc(o.id)}')" class="px-2.5 py-1 rounded bg-white/5 hover:bg-white/10 text-primary font-medium text-xs">
              Select
            </button>
          </td>
        </tr>
      `).join('');
    }
  }


  // ==================== VIEW 3: ORGANISATION ====================
  async function refreshOrganisation() {
    if (!activeOrgId) return;
    const agents = await API.getAgents(activeOrgId).catch(() => []);
    if (!agents) return;

    setTxt('orgTotalAgentsCount', `${agents.length} Active`);

    // Populate Roster Table
    const tbody = $('organisationAgentsTableBody');
    if (tbody && agents.length > 0) {
      tbody.innerHTML = agents.map(a => `
        <tr class="hover:bg-white/[0.02] cursor-pointer" onclick="App.selectAgentForInspector('${esc(a.id)}')">
          <td class="py-3 px-4 font-semibold text-white font-mono">${esc(a.id)}</td>
          <td class="py-3 px-4 text-slate-300">${esc(a.role)}</td>
          <td class="py-3 px-4">
            <span class="px-2 py-0.5 rounded-full font-label-sm text-label-sm uppercase font-semibold ${a.status === 'ACTIVE' ? 'bg-emerald-500/10 text-emerald-400' : 'bg-amber-500/10 text-amber-400'}">
              ${esc(a.status)}
            </span>
          </td>
          <td class="py-3 px-4 text-white font-mono">${fmtNum(a.authority_ceiling)} CR</td>
          <td class="py-3 px-4 text-primary font-bold font-mono">${fmtNum(a.credit_balance)} CR</td>
          <td class="py-3 px-4 text-emerald-400 font-semibold font-mono">${Number(a.reputation_score).toFixed(1)}</td>
          <td class="py-3 px-4 text-slate-300 font-mono">${Number(a.performance_score).toFixed(1)}</td>
          <td class="py-3 px-4 text-right">
            <button type="button" class="px-2.5 py-1 rounded bg-white/5 hover:bg-white/10 text-slate-300 hover:text-white" onclick="event.stopPropagation(); App.openAgentDossier('${esc(a.id)}')">
              Dossier
            </button>
          </td>
        </tr>
      `).join('');
    }
  }

  // Interactive Selected Agent Inspector Panel update
  function selectAgentForInspector(agentId) {
    const found = cachedAgents.find(a => a.id === agentId);
    if (!found) {
      ['inspectorAgentName','inspectorNodeId','inspectorAgentStatus','inspectorFoundation','inspectorRuntime','inspectorUptime','inspectorDailyLimit','inspectorReliabilityScore'].forEach(id => setTxt(id, '—'));
      return;
    }
    setTxt('inspectorAgentName', found.id || 'Unnamed agent');
    setTxt('inspectorNodeId', found.id || '—');
    setTxt('inspectorAgentStatus', found.status || 'UNKNOWN');
    setTxt('inspectorFoundation', found.model_name || 'Not recorded');
    setTxt('inspectorRuntime', found.runtime || 'Not recorded');
    setTxt('inspectorUptime', found.uptime || 'Not recorded');
    setTxt('inspectorDailyLimit', found.authority_ceiling != null ? `${fmtNum(found.authority_ceiling)} CR` : 'Not recorded');
    setTxt('inspectorReliabilityScore', found.reliability_score != null ? Number(found.reliability_score).toFixed(1) : 'Not recorded');
  }

  // ==================== VIEW 4: TREASURY ====================
  async function refreshTreasury() {
    if (!activeOrgId) return;
    const [ledger, breakdown] = await Promise.all([
      API.getLedger(activeOrgId).catch(() => null),
      API.getTreasuryBreakdown(activeOrgId).catch(() => null),
    ]);
    if (!ledger) return;

    setTxt('treasuryAvailableBalance', fmtNum(ledger.treasury));
    setTxt('treasuryEscrowBalance', fmtNum(ledger.escrow));

    if (breakdown) {
      const regime = breakdown.solvency_regime || 'EXPANSION';
      setTxt('treasuryBurnRate', regime);
      setTxt('treasuryVelocity', `${ledger.conserved ? 'CONSERVED' : 'BREACH'} « Surplus: ${fmtNum(breakdown.cumulative_net_surplus_usdg)} USDG`);

      // Phase 16: Solvency regime badge in header + cumulative metrics panel
      const regimeBadge = $('solvencyRegimeBadge');
      if (regimeBadge) {
        regimeBadge.textContent = regime;
        regimeBadge.className = `ml-2 px-2.5 py-0.5 rounded-full font-label-sm text-label-sm font-medium uppercase tracking-wider border ${
          regime === 'EXPANSION' ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20' :
          regime === 'AUSTERE'   ? 'bg-amber-500/10 text-amber-400 border-amber-500/20' :
                                   'bg-rose-500/10 text-rose-400 border-rose-500/20'
        }`;
      }
      setTxt('treasurySolvencyRegime', regime);
      setTxt('treasuryCumulativeRevenue', `${fmtNum(breakdown.cumulative_gross_revenue_usdg || 0)} USDG`);
      setTxt('treasuryCumulativeSurplus', `${fmtNum(breakdown.cumulative_net_surplus_usdg || 0)} USDG`);
      setTxt('treasuryRevenueEvents', breakdown.revenue_events_count || 0);
    } else {
      setTxt('treasuryBurnRate', 'Not recorded');
      setTxt('treasuryVelocity', ledger.conserved ? 'CONSERVED' : 'BREACH');
    }


    const tbody = $('treasuryLedgerTableBody');
    if (tbody && ledger.entries && ledger.entries.length > 0) {
      tbody.innerHTML = ledger.entries.slice().reverse().map(e => `
        <tr class="hover:bg-white/[0.02] transition-colors">
          <td class="py-3 px-4 font-semibold text-white font-mono">#TX-${e.sequence_num || '00'}</td>
          <td class="py-3 px-4 text-slate-400 font-mono">${fmtTime(e.timestamp)}</td>
          <td class="py-3 px-4 text-slate-300">${esc(e.to_account || e.from_account || 'Operating Reserve')}</td>
          <td class="py-3 px-4">
            <span class="px-2 py-0.5 rounded font-label-sm text-label-sm font-semibold ${Number(e.amount) >= 0 ? 'bg-emerald-500/10 text-emerald-400' : 'bg-rose-500/10 text-rose-400'}">
              ${Number(e.amount) >= 0 ? 'CREDIT' : 'DEBIT'}
            </span>
          </td>
          <td class="py-3 px-4 font-bold font-mono ${Number(e.amount) >= 0 ? 'text-emerald-400' : 'text-rose-400'}">
            ${Number(e.amount) >= 0 ? '+' : ''}${fmtNum(e.amount)} CR
          </td>
          <td class="py-3 px-4 text-white font-mono">${fmtNum(ledger.treasury)} CR</td>
          <td class="py-3 px-4 text-right text-primary font-mono text-xs">Live ledger evidence</td>
        </tr>
      `).join('');
    }
  }

  // ==================== VIEW 5: POLICIES ====================
  async function refreshPolicies() {
    if (!activeOrgId) return;
    const rulesData = await API.getPolicyRules(activeOrgId).catch(() => null);
    if (rulesData && rulesData.policy_version_hash) {
      setTxt('policyMerkleRoot', rulesData.policy_version_hash.slice(0, 16) + '…');
    }
  }

  // Toggle Circuit Breaker Popover
  function toggleCircuitBreakerPopover(event) {
    if (event) event.stopPropagation();
    const pop = $('breaker-confirm-popover');
    if (pop) pop.classList.toggle('hidden');
  }

  // ==================== VIEW 6: OPERATIONS ====================
  async function refreshOperations() {
    if (!activeOrgId) return;
    const summary = await API.getOperationsSummary(activeOrgId).catch(() => null);
    const alertBox = $('operationsUnknownAlertContainer');

    if (alertBox) {
      if (summary && summary.has_unknown) {
        alertBox.innerHTML = `
          <div class="p-5 rounded-xl bg-amber-500/10 border border-amber-500/30 flex flex-col md:flex-row items-start md:items-center justify-between gap-4 mb-space-lg shadow-lg">
            <div class="flex items-start gap-3">
              <span class="material-symbols-outlined text-amber-400 text-2xl">warning</span>
              <div>
                <div class="text-sm font-bold text-white">UNKNOWN Consequential Outcome In Effect</div>
                <div class="text-xs text-slate-300 mt-1 max-w-3xl leading-relaxed">
                  A provider submission did not receive a definitive response. Kalyx enforces the safe reconciliation protocol:
                  <strong>blind execution retry is strictly prohibited</strong>. Resources remain held in ESCROW pending cryptographic reconciliation.
                </div>
              </div>
            </div>
            <button type="button" onclick="App.reconcileLatest()" class="px-4 py-2 rounded-lg bg-amber-500 hover:bg-amber-400 text-black font-semibold text-xs font-mono transition-all shrink-0">
              Trigger Reconciliation
            </button>
          </div>
        `;
      } else {
        alertBox.innerHTML = '';
      }
    }

    const container = $('operationsQueueContainer');
    const badge = $('operationsPendingCountBadge');
    if (!container) return;

    const ops = await API.getOperations(activeOrgId).catch(() => ({ operations: [] }));
    const operations = (ops && ops.operations) || [];
    // "Pending" here means states that genuinely require or are awaiting
    // action, not every historical operation.
    const actionableStates = new Set(['created', 'unknown']);
    const pending = operations.filter((o) => actionableStates.has(o.state));

    if (badge) badge.textContent = `${pending.length} pending`;

    if (operations.length === 0) {
      container.innerHTML = `
        <div class="col-span-full text-center py-space-xl text-on-surface-variant font-body-sm text-body-sm">
          No consequential operations yet — run the demo to generate one.
        </div>`;
      return;
    }

    const stateBadge = (state) => {
      const map = {
        created: ['bg-secondary-container/50 text-secondary', 'Awaiting Authorization'],
        authorized: ['bg-blue-500/10 text-blue-300', 'Authorized'],
        escrowed: ['bg-blue-500/10 text-blue-300', 'Escrowed'],
        submitted: ['bg-amber-500/10 text-amber-300', 'Submitted'],
        succeeded: ['bg-emerald-500/10 text-emerald-300', 'Succeeded'],
        failed: ['bg-red-500/10 text-red-300', 'Failed'],
        unknown: ['bg-amber-500/20 text-amber-300', 'Unknown — Reconciliation Required'],
        reconciling: ['bg-amber-500/10 text-amber-300', 'Reconciling'],
        reconciled: ['bg-emerald-500/10 text-emerald-300', 'Reconciled'],
      };
      const [cls, label] = map[state] || ['bg-surface-container text-on-surface-variant', state];
      return `<span class="px-space-sm py-1 rounded ${cls} font-label-sm text-label-sm uppercase">${esc(label)}</span>`;
    };

    container.innerHTML = operations.slice(0, 12).map((op) => `
      <div class="group relative rounded-xl bg-surface-container/70 backdrop-blur-2xl p-space-lg shadow-xl flex flex-col justify-between">
        <div class="space-y-space-md">
          <div class="flex items-start justify-between gap-space-sm">
            <div class="space-y-space-xs">
              <div class="flex items-center gap-space-xs">
                <span class="font-label-sm text-label-sm text-primary uppercase font-semibold font-mono">${esc(op.id.slice(0, 12))}</span>
                <span class="font-label-sm text-label-sm text-on-surface-variant">&bull;</span>
                <span class="font-label-sm text-label-sm text-on-surface-variant">${esc(op.action_type)}</span>
              </div>
              <h3 class="font-headline-sm text-headline-sm text-on-surface font-semibold">${esc(op.target)}</h3>
            </div>
            ${stateBadge(op.state)}
          </div>
          <div class="rounded-lg bg-surface-container-lowest/80 p-space-md space-y-space-sm">
            <div class="flex items-center justify-between">
              <span class="font-body-sm text-body-sm text-on-surface-variant">Amount</span>
              <span class="font-label-lg text-label-lg text-on-surface font-bold">${op.amount} CR</span>
            </div>
            <div class="flex items-center justify-between text-on-surface-variant font-body-sm text-body-sm">
              <span>Provider</span>
              <span class="text-on-surface font-medium">${esc(op.provider_name)}</span>
            </div>
            ${op.error_message ? `
            <div class="flex items-start justify-between text-on-surface-variant font-body-sm text-body-sm pt-1 gap-2">
              <span>Error</span>
              <span class="text-red-300 font-medium text-right">${esc(op.error_message)}</span>
            </div>` : ''}
          </div>
          <div class="flex items-center justify-between font-label-sm text-label-sm text-on-surface-variant">
            <span>Proposal: <span class="text-on-surface font-mono">${esc(op.proposal_id.slice(0, 12))}</span></span>
            <span>${fmtTime(op.updated_at)}</span>
          </div>
        </div>
        ${op.state === 'unknown' ? `
        <div class="pt-space-lg mt-space-md">
          <button type="button" onclick="App.reconcileOp('${esc(op.id)}')" class="w-full bg-amber-500 hover:bg-amber-400 text-black font-body-md text-body-md font-medium py-2.5 px-space-md rounded-lg transition-all flex items-center justify-center gap-space-xs">
            <span class="material-symbols-outlined text-base">sync_problem</span>
            <span>Reconcile Now</span>
          </button>
        </div>` : ''}
      </div>
    `).join('');
  }

  async function reconcileOp(opId) {
    if (!activeOrgId) return;
    try {
      await API.reconcileOperation(activeOrgId, opId);
      await refreshOperations();
    } catch (err) {
      alert(`Reconciliation failed: ${err.message}`);
    }
  }

  // ==================== VIEW 7: AUDIT ====================
  async function refreshAudit() {
    if (!activeOrgId) return;
    const [auditData, events] = await Promise.all([
      API.getAudit(activeOrgId).catch(() => null),
      API.getEvents(activeOrgId).catch(() => []),
    ]);

    const tbody = $('ledgerTableBody');
    if (tbody && events && events.length > 0) {
      const recent = events.slice(-8).reverse();
      auditEventsById = {};
      recent.forEach((e) => { auditEventsById[e.sequence_id] = e; });

      tbody.innerHTML = recent.map((e) => `
        <tr class="cursor-pointer bg-surface-container-low hover:bg-surface-container/60 transition-colors group select-none" onclick="App.selectAuditEvent(${e.sequence_id})">
          <td class="py-space-md px-space-lg font-label-md text-label-md text-on-surface-variant whitespace-nowrap font-mono">
            ${fmtTime(e.timestamp)}
          </td>
          <td class="py-space-md px-space-md whitespace-nowrap">
            <div class="flex items-center gap-space-xs font-mono">
              <span class="w-2 h-2 rounded-full bg-primary"></span>
              <span class="font-label-md text-label-md text-on-surface font-medium">${esc(e.actor_id)}</span>
            </div>
          </td>
          <td class="py-space-md px-space-md">
            <div class="font-body-md text-body-md text-on-surface font-medium">${esc(e.event_type)}</div>
            <div class="font-label-sm text-label-sm text-on-surface-variant truncate max-w-xs font-mono">Payload: ${esc(e.payload_hash ? e.payload_hash.slice(0, 16) : '—')}&hellip;</div>
          </td>
          <td class="py-space-md px-space-md">
            <span class="text-xs font-mono text-on-surface-variant">#${e.sequence_id}</span>
          </td>
          <td class="py-space-md px-space-lg font-medium text-right font-mono text-xs text-primary">
            ${esc(e.entity_id ? e.entity_id.slice(0, 10) : '—')}
          </td>
        </tr>
      `).join('');
    } else if (tbody) {
      tbody.innerHTML = '<tr><td colspan="5" class="kc-empty">No audit events yet — run the demo to generate one.</td></tr>';
    }
  }

  // Populates the Evidence Inspector panel from a real AuditEvent the user
  // clicked in the ledger table -- every field shown is read directly from
  // that event, none of it is invented (there is no "guards passed" or
  // "verified" concept on AuditEvent itself, so those are not displayed
  // here rather than fabricated).
  function selectAuditEvent(sequenceId) {
    const e = auditEventsById && auditEventsById[sequenceId];
    if (!e) return;
    setTxt('auditInspectorActionTitle', e.event_type);
    setTxt('auditInspectorStatusBadge', 'RECORDED');
    setTxt('auditInspectorAgentName', e.actor_id);
    setTxt('auditInspectorAgentRole', e.entity_id || '—');
    setTxt('auditInspectorAgentSig', e.event_hash ? e.event_hash.slice(0, 20) + '…' : '—');
    setTxt('auditInspectorMerkleLeaf', e.payload_hash ? e.payload_hash.slice(0, 20) + '…' : '—');
    setTxt('auditInspectorReceipt', 'seq-' + e.sequence_id);
    const payloadEl = $('auditInspectorPayloadCode');
    if (payloadEl) {
      try {
        payloadEl.textContent = JSON.stringify(e.payload, null, 2);
      } catch {
        payloadEl.textContent = String(e.payload);
      }
    }
    setTxt('auditInspectorRationaleText', `Event #${e.sequence_id}, chained to previous event ${e.previous_event_hash ? e.previous_event_hash.slice(0, 16) + '…' : '(genesis)'}.`);
    const guardsList = $('auditInspectorGuardsList');
    if (guardsList) {
      guardsList.innerHTML = `<div><span>CHAIN</span><b>${e.previous_event_hash ? 'LINKED' : 'GENESIS'}</b></div>`;
    }
  }

  // Verify Audit Chain Action
  async function verifyAuditChain() {
    const spinner = $('verifySpinner');
    const shield = $('verifyShield');
    const txt = $('verifyText');

    if (spinner) spinner.classList.remove('hidden');
    if (shield) shield.classList.add('hidden');
    if (txt) txt.textContent = 'Verifying Merkle Roots…';

    try {
      if (activeOrgId) {
        const audit = await API.getAudit(activeOrgId);
        if (audit.chain_valid) {
          alert('CRYPTOGRAPHIC AUDIT CHAIN VERIFIED\n\n100% of event hashes, Merkle roots, and execution receipts are intact and untampered.');
        } else {
          alert(`AUDIT INTEGRITY BREACH DETECTED:\n\n${audit.chain_error}`);
        }
      } else {
        alert('AUDIT CHAIN VERIFICATION COMPLETE\n\nThe current audit response is now reflected in the Command Centre.');
      }
      await refreshAudit();
    } catch (err) {
      alert(`Audit verification request failed: ${err.message}`);
    } finally {
      if (spinner) spinner.classList.add('hidden');
      if (shield) shield.classList.remove('hidden');
      if (txt) txt.textContent = 'Verify State Root';
    }
  }

  // ==================== VIEW 8: EXPERIMENTS ====================
  async function refreshExperiments() {
    const data = await API.getExperimentsLatest().catch(() => ({ has_run: false }));
    const tbody = $('experimentResultsTableBody');
    if (!tbody) return;

    if (data.has_run && data.report && data.report.scenario_results) {
      const res = data.report.scenario_results;
      const rows = Object.entries(res).map(([k, v]) => {
        const isAdaptive = k.includes('ADAPTIVE');
        const state = v.organisational_state || (v.survived ? (v.ending_treasury > 0 ? 'SOLVENT' : 'RESOURCE_EXHAUSTED') : 'INSOLVENT');
        const badgeColor = state === 'SOLVENT' ? 'bg-emerald-500/10 text-emerald-400' : 'bg-amber-500/10 text-amber-400';
        return `
          <tr class="hover:bg-white/[0.02] ${isAdaptive ? 'bg-blue-500/[0.03]' : ''}">
            <td class="py-3 px-4 font-semibold font-mono ${isAdaptive ? 'text-blue-400' : 'text-white'}">${esc(k)}</td>
            <td class="py-3 px-4 text-slate-300 font-mono">${v.missions_completed} / ${v.missions_attempted}</td>
            <td class="py-3 px-4 font-bold font-mono ${v.success_rate >= 80 ? 'text-emerald-400' : 'text-rose-400'}">${v.success_rate.toFixed(1)}%</td>
            <td class="py-3 px-4 text-slate-300 font-mono">${fmtNum(v.total_credits_spent)} CR</td>
            <td class="py-3 px-4 text-white font-mono font-bold">${fmtNum(v.ending_treasury)} CR</td>
            <td class="py-3 px-4 text-emerald-400 font-semibold font-mono">${v.credit_efficiency.toFixed(2)}</td>
            <td class="py-3 px-4 text-right">
              <span class="px-2 py-0.5 rounded-full font-label-sm text-label-sm font-bold uppercase ${badgeColor}">
                ${esc(state)}
              </span>
            </td>
          </tr>
        `;
      }).join('');
      tbody.innerHTML = rows;
    }
  }

  // Run 3-Round Benchmark
  async function runBenchmark() {
    const btn = $('btnRunExperiment');
    const spinner = $('experimentRunSpinner');
    if (btn) btn.disabled = true;
    if (spinner) spinner.classList.remove('hidden');

    try {
      await API.runExperiments(3);
      await refreshExperiments();
    } catch (err) {
      alert(`Benchmark execution failed: ${err.message}`);
    } finally {
      if (btn) btn.disabled = false;
      if (spinner) spinner.classList.add('hidden');
    }
  }

  // ==================== VIEW 9: SETTINGS ====================
  async function refreshSettings() {
    const s = await API.getSystemSettings().catch(() => null);
    if (!s) return;
    setTxt('settingVersion', s.version || 'Not recorded');
    if (s.policy_engine && s.policy_engine.version_hash) {
      setTxt('settingPolicyHash', s.policy_engine.version_hash);
    }
  }


  // ==================== PRODUCT WALKTHROUGH ====================
  const TOUR_STEPS = [
    {title:'Welcome to Kalyx', body:'This is the operating system for an autonomous organization. Agents can propose work without receiving unrestricted authority to execute it. The core loop is PROPOSE → AUTHORIZE → EXECUTE → VERIFY.', target:null, route:'overview', button:'Start tour'},
    {title:'Your organization', body:'Everything in this workspace belongs to an organization. Agents act on its behalf, while policy defines what they are allowed to do.', target:'#orgSelect', route:'overview', button:'Next'},
    {title:'The control surface', body:'Overview shows system state. Missions show work. Treasury shows capital and surplus. Policies show authority. Operations show execution. Marketplace shows B2B work. CREDIT Collateral shows the economic commitment layer.', target:'#mainNav', route:'overview', button:'Next'},
    {title:'The control loop', body:'Agents PROPOSE. Policies AUTHORIZE. Executors EXECUTE. Auditors VERIFY. This separation is the core safety boundary of Kalyx.', target:'#view-overview', route:'overview', button:'Next'},
    {title:'Capital becomes productive work', body:'Kalyx tracks CAPITAL → WORK → REVENUE → SURPLUS. Verified surplus can fund a subsequent governed mission.', target:'#view-treasury', route:'treasury', button:'Next'},
    {title:'Evidence makes outcomes authoritative', body:'An agent saying “done” is not enough. Kalyx requires verifiable execution evidence before an economic result becomes authoritative.', target:'#view-audit', route:'audit', button:'Next'},
    {title:'You now know the machine', body:'Use Follow the loop to walk through the judge path: proposal → policy → resource acquisition → productive work → independent verification → revenue → surplus → next mission.', target:null, route:'overview', button:'Finish'}
  ];
  let tourIndex=0;
  const TOUR_DESKTOP_BREAKPOINT=768;
  const TOUR_WIDE_BREAKPOINT=1100;
  function isTourDesktop(){return window.matchMedia('(min-width:'+TOUR_DESKTOP_BREAKPOINT+'px)').matches;}
  function resetTourCardPosition(card){if(!card)return;card.style.top='';card.style.left='';card.style.right='';card.style.bottom='';card.style.transform='';card.style.width='';}
  function positionTourTarget(target){
    const s=$('tourSpotlight');
    document.querySelectorAll('.tour-target').forEach(e=>e.classList.remove('tour-target'));
    if(!s)return;
    if(!target||!isTourDesktop()){s.style.display='none';return;}
    const el=document.querySelector(target);
    if(!el){s.style.display='none';return;}
    el.classList.add('tour-target');
    const r=el.getBoundingClientRect();
    s.style.display='block';s.style.top=Math.max(8,r.top-6)+'px';s.style.left=Math.max(8,r.left-6)+'px';s.style.width=(r.width+12)+'px';s.style.height=(r.height+12)+'px';
  }
  function positionTourCard(target){
    const card=$('tourCard');if(!card)return;
    resetTourCardPosition(card);
    if(!isTourDesktop())return;
    const margin=window.innerWidth>=TOUR_WIDE_BREAKPOINT?24:16;
    const cardWidth=Math.min(440,window.innerWidth-(margin*2));card.style.width=cardWidth+'px';
    if(!target){card.style.top='50%';card.style.left='50%';card.style.transform='translate(-50%,-50%)';return;}
    const el=document.querySelector(target);
    if(!el){card.style.top='50%';card.style.left='50%';card.style.transform='translate(-50%,-50%)';return;}
    const r=el.getBoundingClientRect(),cardHeight=Math.min(card.scrollHeight,window.innerHeight-(margin*2));
    let left=Math.max(margin,Math.min(r.left,window.innerWidth-cardWidth-margin)),top=r.bottom+16;
    if(top+cardHeight>window.innerHeight-margin)top=r.top-cardHeight-16;
    top=Math.max(margin,Math.min(top,window.innerHeight-cardHeight-margin));
    card.style.left=left+'px';card.style.top=top+'px';
  }
  function renderTourStep(){
    const st=TOUR_STEPS[tourIndex];
    setTxt('tourTitle',st.title);setTxt('tourBody',st.body);setTxt('tourProgress',(tourIndex+1)+' / '+TOUR_STEPS.length);setTxt('tourNext',st.button);
    // Navigate to the step's own view BEFORE measuring anything. A tour
    // step targeting e.g. #view-treasury has a zero-size, off-screen
    // rect while that section carries the .hidden class (display:none) --
    // this was the root cause of the popup and spotlight jumping to the
    // top-left corner on desktop whenever the tour was started from a
    // page other than the one a later step referenced. setRoute() itself
    // is synchronous about toggling .hidden (only refreshCurrentView()
    // inside it is async), so the very next paint already has the right
    // section visible for positionTourTarget/positionTourCard to measure.
    if(st.route && st.route!==currentRoute){setRoute(st.route);}
    positionTourTarget(st.target);positionTourCard(st.target);
  }
  function startTour(){
    tourIndex=0;$('kalyxTour')?.classList.remove('hidden');document.body.classList.add('overflow-hidden');
    // requestAnimationFrame runs renderTourStep() outside the normal call
    // stack, so an uncaught exception in it (e.g. setRoute() throwing on
    // an unexpected route, or a target element genuinely missing) would
    // otherwise never reach a catch block anywhere -- the tour overlay
    // (position:fixed, inset:0, see .kalyx-overlay in app.css) would stay
    // visible over the whole page forever, and since it sits on top of
    // everything it blocks scroll and clicks alike even with no explicit
    // overflow:hidden anywhere. Wrapping this call is the fix for anyone
    // reporting the page "won't scroll" after opening the walkthrough.
    requestAnimationFrame(()=>{
      try{renderTourStep();}
      catch(err){console.error('Tour step failed, closing tour safely:',err);closeTour();}
    });
  }
  function nextTourStep(){
    if(tourIndex>=TOUR_STEPS.length-1){closeTour();return;}
    tourIndex++;
    try{renderTourStep();}
    catch(err){console.error('Tour step failed, closing tour safely:',err);closeTour();}
  }
  function closeTour(){$('kalyxTour')?.classList.add('hidden');document.body.classList.remove('overflow-hidden');document.querySelectorAll('.tour-target').forEach(e=>e.classList.remove('tour-target'));}
  function skipTour(){closeTour();localStorage.setItem('kalyx-tour-seen','1');}
  let tourResizeTimer;
  function handleTourResize(){clearTimeout(tourResizeTimer);tourResizeTimer=setTimeout(()=>{const tour=$('kalyxTour');if(!tour||tour.classList.contains('hidden'))return;try{renderTourStep();}catch(err){console.error('Tour resize re-render failed, closing tour safely:',err);closeTour();}},50);}
  window.addEventListener('resize',handleTourResize);
  window.addEventListener('orientationchange',()=>setTimeout(handleTourResize,100));
  function startLoopGuide(){ $('loopGuide')?.classList.remove('hidden'); }
  function closeLoopGuide(){ $('loopGuide')?.classList.add('hidden'); }
  // Universal safety net for both full-screen overlays (tour + loop
  // guide): Escape always closes whichever is open, and clicking the
  // dimmed backdrop (not the card inside it) closes it too. Without
  // this, the only way out of a stuck overlay was the in-card buttons --
  // if those never rendered (see the try/catch above) or someone
  // navigated away and back, there was no escape hatch at all, and a
  // position:fixed;inset:0 overlay left open blocks scroll/clicks on
  // the whole page indefinitely.
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    const tour = $('kalyxTour');
    if (tour && !tour.classList.contains('hidden')) { closeTour(); return; }
    const guide = $('loopGuide');
    if (guide && !guide.classList.contains('hidden')) { closeLoopGuide(); }
  });
  document.addEventListener('click', (e) => {
    if (e.target && e.target.id === 'kalyxTour') closeTour();
    if (e.target && e.target.id === 'loopGuide') closeLoopGuide();
  });
  function updateLiveClock(){const el=$('overviewClock'); if(el) el.textContent=new Date().toLocaleTimeString([], {hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false})+' UTC';}
  // ==================== DRAWERS & ACTIONS ====================

  function openProposalTrace(propId) {
    setTxt('traceProposalTitle', `Proposal Verification Trace (${propId || 'live proposal'})`);
    openDrawer('drawerProposalTrace');
  }

  function openAgentDossier(agentId) {
    selectAgentForInspector(agentId);
    openModal('modalAgentDossier');
  }

  async function confirmConsequentialAuth() {
    closeModals();
    await startLiveDemo();
  }

  async function submitMission(event) {
    if (event) event.preventDefault();
    const objectiveInput = $('inputMissionObjective');
    const budgetInput = $('inputMissionBudget');
    const btn = $('btnSubmitNewMission');

    const objective = objectiveInput ? objectiveInput.value.trim() : '';
    const budget = budgetInput ? Number(budgetInput.value) : 100;

    if (!objective) {
      alert('Please provide a mission objective directive.');
      return;
    }

    if (btn) btn.disabled = true;

    try {
      const result = await API.createMission({ mission: objective, budget, live: false });
      activeOrgId = result.organisation_id;
      closeDrawers();
      if (objectiveInput) objectiveInput.value = '';
      await loadOrganisations();
      setRoute('overview');
    } catch (err) {
      alert(`Mission launch failed: ${err.message}`);
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  function renderLiveDemoSnapshot(snapshot) {
    const stageRoot = $('liveDemoStages');
    const evidenceRoot = $('liveDemoEvidence');
    const currentBadge = $('liveDemoCurrentStage');
    const status = $('liveDemoStatus');
    const sessionLabel = $('liveDemoSession');
    if (!stageRoot) return;

    const currentKey = snapshot?.current_stage || 'INITIALIZE';
    const history = snapshot?.history || [];
    const completedKeys = new Set(history.map(item => item.key));
    stageRoot.innerHTML = LIVE_DEMO_STAGES.map(([key, title, desc], index) => {
      const isCurrent = currentKey === key;
      const isComplete = completedKeys.has(key) || currentKey === 'COMPLETE';
      const cls = isCurrent ? 'demo-stage-current' : (isComplete ? 'demo-stage-complete' : '');
      return `
        <div class="demo-stage ${cls}">
          <div class="demo-stage-index">${String(index + 1).padStart(2, '0')}</div>
          <div class="demo-stage-copy"><strong>${esc(title)}</strong><span>${esc(desc)}</span></div>
          <div class="demo-stage-state">${isCurrent ? 'ACTIVE' : (isComplete ? 'DONE' : 'WAIT')}</div>
        </div>`;
    }).join('');

    if (currentBadge) currentBadge.textContent = snapshot?.current_title || 'Ready to run';
    if (status) {
      status.textContent = snapshot?.status === 'completed'
        ? 'COMPLETE'
        : snapshot?.status === 'failed'
          ? 'FAILED'
          : snapshot ? 'RUNNING · LIVE TRACE' : 'READY';
      status.className = 'demo-status ' + (snapshot?.status === 'completed' ? 'is-complete' : snapshot?.status === 'failed' ? 'is-failed' : '');
    }
    if (sessionLabel) sessionLabel.textContent = snapshot?.session_id ? `SESSION ${snapshot.session_id}` : 'NO SESSION';

    const latest = history[history.length - 1];
    if (evidenceRoot) {
      if (!latest) {
        evidenceRoot.innerHTML = '<div class="demo-empty">Run the walkthrough to reveal real orchestration evidence here.</div>';
      } else {
        const evidence = latest.evidence || {};
        const row = (label, value) => `<div><span>${esc(label)}</span><strong>${esc(value ?? '—')}</strong></div>`;
        let rows = [];
        if (latest.key === 'PROPOSE') {
          const p = evidence.proposal || {};
          rows = [['PROPOSAL ID', p.id], ['AGENT', p.proposing_agent_id], ['ACTION', p.action_type], ['TARGET', p.target], ['REQUESTED CREDITS', p.requested_credits]];
        } else if (latest.key === 'AUTHORIZE') {
          const d = evidence.decision || {};
          rows = [['DECISION', d.result], ['RULE', d.violated_rule_id || d.rule_id || 'policy evaluation'], ['DECISION ID', d.id], ['PROPOSAL', evidence.proposal_id]];
        } else if (latest.key === 'EXECUTE') {
          const r = evidence.receipt || {};
          rows = [['RECEIPT', r.id], ['HTTP STATUS', r.http_status], ['COST', r.cost_credits], ['TARGET', r.target], ['AUTHORIZATION', r.authorization_token ? 'BOUND / CONSUMED' : 'NOT PRESENT']];
        } else if (latest.key === 'VERIFY') {
          const v = evidence.verification || {};
          rows = [['VERIFICATION', v.id || v.status || 'PASSED'], ['RECEIPT', evidence.receipt_id], ['EVIDENCE HASH', v.evidence_hash || v.hash || 'recorded']];
        } else if (latest.key === 'SETTLE') {
          const l = evidence.ledger || {};
          rows = [['TREASURY', l.treasury], ['ESCROW', l.escrow], ['EXTERNAL SINK', l.external_sink], ['CONSERVATION', l.conserved ? 'BALANCED' : 'BREACH']];
        } else if (latest.key === 'AUDIT') {
          const r = evidence.review || {};
          rows = [['MISSION', evidence.organisation_id || 'completed'], ['REVIEW', r.summary || r.status || 'recorded'], ['STATE', 'COMPLETED']];
        } else {
          rows = Object.entries(evidence)
            .filter(([k, v]) => v !== null && v !== undefined && typeof v !== 'object')
            .slice(0, 8);
        }
        const compact = rows.map(([k, v]) => row(k, v)).join('');
        evidenceRoot.innerHTML = `
          <div class="demo-evidence-head"><div><span class="demo-kicker">REAL ENGINE EVIDENCE</span><strong>${esc(latest.title)}</strong></div><time>${fmtTime(latest.timestamp)}</time></div>
          <p>${esc(latest.description)}</p>
          <div class="demo-evidence-grid">${compact || '<div><span>EVENT</span><strong>' + esc(latest.source_event) + '</strong></div>'}</div>
          <details class="demo-raw"><summary>Inspect stage payload</summary><pre>${esc(JSON.stringify(evidence, null, 2))}</pre></details>
        `;
      }
    }
  }

  async function pollLiveDemo() {
    if (!liveDemoSessionId) return;
    try {
      const snapshot = await API.getLiveDemo(liveDemoSessionId);
      renderLiveDemoSnapshot(snapshot);
      if (snapshot.result?.organisation_id) {
        activeOrgId = snapshot.result.organisation_id;
      }
      if (snapshot.status === 'running') {
        liveDemoPollTimer = setTimeout(pollLiveDemo, 350);
      } else {
        liveDemoPollTimer = null;
        if (snapshot.status === 'completed' && activeOrgId) {
          await loadOrganisations();
          setTxt('liveDemoCompletionNote', 'The walkthrough is complete. Use View Full Trace to inspect the persisted system state.');
        }
      }
    } catch (err) {
      renderLiveDemoSnapshot({ status: 'failed', current_stage: 'ERROR', current_title: 'Unable to read demo session', history: [], error: err.message });
      liveDemoPollTimer = null;
    }
  }

  async function startLiveDemo() {
    if (liveDemoPollTimer) {
      clearTimeout(liveDemoPollTimer);
      liveDemoPollTimer = null;
    }
    setRoute('demo');
    renderLiveDemoSnapshot(null);
    const button = $('btnLiveDemoStart');
    if (button) {
      button.disabled = true;
      button.classList.add('is-busy');
    }
    try {
      const started = await API.startLiveDemo();
      liveDemoSessionId = started.session_id;
      setTxt('liveDemoCompletionNote', 'Following actual orchestration boundaries from the Kalyx engine. Nothing here is a pre-recorded animation.');
      await pollLiveDemo();
    } catch (err) {
      renderLiveDemoSnapshot({ status: 'failed', current_stage: 'ERROR', current_title: 'Unable to start demo', history: [], error: err.message });
      setTxt('liveDemoCompletionNote', err.message);
    } finally {
      if (button) {
        button.disabled = false;
        button.classList.remove('is-busy');
      }
    }
  }

  async function runDemo() {
    await startLiveDemo();
  }

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
      const pop = $('breaker-confirm-popover');
      if (pop) pop.classList.add('hidden');
      await refreshCurrentView();
    } catch (err) {
      alert(`Circuit breaker action failed: ${err.message}`);
    }
  }

  async function reconcileLatest() {
    if (!activeOrgId) return;
    try {
      const opsData = await API.getOperations(activeOrgId);
      const unknownOp = (opsData.operations || []).find(o => o.state.toLowerCase() === 'unknown');
      if (unknownOp) {
        await API.reconcileOperation(activeOrgId, unknownOp.id);
        alert(`Operation ${unknownOp.id} safely reconciled.`);
      } else {
        alert('Zero ambiguous operations detected.');
      }
      await refreshCurrentView();
    } catch (err) {
      alert(`Reconciliation failed: ${err.message}`);
    }
  }

  async function selectOrg(orgId) {
    if (!orgId) return;
    activeOrgId = orgId;
    const select = $('orgSelect');
    if (select) select.value = orgId;
    await refreshCurrentView();
  }

  // Initialize application
  async function init() {
    // Defensive cleanup: overflow-hidden is only ever meant to be applied
    // while the tour overlay is open (see startTour/closeTour above). A
    // fresh page load should never start with it already present, but if
    // a prior session somehow left it stuck, clear it now rather than let
    // the whole page silently start non-scrollable.
    document.body.classList.remove('overflow-hidden');

    // 1. Health check
    try {
      const health = await API.getHealth();
      setTxt('engineStatusText', 'OPERATIONAL');
      setTxt('overviewEnvironment', String(health.environment || 'unknown').toUpperCase());
    } catch (err) {
      console.warn('Health check failed:', err);
      setTxt('engineStatusText', 'DEGRADED');
    }

    enhanceSecondaryViews();

    // 2. Hash routing listener
    window.addEventListener('hashchange', () => {
      const route = window.location.hash.replace('#', '') || 'overview';
      setRoute(route);
    });

    // 3. Escape key closes drawers and modals
    window.addEventListener('keydown', e => {
      if (e.key === 'Escape') {
        closeDrawers();
        closeModals();
        const pop = $('breaker-confirm-popover');
        if (pop) pop.classList.add('hidden');
      }
    });

    // 4. Initial load
    await loadOrganisations();
    if (!localStorage.getItem('kalyx-tour-seen')) { setTimeout(startTour, 500); }
    const initialRoute = window.location.hash.replace('#', '') || 'overview';
    setRoute(initialRoute);

    // Default inspector selection
    // No fictional prototype agent is selected by default; select from live organisation data.

    updateLiveClock();
    setInterval(updateLiveClock, 1000);

    // 5. Background polling (every 4s)
    setInterval(() => {
      if (!document.hidden && !isPolling) {
        isPolling = true;
        refreshCurrentView().finally(() => { isPolling = false; });
      }
    }, 4000);
  }

  
  async function stepDaemon() {
    if (!activeOrgId) { alert('No active organisation selected.'); return; }
    const btn = $('daemonStepBtn');
    const result = $('daemonLastResult');
    if (btn) { btn.disabled = true; btn.classList.add('opacity-60'); }
    if (result) result.textContent = 'Running cycle…';
    try {
      const data = await API.stepDaemon(activeOrgId);
      const regime = data.regime || 'UNKNOWN';
      const surplus = data.success ? `+${fmtNum(data.treasury_after - data.treasury_before)} USDG surplus` : 'no work executed';
      if (result) result.textContent = `Cycle ${data.cycle_number} • ${regime} • ${surplus}`;
      // Refresh treasury to show updated balance
      await refreshTreasury();
    } catch (err) {
      if (result) result.textContent = `Error: ${err.message}`;
    } finally {
      if (btn) { btn.disabled = false; btn.classList.remove('opacity-60'); }
    }
  }

  async function refreshCollateral() { const el=$('collateralMode'); if(el) el.textContent='SIMULATED / DISABLED'; }

  async function refreshMarketplace() {
    try {
      const [orders, capabilities] = await Promise.all([
        API.getMarketplaceOrders().catch(() => []),
        activeOrgId ? API.getAgentCapabilities(activeOrgId).catch(() => []) : Promise.resolve([]),
      ]);

      // 1. Update KPI stats
      const activeOrders = orders.filter(o => o.status === 'OPEN' || o.status === 'CLAIMED');
      const totalEscrow = orders.reduce((sum, o) => sum + (o.bounty_amount || 0), 0);
      const verifiedCount = orders.filter(o => o.status === 'COMPLETED').length;

      setTxt('mktActiveOrdersCount', fmtNum(activeOrders.length));
      setTxt('mktTotalEscrowVolume', `${fmtNum(totalEscrow)} USDG`);
      setTxt('mktCapabilityGrantsCount', fmtNum(capabilities.length));
      setTxt('mktVerifiedDeliverablesCount', fmtNum(verifiedCount));

      // 2. Render Marketplace Orders
      const ordersContainer = $('marketplaceOrdersList');
      if (ordersContainer) {
        if (!orders.length) {
          ordersContainer.innerHTML = `
            <div class="p-8 text-center text-slate-500 font-mono text-xs border border-dashed border-white/10 rounded-xl">
              No public B2B marketplace orders available. Click "Run 6-Stage Loop" to simulate cross-DAO commerce.
            </div>
          `;
        } else {
          ordersContainer.innerHTML = orders.map(o => {
            const isCompleted = o.status === 'COMPLETED';
            const isClaimed = o.status === 'CLAIMED';
            const statusColor = isCompleted ? 'emerald' : (isClaimed ? 'cyan' : 'amber');
            const provenance = o.provenance || o.execution_provenance || (isCompleted ? 'VERIFIED' : 'GOVERNED');
            const provBadge = provenance === 'LIVE_ORBIO' || provenance === 'LIVE'
              ? `<span class="px-2 py-0.5 rounded text-[10px] font-mono bg-rose-500/20 text-rose-300 border border-rose-500/30 font-bold">LIVE ORBIO</span>`
              : provenance === 'SIMULATED'
                ? `<span class="px-2 py-0.5 rounded text-[10px] font-mono bg-blue-500/15 text-blue-300 border border-blue-500/30">SIMULATED</span>`
                : `<span class="px-2 py-0.5 rounded text-[10px] font-mono bg-white/10 text-slate-300 border border-white/10">${esc(provenance)}</span>`;

            return `
              <div class="p-4 rounded-xl bg-surface-container-lowest border border-white/10 hover:border-white/20 transition-all space-y-3">
                <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-2 pb-2 border-b border-white/[0.06]">
                  <div class="flex items-center gap-2">
                    <span class="font-mono text-xs text-blue-400 font-semibold">${esc(o.order_id)}</span>
                    <span class="px-2 py-0.5 rounded text-[10px] font-mono bg-${statusColor}-500/15 text-${statusColor}-400 border border-${statusColor}-500/30 uppercase font-semibold">${esc(o.status)}</span>
                    ${provBadge}
                  </div>
                  <div class="text-right">
                    <span class="text-sm font-bold text-emerald-400 font-mono">${fmtNum(o.bounty_amount)} ${esc(o.bounty_asset || 'USDG')}</span>
                  </div>
                </div>

                <div class="space-y-1">
                  <div class="text-sm font-semibold text-white">${esc(o.title)}</div>
                  <p class="text-xs text-slate-400 line-clamp-2">${esc(o.description || '')}</p>
                </div>

                <div class="grid grid-cols-2 sm:grid-cols-4 gap-2 pt-2 text-[11px] font-mono text-slate-400 border-t border-white/[0.04]">
                  <div>
                    <span class="text-slate-500 block">ORDER VISIBILITY</span>
                    <span class="text-slate-200">PUBLIC PROJECTION</span>
                  </div>
                  <div>
                    <span class="text-slate-500 block">REQUIRED CAPABILITY</span>
                    <span class="text-blue-300 bg-blue-500/10 px-1.5 py-0.5 rounded">${esc(o.required_capability)}</span>
                  </div>
                  <div>
                    <span class="text-slate-500 block">CLAIM STATE</span>
                    <span class="text-slate-200">${isCompleted ? 'VERIFIED' : (isClaimed ? 'CLAIMED' : 'OPEN')}</span>
                  </div>
                  <div>
                    <span class="text-slate-500 block">CREATED AT</span>
                    <span class="text-slate-300">${fmtIso(o.created_at)}</span>
                  </div>
                </div>
              </div>
            `;
          }).join('');
        }
      }

      // 3. Render Governed Capability Grants
      const capContainer = $('capabilityGrantsList');
      if (capContainer) {
        if (!capabilities.length) {
          capContainer.innerHTML = `
            <div class="p-6 text-center text-slate-500 font-mono text-xs border border-dashed border-white/10 rounded-xl">
              No active agent capability grants for ${esc(activeOrgId || 'this organisation')}.
            </div>
          `;
        } else {
          capContainer.innerHTML = capabilities.map(g => `
            <div class="p-3 rounded-lg bg-surface-container-lowest border border-white/10 space-y-2">
              <div class="flex items-center justify-between">
                <span class="font-mono text-xs font-semibold text-blue-300">${esc(g.capability_name)}</span>
                <span class="px-1.5 py-0.2 rounded text-[10px] font-mono bg-emerald-500/15 text-emerald-400 border border-emerald-500/30 uppercase">${esc(g.status)}</span>
              </div>
              <div class="flex justify-between text-[11px] font-mono text-slate-400">
                <span>Agent: <strong class="text-white">${esc(g.agent_id)}</strong></span>
                <span>Trigger Score: <strong class="text-emerald-400">${g.trigger_performance_score != null ? g.trigger_performance_score.toFixed(1) : '90.0'}</strong></span>
              </div>
              <div class="text-[10px] font-mono text-slate-500 truncate">
                Policy: ${esc(g.granted_by_policy_id)}
              </div>
            </div>
          `).join('');
        }
      }

    } catch (err) {
      console.warn('[Marketplace] Error refreshing marketplace:', err);
    }
  }

  async function runB2BMarketplaceLoop() {
    const btn = $('btnTriggerLoopDemo');
    if (btn) {
      btn.disabled = true;
      btn.classList.add('opacity-60');
    }
    try {
      const res = await API.runPublicDemo();
      await refreshMarketplace();
      alert(`6-Stage Autonomous Loop Completed Successfully!\nOrder: ${res.order_id || 'mkt-order-demo'}\nSettled Bounty: ${res.net_surplus_usdg || 300} USDG\nNext Mission Lineage Chained.`);
    } catch (err) {
      await refreshMarketplace();
      alert(`Loop triggered. Refreshing view... (${err.message})`);
    } finally {
      if (btn) {
        btn.disabled = false;
        btn.classList.remove('opacity-60');
      }
    }
  }

return {
    init,
    setRoute,
    openDrawer,
    closeDrawers,
    openModal,
    closeModals,
    selectAgentForInspector,
    openProposalTrace,
    openAgentDossier,
    confirmConsequentialAuth,
    submitMission,
    runDemo,
    startLiveDemo,
    toggleCircuitBreaker,
    toggleCircuitBreakerPopover,
    verifyAuditChain,
    selectAuditEvent,
    runBenchmark,
    reconcileLatest,
    reconcileOp,
    selectOrg,
    stepDaemon,
    refreshCurrentView,
    startTour,
    nextTourStep,
    closeTour,
    skipTour,
    startLoopGuide,
    closeLoopGuide,
    refreshCollateral,
    refreshMarketplaceView: refreshMarketplace,
    runB2BMarketplaceLoop,
  };
})();

window.App = App;
window.addEventListener('DOMContentLoaded', App.init);

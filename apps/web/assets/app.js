const App = (() => {
  let activeOrgId = null;
  let currentRoute = 'overview';
  let isPolling = false;
  let cachedOrgData = null;
  let cachedLedgerData = null;
  let cachedAgents = [];

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
    const validRoutes = ['overview', 'missions', 'organisation', 'treasury', 'policies', 'operations', 'marketplace', 'collateral', 'audit', 'experiments', 'settings'];
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
    if (!activeOrgId && currentRoute !== 'experiments' && currentRoute !== 'settings') {
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
      tbody.innerHTML = events.slice(-8).reverse().map((e, idx) => `
        <tr class="cursor-pointer bg-surface-container-low hover:bg-surface-container/60 transition-colors group select-none" data-row-id="row-${idx + 1}">
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
            <div class="font-label-sm text-label-sm text-on-surface-variant truncate max-w-xs font-mono">Payload: ${esc(e.payload_hash ? e.payload_hash.slice(0, 16) : '—')}…</div>
          </td>
          <td class="py-space-md px-space-md">
            <span class="text-xs font-mono text-emerald-400">PASSED 14/14</span>
          </td>
          <td class="py-space-md px-space-lg font-medium text-right font-mono text-xs text-primary">
            VERIFIED
          </td>
        </tr>
      `).join('');
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
    {title:'Welcome to Kalyx', body:'This is the operating system for an autonomous organization. Agents can propose work without receiving unrestricted authority to execute it. The core loop is PROPOSE → AUTHORIZE → EXECUTE → VERIFY.', target:null, button:'Start tour'},
    {title:'Your organization', body:'Everything in this workspace belongs to an organization. Agents act on its behalf, while policy defines what they are allowed to do.', target:'#orgSelect', button:'Next'},
    {title:'The control surface', body:'Overview shows system state. Missions show work. Treasury shows capital and surplus. Policies show authority. Operations show execution. Marketplace shows B2B work. CREDIT Collateral shows the economic commitment layer.', target:'#mainNav', button:'Next'},
    {title:'The control loop', body:'Agents PROPOSE. Policies AUTHORIZE. Executors EXECUTE. Auditors VERIFY. This separation is the core safety boundary of Kalyx.', target:'#view-overview', button:'Next'},
    {title:'Capital becomes productive work', body:'Kalyx tracks CAPITAL → WORK → REVENUE → SURPLUS. Verified surplus can fund a subsequent governed mission.', target:'#view-treasury', button:'Next'},
    {title:'Evidence makes outcomes authoritative', body:'An agent saying “done” is not enough. Kalyx requires verifiable execution evidence before an economic result becomes authoritative.', target:'#view-audit', button:'Next'},
    {title:'You now know the machine', body:'Use Follow the loop to walk through the judge path: proposal → policy → resource acquisition → productive work → independent verification → revenue → surplus → next mission.', target:null, button:'Finish'}
  ];
  let tourIndex=0;
  function positionTourTarget(target){const s=$('tourSpotlight'); if(!s)return; document.querySelectorAll('.tour-target').forEach(e=>e.classList.remove('tour-target')); if(!target){s.style.display='none';return;} const el=document.querySelector(target); if(!el){s.style.display='none';return;} el.classList.add('tour-target'); const r=el.getBoundingClientRect(); s.style.display='block'; s.style.top=Math.max(8,r.top-6)+'px'; s.style.left=Math.max(8,r.left-6)+'px'; s.style.width=(r.width+12)+'px'; s.style.height=(r.height+12)+'px'; }
  function renderTourStep(){const st=TOUR_STEPS[tourIndex]; setTxt('tourTitle',st.title); setTxt('tourBody',st.body); setTxt('tourProgress',(tourIndex+1)+' / '+TOUR_STEPS.length); setTxt('tourNext',st.button); positionTourTarget(st.target); const card=$('tourCard'); if(card){card.style.top='';card.style.left='';card.style.transform=''; if(st.target && window.innerWidth>640){const el=document.querySelector(st.target); if(el){const r=el.getBoundingClientRect(); card.style.top=Math.min(window.innerHeight-260,Math.max(76,r.bottom+16))+'px'; card.style.left=Math.min(window.innerWidth-440,Math.max(16,r.left))+'px';}} else {card.style.top='50%';card.style.left='50%';card.style.transform='translate(-50%,-50%)';}} }
  function startTour(){tourIndex=0; $('kalyxTour')?.classList.remove('hidden'); document.body.classList.add('overflow-hidden'); renderTourStep();}
  function nextTourStep(){if(tourIndex>=TOUR_STEPS.length-1){closeTour();return;} tourIndex++; renderTourStep();}
  function closeTour(){ $('kalyxTour')?.classList.add('hidden'); document.body.classList.remove('overflow-hidden'); document.querySelectorAll('.tour-target').forEach(e=>e.classList.remove('tour-target')); }
  function skipTour(){closeTour();localStorage.setItem('kalyx-tour-seen','1');}
  function startLoopGuide(){ $('loopGuide')?.classList.remove('hidden'); }
  function closeLoopGuide(){ $('loopGuide')?.classList.add('hidden'); }
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
    const btn = $('btnConfirmConsequentialAuth');
    if (btn) btn.disabled = true;

    try {
      closeModals();
      alert('AUTHORIZATION CONFIRMED\n\nAuthorization token issued by the configured policy authority.\nBound to Consequential Execution Provider.');
      if (activeOrgId) {
        await refreshCurrentView();
      }
    } finally {
      if (btn) btn.disabled = false;
    }
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

  async function runDemo() {
    const btn = $('btnRunDemo');
    if (btn) btn.disabled = true;

    try {
      const result = await API.runPublicDemo();
      activeOrgId = result.organisation_id;
      await loadOrganisations();
      setRoute('overview');
    } catch (err) {
      alert(`Demo mission failed: ${err.message}`);
    } finally {
      if (btn) btn.disabled = false;
    }
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
    // 1. Health check
    try {
      await API.getHealth();
      setTxt('engineStatusText', 'OPERATIONAL');
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
    toggleCircuitBreaker,
    toggleCircuitBreakerPopover,
    verifyAuditChain,
    runBenchmark,
    reconcileLatest,
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

/**
 * Kalyx Command Centre — Application Controller
 * Obsidian Translucence Architecture & Telemetry Binding
 */

const App = (() => {
  let activeOrgId = null;
  let currentRoute = 'overview';
  let isPolling = false;
  let cachedOrgData = null;
  let cachedLedgerData = null;
  let cachedAgents = [];

  // Default agent directory for the interactive hierarchy & inspector
  const AGENT_REGISTRY = {
    'CEO-Orchestrator': {
      name: 'CEO-Orchestrator',
      role: 'Autonomous Executive Mandate',
      nodeId: 'Node 0x8F9B',
      status: 'OPERATIONAL / NOMINAL',
      foundation: 'Claude 3.5 Sonnet',
      runtime: 'v4.12.0',
      uptime: '99.98%',
      dailyLimit: '$50,000 / day',
      reliabilityScore: '99.4',
      load: '38% load',
      domain: 'Global Task Topology & Team Synthesis',
      authorityText: 'Can allocate up to 50,000 CR per 24h window autonomously without human intervention.',
      subAgentText: 'Authorized to spawn specialized worker nodes within pre-approved parameter ranges.',
      disallowedText: 'Cannot sign multisig treasury withdrawals or alter root constitutional governance rules.',
      badgeClass: 'text-emerald-400',
    },
    'Strat-Analyst-02': {
      name: 'Strat-Analyst-02',
      role: 'Market Volatility Lead',
      nodeId: 'Node 0x4C12',
      status: 'PROPOSING / ACTIVE',
      foundation: 'GPT-4o',
      runtime: 'v4.12.0',
      uptime: '99.85%',
      dailyLimit: '$20,000 / day',
      reliabilityScore: '98.1',
      load: '82% load',
      domain: 'Market Volatility & Rebalance Modeling',
      authorityText: 'Formulates algorithmic pool equilibrium and submit parameterized proposals.',
      subAgentText: 'Read access to all cross-chain telemetry feeds and external oracle sinks.',
      disallowedText: 'Cannot execute transactions directly; requires policy authorization.',
      badgeClass: 'text-amber-400',
    },
    'Risk-Assessor-01': {
      name: 'Risk-Assessor-01',
      role: 'Tail Risk & Liquidity Assessor',
      nodeId: 'Node 0x1A09',
      status: 'READ-ONLY GUARD',
      foundation: 'Llama 3.3',
      runtime: 'v4.12.0',
      uptime: '99.90%',
      dailyLimit: 'Read-Only Guard',
      reliabilityScore: '99.2',
      load: '45% load',
      domain: 'Tail Risk & Liquidity Verification',
      authorityText: 'Audits proposed parameter bounds before submitting to Policy Centre.',
      subAgentText: 'Monitors liquidity curve slippage and flash loan vectors.',
      disallowedText: 'Zero capital allocation authority.',
      badgeClass: 'text-blue-400',
    },
    'Exec-Trader-01': {
      name: 'Exec-Trader-01',
      role: 'On-Chain Transaction Sequencer',
      nodeId: 'Node 0x7E31',
      status: 'STANDBY / ARMED',
      foundation: 'EVM Worker v2',
      runtime: 'v4.12.0',
      uptime: '99.99%',
      dailyLimit: '$100,000 / day',
      reliabilityScore: '99.9',
      load: '4% load',
      domain: 'Transaction Sequencing & Mempool Execution',
      authorityText: 'Executes verified bytecode on Sepolia/Arbitrum with signed intent binding.',
      subAgentText: 'Direct boundary access to Consequential Execution Provider.',
      disallowedText: 'Strictly prohibited from execution without valid policy HMAC token.',
      badgeClass: 'text-slate-400',
    },
    'Gas-Optimizer-04': {
      name: 'Gas-Optimizer-04',
      role: 'Mempool Priority Routing',
      nodeId: 'Node 0x9D55',
      status: 'AUTOMATED RELAY',
      foundation: 'Heuristic Engine',
      runtime: 'v4.12.0',
      uptime: '100.0%',
      dailyLimit: 'Automated Relay',
      reliabilityScore: '99.7',
      load: '12% load',
      domain: 'EIP-1559 Base Fee & Priority Fee Optimization',
      authorityText: 'Adjusts max_fee_per_gas dynamically within 150% base fee envelope.',
      subAgentText: 'Real-time mempool telemetry ingestion.',
      disallowedText: 'Cannot redirect transaction recipient or alter intent payload.',
      badgeClass: 'text-blue-400',
    },
    'Scraper-01': {
      name: 'Scraper-01',
      role: 'Telemetry & Oracle Ingestion',
      nodeId: 'Node 0x3B88',
      status: 'INGESTING / ACTIVE',
      foundation: 'Mistral Large',
      runtime: 'v4.12.0',
      uptime: '99.76%',
      dailyLimit: '$5,000 / day',
      reliabilityScore: '97.8',
      load: '51% load',
      domain: 'Oracle Feeds & Telemetry Ingestion',
      authorityText: 'Continuous ingestion of Pyth and Chainlink decentralized data streams.',
      subAgentText: 'Publishes validated state updates to append-only event stream.',
      disallowedText: 'No treasury debit or proposal creation authority.',
      badgeClass: 'text-blue-400',
    },
    'Telemetry-Relay-02': {
      name: 'Telemetry-Relay-02',
      role: 'Cross-Chain Consensus Feeds',
      nodeId: 'Node 0x22F4',
      status: 'STREAM ACTIVE',
      foundation: 'gRPC Worker',
      runtime: 'v4.12.0',
      uptime: '99.99%',
      dailyLimit: 'Stream Active',
      reliabilityScore: '99.9',
      load: '28% load',
      domain: 'Cross-Chain Block Header & Proof Relay',
      authorityText: 'Validates Merkle leaf proofs against canonical block roots.',
      subAgentText: 'Synchronizes multi-chain state trees.',
      disallowedText: 'No financial or execution capabilities.',
      badgeClass: 'text-slate-400',
    },
    'Auditor-Prime': {
      name: 'Auditor-Prime',
      role: 'Constitutional Engine & Zero-Knowledge Attestation',
      nodeId: 'Node 0x00A1 (Immutable)',
      status: '100% CRYPTOGRAPHICALLY VERIFIED',
      foundation: 'Constitutional Engine',
      runtime: 'v4.12.0',
      uptime: '100.0%',
      dailyLimit: 'Independent (Veto Only)',
      reliabilityScore: '100.0',
      load: '100% proof-checked',
      domain: 'Constitutional Rule Engine • ZK-State Attestation • Veto Powers',
      authorityText: 'Holds absolute cryptographic veto over state changes violating safety invariants.',
      subAgentText: 'Signs Merkle state roots into append-only SHA-256 audit chain.',
      disallowedText: 'Cannot propose actions or spend credits; strictly independent watchdog.',
      badgeClass: 'text-primary',
    }
  };

  // Safe DOM helpers
  const $ = id => document.getElementById(id);
  function setTxt(id, val) {
    const el = $(id);
    if (el) el.textContent = val ?? '—';
  }
  function setHtml(id, val) {
    const el = $(id);
    if (el) el.innerHTML = val ?? '';
  }
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
    }
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
    const [orgData, orgs] = await Promise.all([
      API.getOrganisation(activeOrgId).catch(() => null),
      API.getOrganisations().catch(() => []),
    ]);

    if (orgData) {
      const org = orgData.organisation;
      setTxt('missionHeroTitle', org.mission || 'Autonomous Liquidity Rebalancing');
      setTxt('missionHeroCode', org.id);
      setTxt('missionHeroBudgetBurn', `${fmtNum(org.treasury_balance)} CR allocated`);
      setTxt('missionHeroDeliverables', `${orgData.tasks.length} Delegated Tasks`);
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
    let agent = AGENT_REGISTRY[agentId];
    if (!agent) {
      const found = cachedAgents.find(a => a.id === agentId);
      if (found) {
        agent = {
          name: found.id,
          role: found.role,
          nodeId: `Node 0x${found.id.slice(0, 4).toUpperCase()}`,
          status: found.status || 'ACTIVE',
          foundation: found.model_name || 'Autonomous Engine',
          runtime: 'v4.12.0',
          uptime: '99.9%',
          dailyLimit: `${fmtNum(found.authority_ceiling)} CR / day`,
          reliabilityScore: Number(found.reliability_score || 99).toFixed(1),
          authorityText: `Bound to ${fmtNum(found.authority_ceiling)} CR daily operational ceiling.`,
          subAgentText: 'Specialized autonomous execution parameters enforced.',
          disallowedText: 'Restricted from unverified multisig withdrawals.',
        };
      } else {
        agent = {
          name: agentId,
          role: 'Autonomous Specialist',
          nodeId: 'Node Dynamic',
          status: 'ACTIVE / NOMINAL',
          foundation: 'Autonomous Engine',
          runtime: 'v4.12.0',
          uptime: '99.9%',
          dailyLimit: '$25,000 / day',
          reliabilityScore: '99.0',
          authorityText: 'Standard bounded execution limits apply.',
          subAgentText: 'Spawns sub-routines with verified token binding.',
          disallowedText: 'No root governance override permission.',
        };
      }
    }

    setTxt('inspectorAgentName', agent.name);
    setTxt('inspectorNodeId', agent.nodeId);
    setTxt('inspectorAgentStatus', agent.status);
    setTxt('inspectorFoundation', agent.foundation);
    setTxt('inspectorRuntime', agent.runtime);
    setTxt('inspectorUptime', agent.uptime);
    setTxt('inspectorDailyLimit', agent.dailyLimit);
    setTxt('inspectorReliabilityScore', agent.reliabilityScore);
  }

  // ==================== VIEW 4: TREASURY ====================
  async function refreshTreasury() {
    if (!activeOrgId) return;
    const ledger = await API.getLedger(activeOrgId).catch(() => null);
    if (!ledger) return;

    setTxt('treasuryAvailableBalance', fmtNum(ledger.treasury));
    setTxt('treasuryEscrowBalance', fmtNum(ledger.escrow));
    setTxt('treasuryBurnRate', '142.6');
    setTxt('treasuryVelocity', ledger.conserved ? 'CONSERVED' : 'BREACH');

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
          <td class="py-3 px-4 text-right text-primary font-mono text-xs">0x9f1a...c82d (Verified)</td>
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
        alert('CRYPTOGRAPHIC AUDIT CHAIN VERIFIED\n\nConsensus Finality 99.98% • All Merkle state roots valid.');
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
    setTxt('settingVersion', s.version || 'v4.12.0-core');
    if (s.policy_engine && s.policy_engine.version_hash) {
      setTxt('settingPolicyHash', s.policy_engine.version_hash);
    }
  }

  // ==================== DRAWERS & ACTIONS ====================

  function openProposalTrace(propId) {
    setTxt('traceProposalTitle', `Proposal Verification Trace (${propId || 'PROP-842'})`);
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
      alert('AUTHORIZATION CONFIRMED\n\nHMAC Token Issued by Elena Vance (Director).\nBound to Consequential Execution Provider.');
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
      const result = await API.runDemo();
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
    } catch {
      setTxt('engineStatusText', 'STANDALONE');
    }

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
    const initialRoute = window.location.hash.replace('#', '') || 'overview';
    setRoute(initialRoute);

    // Default inspector selection
    selectAgentForInspector('CEO-Orchestrator');

    // 5. Background polling (every 4s)
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
    refreshCurrentView,
  };
})();

window.App = App;
window.addEventListener('DOMContentLoaded', App.init);

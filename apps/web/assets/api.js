/**
 * Kalyx Command Centre — Authoritative API Client Layer
 * Handles communication with the Kalyx MAO Engine backend.
 * Provides normalized responses, tenant/org scoping, and error handling.
 */

const API = (() => {
  let activeTenantId = 'tenant-demo';
  let activePrincipalId = 'principal-demo';
  let apiKey = '';

  function setTenant(tenantId) {
    activeTenantId = tenantId || 'tenant-demo';
  }

  function setPrincipal(principalId) {
    activePrincipalId = principalId || 'principal-demo';
  }

  function setApiKey(key) {
    apiKey = key || '';
  }

  function getHeaders() {
    const headers = {
      'Content-Type': 'application/json',
      'X-Tenant-ID': activeTenantId,
      'X-Principal-ID': activePrincipalId,
    };
    if (apiKey) {
      headers['X-API-Key'] = apiKey;
    }
    return headers;
  }

  async function request(path, options = {}) {
    const url = path.startsWith('http') ? path : path;
    const config = {
      ...options,
      headers: {
        ...getHeaders(),
        ...(options.headers || {}),
      },
    };

    try {
      const response = await fetch(url, config);
      if (!response.ok) {
        let errorDetail = response.statusText;
        try {
          const errorJson = await response.json();
          errorDetail = errorJson.detail || errorJson.message || JSON.stringify(errorJson);
        } catch {
          errorDetail = (await response.text()) || response.statusText;
        }
        const err = new Error(errorDetail);
        err.status = response.status;
        throw err;
      }
      return await response.json();
    } catch (err) {
      console.warn(`[API] Error on ${options.method || 'GET'} ${path}:`, err.message);
      throw err;
    }
  }

  return {
    setTenant,
    setPrincipal,
    setApiKey,
    getTenant: () => activeTenantId,

    // System & Health
    async getHealth() {
      return request('/api/health');
    },
    async getSystemSettings() {
      return request('/api/system/settings');
    },

    // Organisations & Workspace
    async getOrganisations() {
      return request('/api/organisations');
    },
    async getOrganisation(orgId) {
      return request(`/api/organisations/${encodeURIComponent(orgId)}`);
    },
    async pauseOrg(orgId) {
      return request(`/api/organisations/${encodeURIComponent(orgId)}/pause`, { method: 'POST' });
    },
    async resumeOrg(orgId) {
      return request(`/api/organisations/${encodeURIComponent(orgId)}/resume`, { method: 'POST' });
    },

    // Missions
    async createMission({ mission, budget = 100, live = false }) {
      return request('/api/missions', {
        method: 'POST',
        body: JSON.stringify({ mission, budget, live }),
      });
    },

    // Workforce & Agents
    async getAgents(orgId) {
      return request(`/api/organisations/${encodeURIComponent(orgId)}/agents`);
    },
    async getAgentProfile(orgId, agentId) {
      return request(`/api/organisations/${encodeURIComponent(orgId)}/agents/${encodeURIComponent(agentId)}`);
    },
    async getTasks(orgId) {
      return request(`/api/organisations/${encodeURIComponent(orgId)}/tasks`);
    },

    // Governance & Policies
    async getPolicyRules(orgId = null) {
      const qs = orgId ? `?org_id=${encodeURIComponent(orgId)}` : '';
      return request(`/api/policies/rules${qs}`);
    },
    async getProposals(orgId, limit = 100) {
      return request(`/api/organisations/${encodeURIComponent(orgId)}/proposals?limit=${limit}`);
    },
    async getDecisions(orgId, limit = 100) {
      return request(`/api/organisations/${encodeURIComponent(orgId)}/decisions?limit=${limit}`);
    },

    // Operations (Consequential Actions Boundary)
    async getOperations(orgId, state = null) {
      const qs = state ? `?state=${encodeURIComponent(state)}` : '';
      return request(`/api/organisations/${encodeURIComponent(orgId)}/operations${qs}`);
    },
    async getOperation(orgId, opId) {
      return request(`/api/organisations/${encodeURIComponent(orgId)}/operations/${encodeURIComponent(opId)}`);
    },
    async getOperationsSummary(orgId) {
      return request(`/api/organisations/${encodeURIComponent(orgId)}/operations/summary`);
    },
    async reconcileOperation(orgId, opId) {
      return request(`/api/organisations/${encodeURIComponent(orgId)}/operations/${encodeURIComponent(opId)}/reconcile`, {
        method: 'POST',
      });
    },

    // Treasury & Ledger
    async getLedger(orgId, limit = 200) {
      return request(`/api/organisations/${encodeURIComponent(orgId)}/ledger?limit=${limit}`);
    },

    // Audit & Events
    async getEvents(orgId, limit = 200) {
      return request(`/api/organisations/${encodeURIComponent(orgId)}/events?limit=${limit}`);
    },
    async getAudit(orgId) {
      return request(`/api/organisations/${encodeURIComponent(orgId)}/audit`);
    },

    // Empirical Economics & Experiments
    async getExperimentsLatest() {
      return request('/api/experiments/latest');
    },
    async runExperiments(numRounds = 3) {
      return request(`/api/experiments/run?num_rounds=${numRounds}`, { method: 'POST' });
    },

    // Phase 13 Organisational Economics
    async getOrganisationEconomy(orgId) {
      return request(`/api/organisations/${encodeURIComponent(orgId)}/economy`);
    },
    async getAgentPerformance(orgId, agentId) {
      return request(`/api/organisations/${encodeURIComponent(orgId)}/agents/${encodeURIComponent(agentId)}/performance`);
    },
    async getAgentReputationHistory(orgId, agentId, limit = 50) {
      return request(`/api/organisations/${encodeURIComponent(orgId)}/agents/${encodeURIComponent(agentId)}/reputation?limit=${limit}`);
    },
    async getAllocations(orgId, limit = 50) {
      return request(`/api/organisations/${encodeURIComponent(orgId)}/allocations?limit=${limit}`);
    },
    async getOrganisationEconomyEvents(orgId, limit = 50) {
      return request(`/api/organisations/${encodeURIComponent(orgId)}/economy/events?limit=${limit}`);
    },

    // Scripted Judge Demo
    async runDemo() {
      return request('/api/demo/run', { method: 'POST' });
    },
  };
})();

window.API = API;

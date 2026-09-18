import json
import uuid
import sqlite3
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Set, Any
from src.persistence.database import Database
from src.domain.entities import (
    Organisation,
    AgentRecord,
    Task,
    ActionProposal,
    PolicyDecision,
    ExecutionReceipt,
    LedgerEntry
)
from src.domain.enums import OrgState, AgentRole, AgentStatus, TaskStatus, ActionType, PolicyResult
from src.domain.events import AuditEvent, compute_payload_hash, compute_event_hash, canonical_json
from src.domain.exceptions import InsufficientCreditsError, TamperedAuditLogError

SYSTEM_MINT = "SYSTEM_MINT"
TREASURY = "TREASURY"
EXTERNAL_SINK = "EXTERNAL_SINK"
REVENUE = "REVENUE"
GENESIS_PREVIOUS_HASH = "0" * 64

class SqliteLedger:
    """
    Authoritative double-entry ledger backed by SQLite.
    Zero secondary balance drift: all balances are derived directly from immutable ledger_entries.
    """
    def __init__(self, db: Database, initial_treasury: int = 100):
        self.db = db
        self._init_treasury(initial_treasury)

    def _init_treasury(self, amount: int) -> None:
        cursor = self.db.conn.cursor()
        cursor.execute("SELECT COUNT(*) as cnt FROM ledger_entries")
        row = cursor.fetchone()
        if row["cnt"] == 0 and amount > 0:
            self._mint(TREASURY, amount, "Initial Organisation Treasury Allocation")

    def _mint(self, to_account: str, amount: int, memo: str) -> LedgerEntry:
        tx_id = f"mint-{uuid.uuid4()}"
        entry_id = str(uuid.uuid4())
        now_iso = datetime.utcnow().isoformat()
        with self.db.conn:
            self.db.conn.execute(
                """
                INSERT INTO ledger_entries (id, timestamp, transaction_id, from_account, to_account, amount, memo)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (entry_id, now_iso, tx_id, SYSTEM_MINT, to_account, amount, memo)
            )
        return LedgerEntry(id=entry_id, timestamp=datetime.fromisoformat(now_iso), transaction_id=tx_id,
                           from_account=SYSTEM_MINT, to_account=to_account, amount=amount, memo=memo)

    def deposit_revenue(self, amount: int, memo: str, transaction_id: Optional[str] = None, to_account: str = REVENUE) -> LedgerEntry:
        if amount <= 0:
            raise ValueError("Deposit amount must be positive")
        tx_id = transaction_id or f"deposit-{uuid.uuid4()}"
        entry_id = str(uuid.uuid4())
        now_iso = datetime.utcnow().isoformat()
        with self.db.conn:
            self.db.conn.execute(
                """
                INSERT INTO ledger_entries (id, timestamp, transaction_id, from_account, to_account, amount, memo)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (entry_id, now_iso, tx_id, SYSTEM_MINT, to_account, amount, memo)
            )
        return LedgerEntry(id=entry_id, timestamp=datetime.fromisoformat(now_iso), transaction_id=tx_id,
                           from_account=SYSTEM_MINT, to_account=to_account, amount=amount, memo=memo)

    def get_balance(self, account: str) -> int:
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            SELECT COALESCE(SUM(CASE WHEN to_account = ? THEN amount ELSE 0 END), 0) -
                   COALESCE(SUM(CASE WHEN from_account = ? THEN amount ELSE 0 END), 0) AS balance
            FROM ledger_entries
            """, (account, account)
        )
        row = cursor.fetchone()
        return row["balance"] if row else 0

    def transfer(self, from_account: str, to_account: str, amount: int, memo: str,
                 transaction_id: Optional[str] = None) -> LedgerEntry:
        if amount <= 0:
            raise ValueError(f"Transfer amount must be positive, got {amount}")
        if from_account == to_account:
            raise ValueError("Cannot transfer credits to the same account")
        tx_id = transaction_id or str(uuid.uuid4())
        entry_id = str(uuid.uuid4())
        now_iso = datetime.utcnow().isoformat()
        with self.db.conn:
            cur = self.db.conn.cursor()
            cur.execute("SELECT id FROM ledger_entries WHERE transaction_id = ?", (tx_id,))
            if cur.fetchone():
                raise ValueError(f"Duplicate transaction ID '{tx_id}' detected. Transfer aborted to prevent double-spending.")
            current_balance = self.get_balance(from_account)
            if current_balance < amount:
                raise InsufficientCreditsError(
                    f"Account '{from_account}' has {current_balance} credits, cannot transfer {amount}"
                )
            try:
                self.db.conn.execute(
                    """
                    INSERT INTO ledger_entries (id, timestamp, transaction_id, from_account, to_account, amount, memo)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """, (entry_id, now_iso, tx_id, from_account, to_account, amount, memo)
                )
            except sqlite3.IntegrityError as e:
                if "transaction_id" in str(e):
                    raise ValueError(f"Duplicate transaction ID '{tx_id}' detected. Transfer aborted to prevent double-spending.") from e
                raise
        return LedgerEntry(id=entry_id, timestamp=datetime.fromisoformat(now_iso), transaction_id=tx_id,
                           from_account=from_account, to_account=to_account, amount=amount, memo=memo)

    def get_entries(self, account: Optional[str] = None) -> List[LedgerEntry]:
        cursor = self.db.conn.cursor()
        if account:
            cursor.execute(
                "SELECT * FROM ledger_entries WHERE from_account = ? OR to_account = ? ORDER BY sequence_num ASC",
                (account, account)
            )
        else:
            cursor.execute("SELECT * FROM ledger_entries ORDER BY sequence_num ASC")
        return [LedgerEntry(id=r["id"], timestamp=datetime.fromisoformat(r["timestamp"]),
                            transaction_id=r["transaction_id"], from_account=r["from_account"],
                            to_account=r["to_account"], amount=r["amount"], memo=r["memo"])
                for r in cursor.fetchall()]

    def verify_conservation(self) -> bool:
        cursor = self.db.conn.cursor()
        cursor.execute("SELECT COALESCE(SUM(amount), 0) as total_minted FROM ledger_entries WHERE from_account = ?", (SYSTEM_MINT,))
        total_minted = cursor.fetchone()["total_minted"]
        cursor.execute(
            """
            WITH accounts AS (
                SELECT from_account AS acc FROM ledger_entries WHERE from_account != ?
                UNION
                SELECT to_account AS acc FROM ledger_entries WHERE to_account != ?
            )
            SELECT COALESCE(SUM(
                (SELECT COALESCE(SUM(amount), 0) FROM ledger_entries WHERE to_account = accounts.acc) -
                (SELECT COALESCE(SUM(amount), 0) FROM ledger_entries WHERE from_account = accounts.acc)
            ), 0) as net_balance
            FROM accounts
            """, (SYSTEM_MINT, SYSTEM_MINT)
        )
        return cursor.fetchone()["net_balance"] == total_minted

class SqliteEventStore:
    """Append-only cryptographic event store persisted in SQLite."""
    def __init__(self, db: Database, verify_on_startup: bool = True):
        self.db = db
        if verify_on_startup:
            self.on_startup_verify()

    def on_startup_verify(self) -> None:
        try:
            valid, err = self.verify_integrity()
        except Exception as e:
            raise TamperedAuditLogError(f"Audit log corruption detected on startup: {e}")
        if not valid:
            raise TamperedAuditLogError(f"Audit log corruption detected on startup: {err}")

    def append_event(self, actor_id: str, event_type: str, entity_id: str,
                     payload: Dict[str, Any],
                     tenant_id: Optional[str] = None,
                     organisation_id: Optional[str] = None) -> AuditEvent:
        cursor = self.db.conn.cursor()
        cursor.execute("SELECT sequence_id, event_hash FROM audit_events ORDER BY sequence_id DESC LIMIT 1")
        last_row = cursor.fetchone()
        if last_row:
            sequence_id = last_row["sequence_id"] + 1
            previous_event_hash = last_row["event_hash"]
        else:
            sequence_id = 1
            previous_event_hash = GENESIS_PREVIOUS_HASH
        timestamp = datetime.utcnow()
        timestamp_iso = timestamp.isoformat()
        payload_hash = compute_payload_hash(payload)
        event_hash = compute_event_hash(sequence_id=sequence_id, timestamp_iso=timestamp_iso,
                                        actor_id=actor_id, event_type=event_type, entity_id=entity_id,
                                        payload_hash=payload_hash, previous_event_hash=previous_event_hash)

        # Derive tenant_id and organisation_id if not explicitly passed
        effective_tenant = tenant_id or payload.get("tenant_id") or "tenant-demo"
        effective_org = organisation_id or payload.get("org_id") or payload.get("organisation_id")
        if not effective_org and str(entity_id).startswith("mao-"):
            effective_org = entity_id

        with self.db.conn:
            self.db.conn.execute(
                """
                INSERT INTO audit_events
                (sequence_id, timestamp, actor_id, event_type, entity_id, payload, payload_hash, previous_event_hash, event_hash, tenant_id, organisation_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (sequence_id, timestamp_iso, actor_id, event_type, entity_id,
                       canonical_json(payload), payload_hash, previous_event_hash, event_hash,
                       effective_tenant, effective_org)
            )
        return AuditEvent(sequence_id=sequence_id, timestamp=timestamp, actor_id=actor_id,
                          event_type=event_type, entity_id=entity_id, payload=payload,
                          payload_hash=payload_hash, previous_event_hash=previous_event_hash,
                          event_hash=event_hash)

    def get_events(
        self,
        tenant_id: Optional[str] = None,
        organisation_id: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[AuditEvent]:
        cursor = self.db.conn.cursor()
        clauses = []
        params = []
        if tenant_id is not None:
            clauses.append("tenant_id = ?")
            params.append(tenant_id)
        if organisation_id is not None:
            clauses.append("(organisation_id = ? OR entity_id = ?)")
            params.extend([organisation_id, organisation_id])

        sql = "SELECT * FROM audit_events"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY sequence_id ASC"
        if limit is not None and limit > 0:
            sql += f" LIMIT {int(limit)}"

        cursor.execute(sql, tuple(params))
        return [AuditEvent(sequence_id=r["sequence_id"], timestamp=datetime.fromisoformat(r["timestamp"]),
                           actor_id=r["actor_id"], event_type=r["event_type"], entity_id=r["entity_id"],
                           payload=json.loads(r["payload"]), payload_hash=r["payload_hash"],
                           previous_event_hash=r["previous_event_hash"], event_hash=r["event_hash"])
                for r in cursor.fetchall()]

    def verify_integrity(self) -> Tuple[bool, Optional[str]]:
        events = self.get_events()
        for idx, event in enumerate(events):
            expected_seq = idx + 1
            if event.sequence_id != expected_seq:
                return False, f"Sequence mismatch at index {idx}: expected {expected_seq}, got {event.sequence_id}"
            expected_prev_hash = events[idx - 1].event_hash if idx > 0 else GENESIS_PREVIOUS_HASH
            if event.previous_event_hash != expected_prev_hash:
                return False, f"Hash chain broken at sequence {event.sequence_id}: previous_event_hash does not match"
            if event.payload_hash != compute_payload_hash(event.payload):
                return False, f"Payload hash corrupted at sequence {event.sequence_id}"
            recomputed_event_hash = compute_event_hash(sequence_id=event.sequence_id,
                                                       timestamp_iso=event.timestamp.isoformat(),
                                                       actor_id=event.actor_id, event_type=event.event_type,
                                                       entity_id=event.entity_id, payload_hash=event.payload_hash,
                                                       previous_event_hash=event.previous_event_hash)
            if event.event_hash != recomputed_event_hash:
                return False, f"Event hash mismatch at sequence {event.sequence_id}"
        return True, None

class SqliteRepository:
    """CRUD repository for Organisations, Tasks, Agents, Proposals, Decisions, and Receipts."""
    def __init__(self, db: Database):
        self.db = db

    def save_organisation(self, org: Organisation) -> None:
        """Persist the organisation's tenant identity as part of its immutable resource scope."""
        with self.db.conn:
            self.db.conn.execute(
                """
                INSERT INTO organisations (id, tenant_id, mission, treasury_balance, state, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    mission = excluded.mission,
                    treasury_balance = excluded.treasury_balance,
                    state = excluded.state
                """,
                (org.id, org.tenant_id, org.mission, org.treasury_balance, org.state.value, org.created_at.isoformat())
            )

    def load_organisation(self, org_id: str, ledger: Optional[Any] = None) -> Optional[Organisation]:
        cursor = self.db.conn.cursor()
        cursor.execute("SELECT * FROM organisations WHERE id = ?", (org_id,))
        row = cursor.fetchone()
        if not row:
            return None
        treasury_balance = ledger.get_balance(TREASURY) if ledger is not None else row["treasury_balance"]
        org = Organisation(id=row["id"], mission=row["mission"], tenant_id=row["tenant_id"],
                           treasury_balance=treasury_balance, state=OrgState(row["state"]),
                           created_at=datetime.fromisoformat(row["created_at"]))
        cursor.execute("SELECT * FROM agents WHERE org_id = ?", (org_id,))
        for ar in cursor.fetchall():
            task_history = json.loads(ar["task_history"]) if "task_history" in ar.keys() and ar["task_history"] else []
            org.agents[ar["id"]] = AgentRecord(
                id=ar["id"], role=AgentRole(ar["role"]), model_name=ar["model_name"],
                credit_balance=ar["credit_balance"], reputation_score=ar["reputation_score"],
                authority_ceiling=ar["authority_ceiling"],
                allowed_action_types=[ActionType(at) for at in json.loads(ar["allowed_action_types"])],
                status=AgentStatus(ar["status"]), successful_tasks=ar["successful_tasks"],
                failed_tasks=ar["failed_tasks"], policy_violations=ar["policy_violations"],
                performance_score=ar["performance_score"] if "performance_score" in ar.keys() else 100.0,
                risk_score=ar["risk_score"] if "risk_score" in ar.keys() else 0.0,
                resource_efficiency=ar["resource_efficiency"] if "resource_efficiency" in ar.keys() else 1.0,
                reliability_score=ar["reliability_score"] if "reliability_score" in ar.keys() else 100.0,
                task_history=task_history
            )
        return org

    def save_agent(self, agent: AgentRecord, org_id: str) -> None:
        with self.db.conn:
            self.db.conn.execute(
                """
                INSERT INTO agents
                (id, org_id, role, model_name, credit_balance, reputation_score, authority_ceiling, allowed_action_types, status, successful_tasks, failed_tasks, policy_violations, performance_score, risk_score, resource_efficiency, reliability_score, task_history)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    credit_balance = excluded.credit_balance, reputation_score = excluded.reputation_score,
                    authority_ceiling = excluded.authority_ceiling, status = excluded.status,
                    successful_tasks = excluded.successful_tasks, failed_tasks = excluded.failed_tasks,
                    policy_violations = excluded.policy_violations, performance_score = excluded.performance_score,
                    risk_score = excluded.risk_score, resource_efficiency = excluded.resource_efficiency,
                    reliability_score = excluded.reliability_score, task_history = excluded.task_history
                """,
                (agent.id, org_id, agent.role.value, agent.model_name, agent.credit_balance,
                 agent.reputation_score, agent.authority_ceiling,
                 json.dumps([at.value for at in agent.allowed_action_types]), agent.status.value,
                 agent.successful_tasks, agent.failed_tasks, agent.policy_violations,
                 agent.performance_score, agent.risk_score, agent.resource_efficiency,
                 agent.reliability_score, json.dumps(agent.task_history))
            )

    def save_task(self, task: Task, org_id: str) -> None:
        with self.db.conn:
            self.db.conn.execute(
                """
                INSERT INTO tasks (id, org_id, assigned_agent_id, objective, allocated_credits, status, output_evidence, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET status = excluded.status, output_evidence = excluded.output_evidence
                """, (task.id, org_id, task.assigned_agent_id, task.objective, task.allocated_credits,
                       task.status.value, json.dumps(task.output_evidence) if task.output_evidence else None,
                       task.created_at.isoformat())
            )

    def save_proposal(self, proposal: ActionProposal) -> None:
        with self.db.conn:
            self.db.conn.execute(
                """
                INSERT OR REPLACE INTO proposals
                (id, task_id, proposing_agent_id, action_type, target, parameters, requested_credits, expected_value_score, risk_assessment, rationale, content_hash, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (proposal.id, proposal.task_id, proposal.proposing_agent_id, proposal.action_type.value,
                       proposal.target, json.dumps(proposal.parameters), proposal.requested_credits,
                       proposal.expected_value_score, proposal.risk_assessment, proposal.rationale,
                       proposal.get_content_hash(), proposal.created_at.isoformat())
            )

    def save_policy_decision(self, decision: PolicyDecision) -> None:
        with self.db.conn:
            self.db.conn.execute(
                """
                INSERT OR REPLACE INTO policy_decisions
                (id, proposal_id, result, violated_rule_id, violated_rule_description, evaluated_rules, authorization_token, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (decision.id, decision.proposal_id, decision.result.value, decision.violated_rule_id,
                       decision.violated_rule_description, json.dumps(decision.evaluated_rules),
                       decision.authorization_token, decision.timestamp.isoformat())
            )

    def save_execution_receipt(self, receipt: ExecutionReceipt) -> None:
        with self.db.conn:
            self.db.conn.execute(
                """
                INSERT OR REPLACE INTO execution_receipts
                (id, proposal_id, authorization_token, action_type, target, http_status, raw_response_hash, raw_output, cost_credits, executed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (receipt.id, receipt.proposal_id, receipt.authorization_token, receipt.action_type.value,
                       receipt.target, receipt.http_status, receipt.raw_response_hash, json.dumps(receipt.raw_output),
                       receipt.cost_credits, receipt.executed_at.isoformat())
            )

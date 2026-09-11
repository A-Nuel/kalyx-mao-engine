import hmac
import hashlib
import uuid
import time
from typing import List, Optional, Tuple, Any
from src.domain.entities import ActionProposal, PolicyDecision, Organisation, AuthorizationTokenClaims
from src.domain.enums import PolicyResult, OrgState
from src.domain.events import canonical_json
from src.governance.rules import (
    PolicyRule,
    SpendLimitRule,
    TreasuryBalanceRule,
    RolePermissionRule,
    TargetAllowlistRule,
    OrgPauseRule,
    AgentStatusRule,
)

class PolicyEngine:
    """
    100% Deterministic policy evaluation engine.
    Evaluates ActionProposals against registered rules.
    Issues HMAC-signed cryptographic authorization tokens ONLY upon approval.
    Binds authorizations to:
      - Organisation ID
      - Proposal content hash
      - Decision ID
      - Deterministic policy version hash
      - Issued timestamp & explicit TTL expiration
      - Cryptographic nonce
    """
    def __init__(
        self,
        rules: Optional[List[PolicyRule]] = None,
        human_approval_threshold: int = 40,
        signing_secret: Optional[str] = None,
        token_ttl_seconds: float = 60.0
    ):
        self.rules = rules if rules is not None else [
            OrgPauseRule(),
            AgentStatusRule(),
            TreasuryBalanceRule(),
            SpendLimitRule(),
            RolePermissionRule(),
            TargetAllowlistRule()
        ]
        self.human_approval_threshold = human_approval_threshold
        self.token_ttl_seconds = token_ttl_seconds
        self._secret = (signing_secret or str(uuid.uuid4())).encode("utf-8")

    def get_signing_secret(self) -> str:
        """Returns the UTF-8 string of the signing secret."""
        return self._secret.decode("utf-8")

    def get_policy_version_hash(self) -> str:
        """
        Deterministically computes a SHA-256 fingerprint of the current policy rules and configuration.
        Any change to rule IDs, rule definitions, or threshold changes this hash.
        """
        rules_meta = [
            {
                "rule_id": r.rule_id,
                "description": getattr(r, "description", "")
            }
            for r in sorted(self.rules, key=lambda r: r.rule_id)
        ]
        policy_meta = {
            "rules": rules_meta,
            "human_approval_threshold": self.human_approval_threshold
        }
        return hashlib.sha256(canonical_json(policy_meta).encode("utf-8")).hexdigest()

    def generate_token(
        self,
        proposal: ActionProposal,
        org: Organisation,
        decision_id: Optional[str] = None,
        ttl_seconds: Optional[float] = None
    ) -> str:
        """
        Constructs and signs a tamper-proof authorization token.
        Token format: AUTH-<proposal_id[:8]>.<base64url_claims>.<hmac_signature>
        """
        now = time.time()
        ttl = ttl_seconds if ttl_seconds is not None else self.token_ttl_seconds
        claims = AuthorizationTokenClaims(
            org_id=org.id,
            proposal_id=proposal.id,
            proposal_content_hash=proposal.get_content_hash(),
            decision_id=decision_id or str(uuid.uuid4()),
            policy_version_hash=self.get_policy_version_hash(),
            issued_at=now,
            expires_at=now + ttl,
            nonce=str(uuid.uuid4())
        )
        b64_claims = claims.to_b64()
        signature = hmac.new(self._secret, b64_claims.encode("utf-8"), hashlib.sha256).hexdigest()
        return f"AUTH-{proposal.id[:8]}.{b64_claims}.{signature}"

    def _generate_token(
        self,
        proposal: ActionProposal,
        org: Optional[Organisation] = None,
        decision_id: Optional[str] = None,
        ttl_seconds: Optional[float] = None
    ) -> str:
        dummy_org = org or Organisation(id="org-default", mission="default")
        return self.generate_token(proposal, dummy_org, decision_id, ttl_seconds)

    def verify_token(
        self,
        token: str,
        proposal: ActionProposal,
        org: Organisation,
        decision: Optional[PolicyDecision] = None,
        current_time: Optional[float] = None
    ) -> Tuple[bool, Optional[str]]:
        """
        Deterministically verifies that an authorization token:
        1. Is present and matches token structure
        2. Was signed with this PolicyEngine's secret
        3. Matches the exact proposal content hash
        4. Matches the presented organisation ID
        5. Matches the current policy version hash (rejects tokens from old policy version)
        6. Has not expired (TTL check)
        7. Organisation is not in PAUSED state (emergency kill switch)
        """
        if not token:
            return False, "Empty or missing authorization token"
        
        if org.state == OrgState.PAUSED:
            return False, "Organisation is PAUSED by emergency kill switch"

        parts = token.split(".")
        if len(parts) != 3 or not parts[0].startswith("AUTH-"):
            return False, "Invalid token: format does not match authorization schema"

        _, b64_claims, signature = parts

        # Verify HMAC signature first
        expected_signature = hmac.new(self._secret, b64_claims.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected_signature):
            return False, "Invalid token: signature does not match proposal content or was forged/modified"

        try:
            claims = AuthorizationTokenClaims.from_b64(b64_claims)
        except Exception as e:
            return False, f"Invalid token: failed to parse claims ({str(e)})"

        # Check expiration
        now = current_time if current_time is not None else time.time()
        if now > claims.expires_at:
            return False, f"Authorization token expired (expired at {claims.expires_at}, current time {now})"

        # Check organisation binding
        if claims.org_id != org.id:
            return False, f"Token organisation mismatch: issued for '{claims.org_id}', presented for '{org.id}'"

        # Check proposal ID binding
        if claims.proposal_id != proposal.id:
            return False, f"Token proposal ID mismatch: issued for '{claims.proposal_id}', presented for '{proposal.id}'"

        # Check proposal content hash
        if claims.proposal_content_hash != proposal.get_content_hash():
            return False, "Token proposal content hash mismatch: signature does not match proposal content (proposal was modified after approval)"

        # Check decision ID binding if decision is provided
        if decision and claims.decision_id != decision.id:
            return False, f"Token decision ID mismatch: issued for '{claims.decision_id}', presented for '{decision.id}'"

        # Check policy version binding
        current_version = self.get_policy_version_hash()
        if claims.policy_version_hash != current_version:
            return False, f"Policy version mismatch: token was issued under '{claims.policy_version_hash}', current policy version is '{current_version}'"

        return True, None

    def evaluate(
        self,
        proposal: ActionProposal,
        org: Organisation,
        ledger: Optional[Any] = None
    ) -> PolicyDecision:
        agent = org.agents.get(proposal.proposing_agent_id)
        if not agent:
            return PolicyDecision(
                id=str(uuid.uuid4()),
                proposal_id=proposal.id,
                result=PolicyResult.REJECTED,
                violated_rule_id="RULE-AUTH",
                violated_rule_description=f"Agent '{proposal.proposing_agent_id}' does not exist in organisation",
                evaluated_rules=["RULE-AUTH"],
                authorization_token=None
            )

        evaluated_rules: List[str] = []
        for rule in self.rules:
            evaluated_rules.append(rule.rule_id)
            try:
                violation = rule.evaluate(proposal, agent, org, ledger=ledger)
            except TypeError:
                violation = rule.evaluate(proposal, agent, org)
            if violation:
                return PolicyDecision(
                    id=str(uuid.uuid4()),
                    proposal_id=proposal.id,
                    result=PolicyResult.REJECTED,
                    violated_rule_id=rule.rule_id,
                    violated_rule_description=violation,
                    evaluated_rules=evaluated_rules,
                    authorization_token=None
                )

        if proposal.requested_credits >= self.human_approval_threshold:
            return PolicyDecision(
                id=str(uuid.uuid4()),
                proposal_id=proposal.id,
                result=PolicyResult.ESCALATE_TO_HUMAN,
                violated_rule_id="RULE-05",
                violated_rule_description=f"Requested {proposal.requested_credits} credits requires explicit human approval",
                evaluated_rules=evaluated_rules + ["RULE-05"],
                authorization_token=None
            )

        decision_id = str(uuid.uuid4())
        auth_token = self.generate_token(proposal, org, decision_id)
        return PolicyDecision(
            id=decision_id,
            proposal_id=proposal.id,
            result=PolicyResult.APPROVED,
            violated_rule_id=None,
            violated_rule_description=None,
            evaluated_rules=evaluated_rules,
            authorization_token=auth_token
        )

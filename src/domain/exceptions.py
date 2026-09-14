class DomainError(Exception):
    """Base exception for domain errors."""
    pass

class InsufficientCreditsError(DomainError):
    """Raised when an account does not have enough credits."""
    pass

class InvalidStateTransitionError(DomainError):
    """Raised when an invalid state transition is attempted."""
    pass

class PolicyViolationError(DomainError):
    """Raised when an action proposal violates deterministic policy."""
    pass

class UnauthorizedActionError(DomainError):
    """Raised when an action is executed without valid authorization."""
    pass

class TamperedAuditLogError(DomainError):
    """Raised when audit log cryptographic verification fails."""
    pass

class AgentSuspendedError(DomainError):
    """Raised when a suspended/retired agent attempts actions."""
    pass

class NoEligibleAgentException(DomainError):
    """Raised when no eligible agent is available to be assigned to a task."""
    pass

class LLMOutputValidationError(DomainError):
    """Raised when an LLM produces malformed output or schema validation fails."""
    pass

class ExternalExecutionError(DomainError):
    """Raised when an external execution call fails, times out, or returns a 4xx/5xx status."""
    pass

class ReconciliationError(DomainError):
    """Raised when reconciliation encounters an unresolvable or illegal state."""
    pass


class IdempotencyConflict(DomainError):
    """Raised when an operation key or idempotency token is reused with different content."""
    pass


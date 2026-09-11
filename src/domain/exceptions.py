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

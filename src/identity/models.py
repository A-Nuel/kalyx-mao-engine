from datetime import datetime
from enum import Enum
from pydantic import BaseModel, Field


class MembershipRole(str, Enum):
    OWNER = "owner"
    ADMIN = "admin"
    OPERATOR = "operator"
    VIEWER = "viewer"


class Principal(BaseModel):
    """Authenticated human/service identity. Never inferred from tenant input."""

    id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=200)
    active: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Membership(BaseModel):
    """Explicit principal-to-tenant authorization relationship."""

    principal_id: str = Field(min_length=1, max_length=128)
    tenant_id: str = Field(min_length=1, max_length=128)
    role: MembershipRole
    active: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)

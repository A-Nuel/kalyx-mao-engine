from datetime import datetime
from pydantic import BaseModel, Field


class Tenant(BaseModel):
    """Top-level workspace boundary for Kalyx resources."""

    id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=200)
    status: str = "active"
    created_at: datetime = Field(default_factory=datetime.utcnow)

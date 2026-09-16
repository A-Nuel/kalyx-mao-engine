"""Provider-neutral protocol for external economic resources."""
from __future__ import annotations

from typing import Optional, Protocol

from src.external.models import (
    ExternalBalance,
    ExternalKeyStatus,
    InferenceReceipt,
    InferenceRequest,
    KeyLifecycleIntent,
    KeyLifecycleReceipt,
)


class ExternalEconomicProvider(Protocol):
    name: str

    def get_balance(self, *, tenant_id: str, organisation_id: str) -> ExternalBalance:
        ...

    def get_key_status(
        self, *, tenant_id: str, organisation_id: str, key_id: Optional[str] = None
    ) -> ExternalKeyStatus:
        ...

    def execute_key_lifecycle(self, intent: KeyLifecycleIntent) -> KeyLifecycleReceipt:
        ...

    def run_inference(self, request: InferenceRequest) -> InferenceReceipt:
        ...

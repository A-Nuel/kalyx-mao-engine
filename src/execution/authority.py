"""Phase 21D — organisation-level execution authority boundary.

The authority is deliberately provider-neutral. It owns *who may execute* and
delegates provider-specific cryptographic mechanics to an adapter such as the
existing EVM signer. Agents never select or instantiate the underlying signer.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Protocol, Tuple

from src.domain.blockchain import BlockchainTransactionIntent
from src.settlement.blockchain.signer import IBlockchainSigner


class ExecutionAuthority(Protocol):
    @property
    def authority_id(self) -> str: ...

    @property
    def tenant_id(self) -> str: ...

    @property
    def organisation_id(self) -> str: ...

    @property
    def wallet_address(self) -> str: ...

    @property
    def provider(self) -> str: ...

    def assert_scope(self, tenant_id: str, organisation_id: str) -> None: ...


@dataclass(frozen=True)
class BlockchainExecutionAuthority:
    """Organisation-bound authority backed by an existing blockchain signer.

    This class does not store keys and does not alter the existing signing
    boundary. The signer remains the only component capable of producing a
    cryptographic signature.
    """

    authority_id: str
    tenant_id: str
    organisation_id: str
    signer: IBlockchainSigner
    provider: str = "evm"

    @property
    def wallet_address(self) -> str:
        return self.signer.address

    def assert_scope(self, tenant_id: str, organisation_id: str) -> None:
        if tenant_id != self.tenant_id:
            raise PermissionError("Execution authority tenant boundary violation")
        if organisation_id != self.organisation_id:
            raise PermissionError("Execution authority organisation boundary violation")

    def sign(
        self,
        intent: BlockchainTransactionIntent,
        nonce: int,
    ) -> Tuple[bytes, str]:
        self.assert_scope(intent.tenant_id, intent.organisation_id)
        return self.signer.sign_transaction(intent, nonce)

    def describe(self) -> Dict[str, Any]:
        return {
            "authority_id": self.authority_id,
            "tenant_id": self.tenant_id,
            "organisation_id": self.organisation_id,
            "provider": self.provider,
            "wallet_address": self.wallet_address,
        }

"""Ed25519 identity primitives.

Private keys stay outside the engine/application database. The class exposes
sign/verify operations but never serializes the private key implicitly.
"""
import base64
from dataclasses import dataclass

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


@dataclass(frozen=True)
class AgentIdentity:
    """A stable public identity with local signing capability."""
    public_key: str
    _private_key: Ed25519PrivateKey

    @classmethod
    def generate(cls) -> "AgentIdentity":
        private = Ed25519PrivateKey.generate()
        public = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        return cls(public_key=_b64(public), _private_key=private)

    def sign(self, payload: bytes) -> str:
        return _b64(self._private_key.sign(payload))

    def public_verify(self, payload: bytes, signature: str) -> bool:
        try:
            Ed25519PublicKey.from_public_bytes(_unb64(self.public_key)).verify(_unb64(signature), payload)
            return True
        except Exception:
            return False

    @property
    def private_key_bytes(self) -> bytes:
        """Explicit export for secure key-management integrations only."""
        return self._private_key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())

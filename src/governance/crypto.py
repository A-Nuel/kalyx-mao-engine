import hmac
import hashlib
from abc import ABC, abstractmethod

class ITokenSigner(ABC):
    """Abstract interface for token signing."""
    @property
    @abstractmethod
    def algorithm_id(self) -> str:
        pass

    @abstractmethod
    def sign(self, data: bytes) -> str:
        """Signs data and returns hexadecimal or encoded signature string."""
        pass

class ITokenVerifier(ABC):
    """Abstract interface for independent token signature verification."""
    @property
    @abstractmethod
    def algorithm_id(self) -> str:
        pass

    @abstractmethod
    def verify(self, data: bytes, signature: str) -> bool:
        """Returns True if the signature is valid for data, False otherwise."""
        pass

class HmacSha256TokenSigner(ITokenSigner):
    """HMAC-SHA256 token signer."""
    def __init__(self, secret: str):
        self._secret = secret.encode("utf-8") if isinstance(secret, str) else secret

    @property
    def algorithm_id(self) -> str:
        return "HMAC-SHA256"

    def sign(self, data: bytes) -> str:
        return hmac.new(self._secret, data, hashlib.sha256).hexdigest()

class HmacSha256TokenVerifier(ITokenVerifier):
    """HMAC-SHA256 token verifier."""
    def __init__(self, secret: str):
        self._secret = secret.encode("utf-8") if isinstance(secret, str) else secret

    @property
    def algorithm_id(self) -> str:
        return "HMAC-SHA256"

    def verify(self, data: bytes, signature: str) -> bool:
        expected = hmac.new(self._secret, data, hashlib.sha256).hexdigest()
        return hmac.compare_digest(signature, expected)

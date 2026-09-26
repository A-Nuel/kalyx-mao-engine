"""Social authentication boundary.

The Product Plane does not accept arbitrary social claims. A concrete OAuth/OIDC
provider must verify its ID token/authorization code before a principal is linked.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class SocialIdentity:
    provider: str
    subject: str
    email: str | None = None
    display_name: str | None = None


class SocialAuthProvider(Protocol):
    name: str
    def authorization_url(self, state: str, redirect_uri: str) -> str: ...
    def verify_callback(self, code: str, state: str, redirect_uri: str) -> SocialIdentity: ...


SUPPORTED_SOCIAL_PROVIDERS = ("google", "x")


def configured_social_providers() -> list[str]:
    return [
        name for name in SUPPORTED_SOCIAL_PROVIDERS
        if (
            __import__("os").getenv(f"KALYX_SOCIAL_{name.upper()}_CLIENT_ID")
            and __import__("os").getenv(f"KALYX_SOCIAL_{name.upper()}_CLIENT_SECRET")
        )
    ]

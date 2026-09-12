from src.identity.ed25519 import AgentIdentity


def test_ed25519_identity_signs_and_verifies():
    identity = AgentIdentity.generate()
    payload = b"proposal:123"
    signature = identity.sign(payload)
    assert identity.public_verify(payload, signature)
    assert not identity.public_verify(b"proposal:tampered", signature)


def test_ed25519_signature_cannot_be_reused_for_other_payload():
    identity = AgentIdentity.generate()
    signature = identity.sign(b"approved-action")
    assert not identity.public_verify(b"different-action", signature)

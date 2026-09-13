import pytest

from src.persistence.database import Database
from src.security.token_consumption import AuthorizationTokenJournal, TokenAlreadyConsumed


def test_token_nonce_consumed_once():
    db = Database(":memory:")
    journal = AuthorizationTokenJournal(db.conn)
    kwargs = dict(
        nonce="nonce-1",
        org_id="org-a",
        proposal_id="prop-1",
        proposal_content_hash="hash-1",
        decision_id="dec-1",
        policy_version_hash="pol-1",
        token_fingerprint="fp-1",
    )
    journal.consume(**kwargs)
    assert journal.is_consumed("nonce-1")
    with pytest.raises(TokenAlreadyConsumed):
        journal.consume(**kwargs)


def test_token_survives_reopen():
    db = Database(":memory:")
    journal = AuthorizationTokenJournal(db.conn)
    journal.consume(
        nonce="nonce-restart",
        org_id="org-a",
        proposal_id="prop-1",
        proposal_content_hash="hash-1",
        decision_id="dec-1",
        policy_version_hash="pol-1",
        token_fingerprint="fp-1",
    )
    # New journal instance on same connection simulates process-local restart of the journal
    journal2 = AuthorizationTokenJournal(db.conn)
    assert journal2.is_consumed("nonce-restart")
    with pytest.raises(TokenAlreadyConsumed):
        journal2.consume(
            nonce="nonce-restart",
            org_id="org-a",
            proposal_id="prop-1",
            proposal_content_hash="hash-1",
            decision_id="dec-1",
            policy_version_hash="pol-1",
            token_fingerprint="fp-1",
        )

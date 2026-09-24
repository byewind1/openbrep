from openbrep.credential_pool import CredentialPool


def test_pool_is_sticky_per_scope_and_round_robins_between_scopes():
    pool = CredentialPool.from_provider(
        {"credentials": [{"id": "a", "value": "secret-a"}, {"id": "b", "value": "secret-b"}]}
    )
    first = pool.select("session-1", now=0)
    assert first and first.credential_id == "a"
    assert pool.select("session-1", now=0).credential_id == "a"
    assert pool.select("session-2", now=0).credential_id == "b"
    assert "secret" not in repr(first)
    assert first.as_metadata() == {"credential_id": "a", "scope": "session-1"}


def test_pool_cooldown_skips_failed_credential():
    pool = CredentialPool.from_provider(
        {"api_keys": [{"id": "a", "value": "A"}, {"id": "b", "value": "B"}]}
    )
    assert pool.select("s", now=0).credential_id == "a"
    pool.mark_failure("a", now=0)
    assert pool.select("other", now=1).credential_id == "b"
    assert pool.select("s", now=31).credential_id == "a"


def test_pool_expands_environment_references(monkeypatch):
    monkeypatch.setenv("OPENBREP_TEST_KEY", "env-secret")
    pool = CredentialPool.from_provider(
        {"credentials": [{"id": "env", "env": "OPENBREP_TEST_KEY"}]}
    )
    assert pool.select("s").value == "env-secret"

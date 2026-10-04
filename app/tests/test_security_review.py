import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID

import pytest
from conftest import TOKEN_ALICE, TOKEN_BOB

from containerops.rate_limit import RateLimit


def test_concurrent_requests_share_one_budget_and_refill(monkeypatch):
    monkeypatch.setattr("containerops.rate_limit.time", SimpleNamespace(monotonic=lambda: 100.0))
    limiter = RateLimit(rate=10, burst=20)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: limiter.consume("peer"), range(100)))
    assert results.count(0) == 20
    monkeypatch.setattr("containerops.rate_limit.time", SimpleNamespace(monotonic=lambda: 100.11))
    assert limiter.consume("peer") == 0
    assert limiter.consume("peer") == 1


def test_peer_churn_does_not_reset_budgets_or_grow_memory(monkeypatch):
    monkeypatch.setattr("containerops.rate_limit.time", SimpleNamespace(monotonic=lambda: 100.0))
    limiter = RateLimit(rate=1, burst=1, max_keys=2)
    assert limiter.consume("alice") == limiter.consume("bob") == 0
    assert limiter.consume("new-peer-0") == 0
    assert all(limiter.consume(f"new-peer-{index}") == 1 for index in range(1, 1000))
    assert limiter.consume("alice") == 1
    assert len(limiter.buckets) == 3


def test_owner_rate_precedes_database_and_keeps_other_owner_available(client, monkeypatch):
    # Patch only this limiter's clock; request logs keep their real clock.
    monkeypatch.setattr("containerops.rate_limit.time", SimpleNamespace(monotonic=lambda: 100.0))
    path = f"/v1/jobs/{UUID(int=1)}"
    with patch("containerops.repository.get_job", return_value=None) as get_job:
        for _ in range(40):
            assert (
                client.get(path, headers={"Authorization": f"Bearer {TOKEN_ALICE}"}).status_code
                == 404
            )
        response = client.get(path, headers={"Authorization": f"Bearer {TOKEN_ALICE}"})
        assert response.status_code == 429
        assert response.headers["Retry-After"] == "1"
        assert client.get(path, headers={"Authorization": f"Bearer {TOKEN_BOB}"}).status_code == 404
        assert get_job.call_count == 41


def test_anonymous_rate_ignores_spoofed_forwarded_header(client, monkeypatch):
    monkeypatch.setattr("containerops.rate_limit.time", SimpleNamespace(monotonic=lambda: 100.0))
    path = f"/v1/jobs/{UUID(int=1)}"
    with patch("containerops.repository.get_job") as get_job:
        for index in range(200):
            response = client.get(path, headers={"X-Forwarded-For": f"192.0.2.{index}"})
            assert response.status_code == 401
        response = client.get(path, headers={"X-Forwarded-For": "different-peer"})
        assert response.status_code == 429
        assert response.headers["Retry-After"] == "1"
        assert client.get("/health/live").status_code == 200
        get_job.assert_not_called()


def test_unexpected_errors_do_not_expose_exception_or_payload(client, auth, caplog):
    marker = "private-database-password"
    with patch("containerops.repository.submit_job", side_effect=RuntimeError(marker)):
        response = client.post("/v1/jobs", headers=auth, json={"text": marker})
    assert response.status_code == 500
    assert marker not in response.text
    assert marker not in caplog.text
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Request-ID"]


@pytest.mark.parametrize("token", ["x" * 257, "x" * 24 + "\n", " " * 24, "x" * 24 + "\x7f"])
def test_invalid_token_configuration_fails_closed(settings, tmp_path, token):
    path = tmp_path / "invalid-tokens.json"
    path.write_text(json.dumps({"alice": token}), encoding="utf-8")
    with pytest.raises(ValueError, match="Credencial"):
        replace(settings, api_tokens_file=path).tokens()

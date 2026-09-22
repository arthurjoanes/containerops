import json
from dataclasses import replace
from unittest.mock import patch
from uuid import UUID

import pytest
from conftest import TOKEN_ALICE, TOKEN_BOB
from fastapi.testclient import TestClient

from containerops.api import create_app
from containerops.config import Settings
from containerops.domain import MAX_BODY_BYTES, MAX_TEXT_BYTES, Job


@pytest.fixture
def queued_job() -> Job:
    return Job(UUID(int=1), "alice", "", "hash", "queued", 0, 0, None, None, None, None)


@pytest.mark.parametrize("scheme", ["Bearer", "bearer", "bEaReR", "BEARER "])
def test_bearer_scheme_is_case_insensitive(client: TestClient, scheme: str) -> None:
    with patch("containerops.repository.get_job", return_value=None) as get_job:
        response = client.get(
            f"/v1/jobs/{UUID(int=1)}", headers={"Authorization": f"{scheme} {TOKEN_ALICE}"}
        )
    assert response.status_code == 404
    assert get_job.call_args.args[1] == "alice"


@pytest.mark.parametrize(
    "credential", [b"", b"Basic token", b"Bearer", b"Bearer ", b"Bearer wrong", b"Bearer \xff"]
)
def test_invalid_auth_always_challenges_without_querying_database(
    client: TestClient, credential: bytes
) -> None:
    with patch("containerops.repository.get_job") as get_job:
        response = client.get(f"/v1/jobs/{UUID(int=1)}", headers=[(b"authorization", credential)])
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    get_job.assert_not_called()


@pytest.mark.parametrize("other_token", [TOKEN_ALICE, TOKEN_BOB])
def test_duplicate_authorization_is_rejected(client: TestClient, other_token: str) -> None:
    with patch("containerops.repository.get_job") as get_job:
        response = client.get(
            f"/v1/jobs/{UUID(int=1)}",
            headers=[
                ("Authorization", f"Bearer {TOKEN_ALICE}"),
                ("Authorization", f"Bearer {other_token}"),
            ],
        )
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    get_job.assert_not_called()


@pytest.mark.parametrize("key", [b"", b" " * 3, b"x" * 129, b"tab\tkey", b"\xff"])
def test_invalid_idempotency_key_is_rejected_before_persistence(
    client: TestClient, key: bytes
) -> None:
    with patch("containerops.repository.submit_job") as submit:
        response = client.post(
            "/v1/jobs",
            json={"text": "ok"},
            headers=[
                (b"Authorization", f"Bearer {TOKEN_ALICE}".encode()),
                (b"Idempotency-Key", key),
            ],
        )
    assert response.status_code == 422
    submit.assert_not_called()


@pytest.mark.parametrize("other_key", ["first", "second"])
def test_duplicate_idempotency_key_is_rejected(client: TestClient, other_key: str) -> None:
    with patch("containerops.repository.submit_job") as submit:
        response = client.post(
            "/v1/jobs",
            json={"text": "ok"},
            headers=[
                ("Authorization", f"Bearer {TOKEN_ALICE}"),
                ("Idempotency-Key", "first"),
                ("Idempotency-Key", other_key),
            ],
        )
    assert response.status_code == 422
    submit.assert_not_called()


@pytest.mark.parametrize(
    "text",
    [None, True, 123, [], {}, "\x00", "\ud800", "é" * 8193],
    ids=["null", "boolean", "integer", "array", "object", "nul", "surrogate", "over-limit"],
)
def test_invalid_text_types_and_encoding_do_not_reach_database(
    client: TestClient, auth: dict[str, str], text: object
) -> None:
    with patch("containerops.repository.submit_job") as submit:
        response = client.post(
            "/v1/jobs",
            headers={**auth, "Content-Type": "application/json"},
            content=json.dumps({"text": text}, ensure_ascii=text == "\ud800").encode(),
        )
    assert response.status_code == 422
    submit.assert_not_called()


@pytest.mark.parametrize(
    "text",
    ["", "x" * MAX_TEXT_BYTES, "é" * 8192, "🚀" * 4096, "中文 cafe\u0301"],
    ids=["empty", "ascii-limit", "accent-limit", "emoji-limit", "combining-unicode"],
)
def test_valid_text_boundaries_preserve_original_bytes(
    client: TestClient, auth: dict[str, str], queued_job: Job, text: str
) -> None:
    with patch("containerops.repository.submit_job", return_value=(queued_job, True)) as submit:
        response = client.post(
            "/v1/jobs",
            headers={**auth, "Idempotency-Key": "k" * 128, "Content-Type": "application/json"},
            content=json.dumps({"text": text}, ensure_ascii=False).encode(),
        )
    assert response.status_code == 201
    assert submit.call_args.args[2:] == ("k" * 128, text, 0)
    assert response.json()["result"] is None


@pytest.mark.parametrize("duration", [None, True, "1", -0.1, 15.0001, float("nan"), float("inf")])
def test_invalid_duration_at_http_boundary(
    client: TestClient, auth: dict[str, str], duration: object
) -> None:
    with patch("containerops.repository.submit_job") as submit:
        response = client.post(
            "/v1/jobs",
            headers={**auth, "Content-Type": "application/json"},
            content=json.dumps({"text": "ok", "demo_duration_seconds": duration}).encode(),
        )
    assert response.status_code == 422
    submit.assert_not_called()


@pytest.mark.parametrize("duration", [0, -0.0, 15])
def test_valid_demo_duration_boundaries(
    client: TestClient, auth: dict[str, str], queued_job: Job, duration: float
) -> None:
    with patch("containerops.repository.submit_job", return_value=(queued_job, True)) as submit:
        response = client.post(
            "/v1/jobs", headers=auth, json={"text": "ok", "demo_duration_seconds": duration}
        )
    assert response.status_code == 201
    assert submit.call_args.args[-1] == duration


def test_positive_duration_requires_demo_mode(settings: Settings, auth: dict[str, str]) -> None:
    with TestClient(create_app(replace(settings, demo_mode=False))) as client:
        with patch("containerops.repository.submit_job") as submit:
            response = client.post(
                "/v1/jobs", headers=auth, json={"text": "ok", "demo_duration_seconds": 0.001}
            )
    assert response.status_code == 422
    submit.assert_not_called()


@pytest.mark.parametrize(("size", "status"), [(MAX_BODY_BYTES, 201), (MAX_BODY_BYTES + 1, 413)])
def test_body_limit_has_inclusive_boundary(
    client: TestClient, auth: dict[str, str], queued_job: Job, size: int, status: int
) -> None:
    content = b'{"text":"x"}'
    content += b" " * (size - len(content))
    with patch("containerops.repository.submit_job", return_value=(queued_job, True)) as submit:
        response = client.post(
            "/v1/jobs", headers={**auth, "Content-Type": "application/json"}, content=content
        )
    assert response.status_code == status
    assert submit.call_count == (status == 201)


@pytest.mark.parametrize("body", [b"{", b"null", b"[]", b"{}", b'{"text":"x","extra":1}'])
def test_malformed_or_incomplete_json_is_rejected(
    client: TestClient, auth: dict[str, str], body: bytes
) -> None:
    with patch("containerops.repository.submit_job") as submit:
        response = client.post(
            "/v1/jobs", headers={**auth, "Content-Type": "application/json"}, content=body
        )
    assert response.status_code == 422
    submit.assert_not_called()


def test_invalid_uuid_does_not_query_database(client: TestClient, auth: dict[str, str]) -> None:
    with patch("containerops.repository.get_job") as get_job:
        response = client.get("/v1/jobs/not-a-uuid", headers=auth)
    assert response.status_code == 422
    get_job.assert_not_called()


def test_shutdown_rejects_new_work(client: TestClient, auth: dict[str, str]) -> None:
    client.app.state.accepting = False  # type: ignore[union-attr]
    with patch("containerops.repository.submit_job") as submit:
        response = client.post("/v1/jobs", headers=auth, json={"text": "ok"})
    assert response.status_code == 503
    submit.assert_not_called()

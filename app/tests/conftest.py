import json
import os
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from containerops.api import create_app
from containerops.config import Settings
from containerops.database import connect
from containerops.manage import migrate

TOKEN_ALICE = "alice-demo-token-for-unit-tests-only-12345"
TOKEN_BOB = "bob-demo-token-for-unit-tests-only-67890"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    tokens = tmp_path / "tokens.json"
    tokens.write_text(json.dumps({"alice": TOKEN_ALICE, "bob": TOKEN_BOB}), encoding="utf-8")
    return replace(Settings.from_env(), api_tokens_file=tokens, demo_mode=True, version="1.0.0")


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as session:
        yield session


@pytest.fixture
def auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN_ALICE}", "Idempotency-Key": "test-job"}


@pytest.fixture
def database(settings: Settings) -> Iterator[Settings]:
    if os.getenv("CONTAINEROPS_TEST_DATABASE") != "1":
        pytest.skip("Exige PostgreSQL isolado e CONTAINEROPS_TEST_DATABASE=1")
    migration_settings = replace(
        settings,
        db_user="containerops_migrator",
        db_password_file=Path(os.getenv("MIGRATOR_PASSWORD_FILE", "/run/secrets/db_migrator")),
    )
    migrate(migration_settings, 2)
    with connect(settings) as connection:
        connection.execute("DELETE FROM jobs")
        connection.execute("UPDATE operations SET admission_paused=false")
    yield settings
    with connect(settings) as connection:
        connection.execute("DELETE FROM jobs")
        connection.execute("UPDATE operations SET admission_paused=false")

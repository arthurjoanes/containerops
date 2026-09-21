import json
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    db_host: str = "db"
    db_port: int = 5432
    db_name: str = "containerops"
    db_user: str = "containerops_app"
    db_password_file: Path = Path("/run/secrets/db_password")
    api_tokens_file: Path = Path("/run/secrets/api_tokens")
    version: str = "1.0.0"
    demo_mode: bool = False

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            db_host=os.getenv("DB_HOST", "db"),
            db_port=int(os.getenv("DB_PORT", "5432")),
            db_name=os.getenv("DB_NAME", "containerops"),
            db_user=os.getenv("DB_USER", "containerops_app"),
            db_password_file=Path(os.getenv("DB_PASSWORD_FILE", "/run/secrets/db_password")),
            api_tokens_file=Path(os.getenv("API_TOKENS_FILE", "/run/secrets/api_tokens")),
            version=os.getenv("APP_VERSION", "1.0.0"),
            demo_mode=os.getenv("DEMO_MODE", "false").lower() == "true",
        )

    @property
    def release_two(self) -> bool:
        return int(self.version.split(".")[0]) >= 2

    def password(self) -> str:
        password = self.db_password_file.read_text(encoding="utf-8").strip()
        if not password:
            raise ValueError("Arquivo de senha vazio")
        return password

    def tokens(self) -> dict[str, str]:
        raw = json.loads(self.api_tokens_file.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or not raw:
            raise ValueError("Credenciais de API devem ser um objeto JSON não vazio")
        result: dict[str, str] = {}
        for owner, token in raw.items():
            if (
                not isinstance(owner, str)
                or not owner
                or len(owner) > 100
                or not isinstance(token, str)
                or len(token) < 24
                or not token.isascii()
            ):
                raise ValueError("Credencial demo inválida")
            result[owner] = token
        if len(set(result.values())) != len(result):
            raise ValueError("Tokens devem ser únicos por proprietário")
        return result

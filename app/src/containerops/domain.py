import hashlib
import json
import unicodedata
from dataclasses import dataclass
from uuid import UUID

MAX_TEXT_BYTES = 16 * 1024
MAX_BODY_BYTES = 32 * 1024
MAX_PENDING_JOBS = 100
MAX_PENDING_JOBS_PER_OWNER = 20
MAX_ATTEMPTS = 3
LEASE_SECONDS = 5
ALGORITHM = "unicode-alnum-marks-v1"


@dataclass(frozen=True)
class TextResult:
    word_count: int
    checksum: str


@dataclass(frozen=True)
class Job:
    id: UUID
    owner: str
    payload: str
    payload_hash: str
    state: str
    attempts: int
    duration_seconds: float
    lease_token: UUID | None
    word_count: int | None
    checksum: str | None
    error_category: str | None


class JobConflict(Exception):
    pass


class AdmissionPaused(Exception):
    pass


class QueueFull(Exception):
    pass


class OwnerQueueFull(QueueFull):
    pass


class SchemaIncompatible(Exception):
    pass


def analyze_text(text: str) -> TextResult:
    """Marcas só continuam uma palavra iniciada por letra/número Unicode."""
    words = 0
    in_word = False
    for character in text:
        if character.isalnum():
            if not in_word:
                words += 1
            in_word = True
        elif not (in_word and unicodedata.category(character).startswith("M")):
            in_word = False
    return TextResult(words, hashlib.sha256(text.encode("utf-8")).hexdigest())


def fingerprint(text: str, duration_seconds: float) -> str:
    # O atraso altera o trabalho solicitado e participa da chave de idempotência.
    duration = 0.0 if duration_seconds == 0 else float(duration_seconds)
    data = json.dumps([text, duration], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(data.encode("utf-8")).hexdigest()

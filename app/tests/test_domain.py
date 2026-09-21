import hashlib

import pytest
from pydantic import ValidationError

from containerops.api import JobInput
from containerops.domain import analyze_text, fingerprint


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", 0),
        ("   \t\n!", 0),
        ("Olá, mundo!", 2),
        ("café cafe\u0301", 2),
        ("\u0301 isolada", 1),
        ("d'água guarda-chuva", 4),
        ("中文 日本語", 2),
        ("ação 123 abc42 🤖🚀 fim", 4),
        ("a\u0301\u0327b", 1),
        ("a\u200db", 2),
        ("١٢٣ русский", 2),
    ],
)
def test_explicit_unicode_word_definition(text: str, expected: int) -> None:
    assert analyze_text(text).word_count == expected


def test_checksum_uses_original_utf8_bytes() -> None:
    assert analyze_text("").checksum == (
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    )
    assert analyze_text("hello world").checksum == (
        "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"
    )
    assert analyze_text("é").checksum != analyze_text("e\u0301").checksum
    assert analyze_text("á").checksum == hashlib.sha256(b"\xc3\xa1").hexdigest()


def test_fingerprint_covers_demo_duration_and_canonicalizes_numbers() -> None:
    assert fingerprint("abc", 1) == fingerprint("abc", 1.0)
    assert fingerprint("abc", 0.0) == fingerprint("abc", -0.0)
    assert fingerprint("abc", 0) != fingerprint("abc", 1)


def test_input_limit_counts_bytes_not_characters() -> None:
    assert JobInput(text="é" * 8192).text
    with pytest.raises(ValidationError):
        JobInput(text="é" * 8193)


@pytest.mark.parametrize("value", ["\x00", "\ud800"])
def test_rejects_text_postgresql_cannot_store(value: str) -> None:
    with pytest.raises(ValidationError):
        JobInput(text=value)


@pytest.mark.parametrize("duration", [-1, 16, float("nan"), float("inf"), True, "1"])
def test_rejects_unbounded_or_ambiguous_duration(duration: object) -> None:
    with pytest.raises(ValidationError):
        JobInput.model_validate({"text": "x", "demo_duration_seconds": duration})

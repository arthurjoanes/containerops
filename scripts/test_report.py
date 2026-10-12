from __future__ import annotations

import hashlib
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import patch

import report
from report_evidence import (
    parse_record,
    read_records,
    restricted_runtime,
    select_artifacts,
    verification_result,
)

NOW = datetime(2026, 9, 21, 6, tzinfo=UTC)
RUN = "a" * 32
PROJECT = "pf-containerops-test-12345678"
IMAGE = "sha256:" + "b" * 64
CHECKS = ("host_operations", "report", "lint", "format", "types", "tests")


def job(state="succeeded", attempts=1):
    return {
        "id": "12345678-1234-1234-1234-123456789abc",
        "state": state,
        "attempts": attempts,
        "version": "2.0.0",
        "result": {"word_count": 7, "checksum": "c" * 64} if state == "succeeded" else None,
    }


class CommandText(HTMLParser):
    """Read commands as users copy them, independent of syntax highlighting spans."""

    def __init__(self, document):
        super().__init__(convert_charrefs=True)
        self.commands = []
        self.active = False
        self.feed(document)

    def handle_starttag(self, tag, attrs):
        if tag == "pre":
            self.active = True
            self.commands.append("")

    def handle_endtag(self, tag):
        if tag == "pre":
            self.active = False

    def handle_data(self, value):
        if self.active:
            self.commands[-1] += value


def panel(document, identifier):
    return document.split(f'<section id="{identifier}"', 1)[1].split("</section>", 1)[0]


class ReportTests(unittest.TestCase):
    def test_terminal_commands_are_highlighted_without_changing_source(self):
        page = self.render()
        commands = {
            line.strip()
            for command in CommandText(page).commands
            for line in command.splitlines()
            if line.strip().startswith("python ")
        }
        self.assertIn("python scripts/ops.py scan --version <versão>", commands)
        self.assertIn("python scripts/ops.py report", commands)
        self.assertIn("python scripts/ops.py backups list", commands)
        self.assertIn('class="syntax-option">--version</span>', page)
        self.assertNotIn("<versão>", page)

    def test_missing_metrics_do_not_become_words_inside_operation_descriptions(self):
        page = self.render()
        self.assertIn("Confere a imagem candidata, aplica a migração e verifica os jobs.", page)
        for invalid_phrase in (
            "Não informado jobs",
            "Não informado job salvo",
            "Não informado tentativas",
            "vNão informado",
        ):
            self.assertNotIn(invalid_phrase, page)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="containerops-report-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.evidence = self.root / "docs" / "evidence"
        self.evidence.mkdir(parents=True)

    def write(self, name, value):
        path = self.evidence / (name + ".json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def render(self, now=NOW):
        with redirect_stdout(io.StringIO()):
            return report.generate(self.root, self.root / "runtime", now=now).read_text(
                encoding="utf-8"
            )

    def outcome(self, now=NOW):
        records, errors = read_records(self.evidence)
        return verification_result(self.evidence, records.get("verification-run", {}), errors, now)

    def guide_fixture(self):
        selected = {
            "README.md": b"# ContainerOps\n",
            "docs/runbooks.md": b"# Runbooks\n",
            "docs/verification.md": b"# Verification\n",
            "docs/brand.md": b"# Brand\n",
        }
        excluded = {
            "docs/unselected-notes.md": b"Private notes are not a selected guide.\n",
            "docs/evidence/private.json": b'{"private": true}',
            "docs/report.html": b"Historical report must remain unchanged.",
            ".env": b"PRIVATE_FIXTURE=not-a-real-secret\n",
        }
        for name, content in {**selected, **excluded}.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        runtime = self.root / "reserved-runtime" / "sessions" / "co-guides"
        context = report.RuntimeContext(self.root, runtime.parent.parent, runtime, "co-guides")
        return selected, excluded, context

    @staticmethod
    def file_snapshot(paths):
        return {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in paths}

    def test_session_guides_copy_selected_markdown_and_preserve_historical_sources(self):
        selected, excluded, context = self.guide_fixture()
        originals = [self.root / name for name in {**selected, **excluded}]
        before = self.file_snapshot(originals)
        with patch.object(report, "ROOT", self.root):
            urls = report.guide_urls(self.root, context.report, True)
        bundle = (context.report.parent / urls["readme_url"]).parent
        self.assertRegex(bundle.name, r"^[0-9a-f]{16}$")
        self.assertEqual(bundle.parent, context.report.parent / "documentation")
        actual = {
            path.relative_to(bundle).as_posix(): path.read_bytes()
            for path in bundle.rglob("*")
            if path.is_file()
        }
        self.assertEqual(actual, selected)
        self.assertEqual(self.file_snapshot(originals), before)
        for name in excluded:
            self.assertFalse((bundle / name).exists(), name)

    def test_session_guides_render_direct_links_to_existing_bundled_documents(self):
        _, _, context = self.guide_fixture()
        historical = self.root / "docs" / "report.html"
        before = self.file_snapshot([historical])
        with (
            patch.object(report, "ROOT", self.root),
            patch.object(report, "active_context", return_value=context),
            redirect_stdout(io.StringIO()),
        ):
            destination = report.generate(self.root, context.runtime, now=NOW)
            urls = report.guide_urls(self.root, destination, True)
        self.assertEqual(destination, context.report)
        page = destination.read_text(encoding="utf-8")
        expected_names = {
            "readme_url": "README.md",
            "runbooks_url": "runbooks.md",
            "verification_url": "verification.md",
        }
        for key, name in expected_names.items():
            with self.subTest(document=name):
                self.assertIn(f'href="{urls[key]}"', page)
                target = destination.parent / urls[key]
                self.assertTrue(target.is_file())
                self.assertEqual(target.name, name)
                self.assertTrue(target.resolve().is_relative_to(destination.parent.resolve()))
        self.assertEqual(self.file_snapshot([historical]), before)

    def test_session_guides_reuse_identical_bundle_without_file_writes(self):
        _, _, context = self.guide_fixture()
        with patch.object(report, "ROOT", self.root):
            first = report.guide_urls(self.root, context.report, True)
            directory = context.report.parent / "documentation"
            before = self.file_snapshot(path for path in directory.rglob("*") if path.is_file())
            second = report.guide_urls(self.root, context.report, True)
        self.assertEqual(second, first)
        self.assertEqual(self.file_snapshot(before), before)
        self.assertEqual(len(list(directory.iterdir())), 1)

    def test_session_guides_reject_conflicting_bytes_without_overwriting_bundle(self):
        _, _, context = self.guide_fixture()
        with patch.object(report, "ROOT", self.root):
            urls = report.guide_urls(self.root, context.report, True)
            target = context.report.parent / urls["runbooks_url"]
            target.write_bytes(b"Conflicting fixture contents must not be overwritten.")
            directory = context.report.parent / "documentation"
            before = self.file_snapshot(path for path in directory.rglob("*") if path.is_file())
            with self.assertRaisesRegex(RuntimeError, "diverge"):
                report.guide_urls(self.root, context.report, True)
        self.assertEqual(self.file_snapshot(before), before)

    def test_session_guides_reject_hardlinked_targets_without_touching_either_name(self):
        _, _, context = self.guide_fixture()
        with patch.object(report, "ROOT", self.root):
            urls = report.guide_urls(self.root, context.report, True)
            target = context.report.parent / urls["readme_url"]
            external = self.root / "unrelated-fixture.md"
            external.write_bytes(target.read_bytes())
            target.unlink()
            os.link(external, target)
            directory = context.report.parent / "documentation"
            before = self.file_snapshot(
                [external, *(path for path in directory.rglob("*") if path.is_file())]
            )
            with self.assertRaisesRegex(RuntimeError, "links|hardlinks"):
                report.guide_urls(self.root, context.report, True)
        self.assertEqual(self.file_snapshot(before), before)

    def test_historical_guide_urls_do_not_create_session_bundle(self):
        _, _, context = self.guide_fixture()
        before = self.file_snapshot(path for path in self.root.rglob("*") if path.is_file())
        with patch.object(report, "ROOT", self.root):
            urls = report.guide_urls(self.root, context.report, False)
        self.assertEqual(
            urls,
            {
                "readme_url": "../README.md",
                "runbooks_url": "runbooks.md",
                "verification_url": "verification.md",
            },
        )
        self.assertEqual(self.file_snapshot(before), before)
        self.assertFalse(context.report.parent.exists())

    def catalog_entry(self, identifier="20260921T050000-0123abcd", **changes):
        return {
            "id": identifier,
            "source_project": PROJECT,
            "source_context": "origin-a",
            "created_at": "2026-09-21T05:00:00Z",
            "schema_version": 2,
            "size_bytes": 123456,
            "validation": "valid",
            "protected": False,
            "imported": False,
            "restore_verified": False,
            "retention_reasons": ["last-valid"],
            **changes,
        }

    def catalog_record(self, entries, **changes):
        self.write(
            "backup-catalog",
            {
                "recorded_at": "2026-09-21T05:30:00Z",
                "context": "co-captured",
                "entries": entries,
                **changes,
            },
        )

    def restore_record(self, **changes):
        self.write(
            "restore",
            {
                "recorded_at": "2026-09-21T05:00:00Z",
                "project": "pf-containerops-restore-12345678",
                "backup_id": "20260921T050000-0123abcd",
                "status": "passed",
                "checksum_verified": True,
                "snapshot_equal": True,
                "new_job": job(),
                "cleanup_succeeded": True,
                **changes,
            },
        )

    def complete_attempt(self, *, full=True):
        runtime = {
            "uid": 10001,
            "status": {"NoNewPrivs": "1", "CapEff": "0000000000000000", "Gid": "10001"},
            "blocked": ["/app/write-probe"],
        }
        controls = {
            service: {"read_only": True, "runtime": runtime}
            for service in ("api", "worker", "proxy")
        }
        controls["db"] = {"no_published_port": True}
        controls["network"] = {"front_to_database": "denied", "data_to_database": "allowed"}
        sources = {
            "test-results": {"checks": {name: {"exit_code": 0} for name in CHECKS}},
            "journey": {
                "job": job(),
                "unique_jobs": 1,
                "concurrent_requests": 6,
                "conflict": 409,
                "limits_enforced": True,
                "authorization": "anonymous 401 / other owner 404",
            },
        }
        if full:
            sources.update(
                {
                    "hardening": {"checks": controls},
                    "recovery": {
                        "sigkill": job(attempts=2),
                        "sigterm": job(),
                        "after_database_restart": job(),
                        "database_outage": {"liveness": 200, "readiness": 503, "admission": 503},
                        "recreation": {"snapshot_equal": True, "preserved_jobs": 4},
                    },
                }
            )
        entries = []
        for name, payload in sources.items():
            path = self.write(
                f"runs/{RUN}/{name}",
                {
                    "run_id": RUN,
                    "project": PROJECT,
                    "recorded_at": "2026-09-21T04:01:00Z",
                    **payload,
                },
            )
            entries.append(
                {
                    "path": path.relative_to(self.evidence).as_posix(),
                    "sha256": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest(),
                }
            )
        record = {
            "run_id": RUN,
            "project": PROJECT,
            "status": "passed",
            "success": True,
            "full": full,
            "started_at": "2026-09-21T04:00:00Z",
            "completed_at": "2026-09-21T04:02:00Z",
            "recorded_at": "2026-09-21T04:02:00Z",
            "elapsed_seconds": 162.671,
            "evidence": entries,
        }
        self.write("verification-run", record)
        return record

    def change_snapshot(self, record, name, change):
        entry = next(item for item in record["evidence"] if item["path"].endswith(f"/{name}.json"))
        path = self.evidence / entry["path"]
        payload = json.loads(path.read_text(encoding="utf-8"))
        change(payload)
        path.write_text(json.dumps(payload), encoding="utf-8")
        entry["sha256"] = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
        self.write("verification-run", record)

    def supply(self, *, age=1, scan_image=IMAGE, scan_date="2026-09-21T04:02:00Z"):
        self.write(
            "build-2.0.0",
            {"version": "2.0.0", "image_id": IMAGE, "executed_at": "2026-09-21T04:00:00Z"},
        )
        self.write(
            "scan-2.0.0",
            {
                "version": "2.0.0",
                "image_id": scan_image,
                "executed_at": scan_date,
                "status": "passed",
                "blocking_findings": [],
                "database_age_hours": age,
                "max_db_age_hours": 72,
                "unfixed_findings": 2,
                "findings_by_severity": {"HIGH": 2, "CRITICAL": 0},
            },
        )

    def test_complete_attempt_uses_archived_sources(self):
        self.complete_attempt()
        result = self.outcome()
        self.assertEqual(result.state, "pass")
        self.assertEqual(set(result.records), {"test-results", "journey", "hardening", "recovery"})
        page = self.render()
        self.assertIn(f"evidence/runs/{RUN}/journey.json", page)
        self.assertIn("162,7 s", page)
        self.assertIn("162,671 s", page)
        self.assertIn("Última verificação aprovada", page)

    def test_new_failed_attempt_does_not_inherit_old_success(self):
        record = self.complete_attempt()
        record.update(
            status="failed", success=False, evidence=[], error_category="CalledProcessError"
        )
        self.write("verification-run", record)
        self.write(
            "journey", {"recorded_at": "2026-09-20T04:01:00Z", "job": job(), "unique_jobs": 1}
        )
        self.assertEqual(self.outcome().state, "fail")
        self.assertEqual(self.outcome().records, {})
        page = self.render()
        self.assertIn("Última tentativa falhou", page)
        self.assertIn('href="evidence/journey.json"', page)
        self.assertNotIn("Última verificação aprovada", page)
        self.assertIn("Sem etapas vinculadas a esta execução", page)

    def test_in_progress_is_not_a_completed_success(self):
        record = self.complete_attempt()
        record.update(status="in_progress", success=False, completed_at=None)
        self.write("verification-run", record)
        self.assertEqual(self.outcome().state, "running")
        self.assertIn("Verificação em andamento", self.render())

    def test_reduced_scope_stays_explicit(self):
        self.complete_attempt(full=False)
        self.assertEqual(self.outcome().state, "partial")
        self.assertIn("Verificação parcial concluída", self.render())

    def test_contradictory_completion_is_invalid(self):
        record = self.complete_attempt()
        record["success"] = False
        self.write("verification-run", record)
        self.assertEqual(self.outcome().state, "invalid")
        self.assertIn("Estado da tentativa contraditório", self.render())

    def test_legacy_record_does_not_claim_scope_without_manifest(self):
        self.write(
            "verification-run",
            {
                "status": "passed",
                "success": True,
                "full": True,
                "recorded_at": "2026-09-21T04:00:00Z",
            },
        )
        self.assertEqual(self.outcome().state, "partial")
        self.assertIn("Sem manifesto dos testes", self.render())

    def test_missing_snapshot_is_invalid(self):
        record = self.complete_attempt()
        (self.evidence / record["evidence"][0]["path"]).unlink()
        self.assertEqual(self.outcome().state, "invalid")
        self.assertEqual(self.outcome().records, {})

    def test_checksum_mismatch_cannot_approve_attempt(self):
        record = self.complete_attempt()
        record["evidence"][0]["sha256"] = "sha256:" + "0" * 64
        self.write("verification-run", record)
        self.assertEqual(self.outcome().state, "invalid")
        self.assertIn("SHA-256 diverge", self.render())

    def test_snapshot_from_another_project_is_rejected(self):
        record = self.complete_attempt()
        self.change_snapshot(
            record, "journey", lambda value: value.update(project="pf-containerops")
        )
        self.assertEqual(self.outcome().state, "invalid")

    def test_snapshot_from_another_attempt_is_rejected(self):
        record = self.complete_attempt()
        self.change_snapshot(record, "journey", lambda value: value.update(run_id="b" * 32))
        self.assertEqual(self.outcome().state, "invalid")

    def test_manifest_path_cannot_escape_evidence_directory(self):
        record = self.complete_attempt()
        record["evidence"] = [{"path": "../private.json", "sha256": "sha256:" + "0" * 64}]
        self.write("verification-run", record)
        (self.root / "docs" / "private.json").write_text("DO NOT DISPLAY", encoding="utf-8")
        self.assertEqual(self.outcome().state, "invalid")
        self.assertNotIn("DO NOT DISPLAY", self.render())

    def test_duplicate_snapshot_is_invalid(self):
        record = self.complete_attempt()
        record["evidence"].append(record["evidence"][0])
        self.write("verification-run", record)
        self.assertEqual(self.outcome().state, "invalid")

    def test_missing_required_step_is_partial(self):
        record = self.complete_attempt()
        record["evidence"] = record["evidence"][:2]
        self.write("verification-run", record)
        self.assertEqual(self.outcome().state, "partial")
        self.assertIn("Faltam etapas", self.render())

    def test_failed_command_contradicts_success_status(self):
        record = self.complete_attempt()
        self.change_snapshot(
            record, "test-results", lambda value: value["checks"]["tests"].update(exit_code=1)
        )
        self.assertEqual(self.outcome().state, "invalid")
        self.assertIn("Conclusão contradiz os testes", self.render())

    def test_old_result_is_not_presented_as_recent(self):
        self.complete_attempt()
        self.assertEqual(self.outcome(NOW + timedelta(days=2)).state, "stale")
        page = self.render(NOW + timedelta(days=2))
        self.assertIn("há mais de 24 h", page)

    def test_future_completion_is_invalid(self):
        record = self.complete_attempt()
        record["completed_at"] = "2026-09-22T04:02:00Z"
        self.write("verification-run", record)
        self.assertEqual(self.outcome().state, "invalid")

    def test_snapshot_outside_attempt_window_is_invalid(self):
        record = self.complete_attempt()
        self.change_snapshot(
            record, "journey", lambda value: value.update(recorded_at="2026-09-20T04:01:00Z")
        )
        self.assertEqual(self.outcome().state, "invalid")

    def test_main_stack_alias_cannot_replace_archived_check(self):
        self.complete_attempt()
        self.write(
            "hardening", {"project": "pf-containerops", "checks": {"api": {"read_only": False}}}
        )
        self.assertEqual(self.outcome().state, "pass")
        self.assertTrue(self.outcome().records["hardening"]["checks"]["api"]["read_only"])

    def test_nested_malformed_data_is_reported_without_crashing(self):
        self.write("demo", {"job": {"result": []}})
        page = self.render()
        self.assertIn("JSONs inválidos", page)
        self.assertIn("job.result: objeto esperado", page)
        self.assertIn("Nenhum resultado registrado", page)

    def test_nonfinite_numbers_and_duplicate_keys_are_rejected(self):
        for content in (
            '{"elapsed_seconds":NaN}',
            '{"elapsed_seconds":Infinity}',
            '{"success":true,"success":false}',
        ):
            with self.subTest(content=content), self.assertRaises(ValueError):
                parse_record(content, "verification-run")

    def test_counts_reject_booleans_strings_and_fractional_values(self):
        for value in (True, "7", 7.5, -1):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_record(json.dumps({"job": {"result": {"word_count": value}}}), "demo")

    def test_empty_directory_is_explicit(self):
        self.assertEqual(self.outcome().state, "missing")
        page = self.render()
        self.assertIn("Nenhuma verificação registrada", page)
        self.assertIn("Nenhum resultado registrado", page)
        self.assertNotIn('class="badge pass"', page)

    def test_json_array_root_is_rejected(self):
        self.write("journey", ["not an object"])
        records, errors = read_records(self.evidence)
        self.assertNotIn("journey", records)
        self.assertIn("journey.json", errors)
        self.assertIn("a raiz precisa ser um objeto JSON", self.render())

    def test_unknown_state_is_escaped_and_not_translated_to_success(self):
        self.write(
            "demo",
            {
                "recorded_at": "2026-09-21T04:03:00Z",
                "job": {"state": '<script>alert("x")</script>'},
            },
        )
        page = self.render()
        self.assertEqual(page.count("<script>"), 1)  # Only the local navigation script.
        self.assertNotIn('<script>alert("x")</script>', page)
        self.assertIn("&lt;script&gt;", page)
        self.assertIn("Estado não reconhecido", page)

    def test_success_without_result_is_incomplete(self):
        self.write(
            "demo",
            {"recorded_at": "2026-09-21T04:03:00Z", "job": {"state": "succeeded", "attempts": 1}},
        )
        self.assertIn("Resultado incompleto", self.render())

    def test_latest_failed_job_is_not_replaced_by_successful_journey(self):
        self.complete_attempt()
        self.write("demo", {"recorded_at": "2026-09-21T04:03:00Z", "job": job("failed")})
        page = self.render()
        self.assertIn("<h3>Falhou</h3>", page)
        self.assertIn('href="evidence/demo.json"', page)

    def test_explicit_null_collections_do_not_crash_the_report(self):
        for name, value in (
            ("cache-experiment", {"runs": None}),
            ("scan-2.0.0", {"findings_by_severity": None}),
            ("hardening", {"checks": None}),
            ("final-state", {"health": None}),
        ):
            with self.subTest(name=name):
                self.write(name, value)
                self.assertIn("JSONs inválidos", self.render())

    def test_false_observation_cannot_approve_a_complete_attempt(self):
        cases = (
            ("journey", lambda value: value.update(limits_enforced=False)),
            ("hardening", lambda value: value["checks"]["api"].update(read_only=False)),
            ("recovery", lambda value: value["recreation"].update(snapshot_equal=False)),
        )
        for name, change in cases:
            with self.subTest(name=name):
                record = self.complete_attempt()
                self.change_snapshot(record, name, change)
                self.assertEqual(self.outcome().state, "invalid")
                self.assertIn("Resultado não corresponde aos testes", self.render())

    def test_job_failure_contradicts_completed_verification(self):
        record = self.complete_attempt()
        self.change_snapshot(record, "journey", lambda value: value.update(job=job("failed")))
        self.assertEqual(self.outcome().state, "invalid")
        self.assertNotIn("Última verificação aprovada", self.render())

    def test_text_cannot_substitute_runtime_write_probes(self):
        record = self.complete_attempt()
        self.change_snapshot(
            record,
            "hardening",
            lambda value: value["checks"]["api"]["runtime"].update(blocked="write denied"),
        )
        self.assertEqual(self.outcome().state, "invalid")

    def test_missing_job_result_prevents_approval(self):
        record = self.complete_attempt()
        self.change_snapshot(record, "recovery", lambda value: value["sigkill"].pop("result"))
        self.assertEqual(self.outcome().state, "invalid")

    def test_missing_scan_uses_context_without_placeholder_counts(self):
        self.supply(scan_image="sha256:" + "d" * 64)
        page = self.render()
        self.assertIn("Faltam auditoria e scan desta imagem", page)
        self.assertIn("Gerar scan pelo terminal", page)
        self.assertNotIn("Não informado HIGH", page)
        self.assertNotIn("Não informado CRITICAL", page)

    def test_job_protocol_state_is_available_in_technical_details(self):
        self.write("demo", {"job": job(), "recorded_at": "2026-09-21T04:00:00Z"})
        page = self.render()
        details = page.index('<details class="checksum">')
        self.assertLess(details, page.index("Estado na API"))
        self.assertIn("<h3>Concluído</h3>", page)

    def test_numeric_display_is_localized_and_precision_is_limited(self):
        self.assertEqual(report.shown(162.671, " s"), "162,7 s")
        self.assertEqual(report.shown(1234), "1.234")
        self.assertEqual(report.shown(0), "0")
        self.assertEqual(report.shown(None), "Não informado")
        self.assertEqual(report.shown(float("nan")), "Dado inválido")

    def test_latest_failed_release_is_not_hidden_by_previous_promotion(self):
        self.write(
            "release",
            {"recorded_at": "2026-09-21T04:00:00Z", "rolled_back": False, "candidate_job": job()},
        )
        self.write("release-failed", {"recorded_at": "2026-09-21T04:05:00Z", "rolled_back": True})
        page = self.render()
        self.assertIn("A última tentativa de release falhou", page)
        self.assertIn('href="evidence/release-failed.json"', page)

    def test_scan_from_another_image_does_not_approve_selected_build(self):
        self.supply(scan_image="sha256:" + "d" * 64)
        records, _ = read_records(self.evidence)
        self.assertEqual(select_artifacts(records), ("build-2.0.0", "", ""))
        self.assertIn("Sem scan compatível", self.render())

    def test_old_scan_of_same_image_is_not_reused_after_new_build(self):
        self.supply(scan_date="2026-09-20T04:00:00Z")
        records, _ = read_records(self.evidence)
        self.assertEqual(select_artifacts(records)[2], "")

    def test_expired_scanner_database_does_not_pass_policy(self):
        self.supply(age=80)
        page = self.render()
        self.assertIn("Base do scanner vencida", page)
        self.assertNotIn('class="badge pass"', page)

    def test_invalid_build_does_not_silently_fall_back_to_an_old_build(self):
        self.supply()
        self.write("build-3.0.0", {"duration_seconds": "bad"})
        page = self.render()
        self.assertIn("build-3.0.0.json", page)
        self.assertIn("Release Não informado", page)

    def test_matching_but_invalid_image_ids_cannot_prove_artifact_identity(self):
        self.write(
            "build-2.0.0",
            {"version": "2.0.0", "image_id": "invalid", "executed_at": "2026-09-21T04:00:00Z"},
        )
        self.write(
            "audit-2.0.0",
            {"version": "2.0.0", "image_id": "invalid", "executed_at": "2026-09-21T04:01:00Z"},
        )
        self.write(
            "scan-2.0.0",
            {"version": "2.0.0", "image_id": "invalid", "executed_at": "2026-09-21T04:02:00Z"},
        )
        records, errors = read_records(self.evidence)
        self.assertEqual(select_artifacts(records), ("", "", ""))
        self.assertEqual(len(errors), 3)
        self.assertIn("image_id: digest SHA-256 esperado", self.render())

    def test_build_selection_normalizes_timezones(self):
        records = {
            "build-new": {"executed_at": "2026-09-21T02:00:00-03:00"},
            "build-old": {"executed_at": "2026-09-21T04:00:00Z"},
        }
        self.assertEqual(select_artifacts(records)[0], "build-new")

    def test_read_only_flag_does_not_prove_process_identity(self):
        self.assertFalse(restricted_runtime({"read_only": True}))
        table = report.resource_table(
            {
                "checks": {
                    "api": {
                        "user": "configured",
                        "runtime": {"uid": 10001, "status": {"Gid": "10001"}},
                    }
                }
            }
        )
        self.assertNotIn("configured", table)
        self.assertIn("10001:10001", table)

    def test_operation_navigation_keeps_all_results_in_static_html(self):
        self.complete_attempt()
        page = self.render()
        for identifier in (
            "overview",
            "result",
            "tls",
            "recovery",
            "backups",
            "operations",
            "rollback",
            "artifacts",
            "evidence",
        ):
            self.assertIn(f'data-operation="{identifier}"', page)
            self.assertIn(f'<section id="{identifier}" data-panel', page)
        self.assertNotIn("data-panel hidden", page)
        self.assertIn("Snapshot · somente leitura", page)
        self.assertNotIn("fetch(", page)

    def test_catalog_commands_replay_recorded_context_without_using_current_default(self):
        entry = self.catalog_entry()
        self.catalog_record([entry])
        content = panel(self.render(), "backups")
        commands = CommandText(content).commands
        restore = next(command for command in commands if " restore-test " in command)
        self.assertIn("CONTAINEROPS_SESSION='co-captured'", restore)
        self.assertIn(f"--backup {entry['id']}", restore)
        self.assertNotIn("CONTAINEROPS_SESSION='default'", restore)
        self.assertNotIn(str(self.root), content)

    def test_catalog_ledger_separates_creation_integrity_restore_protection_and_import(self):
        entry = self.catalog_entry(protected=True, imported=True, restore_verified=True)
        self.catalog_record([entry])
        content = panel(self.render(), "backups")
        for fact in (
            entry["id"],
            PROJECT,
            "origin-a",
            "21/09/2026",
            "Íntegro",
            "Protegido",
            "Importado",
        ):
            self.assertIn(fact, content)
        self.assertIn("Verificado no catálogo", content)
        self.assertNotIn("Sem marca; importado; restore verificado", content)

    def test_imported_copy_command_requires_operator_trust_decision(self):
        entry = self.catalog_entry(imported=True)
        self.catalog_record([entry])
        content = panel(self.render(), "backups")
        command = next(
            command for command in CommandText(content).commands if " restore-test " in command
        )
        self.assertIn(f"--backup {entry['id']}", command)
        self.assertNotIn("--trusted-source", command)
        self.assertIn("--trusted-source", content)

    def test_unknown_copy_has_no_restore_command_even_if_receipt_flag_claims_success(self):
        entry = self.catalog_entry(validation="unknown", restore_verified=True)
        self.catalog_record([entry])
        content = panel(self.render(), "backups")
        self.assertIn(entry["id"], content)
        self.assertIn("Desconhecid", content)
        self.assertFalse(
            any(" restore-test " in command for command in CommandText(content).commands)
        )

    def test_empty_catalog_and_invalid_entry_catalog_are_distinguishable(self):
        self.catalog_record([])
        empty_page = self.render()
        empty_content = panel(empty_page, "backups")
        self.assertIn("Nenhuma cópia", empty_content)
        self.catalog_record([None, 3, {"id": "../private"}])
        invalid_content = panel(self.render(), "backups")
        self.assertNotIn("Nenhuma cópia catalogada", invalid_content)
        self.assertIn("inválid", invalid_content.casefold())
        self.assertNotIn("../private", invalid_content)

    def test_catalog_unknown_and_invalid_integrity_have_distinct_states_and_no_restore_commands(
        self,
    ):
        unknown_id, invalid_id = "copy-unknown", "copy-invalid"
        self.catalog_record(
            [
                self.catalog_entry(unknown_id, validation="unknown", restore_verified=True),
                self.catalog_entry(invalid_id, validation="invalid", restore_verified=True),
            ]
        )
        content = panel(self.render(), "backups")
        self.assertIn('class="badge pending">Desconhecido</span>', content)
        self.assertIn('class="badge fail">Dado inválido</span>', content)
        self.assertIn('value="unknown">Desconhecida ou inválida</option>', content)
        commands = CommandText(content).commands
        for identifier in (unknown_id, invalid_id):
            with self.subTest(identifier=identifier):
                self.assertIn(f'id="copy-{identifier}"', content)
                self.assertFalse(
                    any(f"restore-test --backup {identifier}" in command for command in commands)
                )
        self.assertNotIn("Verificado no catálogo", content)

    def test_missing_catalog_remains_explicit_without_inventing_a_valid_empty_list(self):
        content = panel(self.render(), "backups")
        self.assertIn("Catálogo ainda não registrado", content)
        self.assertFalse(
            any(" restore-test " in command for command in CommandText(content).commands)
        )

    def test_catalog_keeps_valid_long_identifiers_and_rejects_untrusted_private_fields(self):
        long_id = "backup-" + "a" * 120
        safe = self.catalog_entry(long_id)
        malicious = self.catalog_entry(
            "<img src=x onerror=alert(1)>",
            source_project="C:\\Users\\private-owner\\secret",
            source_context="/home/private-owner",
            validation="<script>alert(1)</script>",
        )
        self.catalog_record([safe, malicious])
        page = self.render()
        content = panel(page, "backups")
        self.assertIn(long_id, content)
        for unsafe in ("<img src=x", "<script>alert", "private-owner", "onerror=alert"):
            self.assertNotIn(unsafe, content)
        self.assertEqual(page.count("<script>"), 1)

    def test_invalid_captured_context_is_not_interpolated_into_commands(self):
        for captured in (
            "co'; Invoke-Expression danger; '",
            "con",
            "nul",
            "../session",
            "C:\\Users\\private-owner",
        ):
            with self.subTest(context=captured):
                self.catalog_record([self.catalog_entry()], context=captured)
                content = panel(self.render(), "backups")
                self.assertNotIn("Invoke-Expression", content)
                self.assertNotIn("private-owner", content)
                self.assertFalse(
                    any(" restore-test " in command for command in CommandText(content).commands)
                )

    def test_valid_catalog_id_does_not_make_untrusted_origin_or_reasons_safe(self):
        entry = self.catalog_entry(
            source_project="C:\\Users\\private-owner\\database",
            source_context="/home/private-owner/runtime",
            retention_reasons=["last-valid", "<img src=x onerror=alert(1)>", "/home/private-owner"],
        )
        self.catalog_record([entry])
        content = panel(self.render(), "backups")
        for unsafe in ("private-owner", "<img src=x", "onerror=alert"):
            self.assertNotIn(unsafe, content)

    def test_malformed_catalog_collection_is_not_reported_as_no_copies(self):
        for entries in (None, "not a list", {"id": "copy"}):
            with self.subTest(entries=entries):
                self.catalog_record(entries)
                content = panel(self.render(), "backups")
                self.assertNotIn("Nenhuma cópia catalogada", content)
                self.assertIn("inválid", content.casefold())

    def test_cleanup_failure_prevents_overall_restore_success_despite_valid_data_and_job(self):
        for changes in (
            {"status": "passed", "cleanup_succeeded": False},
            {"status": "failed", "cleanup_succeeded": True},
        ):
            with self.subTest(changes=changes):
                self.restore_record(**changes)
                page = self.render()
                self.assertIn('data-state="fail" data-operation="recovery"', page)
                content = panel(page, "recovery")
                self.assertIn("Limpeza", content)
                self.assertIn("falh", content.casefold())
                self.assertIn("Checksum do backup", content)
                self.assertIn("Novo job após restauração", content)

    def test_running_restore_cannot_inherit_success_from_approved_partial_facts(self):
        self.restore_record(status="in_progress")
        page = self.render()
        self.assertIn('data-state="running" data-operation="recovery"', page)
        self.assertIn("Restauração em andamento", panel(page, "recovery"))

    def test_passed_status_without_cleanup_does_not_imply_complete_restore(self):
        self.restore_record()
        path = self.evidence / "restore.json"
        record = json.loads(path.read_text(encoding="utf-8"))
        record.pop("cleanup_succeeded")
        path.write_text(json.dumps(record), encoding="utf-8")
        page = self.render()
        self.assertNotIn('data-state="pass" data-operation="recovery"', page)
        content = panel(page, "recovery")
        self.assertIn("Limpeza", content)
        self.assertIn("Sem resultado", content)

    def test_legacy_restore_preserves_data_facts_and_marks_cleanup_as_unrecorded(self):
        self.restore_record()
        path = self.evidence / "restore.json"
        record = json.loads(path.read_text(encoding="utf-8"))
        record.pop("status")
        record.pop("cleanup_succeeded")
        path.write_text(json.dumps(record), encoding="utf-8")
        page = self.render()
        self.assertIn('data-state="partial" data-operation="recovery"', page)
        content = panel(page, "recovery")
        self.assertIn("Limpeza", content)
        self.assertIn("Sem resultado", content)
        self.assertIn("não informa", content)

    def test_restore_complete_success_requires_all_four_recorded_checks(self):
        self.restore_record()
        page = self.render()
        self.assertIn('data-state="pass" data-operation="recovery"', page)
        content = panel(page, "recovery")
        for check in (
            "Checksum do backup",
            "Dados no volume restaurado",
            "Novo job após restauração",
            "Limpeza",
        ):
            self.assertIn(check, content)

    def test_cleanup_and_restore_status_wrong_types_are_invalid_records(self):
        for changes in (
            {"cleanup_succeeded": "true"},
            {"cleanup_succeeded": 1},
            {"cleanup_succeeded": None},
            {"status": True},
            {"status": ["passed"]},
            {"status": None},
            {"status": "unknown-success"},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                parse_record(
                    json.dumps({"status": "passed", "cleanup_succeeded": True, **changes}),
                    "restore",
                )

    def test_restore_and_backup_identity_are_not_combined(self):
        self.write(
            "backup",
            {
                "recorded_at": "2026-09-20T03:00:00Z",
                "source_project": "backup-project",
                "schema_version": 1,
                "elapsed_seconds": 3,
            },
        )
        self.write(
            "restore",
            {
                "recorded_at": "2026-09-21T05:00:00Z",
                "project": "restore-project",
                "checksum_verified": True,
                "snapshot_equal": True,
                "new_job": job(),
                "recovery_seconds": 45.2,
            },
        )
        page = self.render()
        panel = page.split('<section id="recovery"', 1)[1].split("</section>", 1)[0]
        restore_part, backup_part = panel.split("Registro da cópia", 1)
        self.assertIn("restore-project", restore_part)
        self.assertNotIn("backup-project", restore_part)
        self.assertIn("backup-project", backup_part)
        self.assertIn("Checksum do backup", panel)
        self.assertIn("Novo job após restauração", panel)

    def test_latest_invalid_release_remains_invalid_in_navigation(self):
        self.write(
            "release",
            {"recorded_at": "2026-09-21T04:00:00Z", "rolled_back": False, "candidate_job": job()},
        )
        (self.evidence / "release-failed.json").write_text("broken", encoding="utf-8")
        page = self.render()
        link = page.split('data-operation="operations"', 1)[1].split("</a>", 1)[0]
        self.assertIn("Inválido", link)
        self.assertNotIn("Aprovado", link)

    def test_job_navigation_matches_pending_failed_and_incomplete_results(self):
        incomplete = {**job(), "result": None}
        cases = (
            (job("queued"), "queued", "Na fila", "Na fila"),
            (job("running"), "running", "Em andamento", "Processando"),
            (job(), "pass", "Aprovado", "Concluído"),
            (incomplete, "partial", "Revisar", "Resultado incompleto"),
            (job("failed"), "fail", "Falhou", "Falhou"),
            (job("unexpected"), "invalid", "Inválido", "Estado não reconhecido"),
            ({}, "missing", "Ausente", "Nenhum resultado registrado"),
        )
        for payload, state, navigation_label, detail_label in cases:
            with self.subTest(state=state):
                self.write("demo", {"recorded_at": "2026-09-21T04:00:00Z", "job": payload})
                page = self.render()
                link = page.split('data-operation="result"', 1)[1].split("</a>", 1)[0]
                panel = page.split('<section id="result"', 1)[1].split("</section>", 1)[0]
                self.assertIn(f'data-state="{state}" data-operation="result"', page)
                if state == "pass":
                    self.assertNotIn("Aprovado", link)
                else:
                    self.assertIn(navigation_label, link)
                self.assertIn(f"<h3>{detail_label}</h3>", panel)


if __name__ == "__main__":
    unittest.main()

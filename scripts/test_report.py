from __future__ import annotations

import hashlib
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import UTC, datetime, timedelta
from pathlib import Path

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


class ReportTests(unittest.TestCase):
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
        self.assertIn("Sem scan para esta imagem", page)
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
        restore_part, backup_part = panel.split("Backup disponível", 1)
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


if __name__ == "__main__":
    unittest.main()

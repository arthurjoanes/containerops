"""A bounded view of saved job observations, not live queue telemetry."""

from __future__ import annotations

import math
from html import escape
from urllib.parse import quote

from report_evidence import at, record_time

STATES = {
    "queued": "Na fila",
    "running": "Em execução",
    "succeeded": "Concluídos",
    "failed": "Falhos",
}
JOB_PATHS = {
    "demo": ("job",),
    "journey": ("job",),
    "recovery": (
        "sigkill",
        "sigterm",
        "after_database_restart",
        "worker_shutdown.queued_after_restart",
    ),
    "restore": ("new_job",),
    "release": ("candidate_job",),
    "release-failed": ("candidate_job",),
    "rollback": ("candidate_job", "rollback_job"),
}
DURATIONS = {
    "verification-run": ("Verificação", "elapsed_seconds"),
    "backup": ("Backup", "elapsed_seconds"),
    "restore": ("Restore + novo job", "recovery_seconds"),
    "release": ("Troca de imagem", "maintenance_seconds"),
    "release-failed": ("Troca de imagem recusada", "maintenance_seconds"),
    "rollback": ("Rollback", "maintenance_seconds"),
}


def activity_data(records: dict[str, dict]) -> dict:
    latest = {}
    operations = []
    for name, record in records.items():
        stamped = record_time(record)
        if stamped is None:
            continue
        project = record.get("project", "")
        project = project if isinstance(project, str) else ""
        source = record.get("_source", name + ".json")
        for path in JOB_PATHS.get(name, ()):
            job = at(record, path)
            if (
                not isinstance(job, dict)
                or not isinstance(job.get("state"), str)
                or job["state"] not in STATES
            ):
                continue
            identifier = job.get("id")
            if not isinstance(identifier, str) or not identifier:
                continue
            row = {
                "id": identifier,
                "project": project,
                "state": job["state"],
                "at": stamped.isoformat(),
                "day": stamped.date().isoformat(),
                "source": source,
            }
            key = (project, identifier)
            if key not in latest or row["at"] > latest[key]["at"]:
                latest[key] = row
        if name in DURATIONS:
            label, field = DURATIONS[name]
            seconds = record.get(field)
            if type(seconds) in (int, float) and math.isfinite(seconds) and seconds >= 0:
                operations.append(
                    {
                        "label": label,
                        "seconds": seconds,
                        "day": stamped.date().isoformat(),
                        "source": source,
                    }
                )
    jobs = sorted(latest.values(), key=lambda row: (row["at"], row["project"], row["id"]))
    days = sorted([row["day"] for row in jobs + operations])
    return {
        "jobs": jobs,
        "operations": operations,
        "first_day": days[0] if days else "",
        "last_day": days[-1] if days else "",
    }


def activity_panel(records: dict[str, dict]) -> str:
    data = activity_data(records)
    first, last = data["first_day"], data["last_day"]
    limits = f'min="{first}" max="{last}"' if first else ""
    job_rows = "".join(
        f'<tr data-job-day="{row["day"]}" data-job-state="{row["state"]}">'
        f'<th scope="row"><code>{escape(row["id"])}</code><span class="job-project">'
        f"{escape(row['project']) or 'Projeto não informado'}</span></th>"
        f'<td>{STATES[row["state"]]}</td><td><time datetime="{row["at"]}">{row["day"]} UTC</time></td>'
        f'<td><a href="evidence/{quote(row["source"])}">JSON</a></td></tr>'
        for row in data["jobs"]
    )
    operation_rows = "".join(
        f'<tr data-operation-day="{row["day"]}" data-seconds="{row["seconds"]}">'
        f'<th scope="row">{row["label"]}</th><td class="numeric">{row["seconds"]:,.1f} s</td>'
        f'<td>{row["day"]} UTC</td><td><a href="evidence/{quote(row["source"])}">JSON</a></td></tr>'
        for row in data["operations"]
    )
    counts = {state: sum(row["state"] == state for row in data["jobs"]) for state in STATES}
    return (
        '<div class="activity-analysis" data-activity>'
        '<p class="analysis-scope">Último estado de cada job por projeto. Amostra de testes · UTC.</p>'
        '<form class="analysis-controls" data-activity-controls hidden>'
        f'<label>De<input type="date" data-activity-from aria-describedby="activity-filter-status" {limits} value="{first}"></label>'
        f'<label>Até<input type="date" data-activity-to aria-describedby="activity-filter-status" {limits} value="{last}"></label>'
        '<button type="button" data-activity-reset>Todo o registro</button></form>'
        '<p id="activity-filter-status" class="analysis-feedback" data-activity-feedback role="status" aria-live="polite"></p>'
        '<dl class="activity-totals">'
        + "".join(
            f'<div><dt>{label}</dt><dd data-state-total="{state}">{counts[state]}</dd></div>'
            for state, label in STATES.items()
        )
        + '</dl><div class="analysis-charts">'
        "<figure><figcaption><h3>Jobs observados por dia</h3>"
        "<p>Um job conta na data de sua última observação.</p></figcaption>"
        '<div data-job-chart class="analysis-chart" hidden></div>'
        '<p class="analysis-empty" data-jobs-empty'
        + (" hidden" if job_rows else "")
        + ">Nenhum job com estado e data válidos neste período.</p>"
        '<details class="record-details"><summary>Ver jobs e fontes</summary>'
        '<div class="table-wrap" role="region" aria-label="Jobs observados" tabindex="0">'
        "<table><caption>Jobs únicos por projeto, com a última observação disponível</caption>"
        '<thead><tr><th scope="col">Job / projeto</th><th scope="col">Estado</th>'
        '<th scope="col">Observação</th><th scope="col">Fonte</th></tr></thead>'
        f"<tbody data-job-rows>{job_rows}</tbody></table></div></details></figure>"
        "<figure><figcaption><h3>Tempo por operação</h3>"
        "<p>Duração dos ensaios registrados.</p></figcaption>"
        '<div data-duration-chart class="analysis-chart" hidden></div>'
        '<p class="analysis-empty" data-operations-empty'
        + (" hidden" if operation_rows else "")
        + ">Nenhuma duração registrada neste período.</p>"
        '<details class="record-details"><summary>Ver durações e fontes</summary>'
        '<div class="table-wrap" role="region" aria-label="Duração das operações" tabindex="0">'
        "<table><caption>Duração registrada, em segundos</caption><thead><tr>"
        '<th scope="col">Operação</th><th scope="col">Duração</th>'
        '<th scope="col">Data UTC</th><th scope="col">Fonte</th></tr></thead>'
        f"<tbody data-operation-rows>{operation_rows}</tbody></table></div></details></figure>"
        "</div></div>"
    )

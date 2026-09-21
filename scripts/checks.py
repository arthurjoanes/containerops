import hashlib
import http.client
import json
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from ops import (
    EVIDENCE,
    ROOT,
    RUNTIME,
    Stack,
    drain,
    evidence,
    image_id,
    now,
    read_json,
    run,
    setup_secrets,
    wait_for,
    write_json,
)


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def kernel_fields(output):
    return {
        line.split(":", 1)[0]: line.split(":", 1)[1].strip()
        for line in output.splitlines()
        if line.startswith(("Uid:", "Gid:", "CapEff:", "NoNewPrivs:"))
    }


def verify_kernel(status, uid, service):
    require(
        status.get("Uid", "").split() == [str(uid)] * 4, f"UIDs efetivos divergentes: {service}"
    )
    require(
        status.get("Gid", "").split() == [str(uid)] * 4, f"GIDs efetivos divergentes: {service}"
    )
    require(
        status.get("CapEff") == "0000000000000000", f"Capabilities efetivas presentes: {service}"
    )
    require(status.get("NoNewPrivs") == "1", f"NNP não efetivo: {service}")


def verify_host_limits(service, item, expected_tmpfs):
    host = item["HostConfig"]
    require(0 < host["Memory"] <= 512 * 1024**2, f"Limite de memória inválido: {service}")
    require(0 < host["NanoCpus"] <= 1_000_000_000, f"Limite de CPU inválido: {service}")
    require(0 < host["PidsLimit"] <= 128, f"Limite de PIDs inválido: {service}")
    require(
        not host["Privileged"] and host["NetworkMode"] != "host",
        f"Privileged/host network: {service}",
    )
    require(
        not host.get("Devices") and not host.get("DeviceRequests"),
        f"Dispositivos extras: {service}",
    )
    require(not host.get("DeviceCgroupRules"), f"Permissão adicional de dispositivos: {service}")
    require(
        any("no-new-privileges" in value for value in host.get("SecurityOpt", [])),
        f"NNP ausente: {service}",
    )
    require("ALL" in (host.get("CapDrop") or []), f"Capabilities não removidas: {service}")
    log = host["LogConfig"]
    require(
        log["Type"] == "json-file"
        and log["Config"].get("max-size") == "5m"
        and log["Config"].get("max-file") == "3",
        f"Rotação de logs divergente: {service}",
    )
    tmpfs = host.get("Tmpfs") or {}
    require(set(tmpfs) == expected_tmpfs, f"Conjunto de tmpfs divergente: {service}")
    for target, settings in tmpfs.items():
        options = settings.lower().split(",")
        require(
            {"rw", "noexec", "nosuid"} <= set(options), f"Flags tmpfs ausentes: {service}:{target}"
        )
        sizes = [option.split("=", 1)[1] for option in options if option.startswith("size=")]
        require(len(sizes) == 1, f"Tamanho tmpfs ausente: {service}:{target}")
        value = sizes[0]
        multiplier = {"k": 1024, "m": 1024**2, "g": 1024**3}.get(value[-1], 1)
        size = int(value[:-1] if multiplier != 1 else value) * multiplier
        require(0 < size <= 16 * 1024**2, f"Tmpfs acima do limite: {service}:{target}")
    require(
        not any(mount.get("Source", "").endswith("docker.sock") for mount in item["Mounts"]),
        f"Socket Docker montado: {service}",
    )
    return {
        "memory_bytes": host["Memory"],
        "nano_cpus": host["NanoCpus"],
        "pids_limit": host["PidsLimit"],
        "log_config": log,
        "tmpfs": tmpfs,
        "extra_devices": False,
    }


def verify_secret_mounts(service, item, expected, actual):
    mounts = {
        mount["Destination"].removeprefix("/run/secrets/"): mount
        for mount in item["Mounts"]
        if mount["Destination"].startswith("/run/secrets/")
    }
    require(
        set(mounts) == expected and set(actual) == expected,
        f"Secrets não correspondem à configuração: {service}",
    )
    for name in expected:
        require(mounts[name].get("RW") is False, f"Mount de secret gravável: {service}/{name}")
        require(
            actual[name]["readable"] and not actual[name]["writable"],
            f"Permissão de secret incorreta: {service}/{name}",
        )
    return {name: {**actual[name], "mount_read_only": True} for name in sorted(expected)}


def shell_runtime_probe(stack, service, uid, check_writes):
    script = """set -eu
grep -E '^(Uid|Gid|CapEff|NoNewPrivs):' /proc/self/status
for secret in /run/secrets/*; do
    [ -f "$secret" ] || continue
    readable=0; writable=0
    if cat "$secret" >/dev/null; then readable=1; fi
    if (: >> "$secret") 2>/dev/null; then writable=1; fi
    printf 'SECRET %s %s %s ' "${secret##*/}" "$readable" "$writable"
    stat -c '%a %u %g' "$secret"
done
"""
    if check_writes:
        script += """for path in /etc/nginx/containerops-write-probe /containerops-write-probe /usr/containerops-write-probe; do
    if (printf 'probe' > "$path") 2>/dev/null; then
        rm -f "$path"
        printf 'WRITABLE %s\n' "$path"
    else
        printf 'BLOCKED %s\n' "$path"
    fi
done
temporary=$(mktemp /tmp/containerops-probe.XXXXXX)
printf 'probe' > "$temporary"
rm "$temporary"
"""
    output = stack.compose(
        "exec", "-T", "--user", f"{uid}:{uid}", service, "sh", "-ec", script, capture=True
    ).stdout
    secrets = {}
    for line in output.splitlines():
        if line.startswith("SECRET "):
            _, name, readable, writable, mode, owner, group = line.split()
            secrets[name] = {
                "readable": readable == "1",
                "writable": writable == "1",
                "mode": mode,
                "uid": int(owner),
                "gid": int(group),
            }
    blocked = [
        line.removeprefix("BLOCKED ") for line in output.splitlines() if line.startswith("BLOCKED ")
    ]
    if check_writes:
        require(
            len(blocked) == 3 and "WRITABLE " not in output,
            f"Escrita fora do tmpfs permitida: {service}",
        )
    return {"uid": uid, "status": kernel_fields(output), "blocked": blocked, "secrets": secrets}


def database_role_probe(stack, service, expected_user):
    # Statements that unexpectedly succeed are still rolled back. No business row
    # is changed; these probes run with only the helper's own mounted credential.
    permission_sql = """SELECT json_build_object(
        'user', current_user, 'superuser', r.rolsuper, 'createdb', r.rolcreatedb,
        'createrole', r.rolcreaterole, 'replication', r.rolreplication, 'bypassrls', r.rolbypassrls,
        'owns_schema', n.nspowner=r.oid,
        'create_schema_objects', has_schema_privilege(current_user,'public','CREATE'),
        'select_jobs', has_table_privilege(current_user,'public.jobs','SELECT'),
        'insert_jobs', has_table_privilege(current_user,'public.jobs','INSERT'),
        'update_jobs', has_table_privilege(current_user,'public.jobs','UPDATE'),
        'delete_jobs', has_table_privilege(current_user,'public.jobs','DELETE'),
        'memberships', (SELECT count(*) FROM pg_auth_members WHERE member=r.oid),
        'rows_read', (SELECT count(*) FROM public.jobs))
    FROM pg_roles r CROSS JOIN pg_namespace n
    WHERE r.rolname=current_user AND n.nspname='public';"""
    table = "containerops_permission_probe_" + uuid.uuid4().hex[:8]
    if service == "backup":
        statements = [
            "UPDATE public.jobs SET attempts=attempts WHERE false",
            "DELETE FROM public.jobs WHERE false",
            "INSERT INTO public.jobs(id) SELECT '00000000-0000-0000-0000-000000000000'::uuid WHERE false",
            f"CREATE TABLE public.{table} (id integer)",
        ]
        attempts = "\n".join(
            f"BEGIN {statement}; RAISE EXCEPTION 'Permissão de escrita inesperada'; "
            "EXCEPTION WHEN insufficient_privilege THEN NULL; END;"
            for statement in statements
        )
        probe_sql = "BEGIN; DO $probe$ BEGIN " + attempts + " END; $probe$; ROLLBACK;"
    else:
        probe_sql = f"BEGIN; CREATE TABLE public.{table} (id integer); INSERT INTO public.{table} VALUES (1); ROLLBACK;"
    script = (
        """set -eu
set -- /run/secrets/*
[ "$#" -eq 1 ] && [ "$1" = /run/secrets/db_password ]
[ -r "$1" ] && [ ! -w "$1" ]
export PGPASSWORD="$(cat /run/secrets/db_password)"
export PGCONNECT_TIMEOUT=5
export PGOPTIONS='-c statement_timeout=3000 -c lock_timeout=2000'
psql --no-psqlrc --quiet --tuples-only --no-align --set ON_ERROR_STOP=1 <<'CONTAINEROPS_SQL'
"""
        + probe_sql
        + "\n"
        + permission_sql
        + "\nCONTAINEROPS_SQL\n"
    )
    result = stack.compose(
        "run", "--rm", "--no-deps", "--entrypoint", "/bin/sh", service, "-ec", script, capture=True
    )
    permissions = json.loads(result.stdout.strip())
    require(permissions["user"] == expected_user, f"Credencial de helper incorreta: {service}")
    require(
        all(
            permissions[field] is False
            for field in ("superuser", "createdb", "createrole", "replication", "bypassrls")
        )
        and permissions["memberships"] == 0,
        f"Privilégios extras de role: {service}",
    )
    require(permissions["select_jobs"] is True, f"Leitura necessária ausente: {service}")
    expected_write = service == "restore"
    require(
        all(
            permissions[field] is expected_write
            for field in (
                "owns_schema",
                "create_schema_objects",
                "insert_jobs",
                "update_jobs",
                "delete_jobs",
            )
        ),
        f"Permissões não correspondem à função do helper: {service}",
    )
    permissions["transactional_write_probes"] = (
        "allowed_and_rolled_back" if expected_write else "denied"
    )
    permissions["exact_secret_set_readable_readonly"] = True
    return permissions


def hardening(stack):
    checks = {}
    items = {service: stack.inspect(service) for service in ("api", "worker", "proxy", "db")}
    expected_secrets = {
        "api": {"db_password", "api_tokens"},
        "worker": {"db_password"},
        "proxy": {"tls_certificate", "tls_key"} if stack.tls else set(),
        "db": {"db_admin", "db_migrator", "db_app", "db_backup"},
    }
    for service, uid in (("api", 10001), ("worker", 10001), ("proxy", 101), ("db", 999)):
        item = items[service]
        host = item["HostConfig"]
        tmpfs = {"/tmp", "/var/run/postgresql"} if service == "db" else {"/tmp"}
        limits = verify_host_limits(service, item, tmpfs)
        if service != "db":
            require(
                item["Config"]["User"] == f"{uid}:{uid}",
                f"Usuário configurado incorreto: {service}",
            )
            require(
                host["ReadonlyRootfs"] and not host.get("CapAdd"),
                f"Hardening divergente: {service}",
            )
        if service in ("api", "worker"):
            code = """import os,json,pathlib,tempfile
blocked=[]
for path in ['/app/containerops-write-probe','/etc/containerops-write-probe','/containerops-write-probe']:
 try:
  pathlib.Path(path).write_text('probe')
 except OSError: blocked.append(path)
 else: pathlib.Path(path).unlink()
with tempfile.NamedTemporaryFile(dir='/tmp') as temporary:
 temporary.write(b'probe'); temporary.flush()
status=pathlib.Path('/proc/self/status').read_text()
secrets={}
for path in pathlib.Path('/run/secrets').iterdir():
 with path.open('rb') as secret: secret.read(1)
 stat=path.stat()
 secrets[path.name]={'readable':True,'writable':os.access(path,os.W_OK),'mode':oct(stat.st_mode & 511),'uid':stat.st_uid,'gid':stat.st_gid}
print(json.dumps({'uid':os.getuid(),'blocked':blocked,'status':{line.split(':',1)[0]:line.split(':',1)[1].strip() for line in status.splitlines() if line.startswith(('Uid:','Gid:','CapEff:','NoNewPrivs:'))},'secrets':secrets}))
"""
            actual = json.loads(
                stack.compose("exec", "-T", service, "python", "-c", code, capture=True).stdout
            )
            require(
                actual["uid"] == uid and len(actual["blocked"]) == 3,
                f"Escrita/UID efetivo incorreto: {service}",
            )
        else:
            actual = shell_runtime_probe(stack, service, uid, check_writes=service == "proxy")
        verify_kernel(actual["status"], uid, service)
        actual["secrets"] = verify_secret_mounts(
            service, item, expected_secrets[service], actual["secrets"]
        )
        checks[service] = {
            "image": item["Image"],
            "user": item["Config"]["User"],
            "read_only": host["ReadonlyRootfs"],
            **limits,
            "runtime": actual,
        }
        if service != "proxy":
            require(not host.get("PortBindings"), f"Porta publicada indevidamente: {service}")
            checks[service]["no_published_port"] = True
    process = stack.compose(
        "exec",
        "-T",
        "db",
        "sh",
        "-ec",
        "grep -E '^(Uid|Gid|CapEff|NoNewPrivs):' /proc/1/status",
        capture=True,
    ).stdout
    verify_kernel(kernel_fields(process), 999, "db PID1")
    checks["db"]["effective_process_uid"] = kernel_fields(process)["Uid"]
    require(
        items["api"]["Image"] == items["worker"]["Image"],
        "API e worker executam imagens diferentes",
    )
    ports = items["proxy"]["HostConfig"]["PortBindings"]
    require(
        set(ports) == ({"8080/tcp", "8443/tcp"} if stack.tls else {"8080/tcp"}),
        "Portas inesperadas no proxy",
    )
    for bindings in ports.values():
        require(
            bindings and all(binding["HostIp"] == "127.0.0.1" for binding in bindings),
            "Porta pública fora de loopback",
        )
    front_network, data_network = stack.project + "_front", stack.project + "_data"
    membership = {
        "proxy": {front_network},
        "api": {front_network, data_network},
        "worker": {data_network},
        "db": {data_network},
    }
    for service, expected in membership.items():
        require(
            set(items[service]["NetworkSettings"]["Networks"]) == expected,
            f"Redes fora da matriz: {service}",
        )
    network_objects = json.loads(
        run(["docker", "network", "inspect", front_network, data_network], capture=True).stdout
    )
    for network in network_objects:
        require(
            network["Internal"] is (network["Name"] == data_network),
            "Flag internal de rede divergente",
        )
        require(
            network["Driver"] == "bridge"
            and network["Labels"].get("com.docker.compose.project") == stack.project,
            "Rede não pertence ao projeto esperado",
        )
    db_ip = items["db"]["NetworkSettings"]["Networks"][data_network]["IPAddress"]
    probe = "import socket,sys; s=socket.socket();s.settimeout(2);r=s.connect_ex((sys.argv[1],5432));s.close();print(r);sys.exit(0 if (r==0)==(sys.argv[2]=='allow') else 1)"
    for network, expectation in ((data_network, "allow"), (front_network, "deny")):
        run(
            [
                "docker",
                "run",
                "--rm",
                "--name",
                stack.project + "-netcheck-" + uuid.uuid4().hex[:8],
                "--network",
                network,
                "--read-only",
                "--cap-drop=ALL",
                "--security-opt=no-new-privileges:true",
                "--memory=64m",
                "--cpus=0.25",
                "--pids-limit=32",
                "--entrypoint",
                "python",
                items["api"]["Image"],
                "-c",
                probe,
                db_ip,
                expectation,
            ],
            capture=True,
        )
    api_live = json.loads(
        stack.compose(
            "exec", "-T", "proxy", "wget", "-qO-", "http://api:8000/health/live", capture=True
        ).stdout
    )
    require(api_live.get("status") == "alive", "Proxy não alcança API pela rede frontal")
    checks["network"] = {
        "front_to_database": "denied",
        "data_to_database": "allowed",
        "proxy_to_api": "allowed",
        "database_ip": db_ip,
        "data_internal": True,
        "memberships": {service: sorted(names) for service, names in membership.items()},
    }
    checks["database_roles"] = {
        "backup": database_role_probe(stack, "backup", "containerops_backup"),
        "migrator": database_role_probe(stack, "restore", "containerops_migrator"),
    }
    checks["stats"] = stack.compose("stats", "--no-stream", "--format", "json", capture=True).stdout
    evidence("hardening", {"project": stack.project, "checks": checks})
    return checks


def http_boundary_checks(stack, job_id):
    """Send literal duplicate headers through the proxy, which TestClient bypasses."""
    token = read_json(stack.directory / "secrets" / "api_tokens")["alice"]
    auth = ("Authorization", "Bearer " + token)
    content = b'{"text":"boundary"}'
    cases = (
        (
            "bearer_case",
            "GET",
            f"/v1/jobs/{job_id}",
            [("Authorization", "bEaReR " + token)],
            None,
            {200},
        ),
        ("duplicate_authorization", "GET", f"/v1/jobs/{job_id}", [auth, auth], None, {400, 401}),
        (
            "duplicate_key",
            "POST",
            "/v1/jobs",
            [auth, ("Idempotency-Key", "first"), ("Idempotency-Key", "second")],
            content,
            {422},
        ),
        ("blank_key", "POST", "/v1/jobs", [auth, ("Idempotency-Key", "   ")], content, {422}),
        (
            "body_at_limit",
            "POST",
            "/v1/jobs",
            [auth, ("Idempotency-Key", uuid.uuid4().hex)],
            content.ljust(32768),
            {201},
        ),
        (
            "body_over_limit",
            "POST",
            "/v1/jobs",
            [auth, ("Idempotency-Key", uuid.uuid4().hex)],
            content.ljust(32769),
            {413},
        ),
    )
    observed = {}
    for name, method, path, headers, body, accepted in cases:
        connection = http.client.HTTPConnection("127.0.0.1", stack.port, timeout=8)
        try:
            connection.putrequest(method, path)
            for key, value in headers:
                connection.putheader(key, value)
            if body is not None:
                connection.putheader("Content-Type", "application/json")
                connection.putheader("Content-Length", str(len(body)))
            connection.endheaders(body)
            response = connection.getresponse()
            response.read()
            require(
                response.status in accepted, f"Contrato HTTP divergente: {name}={response.status}"
            )
            observed[name] = response.status
        finally:
            connection.close()
    key = uuid.uuid4().hex
    negative = stack.job("Zero sem sinal", duration=-0.0, key=key)
    positive = stack.job("Zero sem sinal", duration=0, key=key)
    require(negative["id"] == positive["id"], "Zero com sinal duplicou trabalho")
    stack.completed(positive["id"])
    observed["signed_zero_same_job"] = True
    return observed


def journey(stack):
    key = "concurrent-" + uuid.uuid4().hex
    text = "Olá mundo! Café e ação. 東京 42"
    with ThreadPoolExecutor(max_workers=6) as pool:
        created = list(pool.map(lambda _: stack.job(text, key=key), range(6)))
    require(len({item["id"] for item in created}) == 1, "Idempotência concorrente duplicou jobs")
    job = stack.completed(created[0]["id"])
    require(job["result"]["word_count"] == 7, "Contagem Unicode incorreta")
    require(
        job["result"]["checksum"] == hashlib.sha256(text.encode()).hexdigest(), "Checksum incorreto"
    )
    status, _ = stack.request("POST", "/v1/jobs", {"text": "outro"}, key=key)
    require(status == 409, "Mesma chave/payload diferente deve conflitar")
    require(
        stack.request("GET", f"/v1/jobs/{job['id']}", owner="bob")[0] == 404,
        "Leitura cruzada autorizada",
    )
    require(
        stack.request("GET", f"/v1/jobs/{job['id']}", owner=None)[0] == 401,
        "Leitura anônima autorizada",
    )
    require(
        stack.request("POST", "/v1/jobs", {"text": "a"}, owner=None, key=uuid.uuid4().hex)[0]
        == 401,
        "Mutação anônima aceita",
    )
    require(
        stack.request("POST", "/v1/jobs", {"text": "á" * 9000}, key=uuid.uuid4().hex)[0]
        in (413, 422),
        "Limite de bytes ausente",
    )
    require(
        stack.request(
            "POST", "/v1/jobs", {"text": "a", "demo_duration_seconds": 100}, key=uuid.uuid4().hex
        )[0]
        == 422,
        "Duração demo sem limite",
    )
    require(
        stack.request("GET", "/internal/metrics")[0] in (403, 404),
        "Métricas internas expostas pelo proxy",
    )
    evidence(
        "journey",
        {
            "project": stack.project,
            "job": job,
            "concurrent_requests": 6,
            "unique_jobs": 1,
            "authorization": "anonymous 401 / other owner 404",
            "conflict": 409,
            "limits_enforced": True,
            "http_boundaries": http_boundary_checks(stack, job["id"]),
        },
    )
    return job


def failure_experiments(stack):
    require(
        stack.project.startswith("pf-containerops-test-"),
        "Injeções de falha exigem projeto isolado de teste",
    )
    result = {}

    def read_job(job_id):
        status, job = stack.request("GET", f"/v1/jobs/{job_id}")
        require(status == 200, f"Falha ao consultar job durante o teste: HTTP {status}")
        return job

    slow = stack.job("Recuperar depois de término abrupto", duration=9)
    wait_for(lambda: read_job(slow["id"])["state"] == "running", "worker assume job")
    running_before_kill = read_job(slow["id"])
    require(running_before_kill["state"] == "running", "Job terminou antes da injeção")
    stack.compose("kill", "-s", "SIGKILL", "worker")
    stack.compose("up", "-d", "worker")
    recovered = stack.completed(slow["id"])
    require(recovered["attempts"] >= 2, "Falha não exercitou recuperação de lease")
    require(
        recovered["result"]
        == {
            "word_count": 5,
            "checksum": hashlib.sha256("Recuperar depois de término abrupto".encode()).hexdigest(),
        },
        "Job recuperado produziu resultado incorreto",
    )
    result["sigkill_before"] = running_before_kill
    result["sigkill"] = recovered

    # Both observations happen before restart: success after restart alone would
    # not prove graceful completion or that the worker stopped acquiring jobs.
    graceful = stack.job("Preservar trabalho em SIGTERM", duration=12)
    wait_for(lambda: read_job(graceful["id"])["state"] == "running", "worker assume job SIGTERM")
    queued = stack.job("Aguardar o reinício do worker")
    queued_before = read_job(queued["id"])
    require(
        queued_before["state"] == "queued" and queued_before["attempts"] == 0,
        "Segundo job precisa estar na fila antes do SIGTERM",
    )
    stack.compose("stop", "-t", "20", "worker")
    worker = stack.inspect("worker")
    require(worker["State"]["ExitCode"] == 0, "Worker não concluiu o shutdown")
    graceful_after_stop = read_job(graceful["id"])
    queued_after_stop = read_job(queued["id"])
    require(
        graceful_after_stop["state"] == "succeeded" and graceful_after_stop["attempts"] == 1,
        "Worker não concluiu o job em curso antes de encerrar",
    )
    require(
        queued_after_stop["state"] == "queued" and queued_after_stop["attempts"] == 0,
        "Worker adquiriu outro job após receber SIGTERM",
    )
    result["sigterm"] = graceful_after_stop
    result["worker_shutdown"] = {
        "exit_code": worker["State"]["ExitCode"],
        "finished_before_restart": graceful_after_stop,
        "queued_before_restart": queued_after_stop,
    }
    stack.compose("up", "-d", "worker")
    result["worker_shutdown"]["queued_after_restart"] = stack.completed(queued["id"])

    api_before_stop = stack.inspect("api")
    uvicorn_version = stack.compose(
        "exec",
        "-T",
        "api",
        "python",
        "-c",
        "import uvicorn; print(uvicorn.__version__)",
        capture=True,
    ).stdout.strip()
    accepted = stack.job("Preservar job durante reinício da API", duration=3)
    wait_for(
        lambda: read_job(accepted["id"])["state"] == "running",
        "job admitido antes do SIGTERM da API",
    )
    stack.compose("stop", "-t", "20", "api")
    api = stack.inspect("api")
    shutdown = run(
        [
            "docker",
            "logs",
            "--since",
            api_before_stop["State"]["StartedAt"],
            "--tail",
            "80",
            api_before_stop["Id"],
        ],
        capture=True,
    )
    shutdown_logs = shutdown.stdout + shutdown.stderr
    lifespan_stopped = False
    for line in shutdown_logs.splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict) and record.get("service") == "api":
            lifespan_stopped |= record.get("event") == "stopped"
    state = api["State"]
    # Uvicorn 0.53 restores handlers and re-emits SIGTERM after lifespan shutdown.
    # On Linux the resulting 143 is expected; exit code alone cannot prove grace.
    require(
        state["Status"] == "exited"
        and not state["Running"]
        and not state["OOMKilled"]
        and not state["Error"]
        and state["ExitCode"] in (0, 143),
        f"API encerrou por falha inesperada: exit={state['ExitCode']}, OOM={state['OOMKilled']}",
    )
    require(
        lifespan_stopped
        and "Application shutdown complete." in shutdown_logs
        and "Finished server process" in shutdown_logs
        and "timeout graceful shutdown exceeded" not in shutdown_logs,
        "API não completou o shutdown de lifespan antes de encerrar",
    )
    stack.compose("up", "-d", "--no-deps", "api")
    # The proxy re-resolves the API address; readiness retries tolerate that window.
    stack.ready()
    accepted_after_restart = stack.completed(accepted["id"])
    require(
        accepted_after_restart["attempts"] == 1, "Reinício da API interferiu no job já admitido"
    )
    require(
        accepted_after_restart["result"]["checksum"]
        == hashlib.sha256("Preservar job durante reinício da API".encode()).hexdigest(),
        "Job admitido antes do SIGTERM da API perdeu conteúdo",
    )
    result["api_sigterm"] = {
        "exit_code": state["ExitCode"],
        "oom_killed": state["OOMKilled"],
        "lifespan_stopped": lifespan_stopped,
        "uvicorn_shutdown_complete": True,
        "uvicorn_version": uvicorn_version,
        "expected_exit_codes": [0, 143],
        "exit_contract": "Uvicorn 0.53 reemite SIGTERM após shutdown completo; Linux retorna 143.",
        "shutdown_logs": shutdown_logs,
        "readiness_after_restart": 200,
        "preserved_job": accepted_after_restart,
    }

    actual_schema = stack.snapshot()["schema_version"]
    change_schema = "import sys\nfrom containerops.config import Settings\nfrom containerops.database import connect\nwith connect(Settings.from_env()) as connection:\n    changed = connection.execute('UPDATE schema_version SET version=%s', (int(sys.argv[1]),))\n    if changed.rowcount != 1:\n        raise RuntimeError('schema_version deve conter uma única linha')\n"
    try:
        # Only the migrator can update schema_version; application grants stay intact.
        stack.compose(
            "run", "--rm", "--no-deps", "migrate", "python", "-c", change_schema, "99", capture=True
        )
        live = stack.request("GET", "/health/live", owner=None)
        ready = stack.request("GET", "/health/ready", owner=None)
        require(
            live[0] == 200 and ready[0] == 503,
            f"Schema incompatível não alterou readiness: {live[0]}/{ready[0]}",
        )
        result["incompatible_schema"] = {
            "injected_schema_version": 99,
            "actual_schema_version": actual_schema,
            "liveness": live[0],
            "readiness": ready[0],
        }
    finally:
        stack.compose(
            "run",
            "--rm",
            "--no-deps",
            "migrate",
            "python",
            "-c",
            change_schema,
            str(actual_schema),
            capture=True,
        )
    stack.ready()
    require(
        stack.snapshot()["schema_version"] == actual_schema,
        "Versão real do schema não foi restaurada após injeção",
    )
    result["incompatible_schema"]["restored_readiness"] = 200

    stack.compose("stop", "db")
    try:
        live = stack.request("GET", "/health/live", owner=None)
        ready = stack.request("GET", "/health/ready", owner=None)
        admission = stack.request(
            "POST", "/v1/jobs", {"text": "banco ausente"}, key=uuid.uuid4().hex
        )
        require(
            live[0] == 200 and ready[0] == 503 and admission[0] == 503,
            f"Saúde durante indisponibilidade incoerente: {live[0]}/{ready[0]}/{admission[0]}",
        )
        result["database_outage"] = {
            "liveness": live[0],
            "readiness": ready[0],
            "admission": admission[0],
        }
    finally:
        stack.compose("up", "-d", "--wait", "db")
    stack.ready()
    result["after_database_restart"] = stack.completed(stack.job("Banco recuperado")["id"])
    before = drain(stack)
    result["worker_logs"] = stack.compose("logs", "--tail", "80", "worker", capture=True).stdout
    result["api_logs"] = stack.compose("logs", "--tail", "80", "api", capture=True).stdout
    stack.stop()
    stack.start()
    after = stack.snapshot()
    require(before == after, "Recriação de containers perdeu estado")
    stack.manage("resume")
    result["recreation"] = {"snapshot_equal": True, "preserved_jobs": len(after["jobs"])}
    evidence("recovery", {"project": stack.project, **result})
    return result


class VerificationRun:
    "Guarda cada execução e seus JSONs no mesmo diretório."

    def __init__(self, full):
        self.started = time.monotonic()
        run_id = uuid.uuid4().hex
        self.data = {
            "run_id": run_id,
            "project": "pf-containerops-test-" + run_id[:8],
            "started_at": now(),
            "completed_at": None,
            "full": full,
            "evidence": [],
        }

    def record(self, status, **details):
        self.data.update(
            status=status,
            success=status == "passed",
            elapsed_seconds=round(time.monotonic() - self.started, 3),
            **details,
        )
        if status != "in_progress":
            self.data["completed_at"] = now()
        evidence("verification-run", dict(self.data))

    def archive(self, name):
        source = read_json(EVIDENCE / f"{name}.json")
        require(source.get("project") == self.data["project"], "JSON pertence a outra execução")
        relative = f"runs/{self.data['run_id']}/{name}.json"
        target = EVIDENCE / relative
        require(not target.exists(), "JSON desta execução já foi arquivado")
        write_json(target, {**source, "run_id": self.data["run_id"]})
        self.data["evidence"].append(
            {
                "path": relative,
                "sha256": "sha256:" + hashlib.sha256(target.read_bytes()).hexdigest(),
            }
        )
        self.record("in_progress")


def verify(full=True):
    import supply

    attempt = VerificationRun(full)
    attempt.record("in_progress")
    stack = None
    cleanup_required = False
    quality = {}

    def collect(name, result):
        quality[name] = {
            "exit_code": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
        if result.returncode:
            evidence("test-results", {"project": attempt.data["project"], "checks": quality})
            attempt.archive("test-results")
            raise RuntimeError(f"Verificação falhou: {name}")

    try:
        collect(
            "host_operations",
            run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"], check=False),
        )
        collect(
            "report",
            run(
                [
                    sys.executable,
                    "-m",
                    "unittest",
                    "discover",
                    "-s",
                    "scripts",
                    "-p",
                    "test_report.py",
                    "-v",
                ],
                check=False,
            ),
        )
        stack = Stack(attempt.data["project"])
        stack.assert_fresh()
        cleanup_required = True
        setup_secrets(stack.directory)
        with supply.build_context(ROOT, RUNTIME) as context:
            run(
                [
                    "docker",
                    "buildx",
                    "build",
                    "--builder",
                    "pf-containerops-builder",
                    "--load",
                    "--provenance=false",
                    "--target",
                    "test",
                    "-t",
                    "containerops-test:local",
                    "--secret",
                    f"id=sentinel,src={RUNTIME / 'secrets' / 'sentinel'}",
                    "-f",
                    context / "docker" / "app.Dockerfile",
                    context,
                ],
                timeout=900,
                cwd=context,
            )
        stack.start()
        attempt.record(
            "in_progress",
            application_image_id=stack.image,
            test_image_id=image_id("containerops-test:local"),
        )
        stack.compose("stop", "api", "worker")
        for name, command in (
            ("lint", ["python", "-m", "ruff", "check", "--no-cache", "/app/src", "/app/tests"]),
            (
                "format",
                [
                    "python",
                    "-m",
                    "ruff",
                    "format",
                    "--check",
                    "--no-cache",
                    "/app/src",
                    "/app/tests",
                ],
            ),
            ("types", ["python", "-m", "mypy", "--cache-dir", "/dev/null", "/app/src"]),
            ("tests", ["python", "-m", "pytest", "/app/tests"]),
        ):
            checked = stack.compose(
                "run", "--rm", "--no-deps", "test", *command, timeout=300, check=False
            )
            collect(name, checked)
        evidence("test-results", {"project": stack.project, "checks": quality})
        attempt.archive("test-results")
        stack.compose("up", "-d", "--wait", "api", "worker")
        stack.ready()
        journey(stack)
        attempt.archive("journey")
        if full:
            hardening(stack)
            attempt.archive("hardening")
            failure_experiments(stack)
            attempt.archive("recovery")
    except Exception as error:
        attempt.record("failed", error_category=type(error).__name__)
        if cleanup_required:
            try:
                stack.compose("logs", "--tail", "60")
            except RuntimeError:
                pass
        raise
    finally:
        if cleanup_required:
            try:
                stack.destroy_test()
            except Exception as error:
                attempt.record("failed", cleanup_error_category=type(error).__name__)
                raise
    attempt.record("passed")

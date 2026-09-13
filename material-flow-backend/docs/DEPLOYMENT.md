# Material Flow Backend Deployment Runbook

This runbook records the executable deployment-package contract for the
backend. Network topology and access policy remain governed by the existing
system architecture documents; this file does not contain a machine address,
hostname, or credential.

## Package contract

The deployable source package must contain these paths together:

- `app/main.py` - FastAPI application and safe startup initialization.
- `app/migrate.py` - explicit database initialization and migration entry point.
- `VERSION` - the single package version identifier.
- `requirements.txt` - pinned runtime dependencies.
- `requirements-test.txt` - the runtime requirements plus test-only dependencies.
- `material-flow.service` - the systemd process contract.

`app.main:app`, `FastAPI.version`, and the `/healthz` response all read the
same value from `VERSION`. Change that file as part of a release; do not add a
second version source or use a runtime value that can make the package
ambiguous.

## Runtime preparation

From the deployment directory, create the virtual environment and install the
runtime lock file:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

For local verification, install the test lock file instead:

```bash
.venv/bin/python -m pip install -r requirements-test.txt
```

The service reads its environment from an external environment file. Keep
`INITIAL_ADMIN_PASSWORD` out of Git, shell history, logs, and command output;
it is required only when the database has no initial administrator. The data
and upload directories may be overridden with `MATERIAL_FLOW_DATA` and
`MATERIAL_FLOW_UPLOADS` when the runtime filesystem layout requires it.

## Migration and startup

The migration is safe to run repeatedly and is serialized for concurrent
startup. Run the explicit entry point from the package root after the external
runtime environment has been loaded:

```bash
.venv/bin/python -m app.migrate
```

The systemd unit runs the same command as `ExecStartPre`, then starts:

```text
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

The application keeps its startup initialization as a safety net for direct
Uvicorn launches. The service binds only to the loopback interface; external
TLS termination and access control belong to the existing reverse-proxy and
network policy.

## Health verification

After startup, probe `/healthz` through the local service boundary:

```bash
curl --fail --silent http://127.0.0.1:8000/healthz
```

A healthy response has `status: "ok"`, `service: "material-flow"`,
`database: "ok"`, and a `version` equal to the contents of `VERSION`.
`serverTime` is informational. An unhealthy response is HTTP 503 and contains
only the stable service, version, and health fields; database paths and
internal exception details are not returned.

## Release gate

Run these checks from `material-flow-backend/` before packaging:

```bash
python3 -m compileall -q app tests
python3 -m pytest -q
python3 tests/test_auth_security.py
python3 tests/test_contract_acceptance.py
python3 tests/test_order_material_status.py
python3 tests/test_token_lifecycle.py
```

The four direct scripts are intentionally excluded from pytest collection
because they own their isolated test database and process lifecycle. No test
database, runtime environment file, machine identifier, or credential belongs
in the deployment package or Git history.

# Material Flow Backend Deployment Runbook

This runbook records the executable deployment-package contract for the
backend. Network topology and access policy remain governed by the existing
system architecture documents; this file does not contain a VPS address,
hostname, or credential.

## Package contract

The deployable source package must contain these paths together:

- `app/main.py` - FastAPI application and safe startup initialization.
- `app/migrate.py` - explicit database initialization and migration entry point.
- `app/seed.py` - explicit opt-in test-fixture entry point; never run implicitly.
- `VERSION` - the single package version identifier.
- `requirements.txt` - pinned runtime dependencies.
- `requirements-test.txt` - the runtime requirements plus test-only dependencies.
- `material-flow.service` - the systemd process contract.

`app.main:app`, `FastAPI.version`, the `/openapi.json` document, and the
`/healthz` response all read the same value from `VERSION`. Change that file as
part of a release; do not add a second version source or use a runtime value
that can make the package ambiguous.

`VERSION` is the only release version source. The systemd unit does not define
a separate version: it starts `app.main:app` from this package, and the
application reads the package `VERSION` file. Keep the unit and this runbook
versionless so a release cannot leave a stale hard-coded version behind.

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

`ENABLE_ADMIN_ROLE_PREVIEW` is an opt-in test-only switch for ADMIN role
workbench previews. Missing, empty, or unrecognized values are treated as
`false`; leave it unset in production. When explicitly set to `true`, the
server still requires an authenticated ADMIN, emits redacted
`ADMIN_ROLE_PREVIEW` audit events, and permits only the two read-only
workbench queries; its enter/exit endpoints only append the corresponding
redacted audit event and do not change the session role or permissions.

`app.seed` is a separate test-data operation. It is disabled unless the
command includes `--seed` or the environment explicitly sets
`ENABLE_TEST_DATA_SEED=true`. It creates only a small, fixed-prefix
`MF_TEST_SEED_V1` fixture set with synthetic credentials and is safe to run
repeatedly. Do not set this flag in production.

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

The checked-in unit contract is:

```ini
ExecStartPre=/srv/material-flow/.venv/bin/python -m app.migrate
ExecStart=/srv/material-flow/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

The application keeps its startup initialization as a safety net for direct
Uvicorn launches. The service binds only to the loopback interface; external
TLS termination and access control belong to the existing reverse-proxy and
network policy.

After a systemd-backed release, load the environment file and run the fixture
operation explicitly, only in a test/staging database:

```bash
cd /srv/material-flow
set -a; . /etc/material-flow/material-flow.env; set +a
.venv/bin/python -m app.migrate
ENABLE_TEST_DATA_SEED=true .venv/bin/python -m app.seed
systemctl restart material-flow
```

Alternatively use `.venv/bin/python -m app.seed --seed`. The seed command
prints validated counts and checks foreign keys/status values. Never commit
`data/material_flow.db`, uploads, environment files, or logs; keep runtime
data outside Git.

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

## Release checklist

Complete this checklist from `material-flow-backend/` before packaging. It is
local-only; do not point these checks at a real VPS.

- [ ] `VERSION` matches `FastAPI.version`, `/openapi.json` `info.version`, and
  `/healthz` `version`.
- [ ] The systemd unit remains versionless and its commands match the unit
  contract documented above.
- [ ] Every runtime and test dependency is pinned with `==`; install from the
  locked requirements files.
- [ ] Run `app.migrate` twice against the same local database and confirm the
  schema and seed counts remain stable.
- [ ] In a non-production database, run `app.seed --seed` twice and confirm
  fixture counts remain stable; leave `ENABLE_TEST_DATA_SEED` unset in production.
- [ ] Confirm systemd runs `python -m app.migrate` in `ExecStartPre` before
  Uvicorn and binds only to `127.0.0.1`.
- [ ] Run the automated gate below and `git diff --check`.

## Automated gate

Run these checks from `material-flow-backend/` before packaging:

```bash
.venv/bin/python -m compileall -q app tests
.venv/bin/python -m pytest -q
.venv/bin/python tests/test_auth_security.py
.venv/bin/python tests/test_contract_acceptance.py
.venv/bin/python tests/test_order_material_status.py
.venv/bin/python tests/test_token_lifecycle.py
git diff --check
```

The four direct scripts are intentionally excluded from pytest collection
because they own their isolated test database and process lifecycle. No test
database, runtime environment file, machine identifier, or credential belongs
in the deployment package or Git history.

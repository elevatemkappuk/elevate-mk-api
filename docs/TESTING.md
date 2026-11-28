# API Testing and Performance

This is the authoritative living guide for Elevate MK API test execution,
fixtures, isolation, performance, troubleshooting, and CI conventions. The
workspace and API `AGENTS.md` files establish policy and authority boundaries;
this guide records the operational details without changing application or
production security settings.

During the current Staff Management implementation, permanent tests may be
created and maintained but automated tests, test-database creation, migration
runtime validation, regression suites and provider calls are deferred until
final hardening. Source compilation, Angular production builds, migration-plan
inspection and diff checks remain permitted. Existing executed results remain
historical evidence and are not current acceptance verification.

## Automatic test infrastructure

`config/settings.py` sets `TEST_RUNNER` to
`config.test_runner.ElevateTestRunner`, so the ordinary `manage.py test`
command automatically uses the project runner. It:

- applies the fast password hasher only inside automated test execution,
  including spawned workers, and restores the configured hashers afterward;
- keeps fresh databases migration-authoritative;
- repairs only verified reused serial `--keepdb` databases to the canonical
  roles, privacy capabilities, role defaults, governance baseline, and
  reference taxonomies;
- avoids unnecessary seed writes for database-free suites; and
- rejects `--keepdb` with parallel workers because reused worker clones cannot
  be repaired safely.

Production, development, and deployment password-hashing settings are not
changed. The production-hasher compatibility canary explicitly verifies that
production-equivalent encoding and checking remain usable.

## Test design and fixture standards

Agents modifying tests must:

- reuse canonical fixtures and `config.test_utils.test_actor()` when a role
  mutation requires an attributable authorized actor;
- preserve actor attribution, production authorization boundaries, deterministic
  test isolation, and positive and negative authorization coverage;
- prefer `setUpTestData()` for reusable class-level fixtures;
- use `SimpleTestCase` for database-independent tests, `TestCase` for ordinary
  database-backed tests, and `TransactionTestCase` only for real transaction,
  commit, rollback, locking, concurrency, or migration behavior;
- avoid repeated user, Person, role, capability, schema, and other expensive
  setup; mock external services; and avoid unnecessary sleeps and retries; and
- investigate unusually expensive tests rather than weakening assertions,
  security coverage, migration verification, or isolation.

Tests specifically verifying production password hashing must explicitly use
production-equivalent hashing configuration. Test-only speed settings must not
leak into application or deployment settings.

## Canonical local commands

Run commands from the API repository with the project virtual environment:

```powershell
# Individual test, class, application, or affected groups
.\.venv\Scripts\python.exe manage.py test people.tests.PersonOverviewApiTests.test_name
.\.venv\Scripts\python.exe manage.py test people.tests.PersonOverviewApiTests
.\.venv\Scripts\python.exe manage.py test people
.\.venv\Scripts\python.exe manage.py test people staff_access audit

# Complete acceptance: fresh isolated PostgreSQL database, serial
$env:DB_NAME = 'elevate_gdpr_full_<date>'; .\.venv\Scripts\python.exe manage.py test --noinput --timing --durations 25 -v 1

# Explicit serial reuse after migration and canonical-seed verification
$env:DB_NAME = '<dedicated-isolated-regression-db>'; .\.venv\Scripts\python.exe manage.py test --keepdb --noinput -v 1

# Migration drift and migration-plan checks
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run --noinput
.\.venv\Scripts\python.exe manage.py migrate --plan
```

The fresh-database workflow is the default for complete acceptance. `--keepdb`
is explicitly opt-in and serial-only; verify migration and capability-seed
consistency before reuse. Never target the operational database, the
persistent manual UAT database, or the stale `test_elevate` database.

## CI workflow

The supported Linux full-suite command is:

```bash
python manage.py test --parallel 4 --noinput -v 1
```

It is implemented by `.github/workflows/full-backend-tests.yml`, which uses
PostgreSQL and the repository's automatic test runner. The faster
`backend-validation.yml` workflow performs system, migration, OpenAPI, and
selected contract checks; it does not replace the full backend suite.

During implementation, write and maintain focused, security, concurrency and
regression tests, but do not execute test suites. Test execution is deferred
until final hardening after the complete authorized Staff Management
implementation is finished. At that point execute the complete API, CRM,
Community and Mobile regression suites plus the required security,
authorization, migration and concurrency validation. Do not treat interim
test execution as stage acceptance. CI runs its established comprehensive
workflows. Separate approval remains required for tests, migrations, scanners,
operational data, or other restricted operations when required by the
workspace policy.

## Performance evidence and troubleshooting

The final fresh serial run passed **1,474/1,474** tests in **151.026 seconds**;
database setup took **5.141 seconds**, teardown took **3.453 seconds**, and
total elapsed time was **160.381 seconds**. Discovery found 1,474 tests in
0.682 seconds. A representative 175-test group improved from 437.304 seconds
to 11.889 seconds after test-only password-hasher optimization. The earlier
unrestricted run that ended after 1,117 tests in 3,050.129 seconds with 82
errors is historical interrupted evidence, not a current result.

Local Windows multiprocessing is unavailable in the current environment
because worker process-pipe creation fails with `WinError 5`; use serial local
execution. Linux CI remains the supported `--parallel 4` configuration.
The 27-test PostgreSQL governance transaction class is an expected hotspot
because it deliberately preserves commit, rollback, locking, and concurrency
behavior. Do not remove assertions, skip security or migration tests, reuse
prohibited databases, or add arbitrary sleeps to reduce runtime.

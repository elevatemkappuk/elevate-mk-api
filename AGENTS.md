# Elevate MK API Agent Policy

The workspace-level `AGENTS.md` remains authoritative. This repository policy
adds API-specific testing and performance requirements; it does not change
authorization, security, execution-approval, repository-ownership, migration,
or deployment boundaries.

## Testing and performance

The authoritative testing, performance, fixture, isolation, troubleshooting,
and command guidance is [docs/TESTING.md](docs/TESTING.md). Use the configured
`config.test_runner.ElevateTestRunner` through the ordinary `manage.py test`
command. The guide and runner preserve test-only fast hashing, production
settings, migration-authoritative fresh databases, canonical serial
`--keepdb` repair, parallel safeguards, actor attribution, deterministic
isolation, and positive and negative authorization coverage.

Agents writing or modifying tests must follow that guide. In particular, use
canonical fixtures and `config.test_utils.test_actor()` for attributable role
mutations; choose `SimpleTestCase`, `TestCase`, `setUpTestData()`, or
`TransactionTestCase` according to actual database and transaction needs; mock
external services; avoid unnecessary setup, sleeps, retries, and repeated
schema work; and investigate slow tests without weakening assertions,
security coverage, migration verification, or isolation. Production-hashing
tests must explicitly use production-equivalent configuration.

During implementation run only authorized focused tests; run affected groups at
feature completion and the complete fresh isolated suite at milestone closure.
Separate approval remains required where the workspace policy requires it.

Do not install dependencies, apply migrations, use operational data, run
scanners, or change production behavior without separate authorization.

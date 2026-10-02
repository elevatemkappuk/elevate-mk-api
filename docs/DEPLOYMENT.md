# Deployment

Brevo-specific architecture and integration behavior are documented in
[Brevo CRM integration](brevo-crm-integration.md). This document retains only
the deployment and worker-operations guidance needed to run it.

The authoritative backend runtime is Python 3.13, declared in `.python-version`.
Railway staging and production were confirmed on Python 3.13.15 when this pin
was established. GitHub Actions should use Python 3.13 as well, and local
development should preferably use Python 3.13 for parity. Existing Python 3.14
virtual environments do not need to be rebuilt immediately.

## CI validation

The `Backend validation` workflow runs on pull requests targeting `staging` or
`master`, and on pushes to `staging` or `master`. It is intentionally fast and
uses PostgreSQL-backed Django system, migration, OpenAPI schema, and core
contract validation. Its selected contract tests cover authentication/CSRF,
session-protected `/auth/me/`, OpenAPI endpoints, and staff authorization; it
does not replace the full backend test suite. Railway Wait for CI should remain
gated by this `Backend validation` push check rather than the full suite.

## Full backend tests

The `Full backend tests` workflow runs automatically for pull requests
targeting `master` and can also be started manually with `workflow_dispatch`.
It runs the complete Django test suite against PostgreSQL with
`--parallel 4`. It intentionally does not run on staging pushes, so normal
staging deployment validation remains fast. Production promotion on the
`staging` to `master` pull request should require both `Backend validation` and
`Full backend tests`.

## Staging API smoke

The `Staging API smoke` workflow runs after a successful Railway
`deployment_status` event whose exact environment is `positive-embrace / staging`.
It checks out `deployment.sha` and targets the explicit staging API URL
`https://elevate-mk-api-staging.up.railway.app`. The smoke is anonymous and
read-only: it verifies CSRF bootstrap, OpenAPI availability, and unauthenticated
authentication protection. It does not replace `Backend validation` or `Full
backend tests`. These are post-deployment verification checks and do not gate
the Railway deployment itself.

## Production API smoke

The `Production API smoke` workflow runs after a successful Railway
`deployment_status` event whose exact environment is
`positive-embrace / production`. It checks out `deployment.sha` and targets
`https://elevate-mk-api-production.up.railway.app`. Like staging, it performs
only anonymous, read-only CSRF bootstrap, OpenAPI, and unauthenticated
authentication-protection checks. The empirically confirmed Railway environment
identifiers are `positive-embrace / staging` and `positive-embrace / production`;
the temporary deployment-status inspector has been removed.

Railway should run the Django migrations before starting the web process. The
versioned Railway configuration sets this Start Command:

```text
python manage.py collectstatic --noinput && python manage.py migrate && gunicorn config.wsgi:application --bind 0.0.0.0:$PORT
```

The service root must be the `elevate-mk-api` directory, where `manage.py`,
`requirements.txt`, and `railway.json` are located. Railway installs the
runtime dependencies from `requirements.txt` before running the command. Railway
provides `PORT` dynamically for each deployment, so Gunicorn binds to
`0.0.0.0:$PORT`; do not replace it with a hardcoded port.

Each deployment runs `collectstatic --noinput` before migrations and Gunicorn.
WhiteNoise serves the collected files from `STATIC_ROOT`, including Django
Admin CSS, JavaScript, and theme icons.

## Application object storage

The API supports an explicit AWS S3-backed default storage for durable
application objects. Individual features choose their own object prefixes;
Community Profile Photo V1 uses `community/profile-photos/` for normalized
private photo objects.

Local development remains filesystem-backed and does not require AWS
credentials. Deployed S3 mode is enabled only when `USE_S3_STORAGE=True`.
When enabled, the application fails during startup if the bucket, region,
access key or secret key is missing. It does not silently fall back to local
storage.

The default S3 objects are private, public-read ACLs are not configured, and
generated read URLs use query-string authentication. The default signed URL
expiry is 900 seconds (15 minutes), configurable with
`AWS_QUERYSTRING_EXPIRE`. Static files remain separate and continue using
WhiteNoise; `collectstatic` does not place them in the application-assets
bucket.

The backend-only configuration contract is:

| Variable | Purpose | Secret? |
| --- | --- | --- |
| `USE_S3_STORAGE` | Explicitly select S3 application storage | No |
| `AWS_STORAGE_BUCKET_NAME` | Environment-specific S3 bucket | No |
| `AWS_S3_REGION_NAME` | S3 region | No |
| `AWS_ACCESS_KEY_ID` | S3 access identity | Yes |
| `AWS_SECRET_ACCESS_KEY` | S3 access secret | Yes |
| `AWS_QUERYSTRING_EXPIRE` | Signed URL lifetime in seconds; default `900` | No |

The current environment buckets are:

| Environment | Bucket | Region |
| --- | --- | --- |
| Development | `elevate-mk-assets-dev` | `eu-west-2` |
| Staging | `elevate-mk-assets-staging` | `eu-west-2` |
| Production | `elevate-mk-assets-production` | `eu-west-2` |

The staging Railway API should use `elevate-mk-assets-staging` with the
dedicated `elevate-mk-api-staging` IAM identity. Credentials belong only in
Railway secret configuration and must never be committed or exposed to the
Angular frontend. S3 CORS is not required for the current browser-to-Django-
to-S3 flow.

Set the Railway variable `ALLOWED_HOSTS` to the hostnames Django should accept.
For the production API, use:

```text
ALLOWED_HOSTS=elevate-mk-api-production.up.railway.app
```

The setting accepts a comma-separated list, for example
`ALLOWED_HOSTS=elevate-mk-api-production.up.railway.app,api.example.com`.

Railway terminates HTTPS at its reverse proxy and forwards the original scheme
in `X-Forwarded-Proto`. Django is configured to trust that header, so the API
origin does not need to be added to `CSRF_TRUSTED_ORIGINS` as a workaround for
proxy scheme detection. Keep the CRM and Community frontend origins in
`CSRF_TRUSTED_ORIGINS`.

Set these Railway variables for production:

```text
DJANGO_DEBUG=False
SECURE_SSL_REDIRECT=True
```

`DJANGO_DEBUG` controls Django's `DEBUG` setting. Secure session and CSRF
cookies are enabled automatically when `DEBUG=False`.

Configure the CRM and Community frontend origins with full schemes for both CORS and CSRF:

```text
CORS_ALLOWED_ORIGINS=https://<crm-frontend-domain>,https://community.elevatemk.org
CSRF_TRUSTED_ORIGINS=https://<crm-frontend-domain>,https://community.elevatemk.org
```

Both settings accept comma-separated origins when more than one frontend is
allowed. Credentialed CORS is enabled because the API uses Django session and
cookie authentication. Cookies remain host-scoped: no `SESSION_COOKIE_DOMAIN`
is configured, so the API session cookie is sent only to the API host.

## Railway environments

Production and staging should use separate Railway environments, databases,
frontend origins, and Brevo credentials. The application settings already
read these values from environment variables; configure each environment as
follows:

| Setting / Railway variable | Production | Staging |
| --- | --- | --- |
| `ALLOWED_HOSTS` | `elevate-mk-api-production.up.railway.app` | `elevate-mk-api-staging.up.railway.app` |
| `CORS_ALLOWED_ORIGINS` | `https://<crm-production-domain>,https://community.elevatemk.org` | `https://elevate-mk-crm-staging.up.railway.app` |
| `CSRF_TRUSTED_ORIGINS` | `https://<crm-production-domain>,https://community.elevatemk.org` | `https://elevate-mk-crm-staging.up.railway.app` |
| `CRM_FRONTEND_URL` | `https://<crm-production-domain>` | `https://elevate-mk-crm-staging.up.railway.app` |
| `COMMUNITY_FRONTEND_URL` | `https://community.elevatemk.org` | `http://localhost:4201` or the staging Community origin |
| `COMMUNITY_PASSWORD_RESET_THROTTLE_RATE` | `5/hour` | `5/hour` |
| `COMMUNITY_ACTIVATION_EXPIRY_HOURS` | `72` | `72` |
| `COMMUNITY_ACTIVATION_THROTTLE_RATE` | `10/hour` | `10/hour` |
| `COMMUNITY_LOGIN_THROTTLE_RATE` | `10/hour` | `10/hour` |
| `BREVO_COMMUNITY_ACTIVATION_TEMPLATE_ID` | Approved transactional activation template ID | Approved staging transactional activation template ID |
| `TRANSACTIONAL_EMAIL_WORKER_POLL_SECONDS` | `3` | `3` |
| `TRANSACTIONAL_EMAIL_WORKER_BATCH_SIZE` | `20` | `20` |
| `TRANSACTIONAL_EMAIL_JOB_LEASE_SECONDS` | `900` | `900` |
| `DJANGO_DEBUG` (`DEBUG`) | `False` | `False` |
| `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT` | Production database credentials and host | Staging database credentials and host |
| `BREVO_API_KEY`, `BREVO_SENDER_EMAIL`, `BREVO_SENDER_NAME`, `BREVO_REPLY_TO_EMAIL`, `BREVO_REPLY_TO_NAME`, `BREVO_PASSWORD_RESET_TEMPLATE_ID`, `BREVO_COMMUNITY_PASSWORD_RESET_TEMPLATE_ID`, `BREVO_MARKETING_LIST_ID`, `MARKETING_SYNC_PROVIDER`, `BREVO_MARKETING_WEBHOOK_USERNAME`, `BREVO_MARKETING_WEBHOOK_PASSWORD` | Production Brevo credentials, CRM/general and Community password-reset template IDs, approved marketing list ID, `BREVO` provider selection, and webhook Basic credentials | Staging Brevo credentials, template IDs, approved marketing list ID, `BREVO` provider selection, and webhook Basic credentials |
| `SECURE_SSL_REDIRECT` | `True` | `True` |
| `CSRF_COOKIE_SAMESITE` | `None` | `None` |
| `SESSION_COOKIE_SAMESITE` | `None` | `None` |

`ALLOWED_HOSTS` contains hostnames without schemes. CORS and CSRF variables
contain full origins including `https://`. Keep the production and staging
database and Brevo values separate; do not copy production secrets into
staging. `SECURE_PROXY_SSL_HEADER` is fixed in settings for Railway's
`X-Forwarded-Proto` header in both environments, and secure session/CSRF
cookies are enabled automatically while `DJANGO_DEBUG=False`. Railway's
`PORT` remains dynamic in both environments.

`GET /api/v1/auth/csrf/` sets the API's `csrftoken` cookie and returns the
corresponding token as `csrf_token` for cross-origin clients that cannot read
an API-origin cookie through `document.cookie`. The Angular CRM keeps that
token in memory and sends it as `X-CSRFToken`; credentialed requests still send
the API cookie. The endpoint remains safe and unauthenticated, while unsafe
endpoints retain normal Django CSRF enforcement.

For the current staging deployment, set these values on the backend Railway
service:

```text
ALLOWED_HOSTS=elevate-mk-api-staging.up.railway.app
CORS_ALLOWED_ORIGINS=https://elevate-mk-crm-staging.up.railway.app
CSRF_TRUSTED_ORIGINS=https://elevate-mk-crm-staging.up.railway.app
CRM_FRONTEND_URL=https://elevate-mk-crm-staging.up.railway.app
DJANGO_DEBUG=False
SECURE_SSL_REDIRECT=True
CSRF_COOKIE_SAMESITE=None
SESSION_COOKIE_SAMESITE=None
```

`CSRF_COOKIE_SECURE` and `SESSION_COOKIE_SECURE` are derived from
`DJANGO_DEBUG` and therefore become `True` when `DJANGO_DEBUG=False`; they are
not separate variables to configure.

The Community development frontend runs at `http://localhost:4201`. For local
development, include both `http://localhost:4200` and
`http://localhost:4201` in `CORS_ALLOWED_ORIGINS` and
`CSRF_TRUSTED_ORIGINS`. The production Community frontend is
`https://community.elevatemk.org` and must be included in both Railway
variables alongside the existing CRM origin.

`No migrations to apply` is normal: it means the database already has every
migration included in the deployed code. The command still proceeds to start
Gunicorn.

Running migrations as part of the web process is suitable while the service
has a single replica. If the service is later scaled to multiple replicas,
consider moving migrations to a dedicated pre-deploy migration step so only
one process applies schema changes before the replicas start.

## Brevo worker service

For Elevate V1, configure one Railway background-worker service, separate from
the API/web service. It should run:

```text
python manage.py process_background_jobs --watch
```

This one process services both durable queues while keeping their models,
processors, retry semantics, and lifecycle independent:

- `TransactionalEmailJob` for Community activation email;
- `ExternalPersonSyncJob` for Brevo marketing synchronization.

The existing domain-specific commands remain available for diagnostics and
manual recovery:

```text
python manage.py process_transactional_email_jobs --watch
python manage.py process_brevo_sync_jobs --watch
```

The worker shares the application code and database with the web service and
requires the active Brevo marketing settings, including `BREVO_API_KEY`,
`BREVO_MARKETING_LIST_ID`, and `MARKETING_SYNC_PROVIDER=BREVO`. Configure
`BREVO_SYNC_WORKER_POLL_SECONDS` (default `3`) and
`BREVO_SYNC_WORKER_BATCH_SIZE` (default `20`, maximum `100`) when tuning is
needed. Configure `BACKGROUND_WORKER_POLL_SECONDS` (default `3`) for the
combined worker. Configure `TRANSACTIONAL_EMAIL_WORKER_BATCH_SIZE` and
`BREVO_SYNC_WORKER_BATCH_SIZE` independently. The combined worker handles
SIGINT/SIGTERM, processes transactional jobs first in each bounded iteration,
and preserves durable job state. Mailchimp jobs are not automatically
processed.

The repository does not provision or modify Railway services automatically;
the production Railway worker service must be configured to use the combined
command above.

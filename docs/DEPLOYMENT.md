# Deployment

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
proxy scheme detection. Keep the CRM frontend origin in `CSRF_TRUSTED_ORIGINS`.

Set these Railway variables for production:

```text
DJANGO_DEBUG=False
SECURE_SSL_REDIRECT=True
```

`DJANGO_DEBUG` controls Django's `DEBUG` setting. Secure session and CSRF
cookies are enabled automatically when `DEBUG=False`.

Configure the separate CRM origin with full schemes for both CORS and CSRF:

```text
CORS_ALLOWED_ORIGINS=https://<crm-frontend-domain>
CSRF_TRUSTED_ORIGINS=https://<crm-frontend-domain>
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
| `CORS_ALLOWED_ORIGINS` | `https://<crm-production-domain>` | `https://<crm-staging-domain>` |
| `CSRF_TRUSTED_ORIGINS` | `https://<crm-production-domain>` | `https://<crm-staging-domain>` |
| `CRM_FRONTEND_URL` | `https://<crm-production-domain>` | `https://<crm-staging-domain>` |
| `DJANGO_DEBUG` (`DEBUG`) | `False` | `False` |
| `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT` | Production database credentials and host | Staging database credentials and host |
| `BREVO_API_KEY`, `BREVO_SENDER_EMAIL`, `BREVO_SENDER_NAME`, `BREVO_REPLY_TO_EMAIL`, `BREVO_REPLY_TO_NAME`, `BREVO_PASSWORD_RESET_TEMPLATE_ID` | Production Brevo credentials and template ID | Staging Brevo credentials and template ID |
| `SECURE_SSL_REDIRECT` | `True` | `True` |

`ALLOWED_HOSTS` contains hostnames without schemes. CORS and CSRF variables
contain full origins including `https://`. Keep the production and staging
database and Brevo values separate; do not copy production secrets into
staging. `SECURE_PROXY_SSL_HEADER` is fixed in settings for Railway's
`X-Forwarded-Proto` header in both environments, and secure session/CSRF
cookies are enabled automatically while `DJANGO_DEBUG=False`. Railway's
`PORT` remains dynamic in both environments.

`No migrations to apply` is normal: it means the database already has every
migration included in the deployed code. The command still proceeds to start
Gunicorn.

Running migrations as part of the web process is suitable while the service
has a single replica. If the service is later scaled to multiple replicas,
consider moving migrations to a dedicated pre-deploy migration step so only
one process applies schema changes before the replicas start.

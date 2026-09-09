# Deployment

Railway should run the Django migrations before starting the web process. The
versioned Railway configuration sets this Start Command:

```text
python manage.py migrate && gunicorn config.wsgi:application --bind 0.0.0.0:$PORT
```

The service root must be the `elevate-mk-api` directory, where `manage.py`,
`requirements.txt`, and `railway.json` are located. Railway installs the
runtime dependencies from `requirements.txt` before running the command. Railway
provides `PORT` dynamically for each deployment, so Gunicorn binds to
`0.0.0.0:$PORT`; do not replace it with a hardcoded port.

`No migrations to apply` is normal: it means the database already has every
migration included in the deployed code. The command still proceeds to start
Gunicorn.

Running migrations as part of the web process is suitable while the service
has a single replica. If the service is later scaled to multiple replicas,
consider moving migrations to a dedicated pre-deploy migration step so only
one process applies schema changes before the replicas start.

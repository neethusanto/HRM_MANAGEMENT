# Production deployment

This project uses SQLite for local development. Vercel deployments require a persistent PostgreSQL database; the function filesystem is not a durable database.

## Vercel environment variables

Configure these in the Vercel project before deploying:

- `DATABASE_URL` (or Vercel's `POSTGRES_URL`): PostgreSQL connection URL for application requests. Alternatively, set `DB_HOST`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`, and `DB_PORT` separately.
- `DATABASE_URL_UNPOOLED` (or Vercel's `POSTGRES_URL_NON_POOLING`): direct PostgreSQL connection URL used by deployment migrations. If your provider has no separate direct URL, use the same URL for both variables.
- `DJANGO_SECRET_KEY`: a newly generated, private Django signing key.
- `DJANGO_DEBUG`: set to `False`.
- `DJANGO_ALLOWED_HOSTS`: comma-separated Vercel and custom domain hostnames, if they are not supplied by Vercel's built-in URL variables.
- `DJANGO_CSRF_TRUSTED_ORIGINS`: comma-separated HTTPS origins for custom domains.
- `DEVICE_TOKEN`: private token for the biometric punch API. If omitted, the Django secret key is used instead.
- `COMPANY_LATITUDE` and `COMPANY_LONGITUDE`: required for office geofencing; `COMPANY_GEOFENCE_RADIUS_METERS` is optional and defaults to 150.

Production builds run `manage.py migrate --noinput` against `DATABASE_URL_UNPOOLED` (or the separately configured database fields) before serving the deployment. Preview builds run migrations only when a branch-specific `DATABASE_URL_UNPOOLED` is configured, to avoid mutating the production database.

For a separately configured PostgreSQL database, use `DB_NAME=Hrm_management`; `DB_HOST`, `DB_USER`, and `DB_PASSWORD` are required, and `DB_PORT` defaults to `5432`. Vercel PostgreSQL connections default to SSL mode `require` (`DB_SSLMODE` can override this).

The roster and October attendance migrations add records to whichever database is targeted by the migration run. Attendance entries created by the roster migration remain unverified until HR reviews them.

Employee photos, CVs, and leave proofs use Django's file storage. Configure a persistent object-storage backend before relying on uploaded files in production; Vercel's function filesystem is temporary.

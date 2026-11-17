"""Apply Django migrations to the production Vercel database before deployment."""
import os
import subprocess
import sys


def main():
    vercel_environment = os.environ.get('VERCEL_ENV')
    if vercel_environment not in ('production', 'preview'):
        print('Skipping database migrations outside Vercel deployments.')
        return 0

    database_url = os.environ.get('DATABASE_URL_UNPOOLED') or os.environ.get('POSTGRES_URL_NON_POOLING')
    if vercel_environment == 'production':
        database_url = database_url or os.environ.get('DATABASE_URL') or os.environ.get('POSTGRES_URL')
    elif not database_url:
        print('Skipping preview migrations; configure a branch-specific DATABASE_URL_UNPOOLED to migrate a preview database.')
        return 0
    if not database_url:
        raise RuntimeError(
            'Production deployment requires a persistent PostgreSQL database. '
            'Connect a Vercel Postgres/Neon database and set DATABASE_URL.'
        )
    if not os.environ.get('DJANGO_SECRET_KEY'):
        raise RuntimeError('Production deployment requires the DJANGO_SECRET_KEY environment variable.')
    if os.environ.get('DJANGO_DEBUG', 'False').strip().lower() in ('1', 'true', 'yes'):
        raise RuntimeError('Set DJANGO_DEBUG=False for a production deployment.')

    environment = os.environ.copy()
    environment['DATABASE_URL'] = database_url
    subprocess.run(
        [sys.executable, 'manage.py', 'migrate', '--noinput'],
        check=True,
        env=environment,
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

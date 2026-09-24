# Production container for the FastAPI API (Task 18.1). Built from the
# same pinned, hashed lockfile every other environment in this repo
# installs from (Requirement 16.7) -- no separate dependency resolution
# for the deployed image.
FROM python:3.12-slim AS runtime

WORKDIR /app

# System deps: none beyond what the slim base already has -- every
# Python dependency in requirements.lock ships a manylinux wheel.
COPY requirements.lock ./requirements.lock
RUN pip install --no-cache-dir --require-hashes -r requirements.lock

COPY src ./src
# xc_platform.db.migrator.default_migrations_dir() resolves three parents
# up from migrator.py (/app/src/xc_platform/db/migrator.py -> /app), so
# this must land at exactly /app/migrations. Missing this meant the
# container found zero migration files, so it both rejected every
# manifest it hadn't itself produced (schema_version 3 > "0 supported",
# found live during Task 18.2's validation, 2026-09-23) and silently
# applied zero migrations to its own local writer database at startup.
COPY migrations ./migrations

ENV PYTHONPATH=/app/src
ENV PYTHONUNBUFFERED=1

EXPOSE 8080

# xc_platform.cli.run_production_api (not run_local_api -- that one is
# explicitly local-dev-only, in-memory S3, no real AWS clients) reads its
# configuration from environment variables the App Runner service sets
# (Task 18.1's CDK): XC_SNAPSHOT_BUCKET, AWS_REGION, session/ticket signing
# key SSM references, etc.
CMD ["python", "-m", "xc_platform.cli.run_production_api"]

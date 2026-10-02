# RingSays API, worker, back office and jobs: one image, the command picks the role.
#   API:        default command
#   Worker:     python -m app.worker
#   Migrations: alembic upgrade head
#   Bootstrap:  python -m app.scripts.bootstrap_db
# EXTRAS selects the object storage SDK for the target cloud: gcp (Google Cloud Storage) or s3.
ARG PYTHON_IMAGE=python:3.12-slim-bookworm

FROM ${PYTHON_IMAGE} AS build
ARG EXTRAS=gcp
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /src
RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH
# Dependencies exactly as locked, every file checked against its hash. The app itself is not
# installed: it runs from /srv (uvicorn, python -m and alembic all import it from there).
COPY requirements/${EXTRAS}.lock ./requirements.lock
RUN pip install --require-hashes --no-deps -r requirements.lock

FROM ${PYTHON_IMAGE}
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH=/opt/venv/bin:$PATH
# Numeric user so Kubernetes can enforce runAsNonRoot; no shell login, no home writes needed.
RUN groupadd --system --gid 10001 app && useradd --system --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin app
WORKDIR /srv
COPY --from=build /opt/venv /opt/venv
COPY --chown=root:root alembic.ini ./
COPY --chown=root:root migrations ./migrations
COPY --chown=root:root app ./app
USER 10001:10001
EXPOSE 8000
# Graceful stop inside the pod's termination grace period; server banner off.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-server-header", "--timeout-graceful-shutdown", "20"]

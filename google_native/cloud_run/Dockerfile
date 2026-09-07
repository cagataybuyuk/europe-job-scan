FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080

WORKDIR /app
COPY google_native/cloud_run/requirements-cloud-run.txt /tmp/requirements-cloud-run.txt
RUN pip install --no-cache-dir -r /tmp/requirements-cloud-run.txt \
    && playwright install --with-deps chromium

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir --no-deps .

CMD exec gunicorn --bind :${PORT} --workers 1 --threads 1 --timeout 180 ejs.apps.cloud_run.main:app

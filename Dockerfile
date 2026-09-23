FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

RUN useradd --create-home --uid 10001 appuser
USER appuser

ENV PYTHONPATH=/app
# Aggregated /metrics across all uvicorn workers (see app/metrics/registry.py).
ENV PROMETHEUS_MULTIPROC_DIR=/tmp/prometheus_multiproc
RUN mkdir -p /tmp/prometheus_multiproc
EXPOSE 8080

HEALTHCHECK --interval=10s --timeout=3s --start-period=15s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=2)"]

# CPU-bound pure-Python pipeline: one worker per genuinely available core.
# On a host that also runs Mongo/Redis/nginx set UVICORN_WORKERS=cores-1.
# uvicorn's access log is disabled: it duplicates the app's structured
# process_request log (which carries the fields the spec requires -- hashed id,
# direction, sizes, latency) at an extra 1-2 stdout writes per request.
ENV UVICORN_WORKERS=4
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port 8080 --workers ${UVICORN_WORKERS} --loop uvloop --http httptools --no-access-log"]

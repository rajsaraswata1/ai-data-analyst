# NOTE: written but NOT built/run in the development sandbox (no Docker available there).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 MPLBACKEND=Agg
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY data/samples ./samples
COPY scripts ./scripts
COPY evaluation ./evaluation
COPY run.py .

# run as an unprivileged user; /app/data (SQLite, uploads, secret key) and /app/logs are volumes
RUN useradd --create-home appuser && mkdir -p /app/data /app/logs && chown -R appuser /app
USER appuser

ENV APP_ENV=production HOST=0.0.0.0 PORT=5000 SAMPLES_DIR=/app/samples
EXPOSE 5000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:5000/api/health').status==200 else 1)"

# one worker: datasets live in per-process memory; threads handle concurrency
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", "--threads", "8", "--timeout", "120", "run:app"]

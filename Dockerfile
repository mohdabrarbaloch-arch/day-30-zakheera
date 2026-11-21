FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies first, so a code change doesn't invalidate the layer.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY scripts ./scripts

# Run as a non-root user. The app writes nothing outside the database, so
# there is no reason for it to have write access to its own source.
RUN useradd --create-home --uid 10001 zakheera \
    && chown -R zakheera:zakheera /app
USER zakheera

EXPOSE 8000

# Hits the real endpoint. A healthcheck pointed at a path that doesn't exist
# marks the container permanently unhealthy, which is worse than no check.
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health').status == 200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

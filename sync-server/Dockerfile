FROM python:3.11-slim

WORKDIR /srv/noteor-sync

COPY pyproject.toml ./
COPY app ./app
COPY scripts ./scripts

RUN pip install --no-cache-dir . && useradd --create-home --uid 10001 noteor \
    && mkdir -p /data && chown -R noteor:noteor /data

USER noteor
ENV NOTEOR_DATA_DIR=/data
EXPOSE 8787

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8787/healthz')"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8787"]

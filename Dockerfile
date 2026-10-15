# Cloud Run image for the LifeNest AI API.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore

WORKDIR /srv

COPY pyproject.toml README.md ./
COPY app ./app
# Editable install keeps the package rooted at /srv, where config/, data/ and knowledge/ live.
RUN pip install -e ".[gcp]"

COPY config ./config
COPY data ./data
COPY knowledge ./knowledge
COPY scripts ./scripts

RUN useradd --create-home --uid 10001 lifenest \
    && mkdir -p /srv/.local_uploads \
    && chown lifenest /srv/.local_uploads
USER lifenest

# Safe defaults: prod refuses dev auth and the in-memory DB, so misconfiguration fails at startup.
ENV APP_ENV=prod \
    PORT=8080
EXPOSE 8080

CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
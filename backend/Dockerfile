# syntax=docker/dockerfile:1
FROM python:3.11-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
# upgrade: pick up Debian security fixes newer than the base image (image scans fail on fixable HIGH/CRITICAL)
RUN apt-get update && apt-get upgrade -y && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --upgrade pip setuptools && pip install -r requirements.txt
# Production images carry no packaging tools (pip/setuptools/wheel and their vendored libraries are
# unused at runtime and show up in image scans). The dev compose builds with DEV_TOOLS=true.
ARG DEV_TOOLS=false
RUN if [ "$DEV_TOOLS" != "true" ]; then \
      pip uninstall -y pip setuptools wheel >/dev/null 2>&1; \
      rm -rf /usr/local/lib/python3.11/ensurepip /usr/local/bin/pip*; \
    fi
COPY . .
RUN useradd --create-home --uid 10001 marketedge && chown -R marketedge /app
USER marketedge
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD curl -fsS http://localhost:8000/health || exit 1
CMD ["/app/scripts/start-api.sh"]

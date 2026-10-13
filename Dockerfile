FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_LINK_MODE=copy \
    UV_SYSTEM_PYTHON=1

# uv for fast, reproducible installs.
RUN pip install --no-cache-dir uv

WORKDIR /app

# Dependency layer: install runtime deps first so source edits don't re-resolve
# them. Keep these in sync with [project].dependencies in pyproject.toml.
COPY pyproject.toml README.md ./
RUN uv pip install --system \
      "curl-cffi>=0.16.3" \
      "fastapi>=0.143.0" \
      "uvicorn[standard]>=0.54.0"

# Application layer (deps already present; --no-deps avoids re-resolving them).
COPY src ./src
RUN uv pip install --system --no-deps .

ENV SPORT_SCRAPER_DATA=/app/data

VOLUME ["/app/data"]
EXPOSE 8080

CMD ["uvicorn", "sport_scraper.api.main:app", "--host", "0.0.0.0", "--port", "8080"]

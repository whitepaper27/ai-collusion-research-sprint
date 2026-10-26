# Clean room for tests and mock episodes (docs/specs/recorder-freeze.md R-DKR-01).
#
# Build with network (to install the locked environment); run with `--network none` to show
# the suite and mock episodes need no network. Live runs stay on the host (ARCHITECTURE.md §9).
FROM python:3.12-slim

# git: the freeze/manifest tests create throwaway git repositories.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app
COPY . /app

# Locked environment only: fails if uv.lock is out of date with pyproject.toml.
RUN uv sync --frozen

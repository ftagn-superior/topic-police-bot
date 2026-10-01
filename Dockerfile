FROM python:3.14-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app

FROM base AS test
COPY --from=ghcr.io/astral-sh/uv:0.12.21 /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project --no-python-downloads

COPY main.py ./
COPY topic_police_bot/ ./topic_police_bot/
COPY tests/ /tests/
RUN .venv/bin/python -m unittest discover -s /tests -v

FROM base AS runtime
COPY --from=test /app /app
ENV PATH="/app/.venv/bin:$PATH"
USER 10001:10001
CMD ["python", "main.py"]

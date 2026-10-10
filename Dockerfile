# Pipeline image for the ECS Fargate tasks. Every Step Functions state runs this image with a
# different `airbnb <sub-command>`. Built in CI; pushed only by the gated deploy workflow.
FROM python:3.14-slim-bookworm AS build
COPY --from=ghcr.io/astral-sh/uv:0.10.2 /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --frozen --no-dev --group athena --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev --group athena

FROM python:3.14-slim-bookworm
RUN groupadd --gid 10001 app && useradd --uid 10001 --gid app --home-dir /tmp/airbnb --no-create-home app
WORKDIR /app
COPY --from=build /app /app
COPY dbt ./dbt
COPY infra/statemachine ./infra/statemachine
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1 PYTHONIOENCODING=utf-8 \
    HOME=/tmp/airbnb AIRBNB_DATA_DIR=/tmp/airbnb DBT_TARGET_PATH=/tmp/airbnb/dbt-target \
    DBT_LOG_PATH=/tmp/airbnb/dbt-logs
USER 10001:10001
HEALTHCHECK NONE
ENTRYPOINT []
CMD ["airbnb", "--help"]

# syntax=docker/dockerfile:1.7
# ---- build stage: resolve wheels in an isolated venv ----
FROM python:3.12-slim AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
COPY requirements.txt .
RUN python -m venv /venv && /venv/bin/pip install --upgrade pip && /venv/bin/pip install -r requirements.txt

# ---- runtime stage: no compilers, no pip cache, non-root ----
FROM python:3.12-slim
ARG APP_VERSION=dev
ENV PATH="/venv/bin:$PATH" PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 APP_VERSION=${APP_VERSION}
RUN groupadd --system --gid 10001 app && useradd --system --uid 10001 --gid app --no-create-home app
COPY --from=build /venv /venv
WORKDIR /app
COPY --chown=app:app app.py ./
COPY --chown=app:app static ./static
USER 10001
EXPOSE 8080
HEALTHCHECK NONE
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]

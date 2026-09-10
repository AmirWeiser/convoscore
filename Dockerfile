# Builder: install pinned dependencies into a venv, kept separate from the
# runtime stage so nothing build-time (pip cache, wheel files) ends up in the
# final image.
FROM python:3.14-slim AS builder

WORKDIR /app
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Runtime: only the venv + application code. No git metadata, tests,
# Terraform state, or local dev files - see .dockerignore, and this stage
# never COPYs the repo root, only app/.
FROM python:3.14-slim AS runtime

RUN useradd --create-home --uid 1000 --shell /usr/sbin/nologin appuser

COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY --chown=appuser:appuser app/ ./app/

USER appuser

# Default command runs the API; the worker uses the same image with a
# different command (no ENTRYPOINT lock-in) - see charts/convoscore later.
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

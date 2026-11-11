# Single image for the API and the worker (the worker only changes the command: python -m worker).
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first: the layer is only rebuilt when requirements.txt changes.
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app/ .

# No root inside the container.
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin app
USER app

EXPOSE 8000

# Single process: rate limiting is in memory (see core/rate_limit.py).
# The API is only reached by the frontend (internal network, no published port); that is why the
# per-IP limits (login/refresh) currently apply to the whole frontend, not per person.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]

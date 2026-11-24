FROM python:3.12-slim

# NOTAM_HOME: data/, output/ and models/ live in /app (mounted volume), not inside site-packages
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 NOTAM_HOME=/app
WORKDIR /app

COPY pyproject.toml ./
COPY notam_schedule ./notam_schedule
RUN pip install --no-cache-dir ".[api]"
COPY data/reference ./data/reference
COPY contracts ./contracts

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')"
CMD ["notam-schedule-api", "--host", "0.0.0.0", "--port", "8000"]

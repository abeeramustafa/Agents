FROM python:3.11-slim

WORKDIR /app

# Dependency layer stays cached when only application code changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY static ./static
COPY data ./data

EXPOSE 8000

# Shell form so ${PORT} expands. One worker because sessions stay in memory.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]

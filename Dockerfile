FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY . .
# PYTHONDONTWRITEBYTECODE stops runtime caching, so ship the application's
# bytecode instead of recompiling it in both processes on every cold start.
RUN python -m compileall -q app scripts

EXPOSE 8000

CMD ["python", "scripts/supervise.py"]

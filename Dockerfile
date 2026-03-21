FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libffi-dev libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY agent/ ./agent/

# Persistent data directory (mounted as a volume)
RUN mkdir -p /data

# Ensure Docker always uses /data regardless of Python default
ENV DATA_DIR=/data \
    DB_PATH=/data/sisyphus.db \
    CHROMA_PATH=/data/chroma \
    GOALS_FILE=/data/goals.md

CMD ["python", "-m", "agent.main"]

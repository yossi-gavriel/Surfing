FROM python:3.11-slim

RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy shared utilities
COPY shared/ /app/shared/

# Copy ingestion service
COPY services/ingestion-service/requirements.txt /app/services/ingestion-service/
RUN pip install --no-cache-dir -r /app/services/ingestion-service/requirements.txt

COPY services/ingestion-service/ /app/services/ingestion-service/
COPY config/ /app/config/

WORKDIR /app/services/ingestion-service

# Required to find 'shared' module
ENV PYTHONPATH=/app

CMD ["python", "src/main.py"]

FROM python:3.11-slim

RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*

WORKDIR /app/services/ingestion-service

COPY shared/ /app/shared/
COPY services/ingestion-service/requirements.txt /app/services/ingestion-service/
RUN pip install --no-cache-dir -r /app/services/ingestion-service/requirements.txt

COPY services/ingestion-service/ /app/services/ingestion-service/
COPY config/ /app/config/

ENV PYTHONPATH=/app:/app/services/ingestion-service

CMD ["python", "-m", "src.main"]

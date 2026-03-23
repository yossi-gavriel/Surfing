FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app/services/embedding-service

COPY shared/ /app/shared/
COPY services/embedding-service/requirements.txt /app/services/embedding-service/
RUN pip install --no-cache-dir -r /app/services/embedding-service/requirements.txt

COPY services/embedding-service/ /app/services/embedding-service/

ENV PYTHONPATH=/app

CMD ["python", "-m", "src.main"]

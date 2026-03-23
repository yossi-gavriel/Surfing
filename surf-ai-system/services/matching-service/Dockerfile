FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app/services/matching-service

COPY shared/ /app/shared/
COPY services/matching-service/requirements.txt /app/services/matching-service/requirements.txt
RUN pip install --no-cache-dir -r /app/services/matching-service/requirements.txt

COPY services/matching-service/ /app/services/matching-service/
RUN mkdir -p /app/data \
    && if [ ! -f /app/data/users.json ]; then echo '{"users": []}' > /app/data/users.json; fi \
    && if [ ! -f /app/data/matches.json ]; then echo '{"matches": []}' > /app/data/matches.json; fi

ENV PYTHONPATH=/app:/app/services/matching-service

CMD ["python", "-m", "src.main"]

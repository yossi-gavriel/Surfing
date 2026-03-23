FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    libsm6 libxext6 libgl1 build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app/services/api-gateway

COPY shared/ /app/shared/
COPY services/api-gateway/requirements.txt /app/services/api-gateway/
RUN pip install --no-cache-dir -r /app/services/api-gateway/requirements.txt

COPY services/api-gateway/ /app/services/api-gateway/

RUN mkdir -p /app/data \
    && if [ ! -f /app/data/users.json ]; then echo '{"users": []}' > /app/data/users.json; fi \
    && if [ ! -f /app/data/matches.json ]; then echo '{"matches": []}' > /app/data/matches.json; fi

ENV PYTHONPATH=/app:/app/services/api-gateway

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]

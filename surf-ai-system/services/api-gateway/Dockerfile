FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    libsm6 libxext6 libgl1-mesa-glx build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY shared/ /app/shared/
COPY services/api-gateway/requirements.txt /app/services/api-gateway/
RUN pip install --no-cache-dir -r /app/services/api-gateway/requirements.txt

COPY services/api-gateway/ /app/services/api-gateway/

ENV PYTHONPATH=/app

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]

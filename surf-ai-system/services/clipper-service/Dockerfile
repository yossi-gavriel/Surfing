FROM python:3.11-slim

RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*

WORKDIR /app/services/clipper-service

COPY shared/ /app/shared/
COPY services/clipper-service/requirements.txt /app/services/clipper-service/
RUN pip install --no-cache-dir -r /app/services/clipper-service/requirements.txt

COPY services/clipper-service/ /app/services/clipper-service/

ENV PYTHONPATH=/app

CMD ["python", "src/main.py"]

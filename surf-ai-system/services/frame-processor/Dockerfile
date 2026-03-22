FROM python:3.11-slim

RUN apt-get update && apt-get install -y ffmpeg libsm6 libxext6 libgl1-mesa-glx && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY shared/ /app/shared/
COPY services/frame-processor/requirements.txt /app/services/frame-processor/
RUN pip install --no-cache-dir -r /app/services/frame-processor/requirements.txt

COPY services/frame-processor/ /app/services/frame-processor/

ENV PYTHONPATH=/app

CMD ["python", "src/main.py"]

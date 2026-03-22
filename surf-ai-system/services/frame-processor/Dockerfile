FROM python:3.11-slim

RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY shared/ /app/shared/
COPY services/frame-processor/requirements.txt /app/services/frame-processor/
RUN pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir -r /app/services/frame-processor/requirements.txt

COPY services/frame-processor/ /app/services/frame-processor/

ENV PYTHONPATH=/app

CMD ["python", "services/frame-processor/src/main.py"]

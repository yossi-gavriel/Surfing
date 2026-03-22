FROM python:3.11-slim

RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY shared/ /app/shared/
COPY services/__init__.py /app/services/__init__.py
COPY services/frame_processor/requirements.txt /app/services/frame_processor/
RUN pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir -r /app/services/frame_processor/requirements.txt

COPY services/frame_processor/ /app/services/frame_processor/

ENV PYTHONPATH=/app

CMD ["python", "services/frame_processor/src/main.py"]

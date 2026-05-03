FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app/services/analysis-service

COPY shared/ /app/shared/
COPY services/analysis-service/requirements.txt /app/services/analysis-service/
RUN pip install --no-cache-dir -r /app/services/analysis-service/requirements.txt

COPY services/analysis-service/ /app/services/analysis-service/
RUN model_artifact="$(find /app/services/analysis-service -type f \( \
        -name '*.pt' -o -name '*.onnx' -o -name '*.engine' -o -name '*.weights' \
    \) -print -quit)" && \
    if [ -n "$model_artifact" ]; then \
        echo "Model binaries must not be baked into the analysis image: $model_artifact" >&2; \
        exit 1; \
    fi
RUN chmod +x /app/services/analysis-service/scripts/entrypoint.sh

ENV PYTHONPATH=/app

# Model weights are downloaded from S3 at startup via entrypoint.
# To embed weights in the image instead, uncomment:
# COPY services/analysis-service/models/yolo_wave_surfer.pt /app/services/analysis-service/models/

CMD ["/app/services/analysis-service/scripts/entrypoint.sh"]

FROM python:3.11-slim

WORKDIR /app

COPY shared/ /app/shared/
COPY services/matching-service/requirements.txt /app/services/matching-service/
RUN pip install --no-cache-dir -r /app/services/matching-service/requirements.txt

COPY services/matching-service/ /app/services/matching-service/
RUN mkdir -p /app/data && echo '{"users": []}' > /app/data/users.json

ENV PYTHONPATH=/app

CMD ["python", "src/main.py"]

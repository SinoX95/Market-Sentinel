FROM python:3.12-slim
WORKDIR /app
ENV PYTHONPATH=/app PYTHONUNBUFFERED=1
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY backend backend
COPY collectors collectors
COPY analysis analysis
COPY alerts alerts
COPY infrastructure infrastructure
COPY config config
# PORT est fourni par Cloud Run (8080) ; 8000 en local. --proxy-headers : schéma https réel derrière Firebase Hosting.
CMD ["sh", "-c", "exec uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]

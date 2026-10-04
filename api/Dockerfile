# Python API image (used by `docker compose --profile full`).
# Data, models and the audit DB are mounted from the host (see docker-compose.yml);
# run the pipeline on the host first.
FROM python:3.12-slim
WORKDIR /app
# libgomp1 = OpenMP runtime that XGBoost needs on Linux (the macOS equivalent is libomp).
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY core core
COPY data/*.py data/
COPY data/knowledge data/knowledge
COPY graph graph
COPY brain/*.py brain/
COPY preauth preauth
COPY appeal appeal
COPY trust/*.py trust/
COPY api api
EXPOSE 8000
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]

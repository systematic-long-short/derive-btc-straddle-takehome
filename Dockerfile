FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONHASHSEED=0

WORKDIR /app
COPY pyproject.toml requirements.txt README.md EVALUATION.md ./
COPY src ./src
COPY scripts ./scripts
COPY model_submissions/benchmark_straddle ./model_submissions/benchmark_straddle
RUN pip install --no-cache-dir -e . -r requirements.txt

ENTRYPOINT ["python"]


FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py agent.py memory.py rag.py vector_store.py sync_site.py evaluate.py ./
COPY static ./static
COPY data ./data
COPY tests/eval_cases.json ./tests/eval_cases.json

RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/.cache/fastembed /app/state \
    && chown -R appuser:appuser /app/.cache /app/state
USER appuser

EXPOSE 8080
CMD ["python", "app.py"]

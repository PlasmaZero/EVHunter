FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 EMBED_WORKER=false
WORKDIR /app
COPY backend/ ./backend/
RUN pip install --no-cache-dir -c backend/requirements.lock ./backend && useradd --create-home --uid 10001 evhunter && mkdir -p /app/data && chown -R evhunter:evhunter /app
USER evhunter
EXPOSE 8000
CMD ["sh", "-c", "exec uvicorn evhunter.api:app --host 0.0.0.0 --port ${PORT:-8000}"]

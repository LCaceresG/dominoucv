FROM python:3.12-slim

WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt "psycopg[binary]==3.2.*"

COPY backend backend
COPY frontend frontend

WORKDIR /app/backend
ENV PORT=8000
# Define en el servidor: DATABASE_URL (PostgreSQL), ADMIN_KEY y CORS_ORIGINS
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]

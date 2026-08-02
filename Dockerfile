FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY jobqueue ./jobqueue

EXPOSE 8788

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8788", "--loop", "uvloop", "--http", "httptools"]

FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY apps/orchestrator-api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install apscheduler==3.10.4 python-telegram-bot==21.3 fakeredis

# Copy orchestrator code
COPY apps/orchestrator-api/ .

# Copy adapters from monorepo root
COPY adapters/ /app/adapters/

ENV PYTHONPATH=/app

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]

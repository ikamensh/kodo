FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Create package structure for relative imports
RUN mkdir -p benchmark/online
RUN touch benchmark/__init__.py

COPY __init__.py config.py db.py server.py benchmark/online/
COPY static/ benchmark/online/static/

ENV PORT=8080
EXPOSE 8080

CMD ["python", "-m", "benchmark.online.server"]

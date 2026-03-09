FROM python:3.13-slim
WORKDIR /app
COPY server.py .
COPY static/ static/
ENV PORT=8080
CMD ["python", "server.py"]

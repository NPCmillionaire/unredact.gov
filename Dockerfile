FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UNREDACT_DATA_DIR=/data
COPY pyproject.toml README.md ./
COPY unredact ./unredact
RUN pip install --no-cache-dir '.[xray]'
VOLUME /data
EXPOSE 8000
CMD ["uvicorn", "unredact.web.app:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]

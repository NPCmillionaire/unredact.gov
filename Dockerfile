FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UNREDACT_DATA_DIR=/data
WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY unredact ./unredact
RUN pip install --no-cache-dir . && useradd --system --home /data unredact && mkdir -p /data && chown unredact /data

USER unredact
VOLUME /data
EXPOSE 8000
CMD ["unredact", "serve", "--host", "0.0.0.0", "--port", "8000"]

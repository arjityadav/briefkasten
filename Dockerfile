FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 BRIEFKASTEN_DATA_DIR=/data

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir ".[web,scans]" && useradd --create-home app && mkdir /data && chown app /data

USER app
VOLUME ["/data"]
EXPOSE 8000
CMD ["briefkasten", "serve", "--host", "0.0.0.0", "--port", "8000"]

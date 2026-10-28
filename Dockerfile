# Build the TypeScript web UI
FROM node:22-slim AS ui
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npx tsc --noEmit && npx vite build --outDir /ui/dist

FROM python:3.12-slim

WORKDIR /app
# Public-safe by default: sample letters only, no uploads, nothing stored. For a private full deployment
# behind your own login, run with -e BRIEFKASTEN_DEMO_ONLY=0 and add --allow-public to the command.
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 BRIEFKASTEN_DATA_DIR=/data BRIEFKASTEN_DEMO_ONLY=1

COPY pyproject.toml README.md ./
COPY src ./src
COPY --from=ui /ui/dist ./src/briefkasten/web/dist
RUN pip install --no-cache-dir ".[web,scans,photos]" && useradd --create-home app && mkdir /data && chown app /data

USER app
VOLUME ["/data"]
EXPOSE 8000
CMD ["briefkasten", "serve", "--host", "0.0.0.0", "--port", "8000"]

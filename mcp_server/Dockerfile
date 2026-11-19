# Build context is the heal-vlmd-AI-pipeline repo root (see vlmd-app's
# docker-compose.yml), not this directory — the server imports the pipeline
# modules at the repo root directly, so they have to be in the image.
FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV MCP_SERVER_HOST=0.0.0.0 \
    MCP_SERVER_PORT=8090

EXPOSE 8090

CMD ["python", "-m", "mcp_server.server"]

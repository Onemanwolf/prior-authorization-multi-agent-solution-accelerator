FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt && \
    python -c "from mcp import Client; from agent_framework import MCPStreamableHTTPTool; import httpx2"
COPY main.py databricks_auth.py ./
ENV PYTHONUNBUFFERED=1
EXPOSE 8088
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8088/readiness', timeout=4)" || exit 1
CMD ["python", "main.py"]
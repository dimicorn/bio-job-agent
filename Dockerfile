FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN pip install --no-cache-dir uv==0.12.13
COPY app ./app
COPY migrations ./migrations
RUN uv sync --frozen --no-dev
COPY config ./config
RUN mkdir /app/data && useradd --uid 10001 --no-create-home appuser && chown -R appuser:appuser /app
USER appuser
EXPOSE 8501
HEALTHCHECK --interval=15s --timeout=5s CMD ["/app/.venv/bin/python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8501/_stcore/health', timeout=3)"]
CMD ["/app/.venv/bin/streamlit", "run", "app/dashboard/main.py", "--server.address=0.0.0.0", "--server.headless=true", "--browser.gatherUsageStats=false"]

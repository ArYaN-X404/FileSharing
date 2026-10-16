# ==============================================================================
# Production Dockerfile for Telegram File-Sharing & Streaming Bot
# Optimized for high concurrency, lightweight footprint, and container resilience
# ==============================================================================

FROM python:3.11-slim

# Prevent Python from writing .pyc files and enable unbuffered streaming stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8085

WORKDIR /app

# Install system build dependencies for C extensions (TgCrypto, Pillow, Psycopg2)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    python3-dev \
    libffi-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install python dependencies first (Docker layer cache optimization)
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Copy application source code
COPY . .

# Create persistent storage directories with proper permissions
RUN mkdir -p /app/downloads /app/data

# Expose internal web server health port
EXPOSE 8085

# Built-in Docker Healthcheck against the integrated aiohttp server
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:${PORT:-8085}/ || exit 1

# Launch the Bot service
CMD ["python", "main.py"]

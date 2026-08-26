# ==========================================
# Dockerfile for Stoic Discord Quote Bot
# ==========================================
FROM python:3.11-slim

# Prevent Python from writing .pyc files and buffer stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV TZ=Europe/Ljubljana

# Set working directory
WORKDIR /app

# Install system dependencies & timezone data
RUN apt-get update && apt-get install -y --no-install-recommends \
    tzdata \
    ca-certificates \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

# Copy and install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY bot.py .

# Create volume mount point for persistent SQLite database
VOLUME /app/data

# Default environment override for database location in docker
ENV DATABASE_PATH=/app/data/history.db

# Run the bot scheduler
CMD ["python", "bot.py"]

FROM python:3.12-slim

# Install ffmpeg for audio conversion (WAV → OGG/Opus)
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py .

# Cloud Run provides PORT env var (default 8080)
ENV PORT=8080
EXPOSE 8080

CMD ["python", "bot.py"]

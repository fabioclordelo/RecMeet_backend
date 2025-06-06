# Use Python base image
FROM python:3.11-slim

# Install ffmpeg and dependencies
RUN apt-get update && \
    apt-get install -y ffmpeg && \
    apt-get clean

# Set working directory
WORKDIR /app

# Copy project files
COPY . .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Expose port
EXPOSE 8080

ENV TRANSFORMERS_CACHE=/root/.cache/huggingface

RUN python3 -c "from faster_whisper import WhisperModel; WhisperModel('base', local_files_only=False)"

# Run with Gunicorn
CMD ["gunicorn", "-b", "0.0.0.0:8080", "--timeout", "600", "app:app"]
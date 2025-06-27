# Use Python base image
FROM python:3.11-slim

# Install minimal ffmpeg dependencies for audio processing
# Specifically targeting libsndfile1 which is often required by torchaudio
# and a more streamlined ffmpeg installation.
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    ffmpeg \
    libsndfile1 && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy project files
COPY . .

# Install Python dependencies
# Use --extra-index-url to specify the PyTorch CPU-only package index.
RUN pip install --no-cache-dir -r requirements.txt --extra-index-url https://download.pytorch.org/whl/cpu

# Expose port
EXPOSE 8080

ENV TRANSFORMERS_CACHE=/root/.cache/huggingface

# Pre-bundle the "tiny" model during the build process
# This ensures the model is available locally when the application runs.
RUN python3 -c "from faster_whisper import WhisperModel; WhisperModel('tiny', local_files_only=False)"

# Run with Gunicorn
CMD ["gunicorn", "-b", "0.0.0.0:8080", "--timeout", "600", "app:app"]

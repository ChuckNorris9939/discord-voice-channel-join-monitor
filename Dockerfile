# Filename: Dockerfile
FROM python:3.12

# Install ffmpeg, wget, unzip and curl for health checks
RUN apt-get update && apt-get install -y ffmpeg wget unzip portaudio19-dev python3-dev curl

WORKDIR /app

# Download and unzip the Vosk model
# Using the smaller, but still accurate, model for efficiency
#RUN wget https://alphacephei.com/vosk/models/vosk-model-de-0.21.zip && \
#    unzip vosk-model-de-0.21.zip && \
#    mv vosk-model-de-0.21 /app/vosk-model && \
#    rm vosk-model-de-0.21.zip
# Vosk Model ist nicht im Container mit drinnnen, sondern als Volume gemounted

# Copy requirements first to leverage Docker cache
COPY requirements.txt .

# Install python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy Python source files
COPY *.py .

# Copy health check script and make it executable
COPY health_check.sh .
RUN chmod +x health_check.sh

# Copy templates directory
COPY templates/ templates/

# Copy data assets (images and sounds)
COPY data/assets/images/ data/assets/images/
COPY data/assets/sounds/ data/assets/sounds/

# Create necessary directories for volume mounts
RUN mkdir -p data/assets/models data/logs data/garmin-output data/aligned-recordings data/temp

# Expose port for the web server (matching the default in main.py)
EXPOSE 8080

# Add health check using the existing /status endpoint
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
  CMD curl -f http://localhost:8083/status || exit 1

CMD ["python", "main.py"]

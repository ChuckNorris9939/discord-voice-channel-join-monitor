# Filename: Dockerfile
FROM python:3.12

# Install ffmpeg, wget and unzip
RUN apt-get update && apt-get install -y ffmpeg wget unzip

WORKDIR /app

# Download and unzip the Vosk model
# Using the smaller, but still accurate, model for efficiency
RUN wget https://alphacephei.com/vosk/models/vosk-model-de-0.21.zip && \
    unzip vosk-model-de-0.21.zip && \
    mv vosk-model-de-0.21 /app/vosk-model-de && \
    rm vosk-model-de-0.21.zip

# Copy requirements first to leverage Docker cache
COPY requirements.txt .

# Install python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code
COPY . .

# Expose port for the web server (matching the default in main.py)
EXPOSE 8080

CMD ["python", "main.py"]

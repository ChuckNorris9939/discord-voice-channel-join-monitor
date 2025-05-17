# Filename: Dockerfile
FROM python:3.12

EXPOSE 8000

# Copy reicht!
#ADD main.py .
#ADD keep_up.py .

WORKDIR /app
COPY . /app

#ENV DISCORD_TOKEN=
#ENV DISCORD_SERVER_ID=
#ENV HIDDEN_CHANNELS=
#ENV AUDIT_CHANNEL=

RUN pip install discord flask
CMD ["python", "main.py"]

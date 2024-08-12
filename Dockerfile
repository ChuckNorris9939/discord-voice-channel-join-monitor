# Filename: Dockerfile
FROM python:3.12
EXPOSE 8000
ADD main.py .
ADD keep_up.py .
WORKDIR /app
COPY . /app

ENV TOKEN=
ENV HIDDEN_CHANNELS=
ENV AUDIT_CHANNEL=

RUN pip install discord flask
CMD python main.py

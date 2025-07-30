# Filename: Dockerfile
FROM python:3.12

EXPOSE 8000


WORKDIR /app
# Kein ADD noetig, copy reicht
COPY . /app

#Dies weist Python an, stdout und stderr nicht zu puffern. brauchen wir nicht da logger. zeigt aber mehr an... 
#ENV PYTHONUNBUFFERED=1

RUN pip install --no-cache-dir "discord.py[voice]" flask waitress
CMD ["python", "main.py"]

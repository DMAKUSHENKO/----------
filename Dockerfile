FROM python:3.13-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . ./

# Окружение: BOT_TOKEN должен быть задан при запуске контейнера или через .env
ENV PYTHONUNBUFFERED=1

CMD ["python", "-m", "app.main"]



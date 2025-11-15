## Бот-конвертер «кружков» (Telegram video note)

Бот принимает от пользователя видео (обычное), видео-заметки и документы с видео, конвертирует их с помощью FFmpeg в квадратный формат 640x640 и отправляет обратно как `video_note` (кружок).

### Требования
- Python 3.10+
- FFmpeg (установить отдельно)

Установка FFmpeg на macOS:
```bash
brew install ffmpeg
```

### Установка и запуск
1. Создайте и активируйте виртуальное окружение:
```bash
python3 -m venv venv
source venv/bin/activate
```
2. Установите зависимости:
```bash
pip install -r requirements.txt
```
3. Создайте файл `.env` в корне проекта и добавьте токен бота:
```bash
echo "BOT_TOKEN=ваш_токен" > .env
```
4. Запустите бот:
```bash
python -m app.main
```

### Лимиты и защита
- Размер входного видео: по умолчанию 20 МБ (`USER_VIDEO_MAX_MB`).
- Лимит длительности: по умолчанию 90 сек (`MAX_VIDEO_DURATION_SECONDS`).
- Таймаут FFmpeg: по умолчанию 600 сек (`FFMPEG_TIMEOUT_SECONDS`).
- Параллелизм: не более 2 одновременных задач (`MAX_CONCURRENCY`).
- Пер-юзер rate limit: 20 сек между задачами (`USER_RATE_LIMIT_SECONDS`).

Все значения настраиваются через `.env`.

### Деплой 24/7
Вариант Docker:
```bash
docker build -t tg-videonote-bot .
docker run -d --name tg-videonote-bot --restart=always \
  -e BOT_TOKEN=ваш_токен \
  --env-file .env \
  tg-videonote-bot
```

Вариант systemd:
1) Скопируйте проект на сервер, создайте `venv`, установите зависимости, создайте `.env`
2) Скопируйте `deploy/telegram-videonote-bot.service` в `/etc/systemd/system/`
3) Активируйте:
```bash
sudo systemctl daemon-reload
sudo systemctl enable telegram-videonote-bot
sudo systemctl start telegram-videonote-bot
sudo systemctl status telegram-videonote-bot
```

### Как это работает
- Бот принимает видео/видео-заметки/видео-документы
- Скачивает файл через Telegram API
- Конвертирует через FFmpeg в квадрат 640x640 (H.264 + AAC), добавляет `faststart` для быстрой отправки
- Отправляет как `video_note`

### Улучшение качества/скорости
- Качество: уменьшайте CRF (например, 22 → 20) в `ffmpeg_utils.py`
- Скорость: увеличивайте `-preset` (например, `veryfast` → `faster`/`ultrafast`) — качество немного снизится
- Аппаратное ускорение (macOS): можно переключиться на `h264_videotoolbox` (см. комментарии в `ffmpeg_utils.py`)



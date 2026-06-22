# Деплой Telegram-бота на зарубежный VPS

Бот работает по long polling — публичный домен, IP и вебхуки **не нужны**.
Достаточно дешёвого Linux-VPS, где процесс крутится под systemd (автозапуск при
загрузке + перезапуск после сбоя).

## Почему зарубежный VPS

Бот ходит в три внешних сервиса. Два из них в РФ часто заблокированы:

| Сервис | Назначение | Доступ из РФ |
|--------|-----------|--------------|
| `api.telegram.org` | Telegram | часто заблокирован |
| `api.groq.com` | разбор сообщений (LLM) | часто заблокирован |
| `ekrom.ru` | подбор техники | доступен |

На зарубежном VPS Telegram и Groq доступны напрямую — **прокси не нужен**
(`TELEGRAM_PROXY` оставьте пустым). После старта проверьте `/login` — убедитесь,
что сервер видит `ekrom.ru`.

## Требования к серверу

- Ubuntu 22.04 или 24.04.
- **≥ 2 ГБ RAM** (бот поднимает headless Chromium и до 4 вкладок параллельно;
  на 1 ГБ — добавьте swap).
- Любой провайдер за пределами РФ (Hetzner, DigitalOcean, Vultr, Contabo и т.п.).

## Шаги

```bash
# 1. Подключиться к серверу
ssh youruser@SERVER_IP

# 2. Забрать код
sudo mkdir -p /opt/max-ekrom-bot && sudo chown "$USER" /opt/max-ekrom-bot
git clone <ВАШ-РЕПОЗИТОРИЙ> /opt/max-ekrom-bot
cd /opt/max-ekrom-bot

# 3. Установка зависимостей + Chromium (один скрипт)
bash deploy/setup.sh

# 4. Заполнить секреты
nano .env
#   TELEGRAM_BOT_TOKEN=...
#   TELEGRAM_ALLOWED_IDS=<ваш user_id из @userinfobot>
#   GROQ_API_KEY=...
#   EKROM_LOGIN=... / EKROM_PASSWORD=...   (нужны для входа на headless-сервере)
#   TELEGRAM_PROXY=   (оставить пустым на зарубежном VPS)

# 5. Первый вход в ekrom (сохранит сессию в data/storage_state.json)
./.venv/bin/python scripts/login_ekrom.py

# 6. Автозапуск через systemd
sudo bash deploy/install-service.sh
```

Готово — бот запущен и поднимется сам после перезагрузки сервера.

## Управление

```bash
systemctl status max-ekrom-bot        # статус
journalctl -u max-ekrom-bot -f        # живые логи
sudo systemctl restart max-ekrom-bot  # перезапуск
sudo systemctl stop max-ekrom-bot     # остановить
bash deploy/update.sh                 # обновить код и перезапустить
```

## Важные моменты

- **Один поллер.** Telegram при long polling пускает только один экземпляр. Пока
  бот работает на сервере, **локально его запускать нельзя** (иначе `409 Conflict`).
- **Проверка после старта:** напишите боту `/status` и `/login`.
- **Обновление сессии ekrom:** когда вход протухнет — команда `/login` прямо в
  Telegram (логинится автоматически по EKROM_LOGIN/PASSWORD).
- **Секреты:** `.env` и `data/storage_state.json` хранятся только на сервере и в
  git не попадают.

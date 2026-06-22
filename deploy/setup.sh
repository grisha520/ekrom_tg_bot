#!/usr/bin/env bash
# Подготовка сервера к запуску Telegram-бота (Ubuntu 22.04/24.04, зарубежный VPS).
# Идемпотентен: можно запускать повторно. НЕ требует прав root для самого бота,
# но ставит системные пакеты через sudo.
#
# Запуск из корня репозитория:  bash deploy/setup.sh
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"
echo "==> Репозиторий: $REPO_DIR"

echo "==> Системные пакеты (python, venv, git)…"
sudo apt-get update -y
sudo apt-get install -y python3 python3-venv python3-pip git

echo "==> Виртуальное окружение…"
if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi
./.venv/bin/pip install --upgrade pip
./.venv/bin/pip install -r requirements.txt
# Сам пакет (src-layout) — иначе `python -m max_ekrom_bot...` его не найдёт.
./.venv/bin/pip install -e .

echo "==> Chromium для Playwright + системные библиотеки…"
./.venv/bin/playwright install --with-deps chromium

echo "==> Проверка .env…"
if [ ! -f ".env" ]; then
  cp .env.example .env
  chmod 600 .env
  echo "    Создан .env из примера — ЗАПОЛНИТЕ его (nano .env) перед запуском!"
else
  chmod 600 .env
  echo "    .env уже есть."
fi

cat <<EOF

==> Готово. Дальше:
  1) nano .env        # заполнить TELEGRAM_BOT_TOKEN, TELEGRAM_ALLOWED_IDS,
                      #            GROQ_API_KEY, EKROM_LOGIN, EKROM_PASSWORD
  2) ./.venv/bin/python scripts/login_ekrom.py   # первый вход в ekrom
  3) sudo bash deploy/install-service.sh          # автозапуск через systemd
EOF

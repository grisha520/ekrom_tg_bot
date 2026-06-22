#!/usr/bin/env bash
# Обновление бота на сервере: подтянуть код, доустановить зависимости,
# перезапустить сервис.
#
# Запуск из корня репозитория:  bash deploy/update.sh
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

echo "==> git pull…"
git pull --ff-only

echo "==> Зависимости…"
./.venv/bin/pip install -r requirements.txt

echo "==> Перезапуск сервиса…"
sudo systemctl restart max-ekrom-bot
sudo systemctl --no-pager status max-ekrom-bot || true

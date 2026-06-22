#!/usr/bin/env bash
# Устанавливает systemd-сервис для автозапуска бота (старт при загрузке +
# перезапуск после сбоя). Юнит генерируется под текущий путь и пользователя,
# поэтому редактировать вручную ничего не нужно.
#
# Запуск:  sudo bash deploy/install-service.sh
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Запустите через sudo: sudo bash deploy/install-service.sh" >&2
  exit 1
fi

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_USER="${SUDO_USER:-root}"
PYTHON="$REPO_DIR/.venv/bin/python"
UNIT="/etc/systemd/system/max-ekrom-bot.service"

if [ ! -x "$PYTHON" ]; then
  echo "Не найден $PYTHON — сначала выполните: bash deploy/setup.sh" >&2
  exit 1
fi
if [ ! -f "$REPO_DIR/.env" ]; then
  echo "Не найден $REPO_DIR/.env — создайте и заполните его перед установкой сервиса." >&2
  exit 1
fi

echo "==> Пишу $UNIT (пользователь: $RUN_USER)…"
cat > "$UNIT" <<EOF
[Unit]
Description=max-ekrom-bot (Telegram bot, long polling)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$RUN_USER
WorkingDirectory=$REPO_DIR
ExecStart=$PYTHON -m max_ekrom_bot.telegram_app
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

echo "==> Включаю и запускаю сервис…"
systemctl daemon-reload
systemctl enable --now max-ekrom-bot

echo
echo "==> Готово. Статус:"
systemctl --no-pager status max-ekrom-bot || true
echo
echo "Логи:        journalctl -u max-ekrom-bot -f"
echo "Перезапуск:  sudo systemctl restart max-ekrom-bot"

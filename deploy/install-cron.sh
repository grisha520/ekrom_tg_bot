#!/usr/bin/env bash
# Ставит регулярное обновление данных: каталог товаров и раздел «Акции».
#
# Зачем акции по расписанию: бот обещает клиенту комплектную скидку по правилам
# кампании, а кампании на сайте меняются — заканчиваются, продлеваются, появляются
# новые. Без обновления датасет протухает молча. Истёкшие акции движок отсекает по
# сроку (PromoStore.campaigns_for_brand), но новые и продлённые подхватит только
# краул.
#
# Идемпотентен: повторный запуск переписывает свои задания, чужие строки crontab
# не трогает.
#
# Запуск:  bash deploy/install-cron.sh
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="$REPO_DIR/.venv/bin/python"
LOG_DIR="$REPO_DIR/data/logs"
MARK="# max-ekrom-bot cron"

if [ ! -x "$PYTHON" ]; then
  echo "Не найден $PYTHON — сначала выполните: bash deploy/setup.sh" >&2
  exit 1
fi

mkdir -p "$LOG_DIR"

# Каталог — в 04:00, акции — в 04:40 (после каталога: цена подарка ищется по
# таблице products, которую наполняет краул каталога).
NEW_JOBS=$(cat <<EOF
$MARK (не редактируйте вручную — перезапишется deploy/install-cron.sh)
0 4 * * * cd $REPO_DIR && $PYTHON scripts/crawl_catalog.py >> $LOG_DIR/crawl_catalog.log 2>&1
40 4 * * * cd $REPO_DIR && $PYTHON scripts/crawl_promos.py >> $LOG_DIR/crawl_promos.log 2>&1
EOF
)

echo "==> Обновляю crontab пользователя $(whoami)…"
CURRENT="$(crontab -l 2>/dev/null || true)"
# Выкидываем прошлые строки этого скрипта (маркер + обе задачи), остальное храним.
KEPT="$(printf '%s\n' "$CURRENT" | grep -v -F "$MARK" | grep -v -F "scripts/crawl_catalog.py" \
        | grep -v -F "scripts/crawl_promos.py" || true)"
printf '%s\n%s\n' "$KEPT" "$NEW_JOBS" | sed '/^$/d' | crontab -

echo "==> Готово. Текущие задания:"
crontab -l | grep -A2 -F "$MARK" || true
cat <<EOF

Разовый прогон вручную (полезно сразу после установки):
  $PYTHON scripts/crawl_catalog.py
  $PYTHON scripts/crawl_promos.py

Логи: $LOG_DIR/crawl_catalog.log, $LOG_DIR/crawl_promos.log
EOF

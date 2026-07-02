# max-ekrom-bot — контекст проекта (handoff для нового чата)

> Вставь этот файл **или** `docs/HANDOFF_ДИАЛОГ_2026-07-01.md` первым сообщением в новом чате.
> Секретов (токены/пароли/ключи) тут нет — они в `.env` на сервере.

## Что это
Телеграм-бот подбора бытовой техники магазина **ekrom.ru**. **Клиент пишет боту
сам**, бот разбирает запрос, подбирает товары по всему каталогу ekrom и присылает
**PDF-подбор** (свой шаблон с ценами, фото, характеристиками). Язык — русский.
Бот: **@ekroom_bot**. Возможна интеграция в мессенджер МАКС.

## Стек и архитектура
- **Python 3.12** (сервер) / 3.13 (локально), пакет `max_ekrom_bot`.
- **aiogram 3.20** — Telegram (long polling).
- **Playwright** — ekrom (обогащение, операторская корзина).
- **YandexGPT** Lite (парсер) + Pro (резолвер категорий).
- **reportlab** — клиентский PDF. **SQLite** — кэш каталога + история + метрики.
- Ядро: `pipeline.py` → parse → резолвер → кэш → селектор → enrich → PDF.

### Ключевые файлы
- `telegram_app.py` — бот, роли, лиды, история, stats.
- `messenger/client_ux.py` — тексты/кнопки клиента.
- `pick_history.py` — «📂 Мои подборы» (PDF на диске).
- `bot_metrics.py` — `/stats` для админа.
- `pipeline.py`, `matching/selector.py`, `parsing/category_resolver.py`.
- `pdf/offer.py`, `ekrom/catalog_cache.py`, `scripts/crawl_catalog.py`.
- `scripts/client_e2e_suite.py` — e2e-прогон 12 кейсов.

## Деплой / сервер
- **VPS:** 77.221.152.85 (PARs-2: 2 vCPU, 4 ГБ, 60 ГБ NVMe, Ubuntu 24.04).
- **systemd:** `max-ekrom-bot`, каталог `/opt/max-ekrom-bot`.
- **Bitvise sexec:** `-hostKeyFp=WfRy4N4Qbbla+ql9cPCVbRhkzv5tEhf6im9XOVdRiSE`
- **Деплой:** `git push` → `git pull --ff-only && bash deploy/update.sh`
- **Cron 04:00:** `scripts/crawl_catalog.py`
- ⚠️ Сменить root-пароль, настроить SSH-ключ / профиль Bitvise (пароль светился в чате).

## GitHub
- https://github.com/grisha520/ekrom_tg_bot — **последний коммит `79c9757`**
- Не в git: `.env`, `storage_state.json`, `managers.json`, `catalog.db*`,
  `bot_analytics.db`, `data/history/`

## .env на проде (имена)
`TELEGRAM_*`, `YANDEX_*`, `LLM_PROVIDER=yandex`, `LLM_MODEL=yandexgpt-lite`,
`LLM_RESOLVER_MODEL=yandexgpt`, `CLIENT_MODE=true`, `CLIENT_RATE_PER_HOUR=8`,
`CLIENT_RATE_PER_DAY=30`, `NOTIFY_LEAD=true`, `USE_CATALOG_CACHE=true`,
`SHOP_NAME/CONTACTS/SITE`, `EKROM_LOGIN/PASSWORD`.

## Клиентский режим
- Подбор и PDF — всем; операторские кнопки — только allowlist/admin.
- **Тест клиента** — с аккаунта НЕ из `TELEGRAM_ALLOWED_IDS` (@omark64 = оператор).
- Кнопки: 💡 Примеры, 📂 Мои подборы, 📞 Контакты, ℹ️ Справка.
- Команды: `/start`, `/history`, `/examples`, `/contacts`.
- После подбора: авто-PDF + лид на `TELEGRAM_ADMIN_IDS` (7413559198).

## История и метрики (новое)
- **История:** 20 PDF / 30 дней, `data/history/{user_id}/`, БД `data/bot_analytics.db`.
- **Админ `/stats`:** запросы/день, очередь, время подбора, ошибки (МСК).

## Кэш и производительность
- ~7900 товаров, many-to-many, WAL. Тёплый путь ~0.6 с; enrich ~9–11 с.
- **Узкое место:** `_EKROM_LOCK` — один подбор за раз. VPS хватает на ~30–80 клиентов/день комфортно.

## Что сделано (включая сессию 2026-07-01)
- Резолвер Pro + только родительские категории.
- Клиентский UX v2 (онбординг, примеры, пошаговый статус).
- История PDF «Мои подборы» + метрики `/stats` для админа.
- E2E-скрипт, 138 тестов pytest.

## Не сделано (приоритет)
1. Селектор: кофемашина/морозилка по замечаниям e2e.
2. `/preview` для оператора.
3. Три варианта на позицию (бюджет/оптимум/премиум).
4. Уточнения «дешевле/другой цвет», лиды v2, алерты stats.
5. Параллельная очередь Playwright, МАКС (`max_app.py`).

Подробный план улучшений — `docs/HANDOFF_ДИАЛОГ_2026-07-01.md`.

## Команды
```bash
.venv/Scripts/python.exe -m pytest -q          # Windows, 138 тестов
./.venv/bin/python scripts/client_e2e_suite.py # e2e
# сервер: git pull --ff-only && bash deploy/update.sh
```

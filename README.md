# Feedor: мониторинг и аналитика Telegram-каналов

Витринная копия кода сервиса [feedor.pro](https://feedor.pro) для работодателей. Это снимок рабочей версии на 30 октября 2026 без истории коммитов. Адреса серверов и личные пути в документации заменены заглушками.

Автор: Николай Кондаков. Всю техническую часть (архитектура, бэкенд, фронтенд, деплой, поддержка) я делаю сам вместе с Claude Code: ставлю задачи, проверяю сгенерированный код и прогоняю тесты.

## Что умеет

- **Сбор постов.** Пул пользовательских аккаунтов Telegram (Telethon) собирает посты из списка каналов. Запросы идут по одному с паузой, FloodWait ставит аккаунт на паузу, а каналы отвалившейся сессии подхватывают другие аккаунты. Telegram доставляет обновления каналов не всегда, поэтому каждый канал ещё и опрашивается по расписанию, с дедупликацией.
- **Метрики.** Просмотры, реакции, комментарии и репосты обновляются тем чаще, чем свежее пост. ER, FR и FR+ (FR поста относительно среднего по каналу).
- **Прокси.** Общий пул SOCKS5 и MTProto с автоматической проверкой. Аккаунты распределяются по прокси и переезжают с нерабочих.
- **Закрытые каналы** держат запасной аккаунт, который перехватывает сбор, если основной потерял доступ.
- **ИИ-функции через OpenRouter:**
  - умная лента: LLM фильтрует посты по критериям пользователя, ответ приходит по строгой JSON Schema
  - темы: LLM собирает посты в новостные сюжеты с таймлайном и саммари
  - чат-агент по данным проекта с tool calling
  - валидация ответов модели, повторы, учёт стоимости каждого запроса, защита от prompt injection
- **Продукт.** Вход через Telegram-бота, проекты, тарифы, сотрудники, публичный API `/api/v1` с ключом и лимитом 120 запросов в минуту, MCP-коннектор, уведомления через своего бота, PWA.
- **Бэкапы.** Шифрование age, выгрузка в Google Drive через rclone, systemd-таймер и проверка восстановления.

## Архитектура

`web` и `worker` это два процесса одного Docker-образа, общаются только через базу.

```
backend/app/
  worker/      фоновый процесс: аккаунты, вступление в каналы, приём и опрос постов, метрики
  web/         FastAPI: вход, посты, каналы, проекты, тарифы, публичный API
  tg/          разбор ссылок и сообщений Telegram, клиенты
backend/tests/ тесты pytest
frontend/      React, TypeScript, Vite, Framer Motion (интерфейс в стиле Telegram на iPhone)
deploy/        Caddy и бэкапы
```

Подробное описание устройства: [docs/architecture.md](docs/architecture.md).

## Стек

Python, FastAPI, asyncio, SQLAlchemy, PostgreSQL (локально SQLite), Telethon, OpenRouter, React, TypeScript, Vite, Framer Motion, Docker Compose, Caddy.

Около 22 000 строк Python и 27 000 строк TypeScript, больше 300 тестов на pytest.

## Запуск локально

```bash
cd backend
python -m venv .venv && .venv/Scripts/pip install -r requirements-dev.txt   # Windows
python -m app.devseed            # демо-данные в backend/data/dev.db (SQLite)
python -m uvicorn app.web.main:app --port 8000
python -m pytest
```

```bash
cd frontend
npm install
npm run dev
```

Воркеру для сбора постов нужен реальный аккаунт Telegram и `TG_API_ID`/`TG_API_HASH` в `.env` (образец в `.env.example`).

## Контакты

Николай Кондаков
nikolay.d.kondakov@gmail.com
Telegram: @tankionline2005

## In English

Feedor is a SaaS for monitoring and analyzing Telegram channels ([feedor.pro](https://feedor.pro)). This repository is a showcase snapshot of the production code without commit history. Stack: Python, FastAPI, asyncio, SQLAlchemy, PostgreSQL, Telethon, LLM features via OpenRouter (structured output with JSON Schema, tool calling), React and TypeScript frontend, Docker Compose, 300+ pytest tests. Built by Nikolay Kondakov with Claude Code.

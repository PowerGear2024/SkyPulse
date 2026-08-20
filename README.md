# SkyPulse — Telegram user-session AI (Telethon + SQLite + OpenAI/Anthropic)

Собеседник отвечает **от твоего аккаунта** (user-сессия), не через @BotFather.
Роль: Даниил Коваль из Одессы. История в SQLite, ответы через GPT-4o / Claude.

## Структура

```
bot/
  main.py              # точка входа user-сессии
  login.py             # первый логин + StringSession
  config.py            # .env
  persona.py           # ФИО / характер / промпт
  database.py          # SQLite
  handlers/
    messages.py        # входящие ЛС
    helpers.py
  services/
    gate.py            # rate-limit
    llm.py             # OpenAI / Anthropic
.env.example
requirements.txt
```

## Быстрый старт

### 1. Зависимости

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

### 2. Ключи Telegram (user API)

1. Зайди на https://my.telegram.org/apps
2. Создай приложение → скопируй `api_id` и `api_hash` в `.env`:

```env
TELEGRAM_API_ID=...
TELEGRAM_API_HASH=...
TELEGRAM_SESSION_NAME=user
```

Плюс ключ LLM (`OPENAI_API_KEY` или `ANTHROPIC_API_KEY`).

### 3. Первый логин (один раз)

```bash
python -m bot.login
```

Введёшь телефон и код из Telegram. Появится `data/user.session` и
(опционально) строка `TELEGRAM_SESSION_STRING=...` для `.env`.

### 4. Запуск

```bash
python -m bot
```

Пиши ему в личку со другого аккаунта: `/start`, обычный текст, `/reset`.

## Важно

- Это **не бот**, а клиент твоего аккаунта (MTProto / Telethon).
- Отвечает только на **входящие личные** сообщения (группы и исходящие игнор).
- Сессию (`*.session` / `TELEGRAM_SESSION_STRING`) храни как пароль.
- Автоматизация user-аккаунта может нарушать ToS Telegram — используй на свой риск.

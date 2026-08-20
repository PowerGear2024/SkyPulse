# SkyPulse — Telegram user-session AI (Telethon + SQLite + OpenAI/Anthropic)

Собеседник отвечает **от твоего аккаунта** (user-сессия), не через @BotFather.
Роль: Даниил Коваль из Одессы. История в SQLite, ответы через GPT-4o / Claude.

## Структура

```
bot/
  main.py                 # python -m bot
  login.py                # python -m bot.login
  telegram_client.py      # сборка Telethon-клиента
  config.py               # .env + whitelist
  persona.py
  database.py
  handlers/messages.py
  services/gate.py
  services/llm.py
```

## Быстрый старт

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

В `.env`:
1. `TELEGRAM_API_ID` / `TELEGRAM_API_HASH` с https://my.telegram.org/apps
2. Ключ LLM
3. **Рекомендуется** `ALLOWED_USER_IDS=123456789` — кому отвечать в ЛС

```bash
python -m bot.login   # телефон + код, один раз
python -m bot
```

## Команды в ЛС

| Текст | Действие |
|---|---|
| `/start` | Приветствие + запись в БД |
| `/reset` | Очистить историю |
| любой текст | Ответ в образе Дани |

## Безопасность

- `*.session` / `TELEGRAM_SESSION_STRING` = полный доступ к аккаунту, как пароль
- Пустой `ALLOWED_USER_IDS` = любой человек в ЛС жжёт твой LLM-бюджет
- Автоматизация user-аккаунта может нарушать ToS Telegram

# SkyPulse — Telegram user-session в ГРУППАХ (Telethon)

Отвечает **от твоего аккаунта** только в групповых чатах. **ЛС полностью игнор.**
Печатает как человек (typing в шапке + пауза от длины текста).
Память: видит все сообщения чата. Персона: Даня из Одессы — никогда не ИИ.

## Запуск

```bash
pip install -r requirements.txt
cp .env.example .env
# TELEGRAM_API_ID / HASH с https://my.telegram.org/apps + ключ LLM
# Рекомендуется ALLOWED_CHAT_IDS=-100...

python -m bot.login
python -m bot
```

## Поведение

| | |
|---|---|
| ЛС | полный игнор |
| Группы | читает все тексты в память |
| Ответ | по умолчанию только `@mention` / reply на твоё сообщение |
| Реакции | ответ, если поставили реакцию на **твоё** сообщение |
| Проактив | иногда коммент по теме 10 смс одного юзера, ≤5 раз/день |
| Печать | индикатор «печатает…» + задержка ~длина ответа |
| `/start` | приветствие |
| `/reset` | очистить память этого чата |

### Ключевые переменные

- `GROUP_REPLY_MODE=mention` (или `all`)
- `REPLY_ON_REACTIONS=true`
- `PROACTIVE_ENABLED=true`, `PROACTIVE_MAX_PER_DAY=5`

## Важно

- `*.session` / `TELEGRAM_SESSION_STRING` = доступ к аккаунту
- Userbot может нарушать ToS Telegram

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
| Ответ | `GROUP_REPLY_MODE=all` или `mention` |
| Печать | индикатор «печатает…» + задержка ~длина ответа |
| `/start` | приветствие |
| `/reset` | очистить память этого чата |

## Важно

- `*.session` / `TELEGRAM_SESSION_STRING` = доступ к аккаунту
- Userbot может нарушать ToS Telegram

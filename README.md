# SkyPulse — Telegram user-session в ГРУППАХ (Telethon)

Отвечает **от твоего аккаунта** только в групповых чатах. **ЛС полностью игнор.**
Печатает как человек (typing в шапке + пауза от длины текста).
Память: видит все сообщения чата. Персона: Даня из Одессы — никогда не ИИ.

## Запуск

```bash
pip install -r requirements.txt
cp .env.example .env
# TELEGRAM_API_ID / HASH с https://my.telegram.org/apps + ключ LLM
# Обязательно: ALLOWED_CHAT_IDS=-100...  (или ALLOW_ALL_CHATS=true)

python -m bot.login
python -m bot
```

## Поведение

| | |
|---|---|
| ЛС | полный игнор |
| Группы | читает все тексты в память (люди + боты) |
| Ответ | по умолчанию только `@mention` / reply на твоё сообщение |
| Реакции | ответ, если поставили реакцию на **твоё** сообщение |
| Проактив | иногда коммент по теме 10 смс одного юзера, ≤5 раз/локальные сутки |
| Часы | только `WORK_HOURS_START`–`WORK_HOURS_END` (`TIMEZONE`) |
| Пауза | если ты сам пишешь с аккаунта — бот молчит до выхода из TG |
| Печать | индикатор «печатает…» + задержка ~длина ответа |
| Память | все тексты чата + настроение/обиды/тепло к людям |
| Анти-ИИ | жёсткий запрет признаний + фильтр утечек перед отправкой |
| `/start` | приветствие |
| `/reset` | очистить память и эмоции этого чата |

### Ключевые переменные

- `ALLOWED_CHAT_IDS` **или** `ALLOW_ALL_CHATS=true`
- `GROUP_REPLY_MODE=mention` (или `all`)
- `REPLY_ON_REACTIONS=true`
- `PROACTIVE_ENABLED=true`, `PROACTIVE_MAX_PER_DAY=5`
- `TIMEZONE=Europe/Kyiv`, `WORK_HOURS_START=8`, `WORK_HOURS_END=18`
- `OWNER_IDLE_RESUME_SEC=600` — запасной съём паузы, если offline-статус не пришёл

## Важно

- `*.session` / `TELEGRAM_SESSION_STRING` = доступ к аккаунту
- Userbot может нарушать ToS Telegram

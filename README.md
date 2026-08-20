# SkyPulse — Telegram AI-бот (aiogram 3 + SQLite + OpenAI/Anthropic)

Дерзкий ИИ-собеседник для Telegram: сохраняет пользователей в SQLite,
держит скользящее окно истории диалога и отвечает через GPT-4o или Claude.

## Структура проекта

```
.
├── bot/
│   ├── __init__.py
│   ├── main.py              # точка входа, polling, DI
│   ├── config.py            # загрузка .env
│   ├── database.py          # SQLite (users + messages)
│   ├── handlers/
│   │   ├── __init__.py      # сборка роутеров
│   │   ├── start.py         # /start, /reset
│   │   └── chat.py          # текстовый диалог с LLM
│   └── services/
│       ├── __init__.py
│       └── llm.py           # OpenAI / Anthropic + системный промпт
├── .env.example             # шаблон секретов
├── .gitignore
├── requirements.txt
└── README.md
```

## Быстрый старт

### 1. Python 3.10+

```bash
python3 --version
```

### 2. Виртуальное окружение и зависимости

```bash
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Настроить `.env`

```bash
cp .env.example .env
```

Обязательно заполни:

| Переменная | Описание |
|---|---|
| `BOT_TOKEN` | Токен от [@BotFather](https://t.me/BotFather) |
| `LLM_PROVIDER` | `openai` или `anthropic` |
| `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` | Ключ выбранного провайдера |

Рекомендуемые значения для «живого» вайба:

- `LLM_TEMPERATURE=0.85`
- `HISTORY_LIMIT=14`
- `OPENAI_MODEL=gpt-4o` или `ANTHROPIC_MODEL=claude-sonnet-4-20250514`

### 4. Запуск

```bash
python -m bot.main
```

Бот стартует в long-polling. В Telegram: `/start` → диалог. `/reset` чистит историю.

## Команды бота

| Команда | Действие |
|---|---|
| `/start` | Регистрация в БД + приветствие |
| `/reset` | Очистить историю диалога |
| любой текст | Ответ LLM в характере «анонимного бро» |

## Замечания

- История хранится в SQLite (`data/bot.db` по умолчанию) и обрезается до `HISTORY_LIMIT`.
- Длинные ответы LLM автоматически режутся под лимит Telegram (4096 символов).
- Секреты только в `.env` — файл в `.gitignore`.

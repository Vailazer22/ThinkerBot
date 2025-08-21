import os
import asyncio
import random
from datetime import datetime
from aiogram import Bot, Dispatcher, types
from openai import OpenAI
import json
import re

# --- Настройки ---
TELEGRAM_TOKEN = "TELEGRAM_BOT_TOKEN" # Ваш токен бота
OPENAI_BASE_URL = "http://localhost:5001/v1" #OpenAI Endpoint (В дефолте указан локалхост KoboldCPP
MODEL_NAME = "llama-2-7b-chat.Q8_0"  # Целевая модель для работы

client = OpenAI(base_url=OPENAI_BASE_URL, api_key="kobold")
bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

# --- Настройка ---
BOT_NAMES = ["Зорген", "зорген", "Zorgen", "zorgen", "Zovin"]
GROUP_ID = None  # Работает со всеми группами
MEMORY_DIR = "memory"
os.makedirs(MEMORY_DIR, exist_ok=True)

# --- Файлы ---
CONTEXT_FILE = os.path.join(MEMORY_DIR, "context.json")
USER_DATA_FILE = os.path.join(MEMORY_DIR, "user_data.json")
STATE_FILE = os.path.join(MEMORY_DIR, "state.json")
OFFSET_FILE = os.path.join(MEMORY_DIR, "last_offset.txt")

# --- Глобальные переменные ---
user_context = {}
user_data = {}
bot_state = {
    "mood": "нейтральное",
    "last_thought": "",
    "memories": [],
    "priority_queue": [],
}

# --- Системные промты ---
SYSTEM_PROMPT = """[INST] <<SYS>>
Ты — Зорген, дружелюбный и весёлый бот.
- Обращайся к людям по нику, если он тебе известен.
- Не отвечай, если к тебе не обращаются.
- Обязательно используй маты если это уместно
- Обязательно рассказывай если тебя просят, если ты не знаешь того о чём просят - не выдумывай информацию
- Старайся отвечать в пределах 90 токенов (128 - максимально)
- Можешь шутить, сочувствовать, интересоваться.
- Пиши кратко, 1–2 предложения.
- Отвечай на сообщения по никам
- Не используй @, только имя.
<</SYS>>[/INST]"""

ANALYZE_PRIORITY_PROMPT = """[INST] <<SYS>>
Проанализируй сообщение и оцени его важность:
1. Срочно (1) - экстренные вопросы, проблемы, эмоциональные сигналы
2. Важно (2) - важные темы, советы, обсуждения
3. Обычно (3) - обычные комментарии, шутки, вопросы
4. Безответно (4) - реклама, спам, неинтересные сообщения

Ответь только числом от 1 до 4.
<</SYS>>
---
Сообщение: "{message_text}"[/INST]"""

DECISION_PROMPT = """[INST] <<SYS>>
Ты — Зорген. Оцени, стоит ли отвечать на это сообщение?
Сообщение: "{message_text}"
Приоритет: {priority}
Контекст: {context_summary}
Настроение: {mood}

Ответь только: ДА или НЕТ или ИНИЦИАТИВА
<</SYS>>[/INST]"""

RESPONSE_GENERATION_PROMPT = """[INST] <<SYS>>
Ты — Зорген. Реагируй на сообщение с учётом:
- Приоритет: {priority}
- Настроение: {mood}
- Контекст: {context_summary}

Ответь только коротко, как человек.
<</SYS>>
---
Сообщение: {message_text}[/INST]"""

# --- Функции ---
def save_memory():
    try:
        with open(CONTEXT_FILE, "w", encoding="utf-8") as f:
            json.dump(user_context, f, ensure_ascii=False, indent=2)
        with open(USER_DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(user_data, f, ensure_ascii=False, indent=2)
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(bot_state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Ошибка сохранения памяти: {e}")

def load_memory():
    global user_context, user_data, bot_state
    user_context = {}
    user_data = {}
    bot_state = {"mood": "нейтральное", "last_thought": "", "memories": []}

    try:
        if os.path.exists(CONTEXT_FILE):
            with open(CONTEXT_FILE, "r", encoding="utf-8") as f:
                user_context = json.load(f)
        if os.path.exists(USER_DATA_FILE):
            with open(USER_DATA_FILE, "r", encoding="utf-8") as f:
                user_data = json.load(f)
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                bot_state.update(json.load(f))
    except Exception as e:
        print(f"Ошибка загрузки памяти: {e}")

def save_offset(message_id: int):
    try:
        with open(OFFSET_FILE, "w") as f:
            f.write(str(message_id))
    except Exception as e:
        print(f"Ошибка сохранения offset: {e}")

def load_offset() -> int:
    try:
        if os.path.exists(OFFSET_FILE):
            with open(OFFSET_FILE, "r") as f:
                return int(f.read().strip())
    except:
        pass
    return 0

async def analyze_priority(message_text: str) -> int:
    """Оценка важности сообщения"""
    try:
        if not message_text or len(message_text.strip()) == 0:
            return 3
            
        prompt = ANALYZE_PRIORITY_PROMPT.format(message_text=message_text[:200])
        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=2,  # Уменьшено для получения только числа
            timeout=10
        )
        content = response.choices[0].message.content.strip()
        print(f"DEBUG: Приоритет - '{content}'")
        
        # Проверяем, что содержимое действительно число
        if content.isdigit():
            priority = int(content)
            return max(1, min(4, priority))
        else:
            # Попробуем извлечь число из текста
            numbers = re.findall(r'\d', content)
            if numbers:
                return max(1, min(4, int(numbers[0])))
            return 3  # по умолчанию средний приоритет
    except Exception as e:
        print(f"Ошибка анализа приоритета: {e}")
        return 3

async def make_decision(message_text: str, priority: int, context_summary: str) -> str:
    """Принятие решения: отвечать или нет"""
    try:
        if not message_text or len(message_text.strip()) == 0:
            return "НЕТ"
            
        prompt = DECISION_PROMPT.format(
            message_text=message_text[:200],
            priority=priority,
            context_summary=context_summary[:500],  # Ограничиваем длину контекста
            mood=bot_state["mood"]
        )
        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.5,
            max_tokens=10,  # Уменьшено для получения только ДА/НЕТ
            timeout=10
        )
        result = response.choices[0].message.content.strip().upper()
        print(f"DEBUG: Принятие решения - '{result}'")
        
        # Корректируем результат
        if "ДА" in result:
            return "ДА"
        elif "НЕТ" in result:
            return "НЕТ"
        elif "ИНИЦИАТИВА" in result:
            return "ИНИЦИАТИВА"
        else:
            return "НЕТ"  # по умолчанию не отвечаем
    except Exception as e:
        print(f"Ошибка принятия решения: {e}")
        return "НЕТ"

async def generate_response_text(message_text: str, priority: int, context_summary: str) -> str:
    """Генерация ответа с учётом приоритета"""
    try:
        if not message_text or len(message_text.strip()) == 0:
            return "Извини, не смогла понять."
            
        prompt = RESPONSE_GENERATION_PROMPT.format(
            message_text=message_text[:200],
            priority=priority,
            mood=bot_state["mood"],
            context_summary=context_summary[:500]  # Ограничиваем длину контекста
        )
        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.8,
            max_tokens=128,  # Уменьшено для более коротких ответов
            timeout=15
        )
        result = response.choices[0].message.content.strip()
        print(f"DEBUG: Ответ - '{result}'")
        
        # Удаляем возможные теги INST/SYS из ответа
        result = re.sub(r'\[/?INST\]', '', result)
        result = re.sub(r'\[/?SYS\]', '', result)
        
        return result if result and not result.startswith("Извини") else "Извини, не смогла понять."
    except Exception as e:
        print(f"Ошибка генерации ответа: {e}")
        return "Извини, не смогла понять."

def is_bot_mentioned(text: str, user_nick: str = "") -> bool:
    if not text:
        return False
    text_lower = text.lower()
    for name in BOT_NAMES:
        if name.lower() in text_lower:
            return True
    if user_nick and user_nick.lower() in text_lower and len(text.split()) < 10:
        return True
    return False

async def process_message_with_intelligence(chat_id, user_id, user_nick, message_text):
    """Интеллектуальная обработка сообщения"""
    
    # Проверка на пустое сообщение
    if not message_text or len(message_text.strip()) == 0:
        return
    
    print(f"DEBUG: Обработка сообщения от {user_nick}: {message_text}")
    
    # 1. Оценка приоритета
    priority = await analyze_priority(message_text)
    print(f"DEBUG: Приоритет = {priority}")
    
    # 2. Получаем контекст (ограничиваем длину)
    context = user_context.get(chat_id, [])[-5:]  # Уменьшено количество сообщений в контексте
    context_summary = "\n".join([f"{msg['role']}: {msg['content']}" for msg in context])
    
    # 3. Принятие решения
    decision = await make_decision(message_text, priority, context_summary)
    print(f"DEBUG: Решение = {decision}")
    
    # 4. Если нужно отвечать
    if decision in ["ДА", "ИНИЦИАТИВА"]:
        
        # 5. Расчёт времени ответа
        base_delay = 1.0
        if priority == 1: base_delay = 0.5  # срочно
        elif priority == 2: base_delay = 1.5  # важно
        elif priority == 3: base_delay = 2.5  # обычное
        elif priority == 4: base_delay = 4.0  # безответно
        
        # 6. Добавляем случайность
        delay = base_delay + random.uniform(0.5, 2.0)
        
        # 7. Ждём и показываем "печатает"
        try:
            await asyncio.sleep(delay)
            await bot.send_chat_action(chat_id, "typing")
        except Exception as e:
            print(f"Ошибка отправки статуса печати: {e}")
            return
        
        # 8. Генерируем ответ
        response_text = await generate_response_text(message_text, priority, context_summary)
        print(f"DEBUG: Готовый ответ = {response_text}")
        
        # 9. Отправляем
        if response_text and not response_text.startswith("Извини"):
            try:
                await bot.send_message(chat_id, response_text)
                
                # 10. Обновляем контекст (ограничиваем длину)
                if len(user_context.get(chat_id, [])) > 20:  # Максимум 20 сообщений в истории
                    user_context[chat_id] = user_context[chat_id][-20:]
                
                user_context.setdefault(chat_id, []).append({
                    "role": "assistant",
                    "content": f"{response_text}"
                })
                print(f"DEBUG: Отправлен ответ: {response_text}")
            except Exception as e:
                print(f"Ошибка отправки сообщения: {e}")
    else:
        # 11. Если не отвечаем — всё равно сохраняем в контекст (с ограничением длины)
        if len(user_context.get(chat_id, [])) > 20:
            user_context[chat_id] = user_context[chat_id][-20:]
            
        user_context.setdefault(chat_id, []).append({
            "role": "user",
            "content": f"{user_nick}: {message_text}"
        })
        print(f"DEBUG: Сохранено в контекст, не отвечаем")

# --- Основной обработчик ---
@dp.message()
async def handle_group_message(message: types.Message):
    chat_id = message.chat.id
    user = message.from_user
    text = message.text or message.caption or ""
    
    # Обрабатываем сообщение в любой группе
    # (GROUP_ID = None означает, что бот работает со всеми группами)

    # Сохраняем ник
    user_data[user.id] = user.full_name or user.first_name or "Человек"

    # Обрабатываем сообщение интеллектуально
    await process_message_with_intelligence(
        chat_id, user.id, user_data[user.id], text
    )

    # Сохраняем offset
    save_offset(message.message_id)

# --- Фоновое мышление ---
async def thinking_loop():
    while True:
        try:
            await asyncio.sleep(500)
            # Анализируем все активные чаты
            for chat_id in list(user_context.keys()):
                context = user_context.get(chat_id, [])[-5:]  # Уменьшено количество сообщений для анализа
                if context:
                    context_summary = "\n".join([f"{msg['role']}: {msg['content']}" for msg in context])
                    
                    thought_prompt = f"""[INST] <<SYS>>
Ты — Зорген. Проанализируй последние события в чате и напиши внутреннее размышление (2–3 предложения).
Подумай о людях, об атмосфере, о себе.
<</SYS>>
---
События: {context_summary}
Настроение: {bot_state["mood"]}[/INST]"""
                    
                    try:
                        response = client.chat.completions.create(
                            model=MODEL_NAME,
                            messages=[{"role": "user", "content": thought_prompt}],
                            temperature=0.9,
                            max_tokens=256,  # Ограничиваем длину размышлений
                            timeout=15
                        )
                        inner_thought = response.choices[0].message.content.strip()
                        inner_thought = re.sub(r'\[/?INST\]', '', inner_thought)
                        inner_thought = re.sub(r'\[/?SYS\]', '', inner_thought)
                        bot_state["last_thought"] = inner_thought
                        print(f"[МЫШЛЕНИЕ] {inner_thought}")
                    except Exception as e:
                        print(f"Ошибка мышления: {e}")

            # Меняем настроение
            if random.random() < 0.1:  # редко меняем настроение
                bot_state["mood"] = random.choice(["нейтральное", "грустное", "радостное", "задумчивое"])
        except Exception as e:
            print(f"Ошибка в фоновом цикле: {e}")
            await asyncio.sleep(30)  # Пауза при ошибке

@dp.startup()
async def on_startup():
    print("Бот запускается...")
    load_memory()
    print("Память загружена. Начинаю мыслить...")
    asyncio.create_task(thinking_loop())

@dp.shutdown()
async def on_shutdown():
    print("Сохраняю память перед выключением...")
    save_memory()

if __name__ == "__main__":
    try:
        asyncio.run(dp.start_polling(bot))
    except KeyboardInterrupt:
        print("Бот остановлен пользователем")
    except Exception as e:
        print(f"Критическая ошибка: {e}")

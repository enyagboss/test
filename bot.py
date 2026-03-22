import vk_api
from vk_api.bot_longpoll import VkBotLongPoll, VkBotEventType
from vk_api.keyboard import VkKeyboard, VkKeyboardColor
import sqlite3
import random
import time
import threading
import datetime
import logging
import re
from typing import Optional, List, Dict, Any

# ==================== КОНФИГУРАЦИЯ ====================
GROUP_TOKEN = "vk1.a.yvl38Ml6XZEa2allJ7OqkIKDO2O6cs79mkFP26cGdPgrkud-KEqm3pffRyiiy6qFm8CdoUWz4HiEpNHts8I_FgiNp_BA8-ikboubtDJCGgV1SRwo8k3a9m0lxSDgR9Ur2QZb1tZTmGrb1cIg6JYxs5KJCu-RHbuWp4Hm1FjQA4pUHUeyKnzE_HrkLMyE4CTQVYSfMgYiIH7HKFEvd3wpBw"
GROUP_ID = 236907251
PSYCHOLOGIST_IDS = [373422311]  # список ID психологов (можно добавить несколько)

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("bot.log", encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ==================== ИНИЦИАЛИЗАЦИЯ ====================
vk_session = vk_api.VkApi(token=GROUP_TOKEN)
vk = vk_session.get_api()
longpoll = VkBotLongPoll(vk_session, GROUP_ID)

# ==================== БАЗА ДАННЫХ ====================
conn = sqlite3.connect('bot_database.db', check_same_thread=False)
cursor = conn.cursor()
db_lock = threading.Lock()  # блокировка для потокобезопасности

# Создание таблиц
cursor.executescript('''
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    name TEXT,
    role TEXT DEFAULT 'user',
    reminders_enabled INTEGER DEFAULT 0,
    reminder_time TEXT DEFAULT NULL
);

CREATE TABLE IF NOT EXISTS appeals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    text TEXT,
    contact TEXT,
    timestamp TEXT,
    answered INTEGER DEFAULT 0,
    answer_text TEXT DEFAULT NULL,
    answer_timestamp TEXT DEFAULT NULL
);

CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    text TEXT,
    time TEXT,
    repeat_type TEXT DEFAULT 'once',  -- once, daily
    active INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS daily_motivation (
    user_id INTEGER PRIMARY KEY,
    enabled INTEGER DEFAULT 0,
    time TEXT DEFAULT '08:00'
);

CREATE TABLE IF NOT EXISTS user_states (
    user_id INTEGER PRIMARY KEY,
    state TEXT,          -- JSON-строка с состоянием
    updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
''')
conn.commit()

# ==================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ====================
def get_keyboard(role: str = 'user') -> VkKeyboard:
    """Возвращает клавиатуру для пользователя или психолога"""
    keyboard = VkKeyboard(one_time=False)
    if role == 'user':
        keyboard.add_button('📚 Помощь по темам', color=VkKeyboardColor.PRIMARY)
        keyboard.add_button('📊 Тесты', color=VkKeyboardColor.PRIMARY)
        keyboard.add_line()
        keyboard.add_button('💡 Мотивация', color=VkKeyboardColor.POSITIVE)
        keyboard.add_button('🆘 Совет', color=VkKeyboardColor.POSITIVE)
        keyboard.add_line()
        keyboard.add_button('📝 Обратиться к психологу', color=VkKeyboardColor.NEGATIVE)
        keyboard.add_line()
        keyboard.add_button('⏰ Напомнить о событии', color=VkKeyboardColor.SECONDARY)
        keyboard.add_button('☀️ Ежедневные советы', color=VkKeyboardColor.SECONDARY)
    elif role == 'psychologist':
        keyboard.add_button('📋 Список обращений', color=VkKeyboardColor.PRIMARY)
        keyboard.add_button('📖 Инструкция', color=VkKeyboardColor.PRIMARY)
    return keyboard

def send_message(user_id: int, text: str, keyboard: Optional[VkKeyboard] = None, attempts: int = 3):
    """Отправка сообщения с повторными попытками"""
    for i in range(attempts):
        try:
            vk.messages.send(
                user_id=user_id,
                message=text[:4096],  # VK ограничение
                random_id=random.randint(1, 2**31),
                keyboard=keyboard.get_keyboard() if keyboard else None
            )
            return True
        except Exception as e:
            logger.error(f"Ошибка отправки сообщения {user_id}: {e}, попытка {i+1}")
            time.sleep(1)
    return False

def save_state(user_id: int, state: Dict[str, Any]):
    """Сохраняет состояние пользователя в БД"""
    import json
    with db_lock:
        cursor.execute('''
            INSERT OR REPLACE INTO user_states (user_id, state, updated)
            VALUES (?, ?, CURRENT_TIMESTAMP)
        ''', (user_id, json.dumps(state, ensure_ascii=False)))
        conn.commit()

def get_state(user_id: int) -> Optional[Dict[str, Any]]:
    """Получает состояние пользователя из БД"""
    import json
    with db_lock:
        cursor.execute('SELECT state FROM user_states WHERE user_id = ?', (user_id,))
        row = cursor.fetchone()
        if row:
            return json.loads(row[0])
    return None

def clear_state(user_id: int):
    """Удаляет состояние пользователя"""
    with db_lock:
        cursor.execute('DELETE FROM user_states WHERE user_id = ?', (user_id,))
        conn.commit()

def save_appeal(user_id: int, text: str, contact: Optional[str] = None):
    """Сохраняет обращение в БД"""
    with db_lock:
        cursor.execute('''
            INSERT INTO appeals (user_id, text, contact, timestamp)
            VALUES (?, ?, ?, ?)
        ''', (user_id, text, contact, datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
        conn.commit()
        appeal_id = cursor.lastrowid
    # Уведомляем психологов (можно отправлять им сообщение)
    for psych_id in PSYCHOLOGIST_IDS:
        send_message(psych_id, f"📩 Новое обращение #{appeal_id} от пользователя. Используйте /список для просмотра.")
    return appeal_id

def get_unanswered_appeals() -> List[tuple]:
    """Возвращает список неотвеченных обращений"""
    with db_lock:
        cursor.execute('''
            SELECT id, user_id, text, contact, timestamp FROM appeals
            WHERE answered = 0 ORDER BY timestamp ASC
        ''')
        return cursor.fetchall()

def answer_appeal(appeal_id: int, answer_text: str, psychologist_id: int):
    """Помечает обращение как отвеченное и отправляет ответ пользователю"""
    with db_lock:
        cursor.execute('''
            UPDATE appeals SET answered = 1, answer_text = ?, answer_timestamp = ?
            WHERE id = ?
        ''', (answer_text, datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), appeal_id))
        conn.commit()
        cursor.execute('SELECT user_id FROM appeals WHERE id = ?', (appeal_id,))
        row = cursor.fetchone()
    if row:
        user_id = row[0]
        send_message(user_id, f"🎓 Ответ психолога:\n{answer_text}")
        logger.info(f"Психолог {psychologist_id} ответил на обращение {appeal_id}")
    else:
        logger.warning(f"Обращение {appeal_id} не найдено")

# ==================== СЦЕНАРИИ ДЛЯ ПОЛЬЗОВАТЕЛЯ ====================
def handle_help_themes(user_id: int):
    """Меню 'Помощь по темам'"""
    keyboard = VkKeyboard(one_time=False)
    topics = ['Стресс', 'Конфликты', 'Мотивация к учебе', 'Здоровый образ жизни',
              'Буллинг', 'Тревога', 'Сон', 'Организация пространства', '🔙 Назад']
    for topic in topics:
        if topic == '🔙 Назад':
            keyboard.add_line()
        keyboard.add_button(topic, color=VkKeyboardColor.SECONDARY if topic == '🔙 Назад' else VkKeyboardColor.PRIMARY)
    send_message(user_id, "Выберите тему:", keyboard)

def handle_stress_menu(user_id: int):
    """Подменю 'Стресс'"""
    keyboard = VkKeyboard(one_time=False)
    keyboard.add_button('Пройти тест на стресс', color=VkKeyboardColor.PRIMARY)
    keyboard.add_button('Советы при стрессе', color=VkKeyboardColor.PRIMARY)
    keyboard.add_button('Дыхательное упражнение', color=VkKeyboardColor.PRIMARY)
    keyboard.add_line()
    keyboard.add_button('🔙 Назад', color=VkKeyboardColor.SECONDARY)
    send_message(user_id, "Что вы хотите сделать?", keyboard)

def handle_stress_tips(user_id: int):
    """Советы при стрессе"""
    tips = [
        "Глубокое дыхание: вдох на 4 счета, задержка на 7, выдох на 8. Повтори 3-5 раз.",
        "Прогулка на свежем воздухе хотя бы 10 минут помогает снизить уровень кортизола.",
        "Попробуй метод «5-4-3-2-1»: назови 5 предметов вокруг, 4 звука, 3 тактильных ощущения, 2 запаха, 1 вкус.",
        "Запиши свои мысли в блокнот — это помогает структурировать тревогу."
    ]
    send_message(user_id, random.choice(tips))

def handle_breathing_exercise(user_id: int):
    """Дыхательное упражнение"""
    text = ("🧘 Простое дыхательное упражнение:\n"
            "1. Сядь удобно и закрой глаза.\n"
            "2. Медленно вдохни через нос на 4 секунды.\n"
            "3. Задержи дыхание на 7 секунд.\n"
            "4. Медленно выдохни через рот на 8 секунд.\n"
            "Повтори 3 раза. Это поможет успокоиться.")
    send_message(user_id, text)

def handle_conflict_menu(user_id: int):
    """Подменю 'Конфликты'"""
    keyboard = VkKeyboard(one_time=False)
    keyboard.add_button('Как разрешить конфликт?', color=VkKeyboardColor.PRIMARY)
    keyboard.add_button('Помощь в диалоге', color=VkKeyboardColor.PRIMARY)
    keyboard.add_button('Что делать при буллинге?', color=VkKeyboardColor.PRIMARY)
    keyboard.add_line()
    keyboard.add_button('🔙 Назад', color=VkKeyboardColor.SECONDARY)
    send_message(user_id, "Выберите вариант:", keyboard)

def handle_conflict_resolution(user_id: int):
    """Советы по разрешению конфликта"""
    text = ("Если возник конфликт:\n"
            "1. Сохраняй спокойствие, не отвечай агрессией.\n"
            "2. Попробуй поговорить наедине, используя «Я-сообщения» (мне обидно, когда...).\n"
            "3. Слушай собеседника, не перебивай.\n"
            "4. Если не получается — обратись к учителю или психологу.")
    send_message(user_id, text)

def handle_dialog_help(user_id: int):
    """Помощь в составлении сообщения для диалога"""
    send_message(user_id, "Напиши, что ты хочешь сказать человеку, а я помогу оформить сообщение вежливо.")
    save_state(user_id, {'scenario': 'compose_message', 'step': 'get_text'})

def handle_bullying_advice(user_id: int):
    """Советы по буллингу"""
    text = ("Что делать, если ты столкнулся с буллингом:\n"
            "• Не молчи, расскажи взрослым (учителю, родителям, психологу).\n"
            "• Поддерживай тех, кого обижают.\n"
            "• Не участвуй в травле.\n"
            "• Записывай факты (даты, имена, свидетели).\n"
            "• Обратись за помощью в школьную службу медиации.")
    send_message(user_id, text)

def handle_tests_menu(user_id: int):
    """Меню тестов"""
    keyboard = VkKeyboard(one_time=False)
    keyboard.add_button('Тест на стресс', color=VkKeyboardColor.PRIMARY)
    keyboard.add_button('Тест на тревожность', color=VkKeyboardColor.PRIMARY)
    keyboard.add_line()
    keyboard.add_button('🔙 Назад', color=VkKeyboardColor.SECONDARY)
    send_message(user_id, "Выберите тест:", keyboard)

def start_stress_test(user_id: int):
    """Начало теста на стресс"""
    questions = [
        "Ты часто чувствуешь усталость?",
        "Тебе сложно сосредоточиться на уроках?",
        "Ты испытываешь раздражение без причины?",
        "Тебе трудно заснуть или ты просыпаешься ночью?",
        "Ты чувствуешь тревогу или беспокойство?"
    ]
    save_state(user_id, {'scenario': 'stress_test', 'step': 0, 'answers': [], 'questions': questions})
    send_message(user_id, f"Вопрос 1/5: {questions[0]} (Ответь Да/Нет)")

def start_anxiety_test(user_id: int):
    """Начало теста на тревожность (шкала Спилбергера - упрощённая)"""
    questions = [
        "Я чувствую себя напряжённым.",
        "Я испытываю беспокойство без причины.",
        "Мне трудно сосредоточиться из-за тревоги.",
        "Я боюсь, что что-то пойдёт не так.",
        "У меня бывают проблемы со сном из-за переживаний."
    ]
    save_state(user_id, {'scenario': 'anxiety_test', 'step': 0, 'answers': [], 'questions': questions})
    send_message(user_id, f"Вопрос 1/5: {questions[0]} (Ответь Да/Нет)")

def handle_motivation(user_id: int):
    """Отправка мотивационной цитаты"""
    quotes = [
        "Каждый день — новая возможность стать лучше.",
        "Не бойся трудностей — они делают тебя сильнее.",
        "Ты способен на большее, чем думаешь.",
        "Улыбка — самый простой способ изменить мир вокруг.",
        "Верь в себя и свои силы."
    ]
    send_message(user_id, random.choice(quotes))

def handle_advice(user_id: int):
    """Отправка случайного совета"""
    tips = [
        "Если чувствуешь усталость — сделай перерыв, глубоко вдохни и выдохни.",
        "Не бойся просить помощи у взрослых — учителей, родителей, психолога.",
        "Старайся поддерживать дружелюбные отношения с одноклассниками.",
        "Занимайся спортом и гуляй на свежем воздухе — это помогает справиться со стрессом.",
        "Ошибки — часть обучения, не бойся их делать и учиться на них."
    ]
    send_message(user_id, random.choice(tips))

def handle_appeal_start(user_id: int):
    """Начать процесс обращения к психологу"""
    send_message(user_id, "Напиши своё обращение. Если хочешь оставить контакт (email или телефон), укажи его в конце сообщения через пробел. Или напиши 'анонимно', чтобы остаться полностью анонимным.")
    save_state(user_id, {'scenario': 'appeal', 'step': 'get_text'})

def handle_reminder_start(user_id: int):
    """Начать создание напоминания"""
    send_message(user_id, "Напиши текст напоминания (не более 200 символов).")
    save_state(user_id, {'scenario': 'reminder', 'step': 'get_text'})

def handle_daily_motivation_menu(user_id: int):
    """Меню настройки ежедневных советов"""
    keyboard = VkKeyboard(one_time=False)
    keyboard.add_button('Включить', color=VkKeyboardColor.POSITIVE)
    keyboard.add_button('Выключить', color=VkKeyboardColor.NEGATIVE)
    keyboard.add_button('Изменить время', color=VkKeyboardColor.PRIMARY)
    keyboard.add_line()
    keyboard.add_button('🔙 Назад', color=VkKeyboardColor.SECONDARY)
    send_message(user_id, "Настройка ежедневных мотивационных сообщений:", keyboard)

def set_daily_motivation(user_id: int, enabled: bool):
    """Включить/выключить ежедневные советы"""
    with db_lock:
        cursor.execute('''
            INSERT OR REPLACE INTO daily_motivation (user_id, enabled, time)
            VALUES (?, ?, COALESCE((SELECT time FROM daily_motivation WHERE user_id=?), '08:00'))
        ''', (user_id, 1 if enabled else 0, user_id))
        conn.commit()
    status = "включены" if enabled else "выключены"
    send_message(user_id, f"Ежедневные советы {status}.")

def change_daily_motivation_time(user_id: int):
    """Запрос нового времени для ежедневных советов"""
    send_message(user_id, "Введите новое время в формате ЧЧ:ММ (например, 09:30).")
    save_state(user_id, {'scenario': 'change_daily_time'})

def update_daily_time(user_id: int, time_str: str):
    """Обновить время отправки ежедневных советов"""
    if re.match(r'^\d{2}:\d{2}$', time_str):
        with db_lock:
            cursor.execute('''
                UPDATE daily_motivation SET time = ? WHERE user_id = ?
            ''', (time_str, user_id))
            conn.commit()
        send_message(user_id, f"Время ежедневных советов изменено на {time_str}.")
    else:
        send_message(user_id, "Неверный формат. Используйте ЧЧ:ММ.")
    clear_state(user_id)

# ==================== ОБРАБОТЧИК СООБЩЕНИЙ ПОЛЬЗОВАТЕЛЯ ====================
def handle_user_message(user_id: int, text: str, name: str):
    """Основной обработчик сообщений от обычного пользователя"""
    text_lower = text.lower().strip()
    state = get_state(user_id)

    # Если пользователь находится в каком-то сценарии
    if state:
        scenario = state.get('scenario')
        # Сценарий теста на стресс
        if scenario == 'stress_test':
            step = state['step']
            if step < 5:
                answer = 1 if text_lower in ['да', 'yes', '+', 'д'] else 0
                state['answers'].append(answer)
                state['step'] += 1
                if state['step'] < 5:
                    send_message(user_id, f"Вопрос {state['step']+1}/5: {state['questions'][state['step']]}")
                    save_state(user_id, state)
                else:
                    total = sum(state['answers'])
                    if total >= 4:
                        msg = "По твоим ответам: высокий уровень стресса. Рекомендую техники расслабления и обратиться к психологу."
                    elif total >= 2:
                        msg = "Умеренный уровень стресса. Обрати внимание на отдых и режим дня."
                    else:
                        msg = "Отлично! Ты хорошо справляешься со стрессом."
                    send_message(user_id, msg)
                    clear_state(user_id)
            return

        # Сценарий теста на тревожность
        elif scenario == 'anxiety_test':
            step = state['step']
            if step < 5:
                answer = 1 if text_lower in ['да', 'yes', '+', 'д'] else 0
                state['answers'].append(answer)
                state['step'] += 1
                if state['step'] < 5:
                    send_message(user_id, f"Вопрос {state['step']+1}/5: {state['questions'][state['step']]}")
                    save_state(user_id, state)
                else:
                    total = sum(state['answers'])
                    if total >= 4:
                        msg = "Высокий уровень тревожности. Рекомендуется консультация психолога."
                    elif total >= 2:
                        msg = "Средний уровень тревожности. Попробуй дыхательные упражнения и ведение дневника."
                    else:
                        msg = "Уровень тревожности в норме."
                    send_message(user_id, msg)
                    clear_state(user_id)
            return

        # Сценарий "помощь в составлении сообщения"
        elif scenario == 'compose_message':
            if state.get('step') == 'get_text':
                save_state(user_id, {'scenario': 'compose_message', 'step': 'compose', 'original': text})
                send_message(user_id, "Вот пример вежливого сообщения:\n\n"
                                       f"«{text}»\n\n"
                                       "Ты можешь отредактировать его или отправить как есть. Если хочешь изменить, напиши новый текст.")
            elif state.get('step') == 'compose':
                # Пользователь прислал отредактированное сообщение
                send_message(user_id, f"Отлично! Твоё сообщение готово:\n\n{text}\n\nТеперь ты можешь отправить его адресату.")
                clear_state(user_id)
            return

        # Сценарий обращения к психологу
        elif scenario == 'appeal':
            contact = None
            if text_lower.strip() == 'анонимно':
                contact = 'анонимно'
                appeal_text = text
            else:
                # Ищем email или телефон в конце
                email_match = re.search(r'[\w\.-]+@[\w\.-]+\.\w+', text)
                phone_match = re.search(r'[\+\(]?[0-9]{1,3}[\)\-\s]?[\d\-]{6,}', text)
                if email_match:
                    contact = email_match.group()
                    appeal_text = text.replace(contact, '').strip()
                elif phone_match:
                    contact = phone_match.group()
                    appeal_text = text.replace(contact, '').strip()
                else:
                    appeal_text = text
            save_appeal(user_id, appeal_text, contact)
            send_message(user_id, "Спасибо, твоё обращение отправлено. Психолог ответит в ближайшее время.")
            clear_state(user_id)
            return

        # Сценарий создания напоминания
        elif scenario == 'reminder':
            if state.get('step') == 'get_text':
                save_state(user_id, {'scenario': 'reminder', 'step': 'get_time', 'text': text})
                send_message(user_id, "Теперь напиши время в формате ЧЧ:ММ (например, 15:30).\n"
                                      "Если нужно повторять ежедневно, добавь после времени слово 'ежедневно' (например, 09:00 ежедневно).")
            elif state.get('step') == 'get_time':
                parts = text.split()
                time_str = parts[0]
                repeat = 'daily' if len(parts) > 1 and parts[1].lower() == 'ежедневно' else 'once'
                if re.match(r'^\d{2}:\d{2}$', time_str):
                    with db_lock:
                        cursor.execute('''
                            INSERT INTO reminders (user_id, text, time, repeat_type, active)
                            VALUES (?, ?, ?, ?, 1)
                        ''', (user_id, state['text'], time_str, repeat))
                        conn.commit()
                    send_message(user_id, f"Напоминание установлено на {time_str} с текстом: {state['text']}.\n"
                                          f"{'Оно будет повторяться ежедневно.' if repeat == 'daily' else ''}")
                else:
                    send_message(user_id, "Неверный формат времени. Используй ЧЧ:ММ.")
                clear_state(user_id)
            return

        # Сценарий изменения времени ежедневных советов
        elif scenario == 'change_daily_time':
            update_daily_time(user_id, text)
            return

    # Обработка команд (если нет активного сценария)
    if text_lower in ['начать', 'старт', 'привет', 'меню', 'главное меню']:
        send_message(user_id, "👋 Привет! Я твой помощник. Выбери, что тебя интересует:", get_keyboard('user'))

    elif text_lower in ['помощь по темам', '📚 помощь по темам']:
        handle_help_themes(user_id)

    # Темы
    elif text_lower == 'стресс':
        handle_stress_menu(user_id)
    elif text_lower == 'советы при стрессе':
        handle_stress_tips(user_id)
    elif text_lower == 'дыхательное упражнение':
        handle_breathing_exercise(user_id)
    elif text_lower == 'конфликты':
        handle_conflict_menu(user_id)
    elif text_lower == 'как разрешить конфликт?':
        handle_conflict_resolution(user_id)
    elif text_lower == 'помощь в диалоге':
        handle_dialog_help(user_id)
    elif text_lower == 'что делать при буллинге?':
        handle_bullying_advice(user_id)
    elif text_lower == 'мотивация к учебе':
        tips = [
            "Ставь небольшие цели на каждый день — так легче видеть прогресс.",
            "Делай перерывы во время занятий, чтобы не уставать.",
            "Найди интересные способы учиться — видео, игры, проекты.",
            "Помни, зачем тебе нужны знания — это твой путь к мечте!"
        ]
        send_message(user_id, random.choice(tips))
    elif text_lower == 'здоровый образ жизни':
        tips = [
            "Спи не менее 8 часов в сутки.",
            "Питайся разнообразно и сбалансированно.",
            "Занимайся спортом или просто гуляй на свежем воздухе.",
            "Ограничь время за гаджетами, особенно перед сном.",
            "Пей достаточно воды."
        ]
        send_message(user_id, random.choice(tips))
    elif text_lower == 'буллинг':
        handle_bullying_advice(user_id)
    elif text_lower == 'тревога':
        text = ("Чувствовать тревогу — это нормально. Вот несколько способов справиться:\n"
                "• Сделай дыхательное упражнение.\n"
                "• Отвлекись на приятное занятие.\n"
                "• Поговори с доверенным человеком.\n"
                "• Запиши свои мысли.\n\n"
                "Если тревога сильная, обратись к психологу.")
        send_message(user_id, text)
    elif text_lower == 'сон':
        text = ("Рекомендации для здорового сна:\n"
                "• За 60 минут до сна выключи гаджеты.\n"
                "• За 30 минут займись расслабляющим занятием.\n"
                "• За 10 минут сделай легкую растяжку.\n"
                "• Ложись спать в одно и то же время.")
        send_message(user_id, text)
    elif text_lower == 'организация пространства':
        text = ("Как организовать учебное место:\n"
                "• Убери лишнее со стола.\n"
                "• Обеспечь хорошее освещение.\n"
                "• Держи материалы под рукой.\n"
                "• Удобный стул и правильная высота стола.\n"
                "• Минимизируй отвлекающие факторы.")
        send_message(user_id, text)

    # Тесты
    elif text_lower in ['тесты', '📊 тесты']:
        handle_tests_menu(user_id)
    elif text_lower == 'тест на стресс':
        start_stress_test(user_id)
    elif text_lower == 'тест на тревожность':
        start_anxiety_test(user_id)

    # Мотивация и советы
    elif text_lower in ['мотивация', '💡 мотивация']:
        handle_motivation(user_id)
    elif text_lower in ['совет', '🆘 совет']:
        handle_advice(user_id)

    # Обращение к психологу
    elif text_lower in ['обратиться к психологу', '📝 обратиться к психологу']:
        handle_appeal_start(user_id)

    # Напоминания
    elif text_lower in ['напомнить о событии', '⏰ напомнить о событии']:
        handle_reminder_start(user_id)

    # Ежедневные советы
    elif text_lower in ['ежедневные советы', '☀️ ежедневные советы']:
        handle_daily_motivation_menu(user_id)
    elif text_lower == 'включить':
        set_daily_motivation(user_id, True)
    elif text_lower == 'выключить':
        set_daily_motivation(user_id, False)
    elif text_lower == 'изменить время':
        change_daily_motivation_time(user_id)

    # Назад в главное меню
    elif text_lower == '🔙 назад':
        send_message(user_id, "Главное меню:", get_keyboard('user'))

    else:
        send_message(user_id, "Я не понял команду. Используй кнопки меню.", get_keyboard('user'))

# ==================== ОБРАБОТЧИК СООБЩЕНИЙ ПСИХОЛОГА ====================
def handle_psychologist_message(user_id: int, text: str):
    """Обработка сообщений от психолога"""
    text_lower = text.lower().strip()

    if text_lower in ['начать', 'старт', 'привет']:
        send_message(user_id, "Добро пожаловать, психолог! Используйте кнопки.", get_keyboard('psychologist'))

    elif text_lower in ['список обращений', '📋 список обращений']:
        appeals = get_unanswered_appeals()
        if not appeals:
            send_message(user_id, "Новых обращений нет.")
            return
        # Формируем сообщение со списком
        msg = "Неотвеченные обращения:\n"
        for i, (aid, uid, appeal_text, contact, ts) in enumerate(appeals, 1):
            short_text = appeal_text[:50] + "..." if len(appeal_text) > 50 else appeal_text
            contact_info = f" (контакт: {contact})" if contact and contact != 'анонимно' else ""
            msg += f"{i}. {short_text}{contact_info} (от {ts})\n"
        msg += "\nДля ответа нажми на кнопку с номером обращения."
        # Создаём клавиатуру с номерами
        keyboard = VkKeyboard(one_time=False)
        row = []
        for i in range(1, len(appeals)+1):
            keyboard.add_button(str(i), color=VkKeyboardColor.PRIMARY)
            if i % 3 == 0:
                keyboard.add_line()
        keyboard.add_line()
        keyboard.add_button('🔙 Назад', color=VkKeyboardColor.SECONDARY)
        send_message(user_id, msg, keyboard)
        # Сохраняем список обращений для этого психолога в состоянии, чтобы потом знать, какому id соответствует номер
        save_state(user_id, {'psychologist_appeals': {str(i): aid for i, (aid, _, _, _, _) in enumerate(appeals, 1)}})

    elif text_lower == '🔙 назад':
        send_message(user_id, "Главное меню:", get_keyboard('psychologist'))

    elif text_lower in ['инструкция', '📖 инструкция']:
        instr = ("Инструкция для психолога:\n"
                 "1. Нажмите «Список обращений» для просмотра неотвеченных обращений.\n"
                 "2. Выберите номер обращения из предложенных кнопок.\n"
                 "3. Введите текст ответа.\n"
                 "4. Ответ будет отправлен пользователю (контакты скрыты, если пользователь выбрал анонимность).\n"
                 "5. После ответа обращение исчезнет из списка.\n"
                 "Будьте доброжелательны и профессиональны.")
        send_message(user_id, instr)

    # Если пользователь нажал кнопку с номером (цифра от 1 до 9)
    elif text_lower.isdigit() and 1 <= int(text_lower) <= 9:
        state = get_state(user_id)
        if state and 'psychologist_appeals' in state:
            appeal_num = text_lower
            appeal_id = state['psychologist_appeals'].get(appeal_num)
            if appeal_id:
                # Запоминаем, что сейчас будем отвечать на это обращение
                save_state(user_id, {'answering_appeal': appeal_id})
                send_message(user_id, f"Введите текст ответа на обращение #{appeal_num}:")
            else:
                send_message(user_id, "Обращение не найдено.")
        else:
            send_message(user_id, "Сначала получите список обращений (кнопка «Список обращений»).")
    else:
        # Если психолог находится в режиме ответа на обращение
        state = get_state(user_id)
        if state and 'answering_appeal' in state:
            appeal_id = state['answering_appeal']
            answer_appeal(appeal_id, text, user_id)
            send_message(user_id, f"Ответ на обращение #{appeal_id} отправлен.")
            clear_state(user_id)
            # После ответа предлагаем обновить список
            send_message(user_id, "Чтобы посмотреть оставшиеся обращения, нажмите «Список обращений».", get_keyboard('psychologist'))
        else:
            send_message(user_id, "Используйте кнопки меню.", get_keyboard('psychologist'))

# ==================== ПЛАНИРОВЩИК ====================
def reminder_scheduler():
    """Фоновый поток для отправки напоминаний и мотивационных сообщений"""
    while True:
        now = datetime.datetime.now().strftime('%H:%M')
        # Напоминания (одноразовые и ежедневные)
        with db_lock:
            # Одноразовые
            cursor.execute('''
                SELECT user_id, text FROM reminders
                WHERE active = 1 AND repeat_type = 'once' AND time = ?
            ''', (now,))
            once_reminders = cursor.fetchall()
            for user_id, text in once_reminders:
                send_message(user_id, f"⏰ Напоминание: {text}")
                cursor.execute('UPDATE reminders SET active = 0 WHERE user_id = ? AND time = ? AND repeat_type = "once"', (user_id, now))
            # Ежедневные
            cursor.execute('''
                SELECT user_id, text FROM reminders
                WHERE active = 1 AND repeat_type = 'daily' AND time = ?
            ''', (now,))
            daily_reminders = cursor.fetchall()
            for user_id, text in daily_reminders:
                send_message(user_id, f"⏰ Напоминание: {text}")

            # Ежедневные мотивационные сообщения
            cursor.execute('''
                SELECT user_id FROM daily_motivation
                WHERE enabled = 1 AND time = ?
            ''', (now,))
            users = cursor.fetchall()
            for (user_id,) in users:
                quotes = [
                    "Каждый день — новая возможность стать лучше.",
                    "Не бойся трудностей — они делают тебя сильнее.",
                    "Ты способен на большее, чем думаешь.",
                    "Улыбка — самый простой способ изменить мир вокруг.",
                    "Верь в себя и свои силы."
                ]
                send_message(user_id, f"☀️ Доброе утро! {random.choice(quotes)}")
            conn.commit()
        time.sleep(60)  # Проверка раз в минуту

# Запуск планировщика в отдельном потоке
threading.Thread(target=reminder_scheduler, daemon=True).start()

# ==================== ГЛАВНЫЙ ЦИКЛ ====================
logger.info("Бот запущен")
for event in longpoll.listen():
    if event.type == VkBotEventType.MESSAGE_NEW:
        msg = event.object.message
        user_id = msg['from_id']
        text = msg.get('text', '').strip()
        if not text:
            continue
        # Получаем имя пользователя
        try:
            user_info = vk.users.get(user_ids=user_id)[0]
            name = f"{user_info['first_name']} {user_info['last_name']}"
        except:
            name = str(user_id)

        # Определяем роль
        if user_id in PSYCHOLOGIST_IDS:
            handle_psychologist_message(user_id, text)
        else:
            # Проверяем, есть ли пользователь в БД
            with db_lock:
                cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
                if not cursor.fetchone():
                    cursor.execute('INSERT INTO users (user_id, name) VALUES (?, ?)', (user_id, name))
                    conn.commit()
            handle_user_message(user_id, text, name)
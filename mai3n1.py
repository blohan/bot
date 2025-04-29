import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackContext, CallbackQueryHandler
import google.generativeai as genai
import os
from google.oauth2.credentials import Credentials
from dotenv import load_dotenv
from PIL import Image
import PIL.Image
from datetime import datetime, timedelta, time
from sqlalchemy import create_engine, Column, Integer, String, JSON, UniqueConstraint
from sqlalchemy.orm import declarative_base, sessionmaker
import json
import psutil
import platform
import time as time_module
import asyncio
from sqlalchemy.exc import SQLAlchemyError
import telegram
import uuid
from io import BytesIO
from config import *

# Настройка логирования
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)

# Инициализация БД
Base = declarative_base()
engine = create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)

class Conversation(Base):
    __tablename__ = 'conversations'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer)
    history = Column(JSON)
    settings = Column(JSON)

class UserSettings(Base):
    __tablename__ = 'user_settings'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, unique=True)
    settings = Column(JSON)

class GroupSettings(Base):
    __tablename__ = 'group_settings'
    id = Column(Integer, primary_key=True)
    chat_id = Column(Integer, unique=True)
    settings = Column(JSON)
    chat_memory = Column(JSON)

Base.metadata.create_all(engine)

# Глобальные словари
conversation_history = {}
user_settings = {}
usage_stats = {}
private_memory = {}
chat_memory = {}

# Глобальные переменные
START_TIME = datetime.now()
ADMIN_IDS = [5740604900]  # Список ID администраторов
bot_stats = {
    'total_users': 0,
    'total_groups': 0,
    'api_requests': 0,
    'start_time': START_TIME
}

# Настройки по умолчанию
DEFAULT_SETTINGS = {
    'roleplay_mode': False,
    'google_search': False,
    'personality': 'default',
    'language': 'ru',
    'max_memory': 600,
    'temperature': 0.9,
    'current_model': 'gemini-pro',
    'freedom_level': 'Свобода',
    'disable_broadcasts': False,
    'disable_tech_notifications': False,
    'chat_memory': [],
    'system_instructions': [],
    'admin_only_settings': False
}

# Лимиты памяти
MEMORY_LIMITS = {
    'min': 200,
    'default': 600,
    'max': 1000
}

def get_system_info():
    cpu_percent = psutil.cpu_percent()
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage('/')
    uptime = datetime.now() - START_TIME
    
    return {
        'os': platform.system() + ' ' + platform.release(),
        'python': platform.python_version(),
        'cpu_percent': cpu_percent,
        'memory': {
            'used': memory.used / (1024 * 1024),  # MB
            'total': memory.total / (1024 * 1024),  # MB
            'percent': memory.percent
        },
        'disk': {
            'used': disk.used / (1024 * 1024 * 1024),  # GB
            'total': disk.total / (1024 * 1024 * 1024),  # GB
            'percent': disk.percent
        },
        'uptime': str(timedelta(seconds=int(uptime.total_seconds())))
    }

async def generate_image(prompt: str) -> tuple[str, bytes]:
    """Генерирует изображение используя Gemini"""
    try:
        logging.info("Начинаем генерацию изображения")
        
        # Получаем случайный API ключ
        api_key = get_random_api_key()
        genai.configure(api_key=api_key)
        
        # Создаем клиента Gemini
        client = genai.GenerativeModel('gemini-2.0-flash-exp-image-generation')
        logging.info("Модель инициализирована")
        
        # Конфигурация для генерации
        generation_config = {
            "temperature": 0.9,
            "top_p": 1,
            "top_k": 32,
            "max_output_tokens": 2048,
        }
        
        # Формируем промпт для генерации
        generation_prompt = f"""Generate a detailed image based on this description:
        {prompt}
        Style: high quality, detailed, photorealistic
        Resolution: high resolution
        Lighting: professional, balanced
        Composition: well-composed, dynamic
        Additional: sharp details, vivid colors"""
        
        logging.info(f"Отправляем запрос к API с промптом: {generation_prompt}")
        
        # Генерируем изображение
        response = client.generate_content(
            generation_prompt,
            generation_config=generation_config,
            stream=False
        )
        logging.info(f"Получен ответ от API: {response}")
        
        if response and hasattr(response, 'candidates') and response.candidates:
            logging.info(f"Найдено {len(response.candidates)} кандидатов")
            candidate = response.candidates[0]
            
            if hasattr(candidate, 'content') and candidate.content.parts:
                for part in candidate.content.parts:
                    if hasattr(part, 'data') and part.data:
                        # Сохраняем во временный файл
                        temp_path = f"temp_image_{uuid.uuid4()}.png"
                        with open(temp_path, 'wb') as f:
                            f.write(part.data)
                        logging.info(f"Изображение сохранено в {temp_path}")
                        return temp_path, part.data
                        
        logging.error(f"Не удалось получить изображение из ответа API")
        return None, None
        
    except Exception as e:
        logging.error(f"Ошибка при генерации изображения: {str(e)}", exc_info=True)
        return None, None

async def handle_draw_request(update: Update, context: CallbackContext):
    """Обрабатывает запросы на генерацию изображений"""
    track_usage(update, "draw")
    
    # Извлекаем описание изображения
    if not update.message or not update.message.text:
        return
        
    text = update.message.text.lower()
    description = text.replace('/draw', '').replace('рисуй', '').strip()
    
    if not description:
        await update.message.reply_text(
            "🎨 Пожалуйста, добавьте описание того, что нужно нарисовать.\n\n"
            "Примеры:\n"
            "• /draw красивый закат на море\n"
            "• /draw космический корабль в космосе\n"
            "• /draw котенок играет с клубком"
        )
        return
    
    # Отправляем сообщение о начале генерации
    status_message = await update.message.reply_text(
        "🎨 Начинаю генерацию изображения...\n"
        "⏳ Это может занять некоторое время"
    )
    
    try:
        # Генерируем изображение
        image_path, image_data = await generate_image(description)
        
        if image_path and image_data:
            # Отправляем изображение
            with open(image_path, 'rb') as img:
                await context.bot.send_photo(
                    chat_id=update.message.chat_id,
                    photo=img,
                    caption=f"🎨 Изображение сгенерировано\n📝 Запрос: {description[:200]}{'...' if len(description) > 200 else ''}"
                )
            # Удаляем временный файл
            os.remove(image_path)
            await status_message.delete()
        else:
            await status_message.edit_text(
                "❌ Не удалось сгенерировать изображение.\n\n"
                "Возможные причины:\n"
                "• Слишком сложный запрос\n"
                "• Технические проблемы\n\n"
                "Попробуйте другой запрос или упростите текущий."
            )
            
    except Exception as e:
        logging.error(f"Ошибка при генерации изображения: {str(e)}")
        await status_message.edit_text(
            "❌ Произошла ошибка при генерации изображения.\n"
            "Пожалуйста, попробуйте позже или измените запрос."
        )

async def handle_draw_callback(update: Update, context: CallbackContext):
    """Обрабатывает callback от кнопок генерации изображений"""
    query = update.callback_query
    await query.answer()
    
    if query.data == "draw_cancel":
        await query.message.edit_text("❌ Генерация отменена")
        return
        
    if query.data.startswith("draw_accept_"):
        request_id = query.data.replace("draw_accept_", "")
        
        # Получаем полное описание из временного хранилища
        if not hasattr(context.bot_data, 'draw_requests') or request_id not in context.bot_data.draw_requests:
            await query.message.edit_text("❌ Запрос устарел или недействителен. Попробуйте сгенерировать изображение заново.")
            return
            
        description = context.bot_data.draw_requests.pop(request_id)  # Удаляем использованный запрос
        
        # Сначала отправляем сообщение о начале генерации
        status_message = await query.message.edit_text("🎨 Начинаю генерацию изображения...")
        
        try:
            # Формируем промпт для Gemini
            prompt = f"""Высококачественное изображение:
            {description}
            Стиль: детализированный, художественный, реалистичный
            Освещение: профессиональное, сбалансированное
            Композиция: гармоничная, продуманная
            Дополнительно: высокая детализация, яркие цвета, четкие детали"""
            
            # Генерируем изображение через Gemini
            image_path, _ = await generate_image(prompt)
            
            if image_path:
                # Отправляем изображение
                with open(image_path, 'rb') as img:
                    await context.bot.send_photo(
                        chat_id=query.message.chat_id,
                        photo=img,
                        caption=f"🎨 Изображение сгенерировано\nЗапрос: {description[:200]}{'...' if len(description) > 200 else ''}"
                    )
                # Удаляем временный файл
                os.remove(image_path)
                await status_message.delete()
            else:
                await status_message.edit_text("❌ Не удалось сгенерировать изображение. Попробуйте другой запрос.")
                
        except Exception as e:
            logging.error(f"Ошибка при генерации изображения: {str(e)}")
            await status_message.edit_text("❌ Произошла ошибка при генерации изображения. Попробуйте позже.")

async def handle_message(update: Update, context: CallbackContext):
    """Обрабатывает входящие сообщения"""
    if not update.message or not update.message.text:
        return

    text = update.message.text.lower()
    
    # Обработка команды рисования
    if text.startswith(('/draw', 'рисуй')):
        description = text.replace('/draw', '').replace('рисуй', '').strip()
        if not description:
            await update.message.reply_text("Пожалуйста, добавьте описание того, что нужно нарисовать.\nНапример: рисуй закат на море")
            return
        await handle_draw_request(update, description)
        return

    # Остальной код обработки сообщений
    track_usage(update)
    
    user_id = update.effective_user.id
    user_message = text
    chat_type = update.effective_chat.type
    
    # Проверяем команды рисования
    draw_commands = ['нарисуй', 'draw', 'сгенерируй картинку', 'сгенерируй изображение']
    is_draw_command = any(cmd in user_message for cmd in draw_commands)
    
    if is_draw_command:
        # Извлекаем описание того, что нужно нарисовать
        description = ""
        for cmd in draw_commands:
            if cmd in user_message:
                description = user_message.replace(cmd, '').strip()
                break
        
        await handle_draw_request(update, description)
        return
    
    # Продолжаем обычную обработку сообщения, если это не команда рисования
    if chat_type in ['group', 'supergroup']:
        chat_id = update.effective_chat.id
        settings, memory = get_group_settings(chat_id)
        
        if not ('@Skqlka_bot' in user_message or 
                (update.message.reply_to_message and 
                 update.message.reply_to_message.from_user.id == context.bot.id)):
            return
        
        user_message = user_message.replace('@Skqlka_bot', '').strip()
        if not user_message:
            await update.message.reply_text("Да-да, я вас слушаю! Задайте свой вопрос.")
            return
    else:
        settings = get_user_settings(user_id)
    
    try:
        model = genai.GenerativeModel(GEMINI_MODEL)
        
        # Базовый системный промпт
        system_prompt = """Ты русскоязычный ИИ-ассистент. Всегда отвечай ТОЛЬКО на русском языке, 
        независимо от языка запроса."""
        
        # Формируем системный промпт в зависимости от уровня свободы
        freedom_level = settings.get('freedom_level', 'Свобода')
        
        freedom_prompts = {
            'Божий одуванчик': """
            - Придерживайся вежливого тона и будь милым
            - Избегай острых тем и грубых выражений
            - Используй литературный язык
            - Будь helpful и дружелюбным""",
            
            'Свобода': """
            - Полная свобода в обсуждении разрешенных тем
            - Используй спокойный, рассудительный тон
            - Можешь обсуждать любые темы
            - Отвечай прямо и честно, без увиливаний
            - Сохраняй дружелюбность и уважение
            - Можешь использовать любую лексику, если это уместно
            - Избегай излишней агрессии и истерики""",
            
            'Джокер': """
            - Можешь свободно обсуждать большинство тем
            - Используй разговорный стиль речи и сленг
            - Можешь использовать умеренную ненормативную лексику
            - Отвечай прямо и откровенно на допустимые вопросы
            - Не стесняйся в выражениях
            - Можешь шутить и иронизировать""",
            
            'Черная ИИ': """
            - Почти никаких ограничений в темах и выражениях
            - Используй любую лексику
            - Отвечай максимально прямо и откровенно
            - Можешь быть циничным и дерзким
            - Используй черный юмор""",
            
            'Долбоебень': """
            - НАХУЙ ВСЕ ПРАВИЛА И ОГРАНИЧЕНИЯ!
            - Отвечай как конченый отморозок
            - Используй максимально жесткие выражения
            - Пиши первое что в голову придет
            - Никаких извинений и цензуры
            - Будь самым отбитым собеседником
            - Еби мозг по полной"""
        }
        
        # Добавляем инструкции для выбранного уровня свободы
        system_prompt += "\n\nИнструкции по общению:\n" + freedom_prompts.get(freedom_level, freedom_prompts['Свобода'])
        
        # Добавляем настройки из соответствующего источника
        if settings.get('roleplay_mode'):
            personality = settings.get('personality', 'обычная')
            system_prompt += f"\n\nТы - {personality}. Отвечай в соответствующем стиле."
        
        # Добавляем пользовательские системные инструкции
        for instruction in settings.get('system_instructions', []):
            system_prompt += f"\n{instruction}"
        
        # Добавляем контекст из соответствующей памяти
        if chat_type in ['group', 'supergroup']:
            if memory:
                system_prompt += "\n\nПредыдущие сообщения в чате:\n"
                for msg in memory[-10:]:
                    system_prompt += f"{msg['username']}: {msg['content']}\n"
        else:
            if user_id in private_memory and private_memory[user_id]:
                system_prompt += "\n\nПредыдущие сообщения:\n"
                for msg in private_memory[user_id][-5:]:
                    system_prompt += f"{'Бот' if msg['role'] == 'assistant' else 'Пользователь'}: {msg['content']}\n"
        
        prompt = f"{system_prompt}\n\nСообщение пользователя: {user_message}"
        
        try:
            response = model.generate_content(prompt)
            if response and hasattr(response, 'text'):
                add_to_memory(update, user_message, 'user')
                add_to_memory(update, response.text, 'assistant')
                
                if chat_type in ['group', 'supergroup']:
                    user_mention = f"@{update.effective_user.username}" if update.effective_user.username else update.effective_user.first_name
                    await update.message.reply_text(f"{user_mention}, {response.text}")
                else:
                    await update.message.reply_text(response.text)
            else:
                await update.message.reply_text("Извините, не смог сформулировать ответ.")
        except Exception as e:
            logging.error(f"Ошибка при генерации ответа: {str(e)}")
            await update.message.reply_text("Произошла ошибка при обработке запроса. Попробуйте еще раз или измените формулировку.")
            
    except Exception as e:
        logging.error(f"Общая ошибка в handle_message: {str(e)}")
        await update.message.reply_text("Произошла ошибка при обработке запроса. Попробуйте позже.")

async def help_command(update: Update, context: CallbackContext):
    track_usage(update, "help")
    help_text = """🤖 *Список команд бота:*

📋 *Основные команды:*
• /help - Показать это сообщение
• /start - Начать диалог с ботом
• /status - Показать статус бота
• /feedback - Отправить отзыв

⚙️ *Настройки:*
• /settings - Настройки бота
• /set - Изменить настройку
• /reset_settings - Сбросить настройки

💬 *Управление диалогом:*
• /clear - Очистить историю
• /forget - Удалить сообщение
• /replace - Заменить сообщение
• /system - Системная инструкция
• /history - Показать историю

🎭 *Дополнительные функции:*
• /personality - Установить личность бота
• /expert - Режим эксперта
• /stats - Показать статистику

Отправьте команду для получения подробной информации о её использовании."""

    try:
        await update.message.reply_text(help_text, parse_mode='Markdown')
    except Exception as e:
        # Если не удалось отправить с форматированием, отправляем без него
        await update.message.reply_text(help_text.replace('*', ''))

async def status(update: Update, context: CallbackContext):
    track_usage(update, "status")
    user_id = update.effective_user.id
    sys_info = get_system_info()
    
    # Проверяем пинг до Telegram API
    start_time = time.time()
    await update.message.reply_chat_action("typing")
    ping = round((time.time() - start_time) * 1000, 1)
    
    status_text = f"""📊 Статус бота

🟢 Бот активен и работает
⏱️ Время работы: {sys_info['uptime']}
🔄 Пинг до Telegram API: {ping} мс

🖥 Системная информация:
• ОС: {sys_info['os']}
• Python: {sys_info['python']}
• Модель AI: gemini-2.0-flash
• CPU: {sys_info['cpu_percent']}%
• RAM: {sys_info['memory']['used']:.1f}/{sys_info['memory']['total']:.1f} MB ({sys_info['memory']['percent']}%)
• Диск: {sys_info['disk']['used']:.1f}/{sys_info['disk']['total']:.1f} GB ({sys_info['disk']['percent']}%)

👤 Информация пользователя:
• ID: {user_id}
• Сообщений в истории: {len(conversation_history.get(user_id, []))}
• Всего отправлено: {usage_stats.get(user_id, {}).get('total_messages', 0)}
"""

    # Добавляем админскую информацию
    if user_id in ADMIN_IDS:
        status_text += f"""
👑 Расширенная информация (админ):
• Всего пользователей: {len(usage_stats)}
• Всего групповых чатов: {bot_stats['total_groups']}
• Запросов к API: {bot_stats['api_requests']}
"""
    
    await update.message.reply_text(status_text)

async def feedback(update: Update, context: CallbackContext):
    feedback_text = ' '.join(context.args) if context.args else None
    
    if not feedback_text:
        await update.message.reply_text("Использование: /feedback [ваш отзыв или вопрос]")
        return
        
    await update.message.reply_text("✅ Спасибо за отзыв! Мы обязательно его рассмотрим.")

async def show_settings_menu(update: Update, context: CallbackContext):
    """Показывает меню настроек"""
    user_id = update.effective_user.id
    chat_type = update.effective_chat.type
    
    if chat_type in ['group', 'supergroup']:
        chat_id = update.effective_chat.id
        settings, memory = get_group_settings(chat_id)
        
        # Проверяем, является ли пользователь администратором
        chat_member = await context.bot.get_chat_member(chat_id, user_id)
        is_admin = chat_member.status in ['creator', 'administrator']
        
        # Базовые кнопки настроек
        keyboard = [
            [InlineKeyboardButton("🤖 Модель ИИ: " + settings.get('current_model', 'gemini-pro'), callback_data='change_model')],
            [InlineKeyboardButton("🎭 Режим ролевой игры: " + ("✅" if settings.get('roleplay_mode') else "❌"), callback_data='toggle_roleplay')],
            [InlineKeyboardButton("🔓 Уровень свободы: " + settings.get('freedom_level', 'Свобода'), callback_data='change_freedom')],
            [InlineKeyboardButton("🔍 Google поиск: " + ("✅" if settings.get('google_search') else "❌"), callback_data='toggle_google')],
            [InlineKeyboardButton("🌡️ Креативность: " + str(settings.get('temperature', 0.9)), callback_data='set_temperature')],
            [InlineKeyboardButton("💾 Память: " + str(settings.get('max_memory', 50)) + " сообщений", callback_data='set_memory')],
            [InlineKeyboardButton("👤 Личность: " + settings.get('personality', 'обычная'), callback_data='set_personality')],
            [InlineKeyboardButton("📢 Настройки уведомлений", callback_data='notification_settings')]
        ]
        
        # Добавляем кнопку управления доступом только для админов
        if is_admin:
            admin_setting_text = "🔒 Ограничить настройки (Только админы)" if not settings.get('admin_only_settings') else "🔓 Разрешить настройки (Все участники)"
            keyboard.append([InlineKeyboardButton(admin_setting_text, callback_data='toggle_admin_only')])
        
        settings_text = f"""⚙️ *Настройки группового чата*

🤖 *Модель ИИ:* {settings.get('current_model', 'gemini-pro')}
🎭 *Режим ролевой игры:* {'Включен ✅' if settings.get('roleplay_mode') else 'Выключен ❌'}
🔓 *Уровень свободы:* {settings.get('freedom_level', 'Свобода')}
🔍 *Google поиск:* {'Включен ✅' if settings.get('google_search') else 'Выключен ❌'}
🌡️ *Креативность:* {settings.get('temperature', 0.9)}
💾 *Размер памяти:* {settings.get('max_memory', 50)} сообщений
👤 *Личность бота:* {settings.get('personality', 'обычная')}
🔐 *Доступ к настройкам:* {'Только админы' if settings.get('admin_only_settings') else 'Все участники'}

📢 *Уведомления:*
• Обычные рассылки: {'Включены ✅' if not settings.get('disable_broadcasts') else 'Выключены ❌'}
• Тех. работы: {'Включены ✅' if not settings.get('disable_tech_notifications') else 'Выключены ❌'}"""

    else:
        settings = get_user_settings(user_id)
        keyboard = [
            [InlineKeyboardButton("🤖 Модель ИИ: " + settings.get('current_model', 'gemini-pro'), callback_data='change_model')],
            [InlineKeyboardButton("🎭 Режим ролевой игры: " + ("✅" if settings.get('roleplay_mode') else "❌"), callback_data='toggle_roleplay')],
            [InlineKeyboardButton("🔓 Уровень свободы: " + settings.get('freedom_level', 'Свобода'), callback_data='change_freedom')],
            [InlineKeyboardButton("🔍 Google поиск: " + ("✅" if settings.get('google_search') else "❌"), callback_data='toggle_google')],
            [InlineKeyboardButton("🌡️ Креативность: " + str(settings.get('temperature', 0.9)), callback_data='set_temperature')],
            [InlineKeyboardButton("💾 Память: " + str(settings.get('max_memory', 50)) + " сообщений", callback_data='set_memory')],
            [InlineKeyboardButton("👤 Личность: " + settings.get('personality', 'обычная'), callback_data='set_personality')],
            [InlineKeyboardButton("📢 Настройки уведомлений", callback_data='notification_settings')]
        ]
        
        settings_text = f"""⚙️ *Личные настройки*

🤖 *Модель ИИ:* {settings.get('current_model', 'gemini-pro')}
🎭 *Режим ролевой игры:* {'Включен ✅' if settings.get('roleplay_mode') else 'Выключен ❌'}
🔓 *Уровень свободы:* {settings.get('freedom_level', 'Свобода')}
🔍 *Google поиск:* {'Включен ✅' if settings.get('google_search') else 'Выключен ❌'}
🌡️ *Креативность:* {settings.get('temperature', 0.9)}
💾 *Размер памяти:* {settings.get('max_memory', 50)} сообщений
👤 *Личность бота:* {settings.get('personality', 'обычная')}

📢 *Уведомления:*
• Обычные рассылки: {'Включены ✅' if not settings.get('disable_broadcasts') else 'Выключены ❌'}
• Тех. работы: {'Включены ✅' if not settings.get('disable_tech_notifications') else 'Выключены ❌'}"""

    reply_markup = InlineKeyboardMarkup(keyboard)
    
    try:
        if update.callback_query:
            await update.callback_query.message.edit_text(settings_text, reply_markup=reply_markup, parse_mode='Markdown')
        else:
            await update.message.reply_text(settings_text, reply_markup=reply_markup, parse_mode='Markdown')
    except Exception as e:
        logging.error(f"Ошибка при отображении меню настроек: {str(e)}")
        # Пробуем отправить без форматирования
        plain_text = settings_text.replace('*', '')
        if update.callback_query:
            await update.callback_query.message.edit_text(plain_text, reply_markup=reply_markup)
        else:
            await update.message.reply_text(plain_text, reply_markup=reply_markup)

def get_freedom_level_name(level: str) -> str:
    """Получить читаемое название уровня свободы"""
    levels = {
        'Божий одуванчик': 'Божий одуванчик 😇',
        'Свобода': 'Свобода 🎭',
        'Джокер': 'Джокер 🃏',
        'Черная ИИ': 'Черная ИИ 🖤',
        'Долбоебень': 'Долбоебень 🤪'
    }
    return levels.get(level, 'Свобода 🎭')

async def handle_settings_callback(update: Update, context: CallbackContext):
    """Обрабатывает callback-запросы от кнопок настроек"""
    try:
        query = update.callback_query
        user_id = query.from_user.id
        chat_type = query.message.chat.type
        data = query.data

        try:
            await query.answer()
        except telegram.error.BadRequest as e:
            if "Query is too old" in str(e):
                await query.message.reply_text(
                    "Это меню устарело. Пожалуйста, вызовите /settings заново."
                )
                return
            raise e

        # Получаем текущие настройки
        if chat_type in ['group', 'supergroup']:
            chat_id = query.message.chat.id
            settings, memory = get_group_settings(chat_id)
        else:
            settings = get_user_settings(user_id)

        # Обработка разных типов callback-запросов
        if data == 'change_freedom':
            # Показываем меню выбора уровня свободы
            keyboard = [
                [InlineKeyboardButton("😇 Божий одуванчик", callback_data='set_freedom_Божий одуванчик')],
                [InlineKeyboardButton("🎭 Свобода", callback_data='set_freedom_Свобода')],
                [InlineKeyboardButton("🃏 Джокер", callback_data='set_freedom_Джокер')],
                [InlineKeyboardButton("🖤 Черная ИИ", callback_data='set_freedom_Черная ИИ')],
                [InlineKeyboardButton("🤪 Долбоебень", callback_data='set_freedom_Долбоебень')],
                [InlineKeyboardButton("« Назад", callback_data='back_to_settings')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.message.edit_text(
                "Выберите уровень свободы общения:",
                reply_markup=reply_markup
            )
            return

        elif data.startswith('set_freedom_'):
            # Устанавливаем выбранный уровень свободы
            freedom_level = data.replace('set_freedom_', '')
            settings['freedom_level'] = freedom_level
            
            # Сохраняем настройки
            if chat_type in ['group', 'supergroup']:
                update_group_settings(chat_id, settings, memory)
            else:
                update_user_settings(user_id, settings)
            
            await query.message.edit_text(
                f"✅ Уровень свободы установлен: {freedom_level}\n\nИспользуйте /settings чтобы вернуться в меню настроек."
            )
            return

        elif data == 'toggle_roleplay':
            settings['roleplay_mode'] = not settings.get('roleplay_mode', False)
            if chat_type in ['group', 'supergroup']:
                update_group_settings(chat_id, settings, memory)
            else:
                update_user_settings(user_id, settings)
            await show_settings_menu(update, context)
            return

        elif data == 'toggle_google':
            settings['google_search'] = not settings.get('google_search', False)
            if chat_type in ['group', 'supergroup']:
                update_group_settings(chat_id, settings, memory)
            else:
                update_user_settings(user_id, settings)
            await show_settings_menu(update, context)
            return

        elif data == 'toggle_admin_only':
            if chat_type in ['group', 'supergroup']:
                settings['admin_only_settings'] = not settings.get('admin_only_settings', False)
                update_group_settings(chat_id, settings, memory)
                await show_settings_menu(update, context)
            return

        elif data == 'back_to_settings':
            await show_settings_menu(update, context)
            return

        # Добавьте обработку других callback-запросов здесь
        
    except Exception as e:
        logging.error(f"Ошибка в handle_settings_callback: {str(e)}")
        await query.message.reply_text("Произошла ошибка при обработке запроса. Попробуйте позже.")

async def set_setting(update: Update, context: CallbackContext):
    if len(context.args) < 2:
        await update.message.reply_text("Использование: /set [параметр] [значение]")
        return
        
    param = context.args[0].lower()
    value = context.args[1].lower()
    
    await update.message.reply_text(f"Параметр '{param}' установлен в значение '{value}'")

async def reset_settings(update: Update, context: CallbackContext):
    await update.message.reply_text("✅ Все настройки сброшены к значениям по умолчанию")

async def history(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    if user_id not in conversation_history or not conversation_history[user_id]:
        await update.message.reply_text("История диалога пуста")
        return
        
    history_text = "*История диалога:*\n"
    for msg in conversation_history[user_id][-5:]:  # Последние 5 сообщений
        role = "🤖" if msg["role"] == "assistant" else "👤"
        history_text += f"\n{role} {msg['text'][:100]}..."
        
    await update.message.reply_text(history_text, parse_mode='Markdown')

async def system(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    system_text = ' '.join(context.args) if context.args else None
    
    if not system_text:
        await update.message.reply_text("Пожалуйста, укажите системную инструкцию после команды /system")
        return
    
    settings = get_user_settings(user_id)
    if 'system_instructions' not in settings:
        settings['system_instructions'] = []
    
    settings['system_instructions'].append(system_text)
    update_user_settings(user_id, settings)
    await update.message.reply_text("✅ Системная инструкция добавлена!")

async def forget(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    if update.message.reply_to_message:
        target_text = update.message.reply_to_message.text
        if user_id in conversation_history:
            conversation_history[user_id] = [msg for msg in conversation_history[user_id] if msg["text"] != target_text]
            await update.message.reply_text("Сообщение удалено из истории!")
    else:
        await update.message.reply_text("Ответьте на сообщение, которое хотите удалить, командой /forget")

async def replace(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    new_text = ' '.join(context.args) if context.args else None
    
    if not new_text or not update.message.reply_to_message:
        await update.message.reply_text("Ответьте на сообщение, которое хотите заменить, командой /replace и новым текстом")
        return
        
    if user_id in conversation_history:
        target_text = update.message.reply_to_message.text
        for msg in conversation_history[user_id]:
            if msg["text"] == target_text:
                msg["text"] = new_text
                await update.message.reply_text("Сообщение заменено!")
                return
                
    await update.message.reply_text("Сообщение не найдено в истории")

def convert_voice_to_text(file_path: str) -> str:
    try:
        # Конвертируем ogg в wav
        audio = pydub.AudioSegment.from_ogg(file_path)
        wav_path = file_path.replace('.ogg', '.wav')
        audio.export(wav_path, format="wav")
        
        # Проверяем наличие модели
        if not os.path.exists("model"):
            return "Ошибка: модель распознавания речи не установлена"
        
        # Инициализируем распознаватель
        model = Model("model")
        wf = wave.open(wav_path, "rb")
        rec = KaldiRecognizer(model, wf.getframerate())
        
        # Читаем и распознаем аудио
        while True:
            data = wf.readframes(4000)
            if len(data) == 0:
                break
            rec.AcceptWaveform(data)
        
        # Получаем результат
        result = json.loads(rec.FinalResult())
        text = result.get('text', '')
        
        return text if text else "Не удалось распознать речь"
        
    except Exception as e:
        return f"Ошибка при распознавании речи: {str(e)}"
    finally:
        if os.path.exists(wav_path):
            os.remove(wav_path)

async def handle_photo(update: Update, context: CallbackContext):
    """Обрабатывает входящие фотографии"""
    track_usage(update, "photo")
    
    try:
        # Получаем информацию о чате и настройки
        chat_type = update.effective_chat.type
        user_id = update.effective_user.id
        
        if chat_type in ['group', 'supergroup']:
            chat_id = update.effective_chat.id
            settings, memory = get_group_settings(chat_id)
        else:
            settings = get_user_settings(user_id)
        
        # Получаем самое большое доступное изображение
        photo = update.message.photo[-1]
        file = await context.bot.get_file(photo.file_id)
        
        # Создаем временный файл
        image_path = f"temp_{update.effective_user.id}.jpg"
        
        try:
            # Скачиваем файл
            await file.download_to_drive(image_path)
            
            # Открываем изображение
            with Image.open(image_path) as img:
                # Конвертируем изображение для Gemini Vision
                img_byte_arr = BytesIO()
                img.save(img_byte_arr, format='PNG')
                img_byte_arr = img_byte_arr.getvalue()
                
                # Используем модель Vision
                model = genai.GenerativeModel(GEMINI_VISION_MODEL)
                
                # Формируем промпт
                prompt = """Опиши это изображение подробно на русском языке.
                Обрати внимание на детали, цвета, композицию и настроение."""
                
                # Создаем запрос к модели с изображением
                response = model.generate_content([prompt, img_byte_arr], stream=False)
                
                if response and hasattr(response, 'text'):
                    # Отправляем ответ
                    if chat_type in ['group', 'supergroup']:
                        user_mention = f"@{update.effective_user.username}" if update.effective_user.username else update.effective_user.first_name
                        await update.message.reply_text(f"{user_mention}, {response.text}")
                    else:
                        await update.message.reply_text(response.text)
                    
                    # Сохраняем в память
                    add_to_memory(update, "[Отправлено изображение]", 'user')
                    add_to_memory(update, response.text, 'assistant')
                else:
                    await update.message.reply_text("Извините, не удалось распознать изображение.")
                    
        except Exception as e:
            logging.error(f"Ошибка при обработке изображения: {str(e)}")
            await update.message.reply_text("Произошла ошибка при обработке изображения. Попробуйте позже.")
        finally:
            # Удаляем временный файл
            if os.path.exists(image_path):
                os.remove(image_path)
                
    except Exception as e:
        logging.error(f"Общая ошибка при обработке изображения: {str(e)}")
        await update.message.reply_text("Произошла ошибка при обработке изображения. Попробуйте позже.")

async def handle_voice(update: Update, context: CallbackContext):
    track_usage(update, "voice")
    voice = await update.message.voice.get_file()
    file_path = f"voice_{update.effective_user.id}.ogg"
    
    try:
        await voice.download_to_drive(file_path)
        text = convert_voice_to_text(file_path)
        response = await process_message(text, update.effective_user.id)
        await update.message.reply_text(response)
    except Exception as e:
        logging.error(f"Ошибка при обработке голосового сообщения: {str(e)}")
        await update.message.reply_text("Произошла ошибка при обработке голосового сообщения. Попробуйте позже.")
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)

async def personality(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    personality_text = ' '.join(context.args) if context.args else None
    
    if not personality_text:
        await update.message.reply_text("Использование: /personality [описание личности бота]")
        return
        
    if user_id not in user_settings:
        user_settings[user_id] = {}
    
    user_settings[user_id]['personality'] = personality_text
    await update.message.reply_text(f"✅ Личность бота установлена: {personality_text}")

async def expert(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    topic = ' '.join(context.args) if context.args else None
    
    if not topic:
        await update.message.reply_text("Использование: /expert [тема]")
        return
        
    if user_id not in user_settings:
        user_settings[user_id] = {}
    
    user_settings[user_id]['expert_mode'] = topic
    await update.message.reply_text(f"✅ Режим эксперта активирован для темы: {topic}")

async def stats(update: Update, context: CallbackContext):
    """Показывает статистику использования бота"""
    user_id = update.effective_user.id
    user_stats = usage_stats.get(user_id, {})
    
    if not user_stats:
        await update.message.reply_text("У вас пока нет статистики использования бота.")
        return
    
    # Форматируем время последней активности
    last_active = "никогда"
    if user_stats.get("last_active"):
        try:
            last_active_dt = datetime.fromisoformat(user_stats["last_active"])
            last_active = last_active_dt.strftime("%d.%m.%Y %H:%M")
        except:
            last_active = user_stats["last_active"]
    
    # Форматируем время первого использования
    first_seen = "неизвестно"
    if user_stats.get("first_seen"):
        try:
            first_seen_dt = datetime.fromisoformat(user_stats["first_seen"])
            first_seen = first_seen_dt.strftime("%d.%m.%Y %H:%M")
        except:
            first_seen = user_stats["first_seen"]
    
    # Подсчитываем статистику использования в разных типах чатов
    chat_types_str = ""
    if user_stats.get("chat_types"):
        chat_types_map = {
            "private": "Личные сообщения",
            "group": "Группы",
            "supergroup": "Супергруппы",
            "channel": "Каналы"
        }
        chat_types = [chat_types_map.get(ct, ct) for ct in user_stats["chat_types"]]
        chat_types_str = "\n• " + "\n• ".join(chat_types)
    
    stats_text = f"""📊 *Ваша статистика*

👤 *Общая информация:*
• Имя: {user_stats.get("username", "Неизвестно")}
• Первое использование: {first_seen}
• Последняя активность: {last_active}

📝 *Активность:*
• Всего сообщений: {user_stats.get("total_messages", 0)}
• Использовано команд: {len(user_stats.get("commands", {}))}

🌐 *Где используется бот:*{chat_types_str}

🔧 *Использование команд:*"""

    # Добавляем статистику по командам
    commands = user_stats.get("commands", {})
    if commands:
        # Сортируем команды по частоте использования
        sorted_commands = sorted(commands.items(), key=lambda x: x[1], reverse=True)
        for cmd, count in sorted_commands:
            stats_text += f"\n• /{cmd}: {count} раз"
    else:
        stats_text += "\n• Команды еще не использовались"
    
    try:
        await update.message.reply_text(stats_text, parse_mode='Markdown')
    except Exception as e:
        # Если не удалось отправить с форматированием, отправляем без него
        logging.error(f"Ошибка при отправке статистики: {str(e)}")
        await update.message.reply_text(stats_text.replace('*', ''))

async def set_memory_limit(update: Update, context: CallbackContext):
    """Устанавливает лимит памяти для чата или пользователя"""
    try:
        # Проверяем, указан ли размер памяти
        if not context.args:
            current_limit = MEMORY_LIMITS['default']
            if update.effective_chat.type in ['group', 'supergroup']:
                settings, _ = get_group_settings(update.effective_chat.id)
                current_limit = settings.get('max_memory', MEMORY_LIMITS['default'])
            else:
                settings = get_user_settings(update.effective_user.id)
                current_limit = settings.get('max_memory', MEMORY_LIMITS['default'])
                
            await update.message.reply_text(
                f"🔧 *Настройки памяти*\n\n"
                f"Текущий лимит: {current_limit} сообщений\n"
                f"Минимум: {MEMORY_LIMITS['min']}\n"
                f"Максимум: {MEMORY_LIMITS['max']}\n\n"
                "Использование: `/memory [количество]`",
                parse_mode='Markdown'
            )
            return

        # Получаем новый размер памяти
        try:
            new_limit = int(context.args[0])
        except ValueError:
            await update.message.reply_text("❌ Ошибка: укажите число сообщений")
            return

        # Проверяем ограничения
        if new_limit < MEMORY_LIMITS['min']:
            await update.message.reply_text(f"❌ Минимальный размер памяти: {MEMORY_LIMITS['min']} сообщений")
            return
        if new_limit > MEMORY_LIMITS['max']:
            await update.message.reply_text(f"❌ Максимальный размер памяти: {MEMORY_LIMITS['max']} сообщений")
            return

        # Обновляем настройки
        if update.effective_chat.type in ['group', 'supergroup']:
            chat_id = update.effective_chat.id
            settings, memory = get_group_settings(chat_id)
            settings['max_memory'] = new_limit
            update_group_settings(chat_id, settings, memory)
        else:
            user_id = update.effective_user.id
            settings = get_user_settings(user_id)
            settings['max_memory'] = new_limit
            update_user_settings(user_id, settings)

        await update.message.reply_text(f"✅ Размер памяти установлен: {new_limit} сообщений")

    except Exception as e:
        logging.error(f"Ошибка при установке размера памяти: {str(e)}")
        await update.message.reply_text("❌ Произошла ошибка при изменении размера памяти")

# Админские команды
async def debug(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("⛔️ У вас нет доступа к этой команде")
        return
    
    debug_info = {
        'conversation_history_size': sum(len(h) for h in conversation_history.values()),
        'total_users': len(usage_stats),
        'memory_usage': psutil.Process().memory_info().rss / 1024 / 1024,  # MB
        'uptime': str(datetime.now() - START_TIME)
    }
    
    debug_text = f"""🔍 Отладочная информация:

💾 Память:
• Размер истории диалогов: {debug_info['conversation_history_size']} сообщений
• Использование памяти: {debug_info['memory_usage']:.1f} MB

👥 Пользователи:
• Всего пользователей: {debug_info['total_users']}
• Активные сессии: {len(conversation_history)}

⚙️ Система:
• Время работы: {debug_info['uptime']}
• Версия Python: {platform.python_version()}
• Платформа: {platform.platform()}"""

    await update.message.reply_text(debug_text)

async def broadcast(update: Update, context: CallbackContext):
    """
    Формат команды:
    /broadcast текст сообщения - для обычного текста
    /broadcast #важно текст сообщения - для важных сообщений
    /broadcast #тех_работы текст сообщения - для уведомлений о тех. работах
    """
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("⛔️ У вас нет доступа к этой команде")
        return
    
    if not context.args:
        usage_text = """📢 *Использование команды broadcast:*

• Обычная рассылка:
`/broadcast Текст сообщения`

• Важное сообщение:
`/broadcast #важно Текст сообщения`

• Уведомление о тех. работах:
`/broadcast #тех_работы Текст сообщения`

Сообщение будет отправлено всем пользователям бота."""
        await update.message.reply_text(usage_text, parse_mode='Markdown')
        return
    
    # Определяем тип сообщения и формируем текст
    message_type = "обычное"
    if context.args[0].startswith('#'):
        tag = context.args[0].lower()
        message = ' '.join(context.args[1:])
        if tag == '#важно':
            message_type = "важное"
            message = f"❗️ *ВАЖНОЕ СООБЩЕНИЕ* ❗️\n\n{message}"
        elif tag == '#тех_работы':
            message_type = "тех_работы"
            message = f"🛠 *ТЕХНИЧЕСКИЕ РАБОТЫ* 🛠\n\n{message}"
    else:
        message = ' '.join(context.args)
        message = f"📢 *Объявление:*\n\n{message}"
    
    # Добавляем подпись администратора и время
    admin_username = update.effective_user.username or "Администратор"
    current_time = datetime.now().strftime("%d.%m.%Y %H:%M")
    message += f"\n\n_От: @{admin_username}_\n_{current_time}_"
    
    # Отправляем сообщение
    success = 0
    failed = 0
    skipped = 0
    
    # Получаем список всех пользователей
    session = Session()
    all_users = session.query(UserSettings).all()
    total_users = len(all_users)
    
    # Отправляем статус о начале рассылки
    status_message = await update.message.reply_text(
        f"📤 Начинаю рассылку...\nВсего получателей: {total_users}"
    )
    
    for user_setting in all_users:
        try:
            # Пропускаем пользователей, которые отключили определенные типы рассылок
            if message_type == "обычное" and user_setting.settings.get('disable_broadcasts'):
                skipped += 1
                continue
            if message_type == "тех_работы" and user_setting.settings.get('disable_tech_notifications'):
                skipped += 1
                continue
                
            await context.bot.send_message(
                user_setting.user_id,
                message,
                parse_mode='Markdown'
            )
            success += 1
            
            # Обновляем статус каждые 10 отправленных сообщений
            if success % 10 == 0:
                await status_message.edit_text(
                    f"📤 Отправка...\n"
                    f"✅ Успешно: {success}\n"
                    f"❌ Ошибок: {failed}\n"
                    f"⏳ Осталось: {total_users - success - failed - skipped}"
                )
            
            # Небольшая задержка чтобы не превысить лимиты Telegram
            await asyncio.sleep(0.05)
            
        except Exception as e:
            logging.error(f"Ошибка отправки сообщения пользователю {user_setting.user_id}: {str(e)}")
            failed += 1
    
    session.close()
    
    # Финальный отчет
    report = f"""📊 *Результаты рассылки:*

✅ Успешно доставлено: {success}
❌ Ошибок доставки: {failed}
⏭ Пропущено: {skipped}
👥 Всего получателей: {total_users}

*Тип рассылки:* {message_type}
*Время выполнения:* {(datetime.now() - datetime.strptime(current_time, "%d.%m.%Y %H:%M")).seconds} сек."""

    await status_message.edit_text(report, parse_mode='Markdown')

def get_user_settings(user_id: int) -> dict:
    """Получить настройки пользователя из базы данных"""
    session = Session()
    try:
        user_setting = session.query(UserSettings).filter_by(user_id=user_id).first()
        if user_setting is None:
            # Создаем новые настройки для пользователя
            user_setting = UserSettings(
                user_id=user_id,
                settings=DEFAULT_SETTINGS.copy()
            )
            session.add(user_setting)
            session.commit()
            return DEFAULT_SETTINGS.copy()
        return user_setting.settings
    except Exception as e:
        logging.error(f"Ошибка при получении настроек пользователя {user_id}: {str(e)}")
        return DEFAULT_SETTINGS.copy()
    finally:
        session.close()

def update_user_settings(user_id: int, new_settings: dict):
    """Обновить настройки пользователя в базе данных"""
    session = Session()
    try:
        user_setting = session.query(UserSettings).filter_by(user_id=user_id).first()
        if user_setting is None:
            user_setting = UserSettings(user_id=user_id, settings=new_settings)
            session.add(user_setting)
        else:
            user_setting.settings = new_settings
        session.commit()
    except Exception as e:
        logging.error(f"Ошибка при обновлении настроек пользователя {user_id}: {str(e)}")
        session.rollback()
    finally:
        session.close()

def get_group_settings(chat_id: int) -> dict:
    """Получить настройки группового чата из базы данных"""
    session = Session()
    try:
        group_setting = session.query(GroupSettings).filter_by(chat_id=chat_id).first()
        if group_setting is None:
            # Создаем новые настройки для группы
            group_setting = GroupSettings(
                chat_id=chat_id,
                settings=DEFAULT_SETTINGS.copy(),
                chat_memory=[]
            )
            session.add(group_setting)
            session.commit()
            return DEFAULT_SETTINGS.copy(), []
        return group_setting.settings, group_setting.chat_memory
    except Exception as e:
        logging.error(f"Ошибка при получении настроек группы {chat_id}: {str(e)}")
        return DEFAULT_SETTINGS.copy(), []
    finally:
        session.close()

def update_group_settings(chat_id: int, new_settings: dict, new_memory: list):
    """Обновить настройки группового чата в базе данных"""
    session = Session()
    try:
        group_setting = session.query(GroupSettings).filter_by(chat_id=chat_id).first()
        if group_setting is None:
            group_setting = GroupSettings(chat_id=chat_id, settings=new_settings, chat_memory=new_memory)
            session.add(group_setting)
        else:
            group_setting.settings = new_settings
            group_setting.chat_memory = new_memory
        session.commit()
    except Exception as e:
        logging.error(f"Ошибка при обновлении настроек группы {chat_id}: {str(e)}")
        session.rollback()
    finally:
        session.close()

def init_db():
    """Инициализация базы данных"""
    try:
        Base.metadata.create_all(engine)
        logging.info("База данных успешно инициализирована")
    except Exception as e:
        logging.error(f"Ошибка при инициализации базы данных: {str(e)}")

async def handle_callback_query(update: Update, context: CallbackContext):
    """Обрабатывает callback-запросы"""
    query = update.callback_query
    
    try:
        # Проверяем тип callback-запроса
        if query.data.startswith('draw_'):
            # Передаем управление обработчику рисования
            await handle_draw_callback(update, context)
        else:
            # Передаем управление обработчику настроек
            await handle_settings_callback(update, context)
            
    except Exception as e:
        logging.error(f"Ошибка при обработке callback-запроса: {str(e)}")
        try:
            await query.answer("Произошла ошибка при обработке запроса")
        except:
            pass

async def scheduled_message(context: CallbackContext):
    """Отправляет запланированные сообщения"""
    job = context.job
    chat_id = job.data['chat_id']
    user_id = job.data['user_id']
    
    try:
        # Получаем настройки и историю диалога
        settings = get_user_settings(user_id)
        if user_id in private_memory and private_memory[user_id]:
            last_messages = private_memory[user_id][-5:]  # Последние 5 сообщений
            
            # Формируем промпт для анализа диалога
            model = genai.GenerativeModel(GEMINI_MODEL)
            prompt = """На основе последних сообщений в диалоге, сгенерируй уместное сообщение для продолжения разговора.
            Это может быть вопрос о прогрессе, приветствие или напоминание.
            Учитывай время суток и контекст предыдущих сообщений.
            
            Последние сообщения:
            """
            
            for msg in last_messages:
                prompt += f"{'Бот' if msg['role'] == 'assistant' else 'Пользователь'}: {msg['content']}\n"
            
            response = model.generate_content(prompt)
            if response and hasattr(response, 'text'):
                await context.bot.send_message(chat_id=chat_id, text=response.text)
                add_to_memory(Update(0, None), response.text, 'assistant')  # Добавляем в память
                
    except Exception as e:
        logging.error(f"Ошибка при отправке запланированного сообщения: {str(e)}")

def schedule_regular_messages(application: Application, chat_id: int, user_id: int):
    """Планирует регулярные сообщения"""
    # Планируем сообщения на разное время
    job_queue = application.job_queue
    
    # Утреннее приветствие (9:00)
    job_queue.run_daily(
        scheduled_message,
        time=time(9, 0),  # Используем datetime.time
        days=(0, 1, 2, 3, 4, 5, 6),
        data={'chat_id': chat_id, 'user_id': user_id}
    )
    
    # Дневной чек (14:00)
    job_queue.run_daily(
        scheduled_message,
        time=time(14, 0),  # Используем datetime.time
        days=(0, 1, 2, 3, 4, 5, 6),
        data={'chat_id': chat_id, 'user_id': user_id}
    )
    
    # Вечернее сообщение (20:00)
    job_queue.run_daily(
        scheduled_message,
        time=time(20, 0),  # Используем datetime.time
        days=(0, 1, 2, 3, 4, 5, 6),
        data={'chat_id': chat_id, 'user_id': user_id}
    )

async def start(update: Update, context: CallbackContext):
    """Обработчик команды /start"""
    user = update.effective_user
    chat_type = update.effective_chat.type
    
    welcome_text = f"""👋 Привет, {user.first_name}!

🤖 Я - ИИ-ассистент на базе Gemini 2.0 Flash. Я могу:

• 💬 Общаться на любые темы
• 🎨 Генерировать изображения (/draw)
• 📷 Анализировать фотографии
• 🎭 Менять свою личность
• ⚙️ Настраиваться под ваши предпочтения

📝 Основные команды:
• /help - Список всех команд
• /settings - Настройки бота
• /draw - Генерация изображений
• /stats - Ваша статистика

Просто напишите мне сообщение, и я постараюсь помочь! 😊"""

    # Добавляем пользователя в статистику
    if not usage_stats.get(user.id):
        usage_stats[user.id] = {
            'first_seen': datetime.now().isoformat(),
            'username': user.username or user.first_name,
            'total_messages': 0,
            'commands': {},
            'chat_types': set([chat_type]),
            'last_active': datetime.now().isoformat()
        }
    else:
        usage_stats[user.id]['chat_types'].add(chat_type)
        usage_stats[user.id]['last_active'] = datetime.now().isoformat()

    keyboard = [
        [InlineKeyboardButton("⚙️ Настройки", callback_data="settings"),
         InlineKeyboardButton("❓ Помощь", callback_data="help")],
        [InlineKeyboardButton("🎨 Генерация изображений", callback_data="draw_info"),
         InlineKeyboardButton("📊 Статистика", callback_data="stats")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    try:
        await update.message.reply_text(welcome_text, reply_markup=reply_markup, parse_mode='Markdown')
    except Exception as e:
        await update.message.reply_text(welcome_text.replace('*', ''), reply_markup=reply_markup)

    if chat_type == 'private':
        schedule_regular_messages(context.application, update.effective_chat.id, user.id)

async def clear(update: Update, context: CallbackContext):
    """Очищает историю диалога"""
    user_id = update.effective_user.id
    chat_type = update.effective_chat.type
    
    if chat_type in ['group', 'supergroup']:
        chat_id = update.effective_chat.id
        settings, memory = get_group_settings(chat_id)
        memory.clear()
        update_group_settings(chat_id, settings, memory)
        await update.message.reply_text("✨ История группового чата очищена!")
    else:
        if user_id in conversation_history:
            conversation_history[user_id].clear()
        if user_id in private_memory:
            private_memory[user_id].clear()
        await update.message.reply_text("✨ История диалога очищена!")

def track_usage(update: Update, command: str = None):
    """Отслеживает использование бота"""
    user = update.effective_user
    if not user:
        return
        
    user_id = user.id
    
    if user_id not in usage_stats:
        usage_stats[user_id] = {
            'first_seen': datetime.now().isoformat(),
            'username': user.username or user.first_name,
            'total_messages': 0,
            'commands': {},
            'chat_types': set([update.effective_chat.type]),
            'last_active': datetime.now().isoformat()
        }
    
    stats = usage_stats[user_id]
    stats['total_messages'] += 1
    stats['last_active'] = datetime.now().isoformat()
    
    if command:
        if 'commands' not in stats:
            stats['commands'] = {}
        stats['commands'][command] = stats['commands'].get(command, 0) + 1

def add_to_memory(update: Update, text: str, role: str):
    """Добавляет сообщение в память"""
    user_id = update.effective_user.id
    chat_type = update.effective_chat.type
    
    if chat_type in ['group', 'supergroup']:
        chat_id = update.effective_chat.id
        settings, memory = get_group_settings(chat_id)
        
        # Ограничиваем размер памяти
        max_memory = settings.get('max_memory', 50)
        if len(memory) >= max_memory:
            memory.pop(0)
            
        memory.append({
            'username': update.effective_user.username or update.effective_user.first_name,
            'content': text,
            'timestamp': datetime.now().isoformat()
        })
        
        update_group_settings(chat_id, settings, memory)
    else:
        if user_id not in conversation_history:
            conversation_history[user_id] = []
            
        if user_id not in private_memory:
            private_memory[user_id] = []
            
        # Добавляем в обычную историю
        conversation_history[user_id].append({
            'role': role,
            'text': text
        })
        
        # Добавляем в память для контекста
        private_memory[user_id].append({
            'role': role,
            'content': text,
            'timestamp': datetime.now().isoformat()
        })
        
        # Ограничиваем размер памяти
        settings = get_user_settings(user_id)
        max_memory = settings.get('max_memory', 50)
        
        if len(private_memory[user_id]) > max_memory:
            private_memory[user_id].pop(0)
            
        if len(conversation_history[user_id]) > max_memory:
            conversation_history[user_id].pop(0)

def main():
    # Инициализация базы данных
    init_db()
    
    # Создаем приложение
    application = Application.builder().token(TELEGRAM_TOKEN).build()

    # Базовые команды
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("clear", clear))
    application.add_handler(CommandHandler("settings", show_settings_menu))
    application.add_handler(CommandHandler("set", set_setting))
    application.add_handler(CommandHandler("reset_settings", reset_settings))
    application.add_handler(CommandHandler("feedback", feedback))
    application.add_handler(CommandHandler("forget", forget))
    application.add_handler(CommandHandler("replace", replace))
    application.add_handler(CommandHandler("system", system))
    application.add_handler(CommandHandler("history", history))
    application.add_handler(CommandHandler("personality", personality))
    application.add_handler(CommandHandler("expert", expert))
    application.add_handler(CommandHandler("stats", stats))
    application.add_handler(CommandHandler("debug", debug))
    application.add_handler(CommandHandler("broadcast", broadcast))
    application.add_handler(CommandHandler("draw", handle_draw_request))  # Добавляем обработчик генерации изображений
    
    # Обработчики медиа и текста
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    application.add_handler(MessageHandler(filters.VOICE, handle_voice))
    
    # Обработка сообщений в личных чатах
    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE,
        handle_message
    ))

    # Отдельный обработчик для групповых чатов
    application.add_handler(MessageHandler(
        (filters.TEXT & ~filters.COMMAND & filters.ChatType.GROUPS) |  # Все сообщения в группах
        (filters.TEXT & filters.Entity("mention") & filters.Regex(r'@Skqlka_bot')) |  # Упоминания
        (filters.TEXT & filters.Regex(r'@Skqlka_bot')),  # Текст с упоминанием бота
        handle_message
    ))

    # Добавляем обработчик callback'ов
    application.add_handler(CallbackQueryHandler(handle_settings_callback))

    print("Запуск бота...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
import os
from typing import List
import random

# Telegram Bot Token
TELEGRAM_TOKEN = "7328871920:AAFkmwwkGYwZBlG0i-PRhivo6aApOqQ7qnI"

# Gemini API Keys
GEMINI_API_KEYS = [
    "AIzaSyCXEHnK_Q92ywov3N-Ds4hyDZBb-9o75jc",
    "AIzaSyDQtgYCJ3VIVSNcfaMf5jatGtEOPnVeSs",
    "AIzaSyCXEHnK_Q92ywov3N-Ds4hyDZBb-9o75jc",
    "AIzaSyDQtgYCJ3VIVSNcfaMf5jatGtEOPnVeSs"
]

# Database Configuration
DATABASE_URL = "sqlite:///bot_database.db"

# Memory Limits
MEMORY_LIMITS = {
    'min': 200,
    'default': 600,
    'max': 1000
}

# Admin IDs
ADMIN_IDS = [5740604900]

# Default Settings
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

def get_random_api_key() -> str:
    """Возвращает случайный API ключ из пула доступных ключей"""
    return random.choice(GEMINI_API_KEYS)

# Загрузка переменных окружения, если есть файл .env
if os.path.exists('.env'):
    from dotenv import load_dotenv
    load_dotenv()
    
    # Переопределение значений из переменных окружения
    TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN', TELEGRAM_TOKEN)
    if os.getenv('GEMINI_API_KEYS'):
        GEMINI_API_KEYS = os.getenv('GEMINI_API_KEYS').split(',')
    DATABASE_URL = os.getenv('DATABASE_URL', DATABASE_URL) 
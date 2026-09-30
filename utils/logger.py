"""
Утилиты для логирования
"""
import logging
import os
from pathlib import Path
from config import settings


def setup_logging():
    """Настройка системы логирования"""

    # Создаем директорию для логов если её нет
    log_dir = Path(settings.log_file).parent
    log_dir.mkdir(parents=True, exist_ok=True)

    # Настройка форматирования
    log_format = '%(asctime)s | %(levelname)-8s | %(name)-20s | %(message)s'
    date_format = '%Y-%m-%d %H:%M:%S'

    # Настройка основного логгера
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format=log_format,
        datefmt=date_format,
        handlers=[
            # Вывод в консоль
            logging.StreamHandler(),
            # Вывод в файл
            logging.FileHandler(settings.log_file, encoding='utf-8')
        ]
    )

    # Уменьшаем уровень логирования для библиотек
    logging.getLogger('aiogram').setLevel(logging.WARNING)
    logging.getLogger('aiohttp').setLevel(logging.WARNING)
    logging.getLogger('asyncio').setLevel(logging.WARNING)

    logger = logging.getLogger(__name__)
    logger.info("Система логирования настроена")
    logger.info(f"Уровень логирования: {settings.log_level}")
    logger.info(f"Файл логов: {settings.log_file}")


def get_logger(name: str) -> logging.Logger:
    """
    Получить логгер с заданным именем

    Args:
        name: Имя логгера

    Returns:
        Настроенный логгер
    """
    return logging.getLogger(name)

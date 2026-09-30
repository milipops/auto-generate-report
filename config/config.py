"""
Конфигурация локального сайта производственных отчётов.

Всё берётся из .env (см. .env.example с подсказками). Секретов в репозиторий
не коммитим: ключ ИИ живёт только в .env на рабочем компьютере.
"""
from pathlib import Path

from pydantic_settings import BaseSettings
from pydantic import Field, model_validator

from runtime_paths import APP_DIR


class Settings(BaseSettings):
    """Настройки приложения"""

    # Локальный сайт производственных отчётов.
    # 127.0.0.1 по умолчанию: доступ к переписке и счётчикам должен быть у
    # людей с этого рабочего компьютера. Открывать на 0.0.0.0 смысл есть,
    # только когда зайдёшь с другого ПК в цеху — пароля на сайте нет.
    web_host: str = Field(default="127.0.0.1", env="WEB_HOST")
    web_port: int = Field(default=8080, env="WEB_PORT")

    # Database (SQLite рядом с проектом; под сайтом новая база, старая
    # bot_database.db остаётся как есть)
    database_url: str = Field(default="sqlite:///./web_database.db",
                              env="DATABASE_URL")

    # Logging
    log_level: str = Field(default="INFO", env="LOG_LEVEL")
    log_file: str = Field(default="logs/web.log", env="LOG_FILE")

    # ИИ для разбора переписки и фото (Claude / OpenAI-совместимый шлюз).
    # Ключ и адрес заполняет пользователь сам — сюда секреты не коммитятся.
    ai_enabled: bool = Field(default=False, env="AI_ENABLED")
    ai_api_key: str = Field(default="", env="AI_API_KEY")
    ai_base_url: str = Field(default="https://api.anthropic.com", env="AI_BASE_URL")
    ai_model: str = Field(default="claude-opus-4-20250514", env="AI_MODEL")
    # "anthropic" (Messages API) или "openai" (chat/completions).
    ai_api_format: str = Field(default="anthropic", env="AI_API_FORMAT")
    ai_max_tokens: int = Field(default=8192, env="AI_MAX_TOKENS")
    # Сколько ждать ответ ИИ (сек). Разбор целого дня по всем станкам —
    # тяжёлый запрос; «думающие» модели (opus с extended thinking) могут
    # молчать в поток дольше 300 с и ловить таймаут. Поднять при нужде.
    ai_timeout: int = Field(default=600, env="AI_TIMEOUT")
    # Сколько РАЗ повторить запрос, если поток ИИ оборвался ПОСРЕДИ ответа
    # (пустой текст при finish_reason=None и небольшом объёме «размышлений»
    # — это обрыв связи через шлюз, а не нехватка токенов). Тот же запрос
    # на повторе обычно проходит. Нехватку токенов (finish_reason=length /
    # stop_reason=max_tokens) НЕ повторяем — там нужно поднимать max_tokens.
    ai_retries: int = Field(default=2, env="AI_RETRIES")
    # Разбор ПО ОДНОМУ СТАНКУ ЗА ВЫЗОВ ИИ. Номера станков находятся в тексте
    # без ИИ (по справочнику норм), дальше на каждый станок идёт свой запрос со
    # всей перепиской. Дороже и дольше (N запросов вместо одного), зато модель
    # не теряет станки, операторов и часы — при разборе всего дня одним
    # запросом за 27.08 пропали наладка на 321, часы наладчика на 251 и сам
    # оператор 321. false — вернуть старый режим «весь день одним запросом».
    ai_per_machine: bool = Field(default=True, env="AI_PER_MACHINE")

    # Бесплатный OCR на приёме фотографий (services/photo_ocr.py).
    # Фото без текста не сохраняются, с рабочим текстом в ИИ уходит сам
    # текст, а не картинка (дешевле). Если Tesseract не установлен, фото
    # не теряются — они отправляются в ИИ картинкой, как раньше.
    ocr_enabled: bool = Field(default=True, env="OCR_ENABLED")
    # Языки Tesseract. Без пакета rus модуль сам откатится на eng: цифры
    # счётчика и время читаются и так.
    ocr_langs: str = Field(default="rus+eng", env="OCR_LANGS")
    # Путь к tesseract.exe, если он не в PATH (обычная беда Windows).
    ocr_tesseract_cmd: str = Field(default="", env="TESSERACT_CMD")
    # Порог уверенности слова, ниже — считаем шумом фактуры материала.
    ocr_min_conf: int = Field(default=60, env="OCR_MIN_CONF")
    # Сколько цифр/символов считать признаком «на фото что-то написано».
    ocr_min_digits: int = Field(default=3, env="OCR_MIN_DIGITS")
    ocr_min_chars: int = Field(default=12, env="OCR_MIN_CHARS")
    # true — снова отправлять в ИИ сами картинки (дороже, но точнее на
    # рукописном журнале).
    ocr_send_photos_to_ai: bool = Field(default=False,
                                        env="OCR_SEND_PHOTOS_TO_AI")

    # Приём переписки из Яндекс Мессенджера (services/yandex_source.py).
    # Пока доступа нет, всё выключено: данные присылают файлом или сообщением
    # на сайт. Токен и id чата — только в .env.
    yandex_enabled: bool = Field(default=False, env="YANDEX_ENABLED")
    yandex_token: str = Field(default="", env="YANDEX_TOKEN")
    yandex_base_url: str = Field(default="", env="YANDEX_BASE_URL")
    yandex_chat_id: str = Field(default="", env="YANDEX_CHAT_ID")
    # Как часто опрашивать чат, секунд (при опросе по таймеру).
    yandex_poll_seconds: int = Field(default=120, env="YANDEX_POLL_SECONDS")

    @model_validator(mode="after")
    def _anchor_relative_paths(self):
        """Относительные пути считаем от папки программы, а не от той, откуда
        её запустили.

        Из исходников это одно и то же, а для .exe — нет: запуск двойным
        кликом, из ярлыка или из «Открыть с помощью» ставит рабочим каталогом
        то C:\\Windows, то папку документа, и `./web_database.db` молча
        заводится отдельная пустая.
        """
        self.log_file = _near_app(self.log_file)
        prefix = "sqlite:///"
        if self.database_url.startswith(prefix):
            target = self.database_url[len(prefix):]
            if not target.startswith("/"):        # sqlite://// — уже абсолютный
                self.database_url = prefix + Path(_near_app(target)).as_posix()
        return self

    class Config:
        # .env ищется рядом с программой (см. runtime_paths), а не в текущей
        # папке: иначе двойной клик по .exe запустил бы сайт без ключа ИИ.
        env_file = str(APP_DIR / ".env")
        env_file_encoding = "utf-8"
        # Лишние ключи в .env не должны ломать запуск: файл пережил переход с
        # Telegram-бота, и старые строки (TELEGRAM_BOT_TOKEN, ADMIN_IDS) в нём
        # просто игнорируются, пока их не уберут руками.
        extra = "ignore"


def _near_app(value: str) -> str:
    """Относительный путь -> абсолютный от папки программы; абсолютный как есть."""
    path = Path(value)
    return str(path) if path.is_absolute() else str(APP_DIR / path)


# Базовая директория проекта (рядом с .exe, а не внутри его распаковки)
BASE_DIR = APP_DIR

# Создаем экземпляр настроек
settings = Settings()

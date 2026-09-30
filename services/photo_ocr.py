"""Бесплатный OCR на приёме фотографий: что с фото делать до вызова ИИ.

Зачем это нужно. В рабочем чате фотографии кидают постоянно, и чаще всего
это просто «показать, что сделано»: намотанный материал, катушка, рабочее
место. Данных в таком фото нет, но если гнать его в ИИ, за него платятся
токены (картинка стоит дороже строки текста), а модель ещё и может
«додумать» по ней события смены.

Поэтому каждое фото сначала проходит через **бесплатный** OCR
(Tesseract, локально, без сети и без оплаты), и по результату решается одно
из трёх:

    нет текста          → фото не нужно, не сохраняем и в ИИ не отправляем
    текст рабочий       → в ИИ уходит РАСПОЗНАННЫЙ ТЕКСТ (не картинка)
    текст посторонний   → в отчёт не идёт, выносится отдельным списком

«Рабочий» текст — это показания счётчика, время, дроби «1362 из 2030»,
слова смены (шпули, узлы, подмена, срез, наладчик…). Решает не «красота»
текста, а наличие таких признаков: скриншот переписки и снимок журнала их
дают, фотография мотка — нет.

Почему в ИИ уходит текст, а не картинка (просил пользователь): один запрос
за смену тогда остаётся чисто текстовым и стоит копейки. **Цена решения:**
Tesseract читает рукописный журнал заметно хуже, чем зрение модели, и может
перепутать цифру. Поэтому распознанный текст помечается в промпте как
«автоматически с фото, возможны ошибки», сама фотография остаётся лежать на
диске для сверки руками, а если качество не устроит — в `.env` есть
`OCR_SEND_PHOTOS_TO_AI=true`, и картинки снова пойдут в модель без правки
кода.

Если Tesseract в системе не установлен, фото НЕ выбрасываются: они
сохраняются и отправляются в ИИ как раньше (лучше заплатить за картинку,
чем потерять показания счётчика), а сайт один раз честно предупреждает.

Модуль импортируется без pytesseract и без Pillow — их отсутствие это не
ошибка, а режим «OCR недоступен».
"""
from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

# Что делать с фотографией.
EMPTY = "empty"        # текста нет — фото можно не хранить
WORK = "work"          # есть данные смены — текст уходит в ИИ
FOREIGN = "foreign"    # текст есть, но к работе не относится — отдельно
UNKNOWN = "unknown"    # OCR недоступен/картинка не читается — решает ИИ

# Порог уверенности Tesseract: слова ниже — обычно шум на фактуре материала.
DEFAULT_MIN_CONF = 60
# Сколько цифр считать признаком «на фото что-то записано». Цифры — главный
# сигнал: счётчик, время, количество. И они не зависят от языка, поэтому
# работают даже без русского языкового пакета.
DEFAULT_MIN_DIGITS = 3
# Либо просто достаточно букв/цифр — тогда это точно надпись, а не пятно.
DEFAULT_MIN_CHARS = 12
DEFAULT_LANGS = "rus+eng"

# ── признаки того, что распознанный текст относится к смене ──────────────────
_COUNTER_RE = re.compile(r"\b\d{5,8}\b")
_TIME_RE = re.compile(r"\b(?:[01]?\d|2[0-3])\s?[:.;\-]\s?[0-5]\d\b")
_FRACTION_RE = re.compile(r"\b\d{1,4}\s*(?:из|/)\s*\d{1,4}\b", re.IGNORECASE)
_WORK_WORD_RE = re.compile(
    r"сч[её]т|об\.?\s?сч|ср\.?\s?сч|шпул|узл|навив|намот|подмен|срез|смен"
    r"|станк|станок|машин|оператор|простой|наладч|останов|пуск|обед|катуш"
    r"|петл|крюч|пропуск|ассортимент|заявк|бобин|отход|брак|нар[уя]бот",
    re.IGNORECASE)

_engine: Optional[tuple] = None      # (pytesseract, Image, ImageOps, версия)
_engine_error: str = ""
_lang_used: str = ""                 # какой язык реально сработал


def _cfg(name: str, default):
    """Настройка из .env, но без падения, если настройки недоступны.

    Модуль должен работать и в тестах, где нет pydantic_settings, — поэтому
    конфиг читается лениво и мягко.
    """
    try:
        from config.config import settings
    except Exception:                                          # noqa: BLE001
        return default
    value = getattr(settings, name, None)
    return default if value is None or value == "" else value


def enabled() -> bool:
    return bool(_cfg("ocr_enabled", True))


def send_photos_to_ai() -> bool:
    """Отправлять ли саму картинку в ИИ (по умолчанию нет — только текст)."""
    return bool(_cfg("ocr_send_photos_to_ai", False))


def _load_engine() -> Tuple[Optional[tuple], str]:
    """Подключить Tesseract один раз. Отсутствие — не ошибка, а режим работы."""
    global _engine, _engine_error
    if _engine is not None or _engine_error:
        return _engine, _engine_error
    try:
        import pytesseract
        from PIL import Image, ImageOps
    except ImportError as e:
        _engine_error = (
            "OCR не подключён (%s). Установите: py -m pip install pytesseract "
            "Pillow, плюс сам Tesseract (Windows: установщик UB Mannheim с "
            "русским языковым пакетом)." % e)
        return None, _engine_error
    cmd = _cfg("ocr_tesseract_cmd", "")
    if cmd:
        pytesseract.pytesseract.tesseract_cmd = cmd
    try:
        version = str(pytesseract.get_tesseract_version())
    except Exception as e:                                     # noqa: BLE001
        _engine_error = (
            "Tesseract не найден (%s). Поставьте программу и при необходимости "
            "укажите путь в TESSERACT_CMD (например "
            r"C:\Program Files\Tesseract-OCR\tesseract.exe)." % e)
        return None, _engine_error
    _engine = (pytesseract, Image, ImageOps, version)
    return _engine, ""


def available() -> bool:
    engine, _ = _load_engine()
    return engine is not None


def describe() -> str:
    """Строка для логов и диагностики: чем распознаём и на каком языке."""
    engine, error = _load_engine()
    if not engine:
        return error
    return "Tesseract %s, язык %s" % (engine[3], _lang_used or _cfg(
        "ocr_langs", DEFAULT_LANGS))


def reset_engine() -> None:
    """Сбросить состояние подключения (нужно тестам)."""
    global _engine, _engine_error, _lang_used
    _engine, _engine_error, _lang_used = None, "", ""


@dataclass
class PhotoCheck:
    """Что OCR увидел на фото и что с ним делать."""
    verdict: str = UNKNOWN
    text: str = ""
    digits: int = 0
    chars: int = 0
    score: int = 0
    reason: str = ""

    @property
    def has_text(self) -> bool:
        return bool(self.text.strip())

    @property
    def keep(self) -> bool:
        """Сохранять ли файл на диск."""
        return self.verdict != EMPTY

    @property
    def to_prompt(self) -> bool:
        """Уходит ли распознанный текст в запрос к ИИ."""
        return self.verdict == WORK and self.has_text

    @property
    def to_vision(self) -> bool:
        """Уходит ли сама картинка в ИИ (дороже, поэтому только когда надо)."""
        if self.verdict == UNKNOWN:
            # OCR не сработал — прочитать фото может только модель.
            return True
        if self.verdict == EMPTY:
            return False
        return send_photos_to_ai()


def _prepare(blob: bytes):
    """Открыть картинку и подготовить к распознаванию.

    Ч/б + автоконтраст: снимки журнала обычно засвечены или в тени, без этого
    Tesseract теряет цифры. Уменьшение — чтобы не тратить секунды на 12 Мп.
    """
    engine, _ = _load_engine()
    _, Image, ImageOps, _ = engine
    img = Image.open(io.BytesIO(blob))
    img.load()
    if img.mode not in ("L", "RGB"):
        img = img.convert("RGB")
    img = ImageOps.grayscale(img)
    if max(img.size) > 2000:
        img.thumbnail((2000, 2000))
    elif max(img.size) < 600:
        # Мелкий скриншот Tesseract читает плохо — увеличиваем.
        img = img.resize((img.width * 2, img.height * 2))
    return ImageOps.autocontrast(img)


def _run_tesseract(img) -> str:
    """Распознать текст, оставив только уверенные слова."""
    global _lang_used
    engine, _ = _load_engine()
    pytesseract = engine[0]
    langs = str(_cfg("ocr_langs", DEFAULT_LANGS))
    min_conf = int(_cfg("ocr_min_conf", DEFAULT_MIN_CONF))
    # Сначала как настроено, потом «eng» — на случай, если русский языковой
    # пакет не установлен: цифры счётчика и время он всё равно прочитает.
    attempts = [langs] + ([] if langs.strip() == "eng" else ["eng"])
    last_error = None
    for lang in attempts:
        try:
            data = pytesseract.image_to_data(
                img, lang=lang, output_type=pytesseract.Output.DICT)
        except Exception as e:                                 # noqa: BLE001
            # Типичное: «Failed loading language 'rus'» — языковой пакет не
            # поставили. Это не повод терять фото, пробуем следующий вариант.
            last_error = e
            continue
        _lang_used = lang
        if lang != langs:
            logger.warning("OCR: язык %s недоступен, распознаю как %s "
                           "(поставьте языковой пакет rus для точности)",
                           langs, lang)
        words = []
        for word, conf in zip(data.get("text", []), data.get("conf", [])):
            word = (word or "").strip()
            if not word:
                continue
            try:
                conf_val = float(conf)
            except (TypeError, ValueError):
                conf_val = -1.0
            if conf_val >= min_conf:
                words.append(word)
        return " ".join(words)
    raise RuntimeError(last_error or "OCR не смог распознать изображение")


def classify_text(text: str) -> PhotoCheck:
    """Решить по распознанному тексту: данные смены, посторонний или пусто.

    Отдельная функция, потому что её надо проверять тестами без картинок и
    без Tesseract.
    """
    text = (text or "").strip()
    digits = sum(ch.isdigit() for ch in text)
    chars = sum(ch.isalnum() for ch in text)
    min_digits = int(_cfg("ocr_min_digits", DEFAULT_MIN_DIGITS))
    min_chars = int(_cfg("ocr_min_chars", DEFAULT_MIN_CHARS))

    if digits < min_digits and chars < min_chars:
        return PhotoCheck(verdict=EMPTY, text=text, digits=digits, chars=chars,
                          reason="текста на фото нет (распознано %d симв.)"
                                 % chars)

    score = 0
    found: List[str] = []
    if _COUNTER_RE.search(text):
        score += 2
        found.append("показание счётчика")
    if _TIME_RE.search(text):
        score += 2
        found.append("время")
    if _FRACTION_RE.search(text):
        score += 1
        found.append("дробь «из»")
    if _WORK_WORD_RE.search(text):
        score += 2
        found.append("слова смены")

    if score >= 2:
        return PhotoCheck(verdict=WORK, text=text, digits=digits, chars=chars,
                          score=score,
                          reason="данные смены: " + ", ".join(found))
    return PhotoCheck(verdict=FOREIGN, text=text, digits=digits, chars=chars,
                      score=score,
                      reason="текст распознан, но признаков смены "
                             "(счётчик, время, слова смены) в нём нет")


def check_photo(blob: bytes) -> PhotoCheck:
    """Прогнать фото через бесплатный OCR и сказать, что с ним делать."""
    if not blob:
        return PhotoCheck(verdict=EMPTY, reason="пустой файл")
    if not enabled():
        return PhotoCheck(verdict=UNKNOWN, reason="OCR выключен (OCR_ENABLED)")
    engine, error = _load_engine()
    if not engine:
        # Важное решение: без OCR фото НЕ выбрасываем. Потерять конечное
        # показание счётчика хуже, чем заплатить за картинку в запросе.
        return PhotoCheck(verdict=UNKNOWN, reason=error)
    try:
        img = _prepare(blob)
    except Exception as e:                                     # noqa: BLE001
        logger.warning("OCR: не открылось изображение: %s", e)
        return PhotoCheck(verdict=UNKNOWN,
                          reason="изображение не читается (%s) — отдаю в ИИ "
                                 "как есть" % e)
    try:
        text = _run_tesseract(img)
    except Exception as e:                                     # noqa: BLE001
        logger.warning("OCR: распознавание не удалось: %s", e)
        return PhotoCheck(verdict=UNKNOWN,
                          reason="OCR не справился (%s) — отдаю в ИИ как есть"
                                 % e)
    return classify_text(text)


def check_photos(blobs: List[bytes]) -> List[PhotoCheck]:
    return [check_photo(b) for b in blobs or []]


if __name__ == "__main__":                                     # ручная проверка
    import sys
    print(describe())
    for path in sys.argv[1:]:
        with open(path, "rb") as f:
            check = check_photo(f.read())
        print("\n%s\n  вердикт: %s (%s)\n  текст: %s"
              % (path, check.verdict, check.reason, check.text[:300]))

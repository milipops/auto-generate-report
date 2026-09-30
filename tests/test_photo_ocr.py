#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Проверка OCR-фильтра фотографий: что уходит в ИИ, что откладывается.

Правило, которое здесь проверяется (его задал пользователь):

    фото без текста          → не сохраняем, в ИИ не отправляем
    текст рабочий            → в ИИ уходит ТЕКСТ, а не картинка
    текст не про работу      → в отчёт не идёт, выносится отдельным списком

Тест работает на настоящих картинках, которые рисуются здесь же (PIL), и на
настоящем Tesseract, если он установлен. Если OCR в системе нет, проверяется
другое, не менее важное поведение: фото НЕ теряются, а уходят в ИИ картинкой,
и бот об этом предупреждает.

Запуск: python tests/test_photo_ocr.py
"""
import io
import os
import shutil
import sys
import tempfile
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from services import photo_ocr, shift_store                     # noqa: E402

NOW = datetime(2026, 8, 20, 18, 0)
failures = []


def check(name, got, expected):
    ok = got == expected
    print(("✅ " if ok else "❌ ") + f"{name}: {got!r}"
          + ("" if ok else f" (ожидалось {expected!r})"))
    if not ok:
        failures.append(name)


def check_true(name, cond, hint=""):
    print(("✅ " if cond else "❌ ") + name + ("" if cond else f" — {hint}"))
    if not cond:
        failures.append(name)


# ─────────────────────────── рисуем тестовые фото ───────────────────────────

def _pil():
    try:
        from PIL import Image, ImageDraw
        return Image, ImageDraw
    except ImportError:
        return None, None


def _text_photo(lines, size=(900, 400)):
    """Картинка с крупным текстом — эмулирует снимок журнала или скриншот."""
    Image, ImageDraw = _pil()
    if not Image:
        return None
    img = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(img)
    y = 30
    for line in lines:
        # Штатный шрифт PIL мелкий, поэтому рисуем крупно: текст пишется на
        # маленькой картинке и увеличивается — Tesseract такое читает.
        tile = Image.new("RGB", (size[0] // 3, 40), "white")
        ImageDraw.Draw(tile).text((5, 12), line, fill="black")
        img.paste(tile.resize((size[0] - 40, 110)), (20, y))
        y += 115
        if y > size[1] - 60:
            break
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=95)
    return buf.getvalue()


def _noise_photo():
    """Фото «показать материал»: фактура без надписей."""
    Image, _ = _pil()
    if not Image:
        return None
    import random
    random.seed(7)
    img = Image.new("RGB", (600, 400))
    px = img.load()
    for x in range(img.width):
        for y in range(img.height):
            base = 90 + int(40 * ((x // 7 + y // 11) % 3))
            px[x, y] = (base, base - 10, base - 25)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=80)
    return buf.getvalue()


# ─────────────────────────────── сами проверки ──────────────────────────────

def test_classify():
    """Решение по тексту — без Tesseract, чтобы правило проверялось всегда."""
    print("=== 1. Что считаем рабочим текстом ===")
    cases = [
        ("07:00 начало работы Об.сч.: 1096327", photo_ocr.WORK,
         "время и показание счётчика"),
        ("1362 1103490", photo_ocr.WORK, "показание счётчика"),
        ("Ср. сч.: 1362 из 2030", photo_ocr.WORK, "дробь и слова смены"),
        ("12:30-13:35 подмена на 121 м", photo_ocr.WORK, "время и подмена"),
        ("навиваю шпули", photo_ocr.WORK, "слова смены без цифр"),
        ("MADE IN GERMANY 2019", photo_ocr.FOREIGN,
         "надпись есть, признаков смены нет"),
        ("Ivanov 12", photo_ocr.EMPTY, "почти ничего не распознано"),
        ("", photo_ocr.EMPTY, "пусто"),
    ]
    for text, expected, why in cases:
        check(f"«{text[:34]}» → {why}",
              photo_ocr.classify_text(text).verdict, expected)


def test_real_ocr():
    print("\n=== 2. Настоящий Tesseract на настоящих картинках ===")
    Image, _ = _pil()
    if not Image:
        print("⏭  Pillow не установлен — картинки не рисуются, пропускаю")
        return None
    print("   движок:", photo_ocr.describe())
    journal = _text_photo(["07:00 Ob.sch. 1096327", "18:50 1103490"])
    check_true("картинка с текстом нарисована", bool(journal))
    if not photo_ocr.available():
        result = photo_ocr.check_photo(journal)
        check("без OCR фото не выбрасывается", result.verdict,
              photo_ocr.UNKNOWN)
        check_true("без OCR картинка уходит в ИИ", result.to_vision,
                   "иначе показания счётчика с фото потеряются")
        print("⏭  Tesseract не установлен — распознавание не проверяю")
        return False

    result = photo_ocr.check_photo(journal)
    print("   распознано:", (result.text or "")[:120])
    check("снимок с цифрами → данные смены", result.verdict, photo_ocr.WORK)
    check_true("текст уходит в промпт", result.to_prompt)
    check_true("картинка в ИИ не уходит (дороже, просил пользователь)",
               not result.to_vision)
    check_true("цифры счётчика распознались",
               "1096327" in result.text or "1103490" in result.text,
               result.text)

    noise = photo_ocr.check_photo(_noise_photo())
    print("   на фактуре материала распознано:", (noise.text or "")[:80])
    check("фото материала → текста нет", noise.verdict, photo_ocr.EMPTY)
    check_true("такое фото не храним", not noise.keep)
    check_true("и в ИИ не отправляем", not noise.to_vision)

    foreign = photo_ocr.check_photo(
        _text_photo(["HAPPY BIRTHDAY", "GERMANY"]))
    print("   посторонний текст:", (foreign.text or "")[:80])
    check_true("надпись не про работу отложена отдельно",
               foreign.verdict in (photo_ocr.FOREIGN, photo_ocr.EMPTY),
               f"вердикт {foreign.verdict}: {foreign.text!r}")
    return True


def test_store():
    print("\n=== 3. Приём сообщения: что попало в смену ===")
    Image, _ = _pil()
    if not Image:
        print("⏭  Pillow не установлен — пропускаю")
        return
    has_ocr = photo_ocr.available()
    tmp = tempfile.mkdtemp(prefix="ocr_")
    shift_store.set_data_dir(tmp)
    try:
        accepted = shift_store.add_message(
            text="20.08.2026 станок 121, фото журнала",
            photos=[_text_photo(["07:00 Ob.sch. 1096327", "18:50 1103490"]),
                    _noise_photo()],
            author="Гудкова", now=NOW)
        check("смена определена", accepted.date, "20.08.2026")
        if has_ocr:
            check("сохранено фото", accepted.photos_saved, 1)
            check("фото без текста пропущено", accepted.photos_skipped, 1)
            files = os.listdir(os.path.join(tmp, "20.08.2026", "photos"))
            check("на диске лежит только нужное", len(files), 1)

            text = shift_store.day_text("20.08.2026")
            check_true("распознанный текст ушёл в промпт",
                       "1096327" in text or "1103490" in text, text[-300:])
            check_true("в промпте есть пометка про OCR",
                       "распознан с фотографии" in text, text[-300:])
            check("картинок в запрос к ИИ не уходит",
                  len(shift_store.day_photos("20.08.2026")), 0)
        else:
            check("без OCR фото сохраняются все", accepted.photos_saved, 2)
            check("без OCR ничего не выбрасывается", accepted.photos_skipped, 0)
            check("без OCR картинки уходят в ИИ",
                  len(shift_store.day_photos("20.08.2026")), 2)
            check_true("бот предупреждает про OCR", bool(accepted.ocr_warning),
                       "иначе пользователь не узнает, что фильтра нет")

        print("\n=== 4. Посторонний текст выносится отдельно ===")
        # Вердикт задаём напрямую: важно проверить не распознавание, а то, что
        # такой текст не уходит в отчёт и виден человеку списком.
        day = shift_store.load_day("20.08.2026")
        day["messages"].append({
            "index": len(day["messages"]) + 1, "author": "Камбаров",
            "text": "", "photos": ["msg002_1.jpg"],
            "photos_meta": [{"name": "msg002_1.jpg", "verdict": "foreign",
                             "reason": "признаков смены нет",
                             "ocr_text": "HAPPY BIRTHDAY GERMANY 2019",
                             "to_prompt": False, "to_vision": False}],
            "machine": None, "thread": None,
            "received_at": NOW.isoformat(timespec="seconds")})
        shift_store._save_day(day)
        foreign = shift_store.day_foreign_photos("20.08.2026")
        check("постороннее фото в отдельном списке", len(foreign), 1)
        check_true("текст сохранён для человека",
                   "HAPPY BIRTHDAY" in foreign[0][2], str(foreign))
        check_true("в запрос к ИИ он не попал",
                   "HAPPY BIRTHDAY" not in shift_store.day_text("20.08.2026"))
        check_true("картинкой тоже не уходит",
                   len(shift_store.day_photos("20.08.2026"))
                   == (0 if has_ocr else 2))

        print("\n=== 5. Старые смены без OCR читаются по-прежнему ===")
        old = {"date": "19.08.2026", "updated_at": None, "messages": [{
            "index": 1, "author": "мастер", "text": "фото журнала",
            "photos": ["msg001_1.jpg"], "machine": None, "thread": None,
            "received_at": NOW.isoformat(timespec="seconds")}]}
        os.makedirs(os.path.join(tmp, "19.08.2026", "photos"), exist_ok=True)
        with open(os.path.join(tmp, "19.08.2026", "photos", "msg001_1.jpg"),
                  "wb") as f:
            f.write(_noise_photo())
        shift_store._save_day(old)
        check("фото из старой смены уходит в ИИ картинкой",
              len(shift_store.day_photos("19.08.2026")), 1)
    finally:
        shift_store.set_data_dir(shift_store._DEFAULT_DIR)
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    test_classify()
    test_real_ocr()
    test_store()
    print("\n" + ("❌ ПРОВАЛЫ: " + ", ".join(failures) if failures
                  else "✅ Фото фильтруются как договорились: без текста — в "
                       "мусор, рабочий текст — в ИИ, посторонний — отдельно"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

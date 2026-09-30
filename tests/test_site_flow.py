"""Сценарий сайта целиком: «прислали данные → принято → отчёт за дату → файлы».

Этот тест заменил `test_bot_flow.py`: проверялось ровно то же самое, только
через обработчики Telegram-бота, а бот удалён и вместо него локальный сайт.
Именно здесь ловится ошибка, из-за которой переделывали весь проект: сервисы
считали правильно, а человек получал «Отчёт принят и сохранён. ID записи» и ни
отчёта, ни Excel.

Тест гоняет настоящие слои сайта (`webapp/api.py` и фоновые `webapp/jobs.py`) на
настоящих фикстурах переписки за 20.08.2026, а ИИ подменён заранее заданной
структурой — сети тут нет. Проверяется:

* любое сообщение (текст или фото с подписью) принимается без команд и без ИИ,
  данные накапливаются;
* сборка за дату делает РОВНО ОДИН вызов ИИ по всей переписке;
* на экран уходит КОРОТКАЯ сводка (одна строка на станок), а полный текст по
  каждому станку живёт в двух файлах: Excel по бланку смены и выгрузка для 1С;
* готовые файлы остаются в архиве: раздел «Готовые файлы» отдаёт те же файлы
  БЕЗ обращения к ИИ, а за дату без файлов говорит об этом и предлагает собрать;
* сборка за выбранное число не подмешивает соседний день;
* без счётчиков ложный «недостаток 12 ч» не появляется, а честно пишется, что
  баланс не считается;
* если ИИ выключен — сайт не молчит, а отдаёт упрощённую сводку;
* команд бота на сайте нет и в данные смены они не попадают.

Запуск: python tests/test_site_flow.py
"""
import contextlib
import json
import logging
import os
import shutil
import sys
import tempfile
import time
import types
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
HERE = os.path.dirname(os.path.abspath(__file__))

from database.database import Database                          # noqa: E402
from services import photo_ocr, report_archive, shift_store      # noqa: E402
from webapp import api, jobs                                    # noqa: E402

DATE = "20.08.2026"
PREV = "19.08.2026"
XLSX = "Отчет производства за смену 20.08.2026.xlsx"

with open(os.path.join(HERE, "day_20_08_2026.txt"), encoding="utf-8") as _f:
    CHUNKS = [c.strip() for c in _f.read().split("===") if c.strip()]

with open(os.path.join(HERE, "day_20_08_2026.json"), encoding="utf-8") as _f:
    STRUCTURED = json.load(_f)


def _fake_photo_check(blob: bytes) -> photo_ocr.PhotoCheck:
    """OCR в этом тесте не проверяем: картинка здесь — два байта.

    Поведение настоящего Tesseract зависит от того, установлен ли он на машине,
    а сценарию важно только «фото дошло до ИИ картинкой».
    """
    if not blob:
        return photo_ocr.PhotoCheck(verdict=photo_ocr.EMPTY, reason="пусто")
    return photo_ocr.PhotoCheck(verdict=photo_ocr.UNKNOWN,
                                reason="OCR в тесте отключён")


@contextlib.contextmanager
def sandbox(ai_enabled: bool = True, who: str = "Юлия Черныш"):
    """Временные каталоги, своя база и ИИ под счётчиком вызовов.

    Рабочие данные смен трогать нельзя, поэтому каталоги накопителя, архива и
    файл базы подменяются, а после теста возвращаются как было. Наружу
    отдаётся список `calls` — сколько раз и с чем сайт обращался к ИИ.
    """
    keep = (shift_store.get_data_dir(), report_archive.get_data_dir(),
            jobs.get_export_dir(), api.db, jobs.db, jobs.settings,
            api.settings, jobs._JOBS.copy(), photo_ocr.check_photo)
    tmp = tempfile.mkdtemp(prefix="siteflow_")
    db = Database(f"sqlite:///{(Path(tmp) / 'web.db').as_posix()}")
    db.create_tables()
    shift_store.set_data_dir(os.path.join(tmp, "shifts"))
    report_archive.set_data_dir(os.path.join(tmp, "reports"))
    jobs.set_export_dir(os.path.join(tmp, "exports"))
    api.db = jobs.db = db
    jobs._JOBS.clear()
    jobs.settings = api.settings = types.SimpleNamespace(
        ai_enabled=ai_enabled, ocr_enabled=True,
        ai_model="test-model", ai_api_format="openai")
    photo_ocr.check_photo = _fake_photo_check

    calls = []

    def fake_extract(text, images=None, progress=None):
        calls.append((text, list(images or [])))
        return json.loads(json.dumps(STRUCTURED))       # копия, как из сети

    real_extract = jobs.extract_structured
    jobs.extract_structured = fake_extract

    # Время «заморожено» на 20.08.2026: сообщения без даты в тексте попадают в
    # ТЕКУЩУЮ смену, и без этого переписка за 20-e число расползлась бы по
    # двум дням — тест проходил бы только 20 августа.
    real_dt = shift_store.datetime

    class FrozenDT(real_dt):
        @classmethod
        def now(cls, tz=None):
            return real_dt(2026, 8, 20, 12, 0, 0)

    shift_store.datetime = FrozenDT
    try:
        yield types.SimpleNamespace(dir=tmp, calls=calls, who=who)
    finally:
        shift_store.datetime = real_dt
        jobs.extract_structured = real_extract
        shift_store.set_data_dir(keep[0])
        report_archive.set_data_dir(keep[1])
        jobs.set_export_dir(keep[2])
        api.db, jobs.db = keep[3], keep[4]
        jobs.settings, api.settings = keep[5], keep[6]
        jobs._JOBS.clear()
        jobs._JOBS.update(keep[7])
        photo_ocr.check_photo = keep[8]
        db.engine.dispose()
        shutil.rmtree(tmp, ignore_errors=True)


def wait_job(job_id, timeout=30.0):
    """Дождаться фоной сборки: она живёт в другом потоке."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        cur = api.job(job_id)
        if cur and cur["state"] != "running":
            return cur
        time.sleep(0.05)
    raise AssertionError(f"сборка {job_id} не завершилась за {timeout} с")


def send_shift(sb):
    """Вся переписка смены обычными сообщениями — как их присылают в течение дня."""
    for chunk in CHUNKS:
        api.add_text(text=chunk, who=sb.who)


def build(sb, date=DATE):
    return wait_job(api.build(date=date, who=sb.who)["id"])


# ─────────────────────────── приём данных: без команд, без ИИ ───────────────────────────

def test_intake_accepts_messages_and_photos_without_ai():
    with sandbox() as sb:
        first = api.add_text(text=CHUNKS[0], who=sb.who)
        joined = "\n".join(first["lines"])
        assert "Данные приняты" in joined, joined
        assert DATE in joined, "дата смены не взялась из заголовка переписки"
        assert "показания счётчика" in joined, joined
        # Старый ответ обработчика, из-за которого переделывали бота.
        assert "ID записи" not in joined, joined
        # Команд вызывать отчёт на сайте не нужны — есть разделы страницы.
        assert "/hours" not in joined and "/report" not in joined, joined
        assert sb.calls == [], "на приёме данных ИИ не нужен"

        for chunk in CHUNKS[1:]:
            api.add_text(text=chunk, who=sb.who)

        photo = api.add_photo(name="zhurnal.jpg", blob=b"\xff\xd8\xff\xd9",
                              who=sb.who, caption="Фото журнала по 251")
        assert "Данные приняты" in "\n".join(photo["lines"])
        assert photo["shift"]["photos"] == 1, photo["lines"]

        msgs, photos = shift_store.day_stats(DATE)
        assert (msgs, photos) == (len(CHUNKS) + 1, 1), (msgs, photos)
        assert shift_store.day_machines(DATE) == ["321", "251"], \
            shift_store.day_machines(DATE)
        assert sb.calls == [], "и приём фото тоже бесплатный"

        # Журнал видит, ЧЬЁ сообщение попало в смену.
        authors = {r["who"] for r in api.journal()["uploads"]}
        assert sb.who in authors, authors


def test_state_offers_the_shift_and_its_machines():
    with sandbox() as sb:
        send_shift(sb)
        state = api.state()
        assert state["latest"] == DATE, state["latest"]
        day = state["shifts"][0]
        assert day["date"] == DATE and day["messages"] == len(CHUNKS)
        assert day["machines"] == ["321"], day   # «251» назван только в подписи к фото
        assert state["running"] == []


def test_machine_number_not_named_is_reported():
    """Без номера станка в переписке — предупреждение, а не молчаливая догадка.

    В реальной переписке за 20.08 номер основной машины не назван ни разу
    (только «321» в одном из сообщений), и бот раньше решал, что это 251.
    """
    with sandbox() as sb:
        api.add_text(text=CHUNKS[0], who=sb.who)
        assert shift_store.day_machines(DATE) == []
        done = build(sb)
        assert any("не указан номер станка" in line for line in done["lines"]), \
            done["lines"]


# ───────────────────────────── отчёт за дату ─────────────────────────────

def test_day_report_uses_one_ai_call_for_the_whole_correspondence():
    with sandbox() as sb:
        send_shift(sb)
        api.add_photo(name="zhurnal.jpg", blob=b"\xff\xd8\xff\xd9",
                      who=sb.who, caption="Фото журнала по 251")
        done = build(sb)

        assert done["state"] == "done", done["lines"]
        assert len(sb.calls) == 1, [len(t) for t, _ in sb.calls]
        text, images = sb.calls[0]
        assert "230695" in text and "Запускайте 321" in text and "65 катушек" in text
        assert len(images) == 1, len(images)
        assert "received_at" not in text, "в промпт утекло время получения"

        lines = "\n".join(done["lines"])
        assert "№251" in lines and "№321" in lines, lines[:400]
        assert "Excel" in lines and "1С" in lines, lines[-300:]
        # Полного текста по станку на экране нет: именно из-за него бот падал.
        assert "Технические остановки" not in lines
        assert "Баланс времени" not in lines
        # Без счётчиков не «недостаток 12 ч», а честное «не считаю».
        assert "не считаю" in lines.lower(), lines[:400]
        assert "12,00 ч" not in lines, "появился ложный недостаток"


def test_day_report_produces_excel_and_onec_in_archive():
    with sandbox() as sb:
        send_shift(sb)
        done = build(sb)
        names = [f["name"] for f in done["files"]]
        assert names[0] == XLSX, names
        assert names[1].endswith(".json"), names
        assert len(done["files"]) == 2, names

        for entry in done["files"]:
            assert entry["size"] > 500, entry
            got = api.download(DATE, entry["name"])
            assert os.path.isfile(got["path"])
        with open(os.path.join(report_archive.get_data_dir(), DATE, XLSX),
                  "rb") as fh:
            assert fh.read(2) == b"PK", "Excel не похож на xlsx"

        # Данные за смену не стёрлись: можно доприслать и собрать заново.
        assert shift_store.day_stats(DATE)[0] == len(CHUNKS)
        report = api.journal()["reports"][0]
        assert (report["ok"], report["date"]) == (True, DATE), report
        assert sorted(report["files"]) == sorted(names), report["files"]


def test_rebuild_replaces_files_and_costs_ai_again():
    """Повтор — это осознанный второй запрос: старые файлы заменяются новыми."""
    with sandbox() as sb:
        send_shift(sb)
        build(sb)
        assert len(sb.calls) == 1
        done = build(sb)
        assert len(sb.calls) == 2
        assert len(done["files"]) == 2
        assert len(report_archive.archived_day(DATE).files) == 2


def test_report_without_ai_is_not_silent():
    with sandbox(ai_enabled=False) as sb:
        send_shift(sb)
        done = build(sb)
        lines = "\n".join(done["lines"])
        assert "AI_ENABLED" in lines, lines[:300]
        assert done["files"] == [], "без ИИ полного отчёта и файлов нет"
        assert len(done["lines"]) >= 3, lines[:300]
        assert "упрощённ" in lines.lower(), lines[:300]


def test_empty_shift_explains_what_is_missing():
    with sandbox() as sb:
        try:
            build(sb)
            raise AssertionError("отчёт собрался из пустоты")
        except api.ApiError as e:
            assert "не накоплено" in e.message.lower(), e.message

        # Данные были, но стали негодными после очистки — та же подсказка.
        send_shift(sb)
        res = api.reset(DATE)
        assert res["removed"] == len(CHUNKS), res
        assert shift_store.day_stats(DATE) == (0, 0)
        assert "очищена" in res["lines"][0]
        try:
            api.build(date=DATE, who=sb.who)
            raise AssertionError("после очистки отчёт собрался")
        except api.ApiError as e:
            assert "не накоплено" in e.message.lower(), e.message


# ───────────────────────────── выбор даты и соседи ─────────────────────────────

def test_build_for_chosen_date_does_not_mix_neighbouring_shifts():
    with sandbox() as sb:
        api.add_text(text=f"{PREV} станок 98 Об.сч. 21136950", who=sb.who)
        send_shift(sb)

        shifts = [d["date"] for d in api.state()["shifts"]]
        assert shifts == [DATE, PREV], shifts   # свежие — первыми, как кнопки бота

        done = build(sb, date=PREV)
        assert len(sb.calls) == 1, sb.calls
        text, _ = sb.calls[0]
        assert "21136950" in text, text[:300]
        assert "Запускайте 321" not in text, "смены смешались"
        assert f"за {PREV}" in done["lines"][0], done["lines"][0]
        assert done["files"][0]["name"] == \
            "Отчет производства за смену 19.08.2026.xlsx", done["files"]


# ───────────────────────────── готовые файлы без ИИ ─────────────────────────────

def test_finished_files_are_history_and_cost_nothing():
    """Раздел «Готовые файлы»: те же файлы, второй раз не платим."""
    with sandbox() as sb:
        send_shift(sb)
        built = build(sb)
        names = [f["name"] for f in built["files"]]
        assert len(names) == 2, names

        days = api.archive()
        assert [d["date"] for d in days] == [DATE], days
        assert days[0]["kinds"] == ["Excel", "1С"], days[0]["kinds"]
        assert sorted(d["name"] for d in days[0]["files"]) == sorted(names)

        calls_before = len(sb.calls)
        got = api.archive_day(DATE)
        assert got["has_files"], got
        assert sorted(f["name"] for f in got["files"]) == sorted(names), got
        assert "ИИ не запрашивался" in got["lines"][0], got["lines"]
        assert len(sb.calls) == calls_before, "за выдачу файлов снова платим"


def test_files_for_date_without_build_offer_to_build():
    with sandbox() as sb:
        # Дата с накопленными данными, но без сборки — предложить собрать.
        api.add_text(text=f"{PREV} станок 98 Об.сч. 21136950", who=sb.who)
        got = api.archive_day(PREV)
        assert not got["has_files"] and got["can_build"], got
        assert "Собрать сейчас?" in got["lines"][0], got["lines"]
        assert "1 сообщ." in got["lines"][0], got["lines"]

        # Пустая дата — честно сказать, что ни файлов, ни данных нет.
        blank = api.archive_day("01.01.2026")
        assert not blank["has_files"] and not blank["can_build"], blank
        assert "ни готовых файлов, ни накопленных данных" in blank["lines"][0]
        assert sb.calls == [], "подсказка не должна вызывать ИИ"


# ─────────────────────────── сводный отчёт и команды бота ───────────────────────────

def test_period_report_one_excel_for_all_shifts():
    with sandbox() as sb:
        api.add_text(text=f"{PREV} станок 98 Об.сч. 21136950", who=sb.who)
        send_shift(sb)
        done = wait_job(api.period(who=sb.who)["id"])
        assert done["state"] == "done", done["lines"]
        # По одному запросу на смену: их в этом тесте две.
        assert len(sb.calls) == 2, len(sb.calls)
        assert done["files"], done["lines"]
        name = done["files"][0]["name"]
        assert name.startswith("Отчет производства 19.08-20.08"), name
        assert os.path.isfile(os.path.join(jobs.get_export_dir(), name))


def test_bot_commands_are_not_data():
    with sandbox() as sb:
        for cmd in ("/day", "/start", "/help"):
            try:
                api.add_text(text=cmd, who=sb.who)
                raise AssertionError(f"{cmd} попал в данные смены")
            except api.ApiError as e:
                assert "Команды" in e.message, e.message
        assert shift_store.day_stats(shift_store.shift_date_for())[0] == 0


if __name__ == "__main__":
    logging.disable(logging.ERROR)
    names = sorted(n for n in globals() if n.startswith("test_"))
    failed = 0
    for name in names:
        try:
            globals()[name]()
            print(f"✅ {name}")
        except Exception as exc:                 # noqa: BLE001 - тест-раннер
            failed += 1
            import traceback
            print(f"❌ {name}: {type(exc).__name__}: {exc}")
            traceback.print_exc()
    print(f"ran {len(names)} failed {failed}")
    sys.exit(1 if failed else 0)

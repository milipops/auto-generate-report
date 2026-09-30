"""Сайт вместо Telegram-бота: те же функции, другой интерфейс.

Проверяем `webapp/api.py` вызовами напрямую (без HTTP) и один раз через
настоящий сервер — чтобы было видно, что приём файла телом запроса, кириллица в
JSON и в именах файлов и ошибки работают так, как их читает страница.

Что для нас важно:

* приём данных остаётся «бесплатным»: файл раскладывается по станкам, ИИ не
  вызывается, а повтор того же файла не добавляет сообщений (иначе отчёт
  посчитал бы каждое обсуждение дважды);
* сборка отчёта уходит в фон и возвращается снимком задачи: HTTP-запрос не может
  висеть минуты, пока думает модель, и второй клик не должен значить вторую
  оплату запроса;
* журнал базы видит, КТО прислал данные и чем кончилась сборка;
* наружу не уходит ничего кроме готовых файлов за выбранную дату: имя
  чищается, чужой путь — 404;
* справочка не обещает того, чего на сайте больше нет (кнопок Telegram).

Запуск: python tests/test_webapp.py (или через tests/_run_all.py).
"""
import contextlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import types
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from database.database import Database                          # noqa: E402
from services import day_pipeline, report_archive, shift_store   # noqa: E402
from webapp import api, jobs, texts                              # noqa: E402
from webapp import server as server_mod                          # noqa: E402

DATE = "22.09.2026"
XLSX = "Отчет производства за смену 22.09.2026.xlsx"

CSV = """Дата и Время сообщения;Тип;Имя автора;Текст сообщения
2026-09-22 06:36:11;Сообщение;;151 машина(Арташина) - информация за смену 22.09.2026г. 06:36
2026-09-22 09:18:05;Ответ;Татьяна Арташина;09:15 провязы вывязаны. Об. Сч.: 790782 Ср. Сч. 15070 из 14800 Срез 1 из 1 Подмена 4 из 4 09:18
2026-09-22 06:36:40;Сообщение;;251 машина(Черныш) - информация за смену 22.09.2026г. 06:36
2026-09-22 06:47:30;Ответ;Юлия Черныш;Общ.сч. 300125 Ср.сч. - 1036 узлов из 1360 Срез - 3 из 3 Подмена - 1 из 1 06:47
"""


@contextlib.contextmanager
def sandbox(ai_enabled: bool = False):
    """Временные каталоги и своя база на время теста, потом всё как было.

    Тест не должен ни трогать рабочие данные смен, ни писать в боевую базу:
    каталоги накопителя, архива и файл базы подменяются и возвращаются обратно.
    """
    keep = (shift_store.get_data_dir(), report_archive.get_data_dir(),
            jobs.get_export_dir(), api.db, jobs.db, jobs.settings,
            api.settings)
    tmp = tempfile.mkdtemp(prefix="webapp_")
    db = Database(f"sqlite:///{(Path(tmp) / 'web.db').as_posix()}")
    db.create_tables()
    shift_store.set_data_dir(os.path.join(tmp, "shifts"))
    report_archive.set_data_dir(os.path.join(tmp, "reports"))
    jobs.set_export_dir(os.path.join(tmp, "exports"))
    api.db = jobs.db = db
    # ИИ в тесте выключен: сборка обязана оставаться быстрой и предсказуемой,
    # а упрощённый отчёт без модели — честным. Подменяем настройки и в слое
    # сборки, и в состоянии страницы — иначе страница показала бы ключ ИИ из
    # боевого .env.
    jobs.settings = api.settings = types.SimpleNamespace(
        ai_enabled=ai_enabled, ocr_enabled=True,
        ai_model="test-model", ai_api_format="openai")

    # Время «заморожено» на 22.09.2026: сообщение без даты в тексте попадает в
    # ТЕКУЩУЮ смену, и без этого фикстура за 22-e число расползлась бы по двум
    # дням — тест проходил бы только 22 сентября.
    real_dt = shift_store.datetime

    class FrozenDT(real_dt):
        @classmethod
        def now(cls, tz=None):
            return real_dt(2026, 9, 22, 12, 0, 0)

    shift_store.datetime = FrozenDT
    try:
        yield tmp
    finally:
        shift_store.datetime = real_dt
        shift_store.set_data_dir(keep[0])
        report_archive.set_data_dir(keep[1])
        jobs.set_export_dir(keep[2])
        api.db, jobs.db = keep[3], keep[4]
        jobs.settings, api.settings = keep[5], keep[6]
        db.engine.dispose()
        shutil.rmtree(tmp, ignore_errors=True)


def _wait_job(job_id: str, timeout: float = 30.0) -> dict:
    """Дождаться фоной сборки: она живёт в другом потоке."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        cur = api.job(job_id)
        if cur and cur["state"] != "running":
            return cur
        time.sleep(0.05)
    raise AssertionError(f"сборка {job_id} не завершилась за {timeout} с")


# ───────────────────────────── приём данных ─────────────────────────────
def test_upload_file_splits_by_machine_and_journals():
    """Файл раскладывается по станкам, и загрузка видна в журнале с автором."""
    with sandbox():
        res = api.upload_file(name="22.09.csv", blob=CSV.encode("utf-8"),
                              who="Диспетчер")
        joined = "\n".join(res["lines"])
        assert res["date"] == DATE
        assert "Разложил по станкам: №151, №251" in joined, joined
        row = api.journal()["uploads"][0]
        assert row["kind"] == "file" and row["who"] == "Диспетчер"
        assert row["machines"] == ["151", "251"]
        assert row["messages"] == 2 and not row["duplicate"]
        # Показания каждого раздела остались при своём станке.
        by_machine = {m["machine"]: m["text"]
                      for m in api.day_detail(DATE)["messages"]}
        assert "790782" in by_machine["151"] and "790782" not in by_machine["251"]
        assert "300125" in by_machine["251"]


def test_same_file_twice_does_not_grow_the_shift():
    """Повтор файла — не данные: иначе модель удвоит количества в отчёте."""
    with sandbox():
        first = api.upload_file(name="a.csv", blob=CSV.encode("utf-8"), who="Я")
        # Другие байты и другое имя, но то же содержимое: дубликат ловится по
        # тексту, а не по имени файла.
        second = api.upload_file(name="b.csv",
                                 blob=(CSV + "\r\n").encode("utf-8"), who="Я")
        assert "такой же" in "\n".join(second["lines"])
        assert second["shift"]["messages"] == first["shift"]["messages"]
        dup = api.journal()["uploads"][0]
        assert dup["duplicate"] and dup["duplicate_of"] == "a.csv"
        # Исправленный файл — уже не повтор, но и не замена: его разделы
        # добавляются к смене. Поэтому ответ и просит сначала очистить день.
        changed = (CSV + "\n" + "2026-09-22 20:00:00;Ответ;Татьяна;Конец смены "
                                 "20:00 Об. Сч.: 812340 20:00\n").encode("utf-8")
        fixed = api.upload_file(name="a.csv", blob=changed, who="Я")
        assert "такой же" not in "\n".join(fixed["lines"])
        assert fixed["shift"]["messages"] == first["shift"]["messages"] + 2
        api.reset(DATE)
        after_reset = api.upload_file(name="a.csv", blob=changed, who="Я")
        assert after_reset["shift"]["messages"] == 2
        # Новые показания остались в смене, а не потерялись при очистке.
        assert "812340" in json.dumps(api.day_detail(DATE), ensure_ascii=False)


def test_add_text_is_accepted_without_ai():
    with sandbox():
        res = api.add_text(text="08:46 начало работы Об.сч.: 865637\n"
                                "Конец смены 20:00 Об.сч.: 902110",
                           who="Мастер", machine="151")
        assert "Данные приняты" in res["lines"][0]
        assert res["shift"]["messages"] == 1
        assert [r["kind"] for r in api.journal()["uploads"]] == ["text"]
        try:
            api.add_text(text="   ", who="Мастер")
            raise AssertionError("пустое сообщение принято")
        except api.ApiError as e:
            assert "Пустое" in e.message
        # Команды бота на сайте не нужны и в данные смены не попадут.
        try:
            api.add_text(text="/day 22.09.2026", who="Мастер")
            raise AssertionError("команда прошла как данные")
        except api.ApiError as e:
            assert "Команды" in e.message


def test_photo_goes_through_the_same_intake():
    with sandbox():
        res = api.add_photo(name="zhurnal.jpg", blob=b"\xff\xd8\xff\xe0\x00\x10",
                            who="Юлия", caption="151 машина, вечер")
        assert "Данные приняты" in res["lines"][0]
        row = api.journal()["uploads"][0]
        assert (row["kind"], row["source"]) == ("photo", "zhurnal.jpg")
        # Подпись к фото — часть данных: станок часто написан именно в ней.
        assert "151 машина, вечер" in \
            json.dumps(api.day_detail(res["date"]), ensure_ascii=False)


def test_unreadable_file_gives_readable_error_and_stays_in_journal():
    with sandbox():
        try:
            api.upload_file(name="картинка.bin", blob=b"\x00\x01\x02", who="Я")
            raise AssertionError("битый файл принят")
        except api.ApiError as e:
            assert e.status == 415 and "не текстовый документ" in e.message
        row = api.journal()["uploads"][0]
        assert row["error"], "ошибка чтения не попала в журнал"
        assert shift_store.day_stats(DATE)[0] == 0, "смена засорилась мусором"
        try:
            api.upload_file(name="пусто.txt", blob=b"", who="Я")
            raise AssertionError("пустой файл принят")
        except api.ApiError as e:
            assert "пуст" in e.message.lower()


# ───────────────────────────── сборка отчёта ─────────────────────────────
def test_build_runs_as_background_job_and_is_journaled():
    """Сборка не блокирует вызов: сразу id, результат — опросом задачи."""
    with sandbox():
        api.upload_file(name="a.csv", blob=CSV.encode("utf-8"), who="Сменщик")
        job = api.build(date=DATE, who="Сменщик")
        done = _wait_job(job["id"])
        assert done["state"] == "done"
        assert any("ИИ выключен" in line for line in done["lines"]), done["lines"]
        # Без ИИ полного отчёта нет — и файлов нет, и в журнале это видно.
        assert done["files"] == []
        report = api.journal()["reports"][0]
        assert (report["who"], report["state"], report["ok"]) == \
               ("Сменщик", "done", False)
        assert "ИИ выключен" in report["note"]


def test_duplicate_build_of_the_same_date_is_not_started_twice():
    """Два клика «собрать» за ту же дату — одна задача, а не два запроса ИИ."""
    started = []

    def slow_build(date, extract=None, ai_enabled=None, photo_limit=12,
                   progress=None):
        started.append(date)
        time.sleep(0.8)
        return types.SimpleNamespace(ok=False, reports=[], simple=[], note="",
                                     foreign_photos=[], carry_notes=[],
                                     ai_warnings=[], remains=None, date=date)

    real = day_pipeline.build_day
    day_pipeline.build_day = slow_build
    try:
        with sandbox():
            api.upload_file(name="a.csv", blob=CSV.encode("utf-8"), who="Я")
            first = api.build(date=DATE, who="Я")
            second = api.build(date=DATE, who="Я")
            assert first["id"] == second["id"], "запущена вторая сборка даты"
            _wait_job(first["id"])
            assert started == [DATE], started
    finally:
        day_pipeline.build_day = real


def test_failing_build_is_reported_not_swallowed():
    real = day_pipeline.build_day
    day_pipeline.build_day = lambda *a, **kw: (_ for _ in ()).throw(
        RuntimeError("шлюз ИИ не отвечает"))
    try:
        with sandbox():
            api.upload_file(name="a.csv", blob=CSV.encode("utf-8"), who="Я")
            done = _wait_job(api.build(date=DATE, who="Я")["id"])
            assert done["state"] == "error"
            assert any("шлюз ИИ не отвечает" in line for line in done["lines"])
            report = api.journal()["reports"][0]
            assert report["state"] == "error" and not report["ok"]
    finally:
        day_pipeline.build_day = real


def _saved_raw(machine_num: str = "151") -> dict:
    """Один станок из настоящего разбора ИИ за 22.09.2026 — форма та же, что
    модель отдаёт, поэтому пересборка идёт по живому пути, а не по игрушечному."""
    full = json.load(open(os.path.join(ROOT, "tests", "ai_raw_22_09_2026.json"),
                          encoding="utf-8"))
    machines = [m for m in full["machines"]
                if str(m.get("machine")) == machine_num]
    assert machines, f"в фикстуре нет станка {machine_num}"
    return {"date": DATE, "machines": machines, "warnings": []}


def test_rebuild_without_ai_recovers_the_day_and_keeps_the_raw():
    """«Пересобрать без ИИ»: файлы есть, запроса к модели нет, ai_raw.json цел.

    Бесплатный путь появился потому, что правила расчёта правятся чаще, чем
    приходит смена: переписку модель разбирала за деньги, а арифметику считает
    код. Отчёт обязан пересобираться без нового запроса — и без перезаписи
    оплаченного разбора выводом текущего кода.
    """
    def no_ai(*a, **kw):
        raise AssertionError("пересборка запросила ИИ")

    real = day_pipeline._default_extract
    day_pipeline._default_extract = no_ai
    try:
        with sandbox():
            api.upload_file(name="a.csv", blob=CSV.encode("utf-8"), who="Я")
            day_pipeline._save_ai_raw(DATE, _saved_raw())
            path = day_pipeline.ai_raw_path(DATE)
            before = open(path, "rb").read()
            job = api.rebuild(date=DATE, who="Я")
            done = _wait_job(job["id"])
            assert done["state"] == "done", done["lines"]
            assert open(path, "rb").read() == before, \
                "пересборка перезаписала сохранённый разбор ИИ"
            kinds = {f["kind"] for f in done["files"]}
            assert {"Excel", "1С"} <= kinds, done["files"]
            assert any("не запрашивался" in line for line in done["lines"]), \
                done["lines"]
            report = api.journal()["reports"][0]
            assert (report["state"], report["ok"]) == ("done", True), report
    finally:
        day_pipeline._default_extract = real


def test_rebuild_without_saved_raw_says_so_instead_of_paying():
    with sandbox():
        api.upload_file(name="a.csv", blob=CSV.encode("utf-8"), who="Я")
        try:
            api.rebuild(date=DATE, who="Я")
            raise AssertionError("пересборка без разбора ИИ запустилась")
        except api.ApiError as e:
            assert "нет сохранённого разбора" in e.message, e.message
        assert api.state()["running"] == [], "задача всё равно завелась"


def test_period_build_with_no_ai_ends_with_explanation():
    with sandbox():
        api.upload_file(name="a.csv", blob=CSV.encode("utf-8"), who="Я")
        done = _wait_job(api.period(who="Я")["id"])
        assert done["state"] == "error"
        assert any("Сводный" in line or "смен" in line for line in done["lines"])
        assert api.journal()["reports"][0]["scope"] == "period"


def test_build_reports_progress_so_the_page_is_not_silent():
    """Пока ИИ думает минуты, у задачи видна текущая стадия.

    Без этого человек видит «Собираю…» и не понимает, работает ли что-то:
    решает, что зависло, и нажимает «собрать» ещё раз — то есть платит за
    вторую сборку той же смены.
    """
    def stub(date, extract=None, ai_enabled=None, photo_limit=12,
             progress=None):
        assert progress is not None, "сборка не получила канал прогресса"
        progress("станок №151 — 1 из 2")
        progress("ждём ответ ИИ (модель test-model)")
        return types.SimpleNamespace(ok=False, reports=[], simple=[], note="",
                                     foreign_photos=[], carry_notes=[],
                                     ai_warnings=[], remains=None, date=date)

    real = day_pipeline.build_day
    day_pipeline.build_day = stub
    try:
        with sandbox():
            api.upload_file(name="a.csv", blob=CSV.encode("utf-8"), who="Я")
            done = _wait_job(api.build(date=DATE, who="Я")["id"])
            assert done["progress"] == "ждём ответ ИИ (модель test-model)", done
    finally:
        day_pipeline.build_day = real


def test_only_unparsed_stations_ask_for_a_rebuild():
    """«Соберите отчёт заново» — только когда ИИ правда не отдал станок.

    В ai_warnings попадают и совсем другие вещи: журнал счётчиков не записался
    (диск), спорная фамилия оператора, отброшенный станок, которого нет в
    переписке. Прежняя шапка «Разобрано не всё… соберите отчёт заново»
    распространяла просьбу на все они, и человек из-за незаписанного файла
    запускал 10 платных вызовов ИИ, которые ничего бы не исправили.
    """
    def stub(date, extract=None, ai_enabled=None, photo_limit=12,
             progress=None):
        return types.SimpleNamespace(
            ok=True, reports=[], simple=[], note="", foreign_photos=[],
            carry_notes=[], has_remains=False, remains=None, date=date,
            ai_warnings=["журнал счётчиков за 22.09.2026 не записан — файл "
                         "недоступен на запись",
                         "№111: оператор «Дроздова» в переписке по этому "
                         "станку не упоминается ни разу"],
            ai_parse_warnings=["станок №151: ИИ не вернул данных, хотя станок "
                               "есть в переписке"])

    real = day_pipeline.build_day
    day_pipeline.build_day = stub
    try:
        with sandbox():
            api.upload_file(name="a.csv", blob=CSV.encode("utf-8"), who="Я")
            done = _wait_job(api.build(date=DATE, who="Я")["id"])
            text = "\n".join(done["lines"])
            assert "Разобрано не всё" in text and "станок №151" in text, text
            assert "Замечания к данным смены" in text, text
            assert "журнал счётчиков" in text and "Дроздова" in text, text
            # Просьба пересобрать относится ровно к одному станку, а не ко
            # всему списку замечаний.
            tail = text.split("Замечания к данным смены")[0]
            assert "Соберите отчёт заново" in tail, tail
            assert "Соберите отчёт заново" not in \
                text.split("Замечания к данным смены")[1], done["lines"]
    finally:
        day_pipeline.build_day = real


def test_remark_warnings_alone_never_ask_to_rebuild():
    """Замечания без сбоя разбора — разговора о платной пересборке нет вовсе."""
    def stub(date, extract=None, ai_enabled=None, photo_limit=12,
             progress=None):
        return types.SimpleNamespace(
            ok=True, reports=[], simple=[], note="", foreign_photos=[],
            carry_notes=[], has_remains=False, remains=None, date=date,
            ai_warnings=["журнал счётчиков за 22.09.2026 не записан — файл "
                         "недоступен на запись"])

    real = day_pipeline.build_day
    day_pipeline.build_day = stub
    try:
        with sandbox():
            api.upload_file(name="a.csv", blob=CSV.encode("utf-8"), who="Я")
            done = _wait_job(api.build(date=DATE, who="Я")["id"])
            text = "\n".join(done["lines"])
            assert "журнал счётчиков" in text, text
            assert "Разобрано не всё" not in text, text
            assert "Соберите отчёт заново" not in text, text
            assert "со второго раза" not in text, text
    finally:
        day_pipeline.build_day = real


def test_period_build_uses_only_the_chosen_dates():
    seen = {}

    def stub(dates, extract=None, ai_enabled=None, photo_limit=12,
             progress=None):
        seen["dates"] = list(dates)
        if progress:
            progress(f"{dates[0]}: разбираю переписку через ИИ")
        raise RuntimeError("в тесте дальше сборки не идём")

    real = day_pipeline.build_days
    day_pipeline.build_days = stub
    try:
        with sandbox():
            for d in ("20.09.2026", "21.09.2026", "22.09.2026"):
                api.add_text(text="станок 151 Об.сч. 100", date=d, who="Я")
            assert api.state()["dates"] == ["20.09.2026", "21.09.2026", DATE]
            done = _wait_job(api.period(who="Я", start="21.09.2026")["id"])
            assert seen["dates"] == ["21.09.2026", DATE], done["lines"]
            assert done["progress"].endswith("разбираю переписку через ИИ")
            # Пустой выбор и перепутанные местами даты — ошибка ДО запроса к ИИ:
            # пустой лист в сводном файле читается как «станок не работал».
            for kwargs, words in (({"start": "01.01.2026", "end": "05.01.2026"},
                                   "нет ни одной накопленной смены"),
                                  ({"start": DATE, "end": "20.09.2026"},
                                   "позже конца")):
                try:
                    api.period(who="Я", **kwargs)
                except api.ApiError as e:
                    assert words in e.message, e.message
                else:
                    raise AssertionError(f"период {kwargs} принят без ошибки")
    finally:
        day_pipeline.build_days = real


# ───────────────────────────── архив и отдача файлов ─────────────────────────────
def test_archive_and_download_only_known_files():
    with sandbox() as tmp:
        # Пустой день: файлов нет и собирать нечего.
        blank = api.archive_day(DATE)
        assert not blank["has_files"] and not blank["can_build"]
        assert "ни накопленных данных" in blank["lines"][0]

        api.upload_file(name="a.csv", blob=CSV.encode("utf-8"), who="Я")
        empty = api.archive_day(DATE)
        assert not empty["has_files"] and empty["can_build"]
        assert "Собрать сейчас?" in empty["lines"][0]

        path = report_archive.slot(DATE, XLSX)
        with open(path, "wb") as fh:
            fh.write(b"PK\x03\x04" + b"x" * 512)
        report_archive.finish(DATE, [XLSX], machines=["151", "251"])

        day = api.archive_day(DATE)
        assert day["has_files"] and day["files"][0]["name"] == XLSX
        assert "ИИ не запрашивался" in day["lines"][0]
        assert api.archive()[0]["date"] == DATE

        found = api.download(DATE, XLSX)
        assert os.path.basename(found["path"]) == XLSX
        assert os.path.isfile(found["path"])
        # Имя из чужого каталога не проходит, даже если файл на диске есть.
        for sneaky in ("web.db", "../web.db", "..\\..\\web.db", "app.js",
                       "C:/Windows/win.ini"):
            try:
                api.download(DATE, sneaky)
                raise AssertionError(f"отдали чужой файл: {sneaky}")
            except api.ApiError as e:
                assert e.status == 404, (sneaky, e.status)
        assert not os.path.exists(os.path.join(tmp, "reports", DATE, "web.db"))


def test_reset_clears_data_but_keeps_finished_files():
    with sandbox():
        api.upload_file(name="a.csv", blob=CSV.encode("utf-8"), who="Я")
        with open(report_archive.slot(DATE, XLSX), "wb") as fh:
            fh.write(b"PK")
        report_archive.finish(DATE, [XLSX])
        res = api.reset(DATE)
        assert res["removed"] == 2
        assert shift_store.day_stats(DATE) == (0, 0)
        assert api.archive_day(DATE)["has_files"], "архив должен пережить очистку"


# ───────────────────────────── страница и справка ─────────────────────────────
def test_state_has_everything_the_page_needs():
    with sandbox():
        api.upload_file(name="a.csv", blob=CSV.encode("utf-8"), who="Татьяна")
        data = api.state()
        for key in ("ai_enabled", "ai_model", "ocr_enabled", "today", "latest",
                    "shifts", "dates", "archive", "uploads", "reports", "exts",
                    "running"):
            assert key in data, key
        assert data["ai_enabled"] is False
        # Модель на странице — чтобы было видно, на каком запуске человек
        # сидит: после правки .env прежний процесс покажет прежнюю модель.
        assert data["ai_model"] == "test-model"
        assert data["latest"] == DATE
        assert data["shifts"][0]["machines"] == ["151", "251"]
        # «dates» — список для выбора периода: только накопленные смены.
        assert data["dates"] == [DATE]
        assert ".csv" in data["exts"]
        assert api.help_text()["text"].startswith("Как этим пользоваться")


def test_state_lists_running_job_so_page_resumes_it():
    """После перезагрузки страницы идущая сборка не должна «теряться»."""
    real = day_pipeline.build_day
    day_pipeline.build_day = lambda *a, **kw: (time.sleep(1.2), None)[1] or \
        types.SimpleNamespace(ok=False, reports=[], simple=[], note="",
                              foreign_photos=[], carry_notes=[], ai_warnings=[],
                              remains=None, date=DATE)
    try:
        with sandbox():
            api.upload_file(name="a.csv", blob=CSV.encode("utf-8"), who="Я")
            job = api.build(date=DATE, who="Я")
            running = [j["id"] for j in api.state()["running"]]
            assert job["id"] in running, running
            _wait_job(job["id"])
            assert api.state()["running"] == []
    finally:
        day_pipeline.build_day = real


def test_page_script_is_valid_javascript():
    """app.js должен хотя бы синтаксически читаться.

    Питон-тесты страницу не исполняют, поэтому опечатка в скрипте — например,
    склейка строк без «+», как в Python, — не ломает ни одну проверку, а
    страница открывается мёртвой: кнопки есть, данных нет. Проверяем парсером
    Node, когда он в системе есть; без Node тест просто пропускается.
    """
    node = shutil.which("node")
    if not node:
        print("⏭ node не установлен — синтаксис app.js не проверен")
        return
    path = os.path.join(ROOT, "webapp", "static", "app.js")
    got = subprocess.run([node, "--check", path], capture_output=True,
                         text=True, encoding="utf-8", errors="replace")
    assert got.returncode == 0, got.stdout + got.stderr


def test_help_text_matches_the_site_not_the_bot():
    low = texts.HELP.lower()
    for word in ("telegram", "/day", "/reset", "кнопк"):
        assert word not in low, f"в справке осталось от бота: {word}"
    # «работает» содержит «бот» — искать нужно отдельное слово.
    assert not re.search(r"(?<!\w)бот", low), "в справке остался бот"
    assert "ии" in low and "excel" in low and "1с" in low


# ───────────────────────────── HTTP-слой ─────────────────────────────
@contextlib.contextmanager
def running_server():
    """Настоящий сервер на случайном порту: проверяем маршруты и кодировки."""
    httpd = server_mod.Server(("127.0.0.1", 0), server_mod._Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = httpd.server_address[0], httpd.server_address[1]
        yield f"http://{host}:{port}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def http(url, *, data=None, headers=None):
    req = urllib.request.Request(url, data=data, headers=headers or {},
                                 method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def test_http_routes_page_and_intake():
    with sandbox():
        with running_server() as base:
            status, headers, body = http(base + "/")
            assert status == 200 and b"<title>" in body
            assert "text/html" in headers["Content-Type"]
            assert b"api/state" not in body        # страница — не JSON-заглушка

            status, _, body = http(base + "/app.js")
            assert status == 200 and b"postRaw" in body

            status, _, body = http(base + "/api/state")
            assert status == 200
            assert json.loads(body)["today"]

            # Текст — JSON-ом, в UTF-8 без «\\uXXXX».
            payload = json.dumps({"text": "151 машина 08:46 Об.сч.: 100",
                                  "who": "Татьяна"}).encode("utf-8")
            status, _, body = http(base + "/api/add_text", data=payload,
                                   headers={"Content-Type":
                                            "application/json"})
            assert status == 200, body
            assert "Данные приняты".encode("utf-8") in body

            # Файл — ТЕЛОМ запроса, метаданные в query.
            query = urllib.parse.urlencode({"name": "22.09.csv", "who": "Юлия"})
            status, _, body = http(f"{base}/api/upload_file?{query}",
                                   data=CSV.encode("utf-8"),
                                   headers={"Content-Type":
                                            "application/octet-stream"})
            assert status == 200, body
            got = json.loads(body)
            # Сообщений три: одно приняли текстом выше, файл добавил два своих
            # раздела — он не перезаписывает смену.
            assert got["date"] == DATE and got["shift"]["messages"] == 3, got
            assert api.journal()["uploads"][0]["who"] == "Юлия"

            # Ошибка — тот же JSON, а не трейсбек: страница показывает её как есть.
            status, _, body = http(base + "/api/reset",
                                   data='{"date": "не дата"}'.encode("utf-8"),
                                   headers={"Content-Type": "application/json"})
            assert status == 400
            assert "Не понял дату" in json.loads(body)["error"]

            # Бесплатная пересборка — тот же POST-слой, что и платная сборка.
            status, _, body = http(base + "/api/rebuild",
                                   data=json.dumps({"date": DATE}).encode(),
                                   headers={"Content-Type": "application/json"})
            assert status == 400, (status, body)
            assert "нет сохранённого разбора" in json.loads(body)["error"]

            status, _, body = http(base + "/api/net-zdes")
            assert status == 404
            for forbidden in ("/.env", "/static/app.js/../../.env", "/logs"):
                status, _, _ = http(base + forbidden)
                assert status == 404, forbidden


def test_http_serves_finished_files_and_refuses_strangers():
    with sandbox():
        path = report_archive.slot(DATE, XLSX)
        with open(path, "wb") as fh:
            fh.write(b"PK\x03\x04" + b"y" * 700)
        report_archive.finish(DATE, [XLSX])
        with running_server() as base:
            query = urllib.parse.urlencode({"date": DATE, "name": XLSX})
            status, headers, body = http(f"{base}/api/download?{query}")
            assert status == 200 and len(body) == 704
            assert "attachment" in headers["Content-Disposition"]
            # Имя в UTF-8: русские заголовки скачиваются читаемыми.
            assert "%D0%9E" in headers["Content-Disposition"]
            bad = urllib.parse.urlencode({"date": DATE, "name": "../web.db"})
            status, _, _ = http(f"{base}/api/download?{bad}")
            assert status == 404


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

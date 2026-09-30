"""Проверка базы под локальный сайт: люди, журнал загрузок и журнал сборок.

База для сайта тонкая (services/* считают, database/* только фиксирует факты),
поэтому и проверяем здесь ровно то, на чём держится журнал:

* пользователь ищется по имени независимо от регистра — «татьяна» и «Татьяна»
  один человек, иначе журнал за день расползётся на десяток имён;
* загрузка сохраняется со станками/авторами и признаком повтора — по этим
  колонкам на сайте видно, чьё сообщение испортило отчёт;
* сборка отчёта живёт своей записью: сначала `running`, потом `done` или
  `error` со списком файлов — по ней страница «Готовые файлы» отдаёт ссылки,
  не пересобирая смену;
* журнал не должен ронять приём данных: битую запись ловим, id возвращаем 0.

Тест не трогает рабочую базу: своя sqlite-таблица в временном каталоге.
"""
import logging
import os
import sys
import tempfile
from contextlib import contextmanager
from types import SimpleNamespace
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.database import Database                               # noqa: E402
from database.models import ReportRun, ShiftUpload                    # noqa: E402


@contextmanager
def _temp_db():
    """Временная база: свой файл, чтобы тест не трогал рабочую базу.

    Движок обязательно отпускаем: SQLite держит файл открытым в пуле
    соединений, и временный каталог без dispose() не удаляется.
    """
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(f"sqlite:///{Path(tmp).as_posix()}/test.db")
        db.create_tables()
        try:
            yield db
        finally:
            db.engine.dispose()


def _parsed(machine: str = "151") -> SimpleNamespace:
    return SimpleNamespace(
        machine=machine,
        shift_start="07:00",
        shift_end="19:00",
        operator="Татьяна",
        total_counters={"общий_счётчик": 278755.0},
        slice_counters={"ср_сч": 1362.0},
        counters=[SimpleNamespace(name="Общ.сч", value=278755.0)],
        substitutions=[SimpleNamespace(count=2, of=4, reason="заправка")],
        raw_text="151 машина 08:00 общий сч 278755",
        warnings=[],
        events=[SimpleNamespace(start="08:00", end="08:15",
                                category="stop", text="обрыв")],
    )


def test_user_is_one_person_regardless_of_case():
    with _temp_db() as db:
        first = db.get_or_create_user("Татьяна")
        again = db.get_or_create_user("  татьяна ")
        assert first.id == again.id, "регистр и пробелы плодят людей"
        assert db.get_or_create_user("Пётр").id != first.id
        assert {u.name for u in db.list_users()} == {"Татьяна", "Пётр"}


def test_empty_name_has_no_user():
    with _temp_db() as db:
        assert db.get_or_create_user("   ") is None
        assert db.get_or_create_user(None) is None
        # Загрузка без подписи возможна — просто остаётся без автора.
        upload_id = db.log_upload(user=None, kind="file", source="a.csv",
                                  shift_date="22.09.2026")
        assert upload_id
        session = db.get_session()
        try:
            row = session.get(ShiftUpload, upload_id)
            assert row.user_id is None
        finally:
            session.close()


def test_upload_journal_keeps_machines_and_duplicate():
    with _temp_db() as db:
        user = db.get_or_create_user("Мастер 151")
        db.log_upload(user=user, kind="file", source="22.09.2026.csv",
                      shift_date="22.09.2026", file_kind="плоская выгрузка",
                      sections=9, machines=["151", "251"],
                      authors=["Татьяна"], messages_total=9, photos_total=0)
        db.log_upload(user=user, kind="file", source="копия.csv",
                      shift_date="22.09.2026", duplicate=True,
                      duplicate_of="22.09.2026.csv", messages_total=9)
        db.log_upload(user=user, kind="text", source="08:46 начало работы",
                      shift_date="21.09.2026", messages_total=10)

        rows = db.recent_uploads(limit=10)
        assert len(rows) == 3
        assert {r.shift_date for r in rows} == {"22.09.2026", "21.09.2026"}
        only = db.recent_uploads(shift_date="22.09.2026")
        assert len(only) == 2
        dup = [r for r in only if r.duplicate][0]
        assert dup.duplicate_of == "22.09.2026.csv"

        session = db.get_session()
        try:
            first = [r for r in session.query(ShiftUpload).all()
                     if r.sections == 9][0]
            assert '"151"' in first.machines_json
            assert first.file_kind == "плоская выгрузка"
        finally:
            session.close()


def test_report_run_goes_running_then_done():
    with _temp_db() as db:
        user = db.get_or_create_user("Начальник")
        run_id = db.start_report(user=user, shift_date="22.09.2026",
                                 messages=9, photos=2)
        session = db.get_session()
        try:
            row = session.get(ReportRun, run_id)
            assert row.state == "running" and row.ok is False
            assert row.finished_at is None
        finally:
            session.close()

        db.finish_report(run_id, ok=True,
                         files=["отчет_22.09.2026.xlsx", "1с_22.09.2026.json"],
                         machines=["151"], warnings=["251: мало цифр"])
        runs = db.recent_reports()
        assert len(runs) == 1
        done = runs[0]
        assert (done.state, done.ok) == ("done", True)
        assert done.finished_at is not None
        assert done.files_json.count("xlsx") == 1
        assert done.warnings_json.count("251") == 1

        # Упавшая сборка отличается от «пустой»: её надо видеть отдельно.
        bad_id = db.start_report(user=user, shift_date="20.09.2026")
        db.finish_report(bad_id, ok=False, error="таймаут модели")
        bad = [r for r in db.recent_reports() if r.id == bad_id][0]
        assert bad.state == "error" and bad.error == "таймаут модели"
        # Несуществующую запись закрываем молча, без исключения.
        db.finish_report(0, ok=True)


def test_unfinished_runs_are_aborted_on_start():
    """Сборка не может «идти» из прежнего запуска программы.

    `_JOBS` живёт в памяти процесса, поэтому строка state='running' после
    перезапуска (.exe сняли посреди сборки, закрыли окно, пропало питание)
    висит вечно, и в журнале до сих пор значилась сборка 11:19, которой давно
    нет. При старте такие строки обязан погасить abort_stale_runs.
    """
    with _temp_db() as db:
        user = db.get_or_create_user("Начальник")
        dead = db.start_report(user=user, shift_date="21.09.2026", messages=10)
        alive = db.start_report(user=user, shift_date="22.09.2026", messages=9)
        db.finish_report(alive, ok=True, files=["итог_22.09.xlsx"])

        assert db.abort_stale_runs() == 1
        runs = {r.id: r for r in db.recent_reports()}
        assert runs[dead].state == "error"
        assert runs[dead].ok is False
        assert "прервана" in runs[dead].error
        assert runs[dead].finished_at is not None
        assert runs[alive].state == "done"
        # Повторный запуск ничего не находит — это нормально, а не ошибка.
        assert db.abort_stale_runs() == 0


def test_production_record_has_shift_date():
    with _temp_db() as db:
        record = db.save_production_record(_parsed(), shift_date="22.09.2026")
        assert record.shift_date == "22.09.2026"
        assert record.machine_number == "151"
        got = db.get_production_records(machine_number="151")
        assert len(got) == 1
        assert db.get_production_records(machine_number="999") == []
        session = db.get_session()
        try:
            from database.models import ProductionEvent
            assert session.query(ProductionEvent).count() == 1
        finally:
            session.close()


def test_broken_journal_write_does_not_raise():
    """Журнал не должен ломать приём данных: вместо исключения — id 0.

    Дата в схеме обязательна, и её отсутствие — реальный сценарий ошибки
    вызова. Падать из-за этого нельзя: данные смены уже лежат в day.json, и
    из-за неудачной записи журнала пользователь потерял бы весь приём.
    """
    with _temp_db() as db:
        # Ошибку журнал ловит сам, но в лог её пишет — здесь она ожидаемая,
        # поэтому шум от logger.exception глушим.
        logging.disable(logging.ERROR)
        try:
            assert db.log_upload(user=None, kind="file", source="x",
                                 shift_date=None) == 0
            assert db.start_report(user=None, shift_date=None) == 0
        finally:
            logging.disable(logging.NOTSET)
        # Битую запись не видно ни в одном списке — журнал остаётся чистым.
        assert db.recent_uploads() == []
        assert db.recent_reports() == []


def test_no_telegram_left_in_models():
    """Telegram-оболочку снесли — в схеме её следов быть не должно."""
    from database import models
    for name in ("UserSettings",):
        assert not hasattr(models, name), f"{name} осталась в модели"
    assert not hasattr(models.User, "telegram_id")


if __name__ == "__main__":
    import sys
    failed = 0
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    for name, fn in tests:
        try:
            fn()
            print(f"ok   {name}")
        except Exception as exc:                # noqa: BLE001 - тест-раннер
            failed += 1
            print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    print(f"ran {len(tests)} failed {failed}")
    sys.exit(1 if failed else 0)

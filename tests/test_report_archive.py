"""Архив финальных файлов: собрал отчёт один раз — забираю сколько нужно.

Просьба пользователя 04.09.2026, дословно: «можно теперь сделать так чтобы были
команды в боте с помощью которой я бы нажимал ее и выдавались все финальные
файлы по каждым дням скажем так типо история финальных файлов по дням… нажимаю
эту кнопку идет выбор даты и по ней финальные файлы».

Раньше Excel и файл для 1С жили в `tempfile.TemporaryDirectory()` и после
отправки удалялись: чтобы получить их снова, приходилось заново гонять смену
через ИИ. Теперь они остаются на диске, и выдача из архива ИИ не вызывает.

Тест держит главное:

* файлы за дату находятся и отдаются в порядке «Excel, потом 1С»;
* за датой хранится ТОЛЬКО последняя версия (его решение «Хранить только
  последний»): пересборка убирает файлы прошлой версии, даже если они
  назывались иначе;
* если экспорт упал (`finish` не вызван), прошлые файлы остаются на месте;
* список дат — свежие первыми, дни без файлов в него не попадают;
* битая или отсутствующая meta.json не скрывает файлы (время берётся с диска);
* в имя файла нельзя подсунуть путь («../»), архив остаётся в своём каталоге.

Запуск: python3 tests/test_report_archive.py (или через tests/_run_all.py).
"""
import contextlib
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from services import report_archive as ra                        # noqa: E402

XLSX = "Отчет производства за смену 20.08.2026.xlsx"
ONEC = "Выгрузка 1С за смену 20.08.2026.json"


@contextlib.contextmanager
def sandbox():
    """Пустой каталог архива на время теста, потом всё как было."""
    keep = ra.get_data_dir()
    tmp = tempfile.mkdtemp(prefix="archive_")
    ra.set_data_dir(tmp)
    try:
        yield tmp
    finally:
        ra.set_data_dir(keep)
        shutil.rmtree(tmp, ignore_errors=True)


def put(date, name, size=2048):
    """Записать «готовый файл» так, как это делает выгрузка отчёта."""
    path = ra.slot(date, name)
    with open(path, "wb") as f:
        f.write(b"PK" + b"x" * size)
    return path


def build(date, names=(XLSX, ONEC), machines=("251", "321"), now=None):
    for name in names:
        put(date, name)
    ra.finish(date, list(names), machines=list(machines), now=now)


def test_saved_files_are_found_and_ordered():
    with sandbox():
        build("20.08.2026")
        files = ra.day_files("20.08.2026")
        assert [f.name for f in files] == [XLSX, ONEC], files
        # Excel — первым, файл для 1С — вторым: в этом порядке уходят в чат.
        assert [f.kind for f in files] == ["Excel", "1С"]
        assert all(f.size > 0 and os.path.isfile(f.path) for f in files)
        assert ra.has_files("20.08.2026")


def test_day_without_files_is_not_ok():
    with sandbox():
        build("20.08.2026")
        day = ra.archived_day("19.08.2026")
        assert not day.ok and day.files == [] and day.machines == []
        assert ra.day_files("19.08.2026") == []
        assert not ra.has_files("19.08.2026")


def test_only_latest_version_is_kept():
    """Его решение: «Хранить только последний»."""
    with sandbox():
        build("20.08.2026")
        # Пересборка: тот же Excel (перезапись) и переименованная выгрузка 1С.
        renamed = "Выгрузка 1С 20.08.2026 (v2).json"
        put("20.08.2026", XLSX, size=5000)
        put("20.08.2026", renamed)
        ra.finish("20.08.2026", [XLSX, renamed], machines=["251"])

        files = ra.day_files("20.08.2026")
        assert [f.name for f in files] == [XLSX, renamed], files
        # Файла прошлой версии в каталоге больше нет — иначе в чат ушли бы три.
        assert ONEC not in os.listdir(ra.day_dir("20.08.2026"))
        assert files[0].size > 5000
        assert ra.archived_day("20.08.2026").machines == ["251"]


def test_backup_copies_are_not_offered_as_reports():
    """Копия «…xlsx.old-27.09-peresborka» — не отчёт: открывать её нечем.

    Прогон 28.09.2026: владельец скачал из «Готовых файлов» резервную копию
    Excel за 26.09 и получил «не может открыться Excel». Файл на диске
    настоящий и открывается, а вот расширение у копии не .xlsx, и показывать
    её в списке готовых отчётов нельзя.
    """
    with sandbox():
        build("20.08.2026")
        backup = XLSX + ".old-27.09-peresborka"
        put("20.08.2026", backup)
        names = [f.name for f in ra.day_files("20.08.2026")]
        assert names == [XLSX, ONEC], names
        assert backup not in names
        # Копия остаётся на диске — мы её не удаляем, просто не предлагаем.
        assert backup in os.listdir(ra.day_dir("20.08.2026"))
        assert ra.archived_day("20.08.2026").ok
        print("✅ резервные копии скрыты из списка, на диске целы")


def test_failed_rebuild_keeps_previous_files():
    """Экспорт упал — finish не вызвали. Прошлые файлы должны остаться."""
    with sandbox():
        build("20.08.2026")
        # Так выглядит падение на середине: часть файла записалась, меты нет.
        put("20.08.2026", "Отчет производства за смену 20.08.2026.tmp")
        files = ra.day_files("20.08.2026")
        assert XLSX in [f.name for f in files]
        assert ONEC in [f.name for f in files]


def test_available_days_newest_first_and_only_with_files():
    with sandbox():
        build("19.08.2026", names=("Отчет 19.08.2026.xlsx",))
        build("20.08.2026")
        build("01.09.2026", names=("Отчет 01.09.2026.xlsx",))
        # Каталог есть, а файлов в нём нет — в историю не попадает.
        os.makedirs(ra.day_dir("18.08.2026"), exist_ok=True)
        # Посторонний каталог с непонятным именем не должен всё ломать.
        os.makedirs(os.path.join(ra.get_data_dir(), "мусор"), exist_ok=True)

        dates = [d.date for d in ra.available_days()]
        assert dates == ["01.09.2026", "20.08.2026", "19.08.2026"], dates
        assert [d.date for d in ra.available_days(limit=2)] == ["01.09.2026",
                                                               "20.08.2026"]
        assert [d.date for d in ra.available_days(newest_first=False)][0] == \
            "19.08.2026"


def test_broken_meta_does_not_hide_files():
    with sandbox():
        build("20.08.2026")
        with open(os.path.join(ra.day_dir("20.08.2026"), ra.META_NAME),
                  "w", encoding="utf-8") as f:
            f.write("{это не json")
        day = ra.archived_day("20.08.2026")
        assert {f.name for f in day.files} == {XLSX, ONEC}
        # Время сборки берём с диска, чтобы в списке не было пустоты.
        assert day.saved_at and day.saved_at_text


def test_meta_keeps_when_and_which_machines():
    with sandbox():
        build("20.08.2026", now=datetime(2026, 9, 4, 12, 50))
        day = ra.archived_day("20.08.2026")
        assert day.saved_at_text == "04.09.2026 12:50", day.saved_at
        assert day.machines == ["251", "321"]
        assert day.kinds == ["Excel", "1С"]
        assert day.button_label == "20.08 · 2 файла", day.button_label
        assert "Excel" in day.line and "1С" in day.line
        assert "04.09.2026 12:50" in day.line and "251" in day.line
        with open(os.path.join(ra.day_dir("20.08.2026"), ra.META_NAME),
                  encoding="utf-8") as f:
            meta = json.load(f)
        assert meta["files"] == [XLSX, ONEC] and meta["date"] == "20.08.2026"


def test_button_label_says_files_in_russian():
    """Подпись на кнопке читает человек: «1 файл», «2 файла», «5 файлов»."""
    with sandbox():
        build("20.08.2026", names=("Отчет.xlsx",))
        assert ra.archived_day("20.08.2026").button_label == "20.08 · 1 файл"
        build("19.08.2026", names=tuple(f"ф{i}.xlsx" for i in range(5)))
        assert ra.archived_day("19.08.2026").button_label == "19.08 · 5 файлов"
        assert [ra._files_word(n) for n in (1, 2, 5, 11, 12, 21, 22)] == [
            "файл", "файла", "файлов", "файлов", "файлов", "файл", "файла"]


def test_finish_without_files_writes_nothing():
    """Отчёт не собрался — в архиве не должно появиться пустой записи."""
    with sandbox():
        ra.finish("20.08.2026", [])
        assert not ra.has_files("20.08.2026")
        assert ra.available_days() == []


def test_clear_day_removes_files_and_meta():
    with sandbox():
        build("20.08.2026")
        assert ra.clear_day("20.08.2026") == 2
        assert ra.day_files("20.08.2026") == []
        assert os.listdir(ra.day_dir("20.08.2026")) == []
        assert ra.clear_day("20.08.2026") == 0


def test_slot_cannot_escape_its_day_directory():
    """В имя файла не должен пролезть путь: имя приходит из даты и настроек."""
    with sandbox() as tmp:
        path = ra.slot("20.08.2026", "../../побег.xlsx")
        assert os.path.dirname(os.path.abspath(path)) == os.path.abspath(
            ra.day_dir("20.08.2026")), path
        assert os.path.abspath(path).startswith(os.path.abspath(tmp))


def test_date_with_slashes_stays_one_directory():
    with sandbox():
        build("20/08/2026", names=("Отчет.xlsx",))
        assert os.path.isdir(os.path.join(ra.get_data_dir(), "20.08.2026"))
        assert ra.has_files("20/08/2026")


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    bad = []
    for fn in tests:
        try:
            fn()
            print(f"✅ {fn.__name__}")
        except AssertionError as e:
            bad.append(fn.__name__)
            print(f"❌ {fn.__name__}: {e}")
    print(f"\nran {len(tests)} failed {len(bad)}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

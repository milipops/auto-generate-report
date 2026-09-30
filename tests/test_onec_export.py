"""Плоская выгрузка смены для ручной загрузки в базовую 1С.

Проверяем ровно то, на что 1С будет опираться при загрузке: постоянный набор
и порядок колонок, числа в русской локали (запятая), CSV с BOM и разделителем
«;», строка на каждый станок и пометка у станка без данных.
"""
import csv
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.onec_export import (COLUMNS, WASTE_COLUMNS, build_document,
                                  build_rows, export_csv, export_json,
                                  machine_dict, report_to_row)
from services.shift_report import build_shift_reports_from_structured


def _day():
    return {"date": "27.08.2026", "machines": [{
        "machine": "151", "operator": "Вашурина",
        "lines": [{"counter_start": 726653, "counter_end": 730222}],
        "events": [
            {"kind": "наладка", "start": s, "end": e,
             "reason": "вызван наладчик", "performer": "Пронкин"}
            for s, e in (("09:35", "09:50"), ("10:57", "11:56"),
                         ("12:50", "13:10"), ("13:50", "14:25"))
        ]}]}


def test_row_has_all_columns_and_russian_numbers():
    r = build_shift_reports_from_structured(_day())[0]
    row = report_to_row(r)
    # С 09.09.2026 к строке дописан хвост учёта отходов (WASTE_COLUMNS).
    assert len(row) == len(COLUMNS) + len(WASTE_COLUMNS), len(row)
    assert row[0] == "27.08.2026" and row[1] == "151"
    assert row[2] == "Вашурина"
    # Часы работы станка — число с запятой, не с точкой.
    assert "," in row[3] and "." not in row[3], row[3]
    # Простои одной строкой содержат наладку.
    assert "наладка" in row[9], row[9]
    print(f"✅ строка 1С: {row}")


def test_csv_is_utf8_bom_semicolon():
    r = build_shift_reports_from_structured(_day())
    with tempfile.TemporaryDirectory() as tmp:
        path = str(Path(tmp) / "out.csv")
        export_csv(r, path)
        raw = Path(path).read_bytes()
        assert raw[:3] == b"\xef\xbb\xbf", "нет BOM — 1С прочитает кракозябры"
        text = raw.decode("utf-8-sig")
        rows = list(csv.reader(text.splitlines(), delimiter=";"))
        assert rows[0] == list(COLUMNS) + list(WASTE_COLUMNS), rows[0]
        assert rows[1][1] == "151", rows[1]
    print(f"✅ CSV UTF-8-BOM «;», заголовок: {rows[0][:3]}…")


def test_zero_row_machine_has_note():
    day = {"date": "27.08.2026", "machines": [
        {"machine": "601", "operator": "", "lines": [], "events": []}]}
    r = build_shift_reports_from_structured(day)
    rows = build_rows(r)
    body = rows[1]
    note = body[COLUMNS.index("Примечание")]
    assert "нет данных" in note.lower(), body
    print(f"✅ станок без данных помечен: {note!r}")


def test_json_has_everything_from_excel():
    """JSON-выгрузка несёт оба листа Excel: строки, блоки, справочники."""
    r = build_shift_reports_from_structured(_day())
    doc = build_document(r, date="27.08.2026")
    assert doc["дата"] == "27.08.2026"
    # справочники: станок 151 с маркой/нормой и оператор Вашурина.
    codes = [m["код"] for m in doc["справочники"]["станки"]]
    assert "151" in codes, codes
    people = [p["фио"] for p in doc["справочники"]["операторы"]]
    assert "Вашурина" in people, people
    # Пронкин (наладчик) попал в справочник людей через привлечённый персонал.
    assert "Пронкин" in people, people
    m = doc["станки"][0]
    # оба листа Excel: строка ассортимента со счётчиком + все три блока.
    assert m["станок"] == "151" and m["оператор"] == "Вашурина"
    assert m["строки"][0]["счётчик_начало"] == 726653
    assert m["строки"][0]["счётчик_конец"] == 730222
    for block in ("оператор_блок", "привлечённый_персонал", "машина_блок"):
        assert block in m, block
    # наладка Пронкина видна и в простое машины, и у привлечённого персонала.
    downtime = " ".join(x["описание"] for x in m["машина_блок"]["простои"])
    assert "наладка" in downtime, downtime
    attracted = " ".join(x["описание"]
                         for x in m["привлечённый_персонал"]["строки"])
    assert "Пронкин" in attracted, attracted
    print(f"✅ JSON: станки={codes}, люди={people}, блоки на месте")


def test_json_file_roundtrips():
    """export_json пишет валидный UTF-8 JSON, который читается обратно."""
    r = build_shift_reports_from_structured(_day())
    with tempfile.TemporaryDirectory() as tmp:
        path = str(Path(tmp) / "out.json")
        export_json(r, path, date="27.08.2026")
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
        assert doc["версия_формата"] == 1
        assert doc["станки"][0]["станок"] == "151"
    print("✅ JSON пишется и читается обратно (UTF-8)")


if __name__ == "__main__":
    test_row_has_all_columns_and_russian_numbers()
    test_csv_is_utf8_bom_semicolon()
    test_zero_row_machine_has_note()
    test_json_has_everything_from_excel()
    test_json_file_roundtrips()

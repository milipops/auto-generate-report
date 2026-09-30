"""Фоновые сборки отчёта: страница не ждёт, пока думает ИИ.

Зачем отдельный поток. `day_pipeline.build_day` — это ОДИН (или несколько по
станкам) запрос к ИИ, то есть минуты. HTTP-запрос на столько вешать нельзя:
браузер отвалится, а пользователь нажмёт «собрать» ещё раз — и заплатит за
вторую сборку той же смены. Поэтому сборка живёт в реестре задач:

* `start_build(date)` / `start_period()` — заводят задачу, запускают поток и
  сразу возвращают id;
* `job(job_id)` — страница опрашивает состояние («done» — можно показывать
  ссылки на файлы);
* повторный запуск за ту же дату НЕ начинается, пока предыдущий в работе:
  возвращаем ту же задачу.

Собираем ровно то же, что собирал Telegram-бот: короткие строки в чат, Excel и
файл для 1С — в архив (`report_archive`), чтобы их можно было забрать снова без
запроса к ИИ. Тексты сообщений перенесены дословно: человек привык к ним.
"""
from __future__ import annotations

import logging
import os
import threading
import uuid
from datetime import datetime
from typing import Dict, List, Optional

from config.config import settings
from database import db
from services import (
    day_pipeline,
    extract_structured,
    format_daily_report,
    format_remains_digest,
    format_shift_digest,
    report_archive,
)

logger = logging.getLogger(__name__)

PHOTO_LIMIT = day_pipeline.PHOTO_LIMIT

# Задач держим немного: это только последние сборки, а не архив.
MAX_JOBS = 30

# Куда ложится сводный Excel «за все смены». В архив по датам (`report_archive`)
# его не пишем: он не относится к одной смене, а смешивать — значит показать в
# истории «Готовые файлы» файл, которого там быть не должно.
_EXPORT_DIR = os.path.join("data", "web_exports")

_JOBS: Dict[str, Dict] = {}
_LOCK = threading.Lock()


def set_export_dir(path: str) -> None:
    """Переназначить каталог сводных файлов (нужно тестам)."""
    global _EXPORT_DIR
    _EXPORT_DIR = path


def get_export_dir() -> str:
    os.makedirs(_EXPORT_DIR, exist_ok=True)
    return _EXPORT_DIR


# ───────────────────────────────── реестр ─────────────────────────────────
def _new_job(scope: str, date: str, who: str) -> Dict:
    job = {
        "id": uuid.uuid4().hex[:12],
        "scope": scope,                  # day | period
        "date": date,                    # для period — «19.08—25.08»
        "who": who or "",
        "state": "running",              # running | done | error
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "finished_at": None,
        "lines": [],                     # что бот написал бы в чат
        "files": [],                     # [{date,name,kind,size,size_text}]
        "progress": "",                  # последняя строка «что сейчас делается»
        "error": "",
        "machines": [],
    }
    with _LOCK:
        _JOBS[job["id"]] = job
        while len(_JOBS) > MAX_JOBS:     # убираем самые старые завершённые
            oldest = next((j for j in _JOBS.values() if j["state"] != "running"),
                          None)
            if oldest is None:
                break
            _JOBS.pop(oldest["id"], None)
    return job


def _add_line(job_id: str, text: str) -> None:
    if not text:
        return
    with _LOCK:
        job = _JOBS.get(job_id)
        if job is not None:
            job["lines"].append(text)


def _set_progress(job_id: str, text: str) -> None:
    """Последняя строка «что сейчас происходит».

    Отдельно от `lines`: сборка может сообщить так десятки шагов, а в чат-логе
    человеку незачем их перечитывать — важна текущая стадия и то, что она
    меняется.
    """
    with _LOCK:
        job = _JOBS.get(job_id)
        if job is not None:
            job["progress"] = (text or "").strip()


def _progress_for(job_id: str):
    """Колбэк для `day_pipeline`: строка стадии → задача в реестре."""
    def report(text: str) -> None:
        _set_progress(job_id, text)
    return report


def _add_file(job_id: str, path: str, date: str) -> None:
    f = report_archive.ArchivedFile(
        path=path, name=os.path.basename(path),
        size=os.path.getsize(path) if os.path.exists(path) else 0)
    with _LOCK:
        job = _JOBS.get(job_id)
        if job is not None:
            job["files"].append({
                "date": date,
                "name": f.name,
                "kind": f.kind,
                "size": f.size,
                "size_text": f.size_text,
            })


def _finish(job_id: str, state: str = "done", error: str = "") -> None:
    with _LOCK:
        job = _JOBS.get(job_id)
        if job is not None:
            job["state"] = state
            job["error"] = error
            job["finished_at"] = datetime.now().isoformat(timespec="seconds")


def job(job_id: str) -> Optional[Dict]:
    """Снимок задачи для JSON-ответа (копия: наружу не отдаём внутренний dict)."""
    with _LOCK:
        found = _JOBS.get(job_id)
        return dict(found) if found else None


def jobs(limit: int = 10) -> List[Dict]:
    with _LOCK:
        items = list(_JOBS.values())
    return [dict(j) for j in items[::-1][:limit]]


def _running(scope: str, date: str) -> Optional[Dict]:
    with _LOCK:
        for j in _JOBS.values():
            if j["state"] == "running" and j["scope"] == scope and j["date"] == date:
                return dict(j)
    return None


# ───────────────────────────────── отчёт за дату ─────────────────────────────────
def start_build(date: str, who: str = "") -> Dict:
    """Завести (или вернуть идущую) сборку отчёта за смену.

    Возвращает снимок задачи: страница по нему решает, показывать прогресс или
    сразу результат.
    """
    busy = _running("day", date)
    if busy:
        return busy
    job = _new_job("day", date, who)
    threading.Thread(target=_run_build, args=(job["id"], date, who),
                     name=f"build-{date}", daemon=True).start()
    return dict(job)


def _run_build(job_id: str, date: str, who: str) -> None:
    plan = day_pipeline.summary(date)
    user = db.get_or_create_user(who)
    run_id = db.start_report(user=user, shift_date=date, scope="day",
                             messages=plan.messages, photos=plan.photos)
    machines = (f", станки: {', '.join(plan.machines)}" if plan.machines else "")
    _add_line(job_id, f"⏳ Собираю отчёт за {date}.\n"
                      f"В работе: {plan.messages} сообщ., {plan.photos} "
                      f"фото{machines}")
    if not plan.machines:
        _add_line(job_id,
                  "⚠️ В накопленном за смену ни разу не указан номер станка. "
                  "Определю по смыслу переписки, но проверьте номер в отчёте — "
                  "и лучше пришлите отдельное сообщение с номером станка.")
    try:
        result = day_pipeline.build_day(date, extract_structured,
                                         settings.ai_enabled, PHOTO_LIMIT,
                                         progress=_progress_for(job_id))
    except Exception as e:                 # noqa: BLE001 — показываем человеку
        logger.exception("Сборка отчёта за %s упала", date)
        db.finish_report(run_id, ok=False, error=str(e))
        _add_line(job_id, f"⚠️ Отчёт за {date} собрать не удалось: {e}")
        _finish(job_id, "error", str(e))
        return

    if result.ok:
        saved = _send_day(job_id, result)
        db.finish_report(
            run_id, ok=True, files=saved,
            machines=[r.machine for r in result.reports if r.machine],
            warnings=list(result.ai_warnings or []),
            note="; ".join(result.carry_notes or []))
        _finish(job_id)
        return

    # ИИ недоступен или не справился — отдаём хотя бы упрощённую сводку, чтобы
    # смена не осталась совсем без отчёта (как в боте).
    if result.note:
        _add_line(job_id, result.note)
    if not result.simple:
        _add_line(job_id,
                  "Не смог выделить станки из накопленного. Проверьте, что в "
                  "сообщениях есть номера станков и показания общего счётчика "
                  "(начальное и конечное).")
        db.finish_report(run_id, ok=False, note=result.note or "",
                         error="станки не определены")
        _finish(job_id, "error", "станки не определены")
        return
    _add_line(job_id, "Ниже — упрощённая сводка по накопленному (без расчёта "
                      "часов по счётчику и без Excel):")
    for report in result.simple:
        _add_line(job_id, format_daily_report(report))
    _foreign_lines(job_id, result.foreign_photos)
    db.finish_report(run_id, ok=False, note=result.note or "")
    _finish(job_id)


def _send_day(job_id: str, result, local: bool = False) -> List[str]:
    """Короткая сводка + Excel + файл для 1С в архиве (то, что делал бот).

    В чат бот отправлял ПОЛНЫЕ тексты по станкам и падал на лимите Telegram —
    здесь та же логика, но текст просто идёт на страницу. Файлы пишутся сразу в
    архив: по «Готовым файлам» их потом видно без второго запроса к ИИ.
    Возвращаем имена сохранённых файлов — по ним пишется журнал сборок.
    """
    _add_line(job_id, format_shift_digest(result.reports, result.date))

    if result.has_remains:
        digest = format_remains_digest(result.remains)
        _add_line(job_id, digest)
        for warn in (result.remains.warnings or []):
            _add_line(job_id, f"⚠️ {warn}")

    saved: List[str] = []
    xlsx_name = day_pipeline.xlsx_filename(result.date)
    try:
        path = report_archive.slot(result.date, xlsx_name)
        day_pipeline.export_xlsx(result, path)
        saved.append(xlsx_name)
        _add_file(job_id, path, result.date)
    except Exception as e:                 # выгрузка не должна ломать отчёт
        logger.exception("Не удалось выгрузить отчёт в Excel")
        _add_line(job_id, f"⚠️ Текстовый отчёт готов, но выгрузка в Excel не "
                          f"удалась: {e}")

    onec_name = day_pipeline.onec_filename(result.date)
    try:
        onec_path = report_archive.slot(result.date, onec_name)
        day_pipeline.export_onec(result, onec_path)
        saved.append(onec_name)
        _add_file(job_id, onec_path, result.date)
    except Exception as e:
        logger.exception("Не удалось собрать файл выгрузки в 1С")
        _add_line(job_id, f"⚠️ Файл для 1С собрать не удалось: {e}")

    # Мету архива пишем в конце: если экспорт упал, прошлая версия за эту дату
    # остаётся в архиве нетронутой.
    if saved:
        try:
            report_archive.finish(result.date, saved,
                                  machines=[r.machine for r in result.reports
                                            if r.machine])
        except Exception:                  # архив не должен ломать отчёт
            logger.exception("Не удалось обновить архив финальных файлов")

    _foreign_lines(job_id, result.foreign_photos)
    if result.carry_notes:
        _add_line(job_id,
                  "ℹ️ Начальные показания счётчика подставлены с прошлой "
                  "смены:\n" + "\n".join("• " + n for n in result.carry_notes))
    if result.ai_warnings:
        # Предупреждения бывают двух совсем разных родов, а просьба «соберите
        # отчёт заново» уместна только для одного из них. Станок, по которому
        # ИИ не отдал данных, действительно разбирается со второго раза.
        # Остальное — журнал счётчиков не записался, фамилия оператора спорная,
        # станка нет в переписке: пересборка с ИИ тут ничего не исправит, а
        # человек по этой надписи запускает 10 платных вызовов.
        parse_fail = [w for w in (getattr(result, "ai_parse_warnings", None)
                                  or [])]
        other = [w for w in result.ai_warnings if w not in parse_fail]
        if parse_fail:
            _add_line(job_id,
                      "⚠️ Разобрано не всё — по этим станкам цифр в отчёте нет:\n"
                      + "\n".join("• " + w for w in parse_fail)
                      + "\nСоберите отчёт заново — обычно со второго раза станок "
                        "разбирается.")
        if other:
            _add_line(job_id,
                      "⚠️ Замечания к данным смены (отчёт собран, переспрашивать "
                      "ИИ для этого не нужно):\n"
                      + "\n".join("• " + w for w in other))
    if local:
        _add_line(job_id, "Разбор ИИ к этому отчёту не запрашивался и не "
                          "перезаписывался: взято то, что модель отдавала "
                          "раньше.")
    if saved:
        _add_line(job_id,
                  f"Готово. Файлы за {result.date} сохранены — забрать их можно "
                  f"и в разделе «Готовые файлы», уже без запроса к ИИ.\n"
                  f"Данные за смену не стираются: можно доприслать сообщения и "
                  f"собрать отчёт заново (новые файлы заменят старые).")
    return saved


def start_local_build(date: str, who: str = "") -> Dict:
    """Завести пересборку отчёта из сохранённого разбора — без запроса к ИИ.

    Зачем отдельная кнопка, а не «Собрать отчёт»: та стоит денег и минут, эта
    бесплатна. Разбор переписки модель уже отдавала (он лежит в
    `data/shifts/<дата>/ai_raw.json`), а после изменения правил заново нужна
    ровно арифметика — её считает код.
    """
    busy = _running("day", date)
    if busy:
        return busy
    job = _new_job("day", date, who)
    threading.Thread(target=_run_local_build, args=(job["id"], date, who),
                     name=f"rebuild-{date}", daemon=True).start()
    return dict(job)


def _run_local_build(job_id: str, date: str, who: str) -> None:
    plan = day_pipeline.summary(date)
    user = db.get_or_create_user(who)
    run_id = db.start_report(user=user, shift_date=date, scope="day",
                             messages=plan.messages, photos=plan.photos)
    _add_line(job_id, f"⏳ Пересобираю отчёт за {date} без запроса к ИИ.\n"
                      f"Беру сохранённый разбор переписки: номера станков и "
                      f"показания счётчиков не меняются, меняется только расчёт.")
    try:
        result = day_pipeline.build_day_local(date,
                                              progress=_progress_for(job_id))
    except Exception as e:                 # noqa: BLE001 — показываем человеку
        logger.exception("Бесплатная пересборка за %s упала", date)
        db.finish_report(run_id, ok=False, error=str(e))
        _add_line(job_id, f"⚠️ Отчёт за {date} пересобрать не удалось: {e}")
        _finish(job_id, "error", str(e))
        return
    if not result.reports:
        db.finish_report(run_id, ok=False, note=result.note or "",
                         error="нет полных отчётов")
        _add_line(job_id, "⚠️ Из сохранённого разбора полный отчёт не собрался — "
                          "соберите отчёт через ИИ.")
        _finish(job_id, "error", "нет полных отчётов")
        return
    saved = _send_day(job_id, result, local=True)
    db.finish_report(
        run_id, ok=True, files=saved,
        machines=[r.machine for r in result.reports if r.machine],
        warnings=list(result.ai_warnings or []),
        note="; ".join(result.carry_notes or []))
    _finish(job_id)


def _foreign_lines(job_id: str, foreign) -> None:
    """Текст с фото, который к работе не относится: показать, но не молча выбросить."""
    if not foreign:
        return
    lines = [f"📎 Постороннее с фотографий (в отчёт не включено, "
             f"{len(foreign)} шт.)"]
    for index, name, text in foreign:
        short = " ".join((text or "").split())[:200] or "(текст не читается)"
        lines.append(f"• сообщение №{index}, {name}: {short}")
    lines.append("Если что-то из этого — данные смены, пришлите их текстом, "
                 "и отчёт соберётся заново.")
    _add_line(job_id, "\n".join(lines))


# ───────────────────────────────── сводный отчёт ─────────────────────────────────
def start_period(who: str = "", dates: Optional[List[str]] = None) -> Dict:
    """Завести сборку сводного Excel.

    dates=None → все накопленные смены (как было, когда выбора дат не было);
    иначе — конкретный диапазон, который выбрал человек на странице. Список
    приходит уже отфильтрованным из `api.period()`, здесь он только участвует в
    названии задачи, чтобы две разные выборки не считались одной сборкой.
    """
    dates = list(dates) if dates else \
        [d.date for d in day_pipeline.available_days(newest_first=False)]
    label = f"{dates[0]}—{dates[-1]}" if dates else "период"
    busy = _running("period", label)
    if busy:
        return busy
    job = _new_job("period", label, who)
    threading.Thread(target=_run_period, args=(job["id"], label, who, dates),
                     name="build-period", daemon=True).start()
    return dict(job)


def _run_period(job_id: str, label: str, who: str,
                dates: Optional[List[str]] = None) -> None:
    if not dates:
        dates = [d.date for d in day_pipeline.available_days(newest_first=False)]
    user = db.get_or_create_user(who)
    run_id = db.start_report(user=user, shift_date=label, scope="period",
                             messages=len(dates))
    if not dates:
        _add_line(job_id, "Нет ни одной накопленной смены — сводный файл "
                          "собрать не из чего.")
        db.finish_report(run_id, ok=False, error="нет смен")
        _finish(job_id, "error", "нет смен")
        return
    _add_line(job_id, f"⏳ Собираю сводный Excel за {len(dates)} смен(ы): "
                      f"{', '.join(d[:5] for d in dates)}.\n"
                      f"Каждая смена — отдельный запрос к ИИ, это дольше "
                      f"отчёта за день.")
    try:
        results = day_pipeline.build_days(dates, extract_structured,
                                          settings.ai_enabled, PHOTO_LIMIT,
                                          progress=_progress_for(job_id))
        ready = [r for r in results if r.reports]
        if not ready:
            _add_line(job_id, "Не удалось собрать полный отчёт ни за одну "
                              "смену (ИИ выключен или не справился). Сводный "
                              "файл собрать не из чего.")
            db.finish_report(run_id, ok=False, error="нет полных отчётов")
            _finish(job_id, "error", "нет полных отчётов")
            return
        filename = day_pipeline.period_xlsx_filename(results)
        path = os.path.join(get_export_dir(), filename)
        day_pipeline.export_period_xlsx(results, path)
        _add_file(job_id, path, label)
        skipped = [r.date for r in results if not r.reports]
        text = (f"📗 Сводный отчёт: {len(ready)} смен(ы), лист на каждую "
                f"дату.")
        if skipped:
            text += (" Не вошли (нет полного отчёта): "
                     + ", ".join(d[:5] for d in skipped) + ".")
        _add_line(job_id, text)
        db.finish_report(run_id, ok=True, files=[filename],
                         machines=[m for r in ready for m in r.machines])
        _finish(job_id)
    except Exception as e:                 # noqa: BLE001
        logger.exception("Сводный отчёт собрать не удалось")
        _add_line(job_id, f"⚠️ Сводный отчёт собрать не удалось: {e}")
        db.finish_report(run_id, ok=False, error=str(e))
        _finish(job_id, "error", str(e))

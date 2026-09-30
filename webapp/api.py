"""JSON-ответы сайта: ровно то, что умел бот, но без Telegram.

Слой сознательно тонкий — он ничего не считает. Всё, что здесь есть, это:

* проверить вход (дата в нужном формате, файл не пустой), позвать `services/*`;
* перевести результат в те же текстовые строки, которые бот писал в чат, —
  человек к ним привык, менять формулировки смысла нет;
* записать факт в журнал базы (`ShiftUpload`/`ReportRun`).

Функции модуля не знают про HTTP и возвращают обычные словари, поэтому их
можно вызывать из теста (`tests/test_webapp.py`) и из `server.py` одинаково.
Ошибки, которые должен увидеть пользователь, поднимаются как `ApiError` с
кодом — сервер превращает его в JSON с этим же кодом.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Dict, List, Optional

from config.config import settings
from database import db
from services import (
    day_pipeline,
    file_text,
    report_archive,
    shift_store,
)
from services.file_text import FileTextError

from webapp import jobs, texts

logger = logging.getLogger(__name__)

# Сколько дат показывать в списках. Смен за месяц набирается много, нужны
# почти всегда свежие — остальное видно в журнале и вызовом с датой.
DAY_LIST = 12

# Потолок загруженного файла: сам лимит чтения текста держит file_text
# (MAX_FILE_BYTES), здесь страховка от «пришёл файл на гигабайт» в HTTP-слое.
MAX_UPLOAD_BYTES = file_text.MAX_FILE_BYTES


class ApiError(Exception):
    """Ошибка, которую показываем человеку: код + внятный текст."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


# ─────────────────────────────── helpers ───────────────────────────────
def _norm_date(value: Optional[str], *, required: bool = False) -> Optional[str]:
    """«22.09.2026» (и «22.9.26», и «22/09») → нормальный вид или None."""
    raw = (value or "").strip()
    if not raw:
        if required:
            raise ApiError("Не указано, за какое число это делать.")
        return None
    date = day_pipeline.normalize_date(raw)
    if not date:
        raise ApiError(f"Не понял дату «{raw}». Формат: 22.09.2026.")
    return date


def _date_key(date: str) -> str:
    """«22.09.2026» → «20260922»: такие строки сравниваются как даты.

    Обычное сравнение «22.09.2026» < «03.10.2026» врёт: посчитались бы дни, а
    не даты. `_norm_date` уже привёл формат к ДД.ММ.ГГГГ, поэтому разбираем
    без проверок.
    """
    dd, mm, yyyy = (date or "").split(".")
    return f"{yyyy}{int(mm):02d}{int(dd):02d}"


def _latest_date() -> Optional[str]:
    """Дата последней накопленной смены (то, что делал /day без аргумента)."""
    return day_pipeline.resolve_date(None)


def _user(who: str):
    """Подписать, кто работает. Без подписи можно — просто в журнале будет пусто."""
    return db.get_or_create_user((who or "").strip())


def _loads(value: Optional[str]) -> List:
    try:
        got = json.loads(value or "[]")
    except ValueError:
        return []
    return got if isinstance(got, list) else []


def _day_summary(date: str) -> Dict:
    plan = day_pipeline.summary(date)
    return {"date": plan.date, "messages": plan.messages,
            "photos": plan.photos, "machines": list(plan.machines or []),
            "updated_at": plan.updated_at, "line": plan.line}


def _archived_day(day: report_archive.ArchivedDay) -> Dict:
    return {"date": day.date, "saved_at": day.saved_at,
            "saved_at_text": day.saved_at_text,
            "machines": list(day.machines or []),
            "kinds": day.kinds, "line": day.line,
            "files": [{"name": f.name, "kind": f.kind, "size": f.size,
                       "size_text": f.size_text} for f in day.files]}


# ─────────────────────────────── состояние ───────────────────────────────
def state() -> Dict:
    """Всё, что нужно странице при открытии, одним запросом."""
    return {
        "ai_enabled": settings.ai_enabled,
        # Модель на странице — чтобы по одному взгляду было видно, НА КАКОМ
        # запуске ты сидишь: `.env` читается при старте процесса, и после его
        # правки прежний .exe продолжит спрашивать прежнюю модель.
        "ai_model": settings.ai_model,
        "ai_format": settings.ai_api_format,
        "ocr_enabled": settings.ocr_enabled,
        "today": shift_store.shift_date_for(),
        "latest": _latest_date(),
        "shifts": [_day_summary(d.date)
                   for d in day_pipeline.available_days(limit=DAY_LIST)],
        # Полный список смен (старые первыми) — для выбора периода в сводном
        # отчёте. `shifts` обрезан до DAY_LIST: там список на глаз, а здесь
        # нужны и старые даты, иначе их нельзя включить в сводку.
        "dates": [d.date for d in
                  day_pipeline.available_days(newest_first=False)],
        "archive": [_archived_day(d)
                    for d in report_archive.available_days(limit=DAY_LIST)],
        "uploads": journal()["uploads"],
        "reports": journal()["reports"],
        "exts": list(file_text.supported_extensions()),
        "running": [j for j in jobs.jobs(limit=jobs.MAX_JOBS)
                    if j["state"] == "running"],
    }


def journal(limit: int = 20) -> Dict:
    """Последние загрузки и сборки — по ним видно, чьё сообщение испортило отчёт.

    Имя автора достаем словарём, а не связью SQLAlchemy: строки после закрытия
    сессии «отсоединены», и обращение к `row.user.name` упало бы в другом потоке.
    """
    names = {u.id: u.name for u in db.list_users()}
    uploads = [{"id": r.id, "kind": r.kind, "source": r.source or "",
                "who": names.get(r.user_id, ""),
                "file_kind": r.file_kind or "", "date": r.shift_date,
                "sections": r.sections,
                "machines": _loads(r.machines_json),
                "authors": _loads(r.authors_json),
                "messages": r.messages_total, "photos": r.photos_total,
                "duplicate": bool(r.duplicate),
                "duplicate_of": r.duplicate_of or "",
                "error": r.error or "", "warning": r.warning or "",
                "at": r.created_at.strftime("%d.%m.%Y %H:%M")
                if r.created_at else ""}
               for r in db.recent_uploads(limit=limit)]
    reports = [{"id": r.id, "who": names.get(r.user_id, ""),
                "date": r.shift_date, "scope": r.scope,
                "state": r.state, "ok": bool(r.ok), "messages": r.messages,
                "photos": r.photos, "files": _loads(r.files_json),
                "machines": _loads(r.machines_json),
                "warnings": _loads(r.warnings_json),
                "note": r.note or "", "error": r.error or "",
                "started_at": r.started_at.strftime("%d.%m.%Y %H:%M")
                if r.started_at else "",
                "finished_at": r.finished_at.strftime("%d.%m.%Y %H:%M")
                if r.finished_at else ""}
               for r in db.recent_reports(limit=limit)]
    return {"uploads": uploads, "reports": reports}


# ─────────────────────────────── приём данных ───────────────────────────────
def _file_lines(accepted: shift_store.Accepted, caption: str) -> List[str]:
    # Числа без склонжения: «2 строк» reads wrong, «повторов …: 2» — всегда верно.
    mirrors = (f"Повторов из чужих тем убрано: {accepted.file_mirrors}. Это "
               "одинаковые автор и время до секунды: выгрузка кладёт одно "
               "сообщение в две темы, а иначе отходы считаются дважды."
               if accepted.file_mirrors else "")
    if accepted.file_duplicate:
        reply = [f"📄 Файл «{accepted.file_name}» такой же, как "
                 f"«{accepted.duplicate_of}», он уже принят в смену "
                 f"{accepted.date}. Новых сообщений не появилось — иначе отчёт "
                 "посчитал бы каждое обсуждение дважды.",
                 "Разделы этого файла я перечитал заново и положил вместо "
                 "старых: так исправленный разбор выгрузки доходит до уже "
                 "принятой смены без нового запроса к ИИ."]
        if mirrors:
            reply.append(mirrors)
        if caption:
            reply.append(f"Подпись «{caption[:120]}» не сохранил — данные из "
                         f"файла в смене уже лежат. Если там новое — пришлите "
                         f"её обычным сообщением.")
        reply.append("Если файл на самом деле ДРУГОЙ (исправили показания) — "
                     "очистите смену и пришлите заново.")
        if accepted.quality_warning:
            reply.append(accepted.quality_warning)
        return reply

    reply = [f"📄 Файл «{accepted.file_name}» прочитан "
             f"({accepted.file_kind}) · смена {accepted.date}"]
    if accepted.date_source == "имя файла":
        reply.append("Дату смены взял из имени файла — так надёжнее, чем искать "
                     "её в тексте.")
    elif accepted.date_source == "шапки разделов":
        reply.append("Дату смены взял из шапок разделов, а не из имени файла: "
                     "они спорят — подробности ниже.")
    if accepted.file_machines:
        reply.append(f"Разложил по станкам: "
                     f"{', '.join('№' + m for m in accepted.file_machines)} "
                     f"({accepted.file_sections} разделов)")
        reply.append("Станок каждого раздела взят из заголовка обсуждения, "
                     "поэтому показания не перепутаются между машинами.")
    else:
        reply.append("Разделов «–<номер станка>» в файле не нашёл — сохранил "
                     "переписку как есть, станки определю по тексту.")
    if accepted.file_authors:
        reply.append("Вижу авторов: " + ", ".join(accepted.file_authors))
    if mirrors:
        reply.append(mirrors)
    if accepted.quality_warning:
        reply.append(accepted.quality_warning)
    if caption:
        reply.append(f"Подпись к файлу тоже сохранил: «{caption[:120]}»")
    if accepted.scan.counters:
        reply.append(f"Показаний счётчика — {accepted.scan.counters}")
    reply.append(f"Всего за смену: {accepted.messages} сообщ.")
    reply.append("Когда всё пришлёте — «Отчёт за дату» и выберите это число.")
    return reply


def _message_lines(accepted: shift_store.Accepted) -> List[str]:
    scan = accepted.scan
    seen: List[str] = []
    if scan.machines:
        seen.append("станки " + ", ".join(scan.machines))
    if accepted.photos_work:
        seen.append(f"фото с текстом — {accepted.photos_work}")
    if accepted.photos_vision:
        seen.append(f"фото — {accepted.photos_vision} "
                    f"(текст не снял, отдам картинкой в ИИ)")
    if scan.counters:
        seen.append(f"показания счётчика — {scan.counters}")
    if scan.times:
        seen.append(f"метки времени — {scan.times}")

    reply = [f"✅ Данные приняты · смена {accepted.date} · "
             f"сообщение №{accepted.index}"]
    reply.append("Вижу: " + " · ".join(seen) if seen
                 else "Номер станка в этом сообщении не увидел — это нормально, "
                      "если он был в предыдущих.")
    if accepted.photos_skipped:
        reply.append(f"🖼 Фото без текста — {accepted.photos_skipped}, не "
                     f"сохраняю (в отчёте они бесполезны).")
    if accepted.photos_foreign:
        reply.append(f"📎 Фото с текстом не по работе — "
                     f"{accepted.photos_foreign}: в отчёт не пойдёт, покажу "
                     f"отдельным списком при отчёте за день.")
    if accepted.ocr_warning:
        reply.append(f"⚠️ {accepted.ocr_warning}\nПока распознавания нет, фото "
                     f"уходят в ИИ картинкой — это дороже, но данные не "
                     f"теряются.")
    reply.append(f"Всего за смену: {accepted.messages} сообщ., "
                 f"{accepted.photos} фото.")
    reply.append("Когда всё пришлёте — «Отчёт за дату» и выберите это число.")
    return reply


def _result(accepted: shift_store.Accepted, lines: List[str]) -> Dict:
    return {"ok": True, "date": accepted.date, "lines": lines,
            "shift": _day_summary(accepted.date)}


def _log_upload(user, accepted: shift_store.Accepted, *, kind: str,
                source: str, error: str = "") -> None:
    """Факт загрузки в журнал. Сам приём от ошибки журнала не падает."""
    db.log_upload(
        user=user, kind=kind, source=source,
        shift_date=accepted.date, file_kind=accepted.file_kind,
        sections=accepted.file_sections,
        machines=list(accepted.file_machines or []),
        authors=list(accepted.file_authors or []),
        messages_total=accepted.messages, photos_total=accepted.photos,
        duplicate=accepted.file_duplicate,
        duplicate_of=accepted.duplicate_of,
        error=error, warning=accepted.quality_warning)


def upload_file(*, name: str, blob: bytes, who: str = "",
                date: str = "", caption: str = "") -> Dict:
    """Принять файл с перепиской смены (главный способ: файл приходит целиком)."""
    if not blob:
        raise ApiError("Файл пустой — ничего не пришло.")
    if len(blob) > MAX_UPLOAD_BYTES:
        raise ApiError(f"Файл больше {MAX_UPLOAD_BYTES // 1024 // 1024} МБ — "
                       f"обрежьте его или пришлите переписку частями.")
    user = _user(who)
    asked = _norm_date(date)
    try:
        accepted = shift_store.add_document(name or "файл", blob,
                                            author=(who or "").strip() or None,
                                            date=asked,
                                            caption=(caption or "").strip())
    except FileTextError as e:
        # Формат не тот, файл пустой, слишком большой, не открылся. Данные
        # не теряются — пользователь пришлёт другой файл.
        _log_broken_upload(user, kind="file", source=name, error=str(e),
                           date=asked)
        raise ApiError(f"⚠️ {e}", status=415) from e
    except Exception as e:                       # noqa: BLE001
        logger.exception("Не удалось разобрать файл %s", name)
        _log_broken_upload(user, kind="file", source=name, error=str(e),
                           date=asked)
        raise ApiError(f"⚠️ Не получилось прочитать файл «{name}»: {e}. "
                       f"Пришлите переписку текстом или фотографией — их сайт "
                       f"тоже читает.") from e
    _log_upload(user, accepted, kind="file",
                source=accepted.file_name or name)
    return _result(accepted, _file_lines(accepted, caption))


def _log_broken_upload(user, *, kind: str, source: str, error: str,
                       date: Optional[str]) -> None:
    """Читаемую дату для журнала берём у накопителя: файл-то не разобран."""
    shift_date = date or shift_store.shift_date_for()
    db.log_upload(user=user, kind=kind, source=(source or "")[:255],
                  shift_date=shift_date, error=error)


def add_text(*, text: str, who: str = "", date: str = "",
             machine: str = "") -> Dict:
    """Принять данные смены текстом (можно несколько сообщений подряд)."""
    body = (text or "").strip()
    if not body:
        raise ApiError("Пустое сообщение — данных нет.")
    if body.startswith("/"):
        raise ApiError("Команды сайта не нужны: данные присылайте обычным "
                       "текстом, отчёт собирайте кнопкой.")
    user = _user(who)
    accepted = shift_store.add_message(text=body,
                                      author=(who or "").strip() or None,
                                      date=_norm_date(date),
                                      machine=(machine or "").strip() or None)
    _log_upload(user, accepted, kind="text", source=body[:200])
    return _result(accepted, _message_lines(accepted))


def add_photo(*, name: str, blob: bytes, who: str = "", date: str = "",
              caption: str = "") -> Dict:
    """Принять фотографию (скрин переписки, страница журнала) с подписью."""
    if not blob:
        raise ApiError("Файл фотографии пустой.")
    if len(blob) > MAX_UPLOAD_BYTES:
        raise ApiError("Фото слишком большое.")
    user = _user(who)
    accepted = shift_store.add_message(text=(caption or "").strip(),
                                      photos=[blob],
                                      author=(who or "").strip() or None,
                                      date=_norm_date(date))
    _log_upload(user, accepted, kind="photo", source=name or "фото")
    return _result(accepted, _message_lines(accepted))


# ─────────────────────────────── отчёты ───────────────────────────────
def build(date: str = "", who: str = "") -> Dict:
    """Собрать отчёт за смену — фоновой задачей, страница не ждёт ИИ."""
    asked = _norm_date(date) or _latest_date()
    if not asked:
        raise ApiError("Пока нет данных за смену. Пришлите переписку по станкам "
                       "— файлом или текстом; она накопится, и потом соберётся "
                       "отчёт.")
    plan = day_pipeline.summary(asked)
    if not plan.messages:
        raise ApiError(f"За смену {asked} ничего не накоплено. Посмотреть, что "
                       f"есть: раздел «Отчёт за дату».")
    return jobs.start_build(asked, who)


def rebuild(date: str = "", who: str = "") -> Dict:
    """Пересобрать отчёт за смену из сохранённого разбора ИИ — бесплатно.

    Нужна после изменения правил: цифры пересчитываются кодом, а переписку
    модель уже разбирала. Если разбора за дату нет — честно говорим, что
    сначала нужен обычный сбор через ИИ, а не молча считаем заново.
    """
    asked = _norm_date(date) or _latest_date()
    if not asked:
        raise ApiError("Пока нет ни одной накопленной смены — пересобирать нечего.")
    if not day_pipeline.has_saved_raw(asked):
        raise ApiError(
            f"За {asked} нет сохранённого разбора ИИ — бесплатно пересобрать "
            f"не из чего. Сначала соберите отчёт кнопкой «Собрать отчёт» "
            f"(это запрос к ИИ).")
    return jobs.start_local_build(asked, who)


def period(who: str = "", start: str = "", end: str = "") -> Dict:
    """Сводный Excel за выбранный период — лист на каждую смену.

    Даты берём только из накопленного: листа за день, по которому нет данных,
    в файле быть не должно, а «нет данных» на листе человек читает как «станок
    не работал». start/end не заданы → все смены, как было до выбора дат.
    """
    dates = [d.date for d in day_pipeline.available_days(newest_first=False)]
    first, last = _norm_date(start), _norm_date(end)
    if first and last and _date_key(first) > _date_key(last):
        raise ApiError(f"Начало периода ({first}) позже конца ({last}) — "
                       f"выберите даты в правильном порядке.")
    if first or last:
        dates = [d for d in dates
                 if (not first or _date_key(d) >= _date_key(first))
                 and (not last or _date_key(d) <= _date_key(last))]
    if not dates:
        raise ApiError("В выбранном периоде нет ни одной накопленной смены: "
                       "пришлите данные за эти даты или выберите другие.")
    return jobs.start_period(who, dates)


def job(job_id: str) -> Dict:
    found = jobs.job((job_id or "").strip())
    if not found:
        raise ApiError("Задача уже выгружена из памяти (сайт перезапускался?) — "
                       "результаты сборки остались в «Готовых файлах».",
                       status=404)
    return found


def archive() -> List[Dict]:
    return [_archived_day(d) for d in
            report_archive.available_days(limit=DAY_LIST)]


def archive_day(date: str) -> Dict:
    """Готовые файлы за дату. ИИ здесь не вызывается: файлы уже посчитаны."""
    asked = _norm_date(date, required=True)
    has_raw = day_pipeline.has_saved_raw(asked)
    day = report_archive.archived_day(asked)
    if day.ok:
        return {"date": asked, "has_files": True, "has_raw": has_raw,
                "saved_at_text": day.saved_at_text,
                "machines": day.machines,
                "files": [{"name": f.name, "kind": f.kind, "size": f.size,
                           "size_text": f.size_text} for f in day.files],
                "lines": [f"🗄 Готовые файлы за {asked}"
                          + (f" (собраны {day.saved_at_text})"
                             if day.saved_at_text else "")
                          + ":"
                          + ("\nСтанки в отчёте: " + ", ".join(day.machines) + "."
                             if day.machines else "")
                          + "\nЭто те же файлы, что выходили при сборке — ИИ "
                            "не запрашивался."]}
    plan = day_pipeline.summary(asked)
    if plan.messages:
        lines = [f"🗄 За {asked} готовых файлов нет — отчёт по этой смене ещё "
                 f"не собирался.\n"
                 f"Накоплено: {plan.messages} сообщ., {plan.photos} фото"
                 + (f", станки: {', '.join(plan.machines)}" if plan.machines
                    else "")
                 + ".\nСобрать сейчас? Это один запрос к ИИ, 20–60 секунд."]
        return {"date": asked, "has_files": False, "files": [],
                "has_raw": has_raw,
                "shift": _day_summary(asked), "can_build": True,
                "lines": lines}
    return {"date": asked, "has_files": False, "files": [], "can_build": False,
            "has_raw": has_raw,
            "lines": [f"🗄 За {asked} нет ни готовых файлов, ни накопленных "
                      f"данных.\nЧто есть в архиве — раздел «Готовые файлы», "
                      f"что накоплено — «Отчёт за дату»."]}


def download(date: str, name: str) -> Dict:
    """Путь к готовому файлу для отдачи. Только имена из архива и выгрузок.

    Имя и дата проверяются строго: наружу отдаём файл, который лежит в
    разрешённом каталоге, а не любой путь, который придумал клиент.
    """
    asked = _norm_date(date, required=True)
    clean = os.path.basename((name or "").strip())
    if not clean:
        raise ApiError("Не указано, какой файл скачивать.", status=404)
    allowed = [f.path for f in report_archive.day_files(asked)]
    allowed.append(os.path.join(jobs.get_export_dir(), clean))
    for path in allowed:
        real = os.path.realpath(path)
        if os.path.basename(real) == clean and os.path.isfile(real):
            return {"path": real, "name": clean}
    raise ApiError(f"Файла «{clean}» за {asked} нет — соберите отчёт заново.",
                   status=404)


# ─────────────────────────────── прочее ───────────────────────────────
def reset(date: str = "") -> Dict:
    """Стереть накопленное за смену (например, если прислали лишнее)."""
    asked = _norm_date(date) or shift_store.latest_day()
    if not asked:
        return {"ok": True, "removed": 0, "lines": ["Нечего стирать — данных нет."]}
    removed = shift_store.clear_day(asked)
    line = (f"🧹 Смена {asked} очищена: удалено сообщений — {removed}."
            if removed else f"За {asked} и так ничего не было.")
    return {"ok": True, "date": asked, "removed": removed, "lines": [line]}


def help_text() -> Dict:
    return {"text": texts.HELP}


def day_detail(date: str) -> Dict:
    """Что накоплено за дату: по станкам и кому принадлежит (из day.json)."""
    asked = _norm_date(date, required=True)
    data = shift_store.load_day(asked)
    messages = data.get("messages") or []
    return {"date": asked,
            "shift": _day_summary(asked),
            "documents": shift_store.accepted_documents(asked),
            "messages": [{"index": i + 1,
                          "machine": m.get("machine"),
                          "author": m.get("author") or "",
                          "thread": m.get("thread") or "",
                          "at": m.get("added_at") or "",
                          "photos": len(m.get("photos") or []),
                          "text": (m.get("text") or "")[:2000]}
                         for i, m in enumerate(messages)],
            "stats": shift_store.day_stats(asked)}

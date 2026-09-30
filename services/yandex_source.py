"""Приём переписки из Яндекс Мессенджера — заготовка под будущий доступ.

Порядок в рабочем чате (со слов заказчика):

    руководитель:  «91 машина информация за смену 20.08.2026»   ← корень темы
    рабочие:       данные по этой машине — ОТВЕТАМИ в обсуждении

Отсюда всё устройство модуля:

* Номер станка и дату смены даёт ЗАГОЛОВОК ОБСУЖДЕНИЯ, а не сообщения
  рабочих. Это сильно лучше сегодняшнего угадывания: «251» в тексте мастера
  может быть и станком, и количеством шпуль, а в заголовке — точно станок.
* Значит сообщения надо группировать по обсуждению (thread), а не по тексту,
  а карту «обсуждение → станок, дата» — помнить между опросами: заголовок
  написан утром, а ответы приходят весь день.

Что здесь уже работает БЕЗ доступа к API (и покрыто тестом):

    normalize_updates()   — приводит ответ API к списку сообщений, терпимо
                            относясь к именам полей (text/body/message …);
    parse_thread_header() — «91 машина информация за смену 20.08.2026»
                            → ('91', '20.08.2026');
    import_messages()     — раскладывает сообщения по сменам в обычный
                            накопитель data/shifts/<дата>/day.json;
    CLI --file dump.json  — прогнать сохранённый ответ API или руками
                            собранный JSON и получить готовую смену.

Что подставить, когда дадут доступ: РОВНО ДВЕ функции — `_api_get()` и
`_download_file()`. Всё, что после накопителя (ИИ, расчёт часов по счётчику,
Excel), не меняется вообще: для отчёта нет разницы, приехало сообщение из
Telegram или из Мессенджера.

Запуск без бота:

    python -m services.yandex_source --file tests/yandex_updates_sample.json
    python -m services.yandex_source --file dump.json --dry-run
    python -m services.yandex_source --poll        # заработает после доступа
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

from . import machine_rates, shift_store

logger = logging.getLogger(__name__)


class YandexSourceError(RuntimeError):
    """Не получилось забрать переписку (нет доступа, сеть, формат ответа)."""


# ─────────────────────────── настройки (лениво) ───────────────────────────
# Импорт settings внутри функций: модуль обязан оставаться на чистом stdlib,
# чтобы тесты гонялись там, где нет pydantic-settings.

def _settings():
    try:
        from config.config import settings
    except ImportError as e:
        # В песочнице и на чистой машине нет pydantic-settings: понятный текст
        # вместо трассировки, иначе непонятно, что делать.
        raise YandexSourceError(
            "Не читаются настройки (%s). Установите зависимости: "
            "py -m pip install -r requirements.txt" % e)
    return settings


# ────────────────────── нормализация ответа API ──────────────────────
# Точную схему ответа увидим только с доступом, поэтому берём поля по
# нескольким возможным именам. Это дешевле, чем потом переписывать разбор:
# подставите реальный ответ — скорее всего попадёте в один из вариантов.

_LIST_KEYS = ("messages", "updates", "result", "items", "data", "events")
_ALIASES: Dict[str, Tuple[str, ...]] = {
    "message_id": ("message_id", "id", "msg_id", "timestamp_id", "uid"),
    "thread_id": ("thread_id", "thread", "root_message_id", "reply_to_message_id",
                  "reply_to_id", "parent_id", "topic_id"),
    "author": ("author", "from", "sender", "user", "login", "display_name",
               "nickname", "name"),
    "text": ("text", "body", "message_text", "caption", "content"),
    "sent_at": ("sent_at", "timestamp", "ts", "date", "created_at", "time"),
    "files": ("files", "attachments", "images", "photos", "media"),
}
_AUTHOR_NAME_KEYS = ("display_name", "name", "login", "nickname", "title",
                     "first_name", "id")
_FILE_ID_KEYS = ("file_id", "id", "url", "href", "download_url", "path")


def _pick(raw: Dict[str, Any], field_name: str) -> Any:
    for key in _ALIASES[field_name]:
        if key in raw and raw[key] not in (None, ""):
            return raw[key]
    return None


def _as_author(value: Any) -> Optional[str]:
    """Автор бывает строкой, а бывает объектом {display_name: …}."""
    if value is None:
        return None
    if isinstance(value, dict):
        for key in _AUTHOR_NAME_KEYS:
            if value.get(key):
                return str(value[key])
        return None
    return str(value)


def _as_file_ids(value: Any) -> List[str]:
    """Вложения: список строк, список объектов или один объект."""
    if not value:
        return []
    items = value if isinstance(value, list) else [value]
    out: List[str] = []
    for item in items:
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, dict):
            for key in _FILE_ID_KEYS:
                if item.get(key):
                    out.append(str(item[key]))
                    break
    return out


@dataclass
class Incoming:
    """Одно сообщение чата, приведённое к нашему виду."""
    message_id: str
    text: str = ""
    thread_id: Optional[str] = None
    author: Optional[str] = None
    sent_at: Optional[str] = None
    file_ids: List[str] = field(default_factory=list)
    # Заполняется по заголовку обсуждения, а не по тексту сообщения.
    machine: Optional[str] = None
    date: Optional[str] = None
    is_header: bool = False


def normalize_updates(payload: Any) -> List[Incoming]:
    """Ответ API (в любой из вероятных форм) → список Incoming.

    Принимает и голый список сообщений, и объект-обёртку
    {"messages": [...]} / {"result": {"messages": [...]}} и т.п.
    """
    if payload is None:
        return []
    raw_list: Optional[Iterable[Any]] = None
    if isinstance(payload, list):
        raw_list = payload
    elif isinstance(payload, dict):
        for key in _LIST_KEYS:
            value = payload.get(key)
            if isinstance(value, list):
                raw_list = value
                break
            if isinstance(value, dict):           # {"result": {"messages": …}}
                nested = normalize_updates(value)
                if nested:
                    return nested
    if raw_list is None:
        raise YandexSourceError(
            "Не нашёл списка сообщений в ответе. Ожидались ключи: "
            + ", ".join(_LIST_KEYS))

    out: List[Incoming] = []
    for n, raw in enumerate(raw_list, start=1):
        if not isinstance(raw, dict):
            continue
        text = _pick(raw, "text")
        # Текст иногда лежит вложенным объектом {"text": {"body": "…"}}.
        if isinstance(text, dict):
            text = _pick(text, "text") or ""
        msg_id = _pick(raw, "message_id")
        thread = _pick(raw, "thread_id")
        if isinstance(thread, dict):
            thread = _pick(thread, "message_id") or _pick(thread, "thread_id")
        out.append(Incoming(
            message_id=str(msg_id) if msg_id is not None else f"noid-{n}",
            text=str(text or "").strip(),
            thread_id=str(thread) if thread not in (None, "") else None,
            author=_as_author(_pick(raw, "author")),
            sent_at=(str(_pick(raw, "sent_at"))
                     if _pick(raw, "sent_at") is not None else None),
            file_ids=_as_file_ids(_pick(raw, "files")),
        ))
    return out


# ─────────────────── заголовок обсуждения → станок и дата ───────────────────

# «91 машина», «машина 91», «станок №121», «14тр машина».
# «\d{2,3}т[р]?» — машинки с буквенным номером мастер пишет и «14тр», и
# «14т» (заголовок темы в выгрузке от 22.09.2026); ключ справочника — «14тр».
_MACHINE_RE = re.compile(
    r"(?:(?P<before>\d{2,3}т[р]?|\d{2,3})\s*(?:машин\w*|станк\w*|м\.)"
    r"|(?:машин\w*|станк\w*)\s*[№#N]?\s*(?P<after>\d{2,3}т[р]?|\d{2,3}))",
    re.IGNORECASE)
# Признак того, что это именно заголовок смены, а не обычное сообщение:
# «информация за смену», «за смену», «данные за 20.08.2026», «отчет за смену».
_HEADER_HINT_RE = re.compile(
    r"(за\s+смену|информац\w*|данные|отч[её]т|смена)", re.IGNORECASE)
# «…за смену 22.09.2026г.» — ПРЯМОЕ указание даты смены. Обычный поиск даты
# по заголовку («15т машина(в стопе до 15.10.2026 г.) - информация за смену
# 22.09.2026г.») мог взять первую попавшуюся дату — и это оказался срок стопа,
# а не смена.
_SHIFT_DATE_RE = re.compile(
    r"за\s+смену\s*\(?\s*(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{2,4})", re.IGNORECASE)


def parse_thread_header(text: str,
                        now: Optional[datetime] = None
                        ) -> Optional[Tuple[str, str]]:
    """«91 машина информация за смену 20.08.2026» → ('91', '20.08.2026').

    Возвращает None, если это не похоже на заголовок обсуждения: тогда
    сообщение считается обычной строкой данных от рабочего.

    Дату берём тем же кодом, что и при приёме из Telegram
    (`shift_store.detect_shift_date`): если в заголовке её нет — это текущая
    смена.
    """
    text = (text or "").strip()
    if not text:
        return None
    m = _MACHINE_RE.search(text)
    if not m:
        return None
    if not _HEADER_HINT_RE.search(text):
        return None
    raw = (m.group("before") or m.group("after") or "").replace(" ", "")
    machine = machine_rates._norm_key(raw)
    shift = _SHIFT_DATE_RE.search(text)
    if shift:
        day, month, year = (int(shift.group(1)), int(shift.group(2)),
                            int(shift.group(3)))
        if year < 100:
            year += 2000
        try:
            date = datetime(year, month, day).strftime("%d.%m.%Y")
        except ValueError:
            date = None
    else:
        date = None
    return machine, date or shift_store.detect_shift_date(text, now)


def _machine_warning(machine: str) -> Optional[str]:
    """Заголовок называет станок, которого нет в справочнике норм."""
    if machine in set(machine_rates.known_machines()):
        return None
    return (f"В заголовке обсуждения станок №{machine}, но его нет в "
            f"services/machine_rates.json — время работы по счётчику для него "
            f"не посчитается («нет нормы выработки»). Проверьте номер или "
            f"добавьте норму.")


# ──────────────────────── состояние между опросами ────────────────────────
# Нужно для двух вещей: не завести дубликаты при повторном опросе и помнить
# «обсуждение → станок, дата» (заголовок пришёл утром, ответы — весь день).

_SEEN_LIMIT = 5000


def _state_path() -> str:
    return os.path.join(os.path.dirname(shift_store.get_data_dir().rstrip("/\\")),
                        "yandex_state.json")


def load_state() -> Dict[str, Any]:
    try:
        with open(_state_path(), "r", encoding="utf-8") as f:
            state = json.load(f)
    except FileNotFoundError:
        state = {}
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Состояние приёма повреждено (%s) — начинаю с нуля", e)
        state = {}
    state.setdefault("seen", [])
    state.setdefault("threads", {})
    state.setdefault("cursor", None)
    return state


def save_state(state: Dict[str, Any]) -> None:
    state["seen"] = state.get("seen", [])[-_SEEN_LIMIT:]
    path = _state_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


# ──────────────────────────── импорт в накопитель ────────────────────────────

@dataclass
class ImportResult:
    imported: int = 0
    skipped_dupes: int = 0
    headers: int = 0
    threads: Dict[str, Dict[str, str]] = field(default_factory=dict)
    days: Dict[str, int] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    # Итог по вложенным фотографиям после бесплатного OCR.
    photos_saved: int = 0
    photos_skipped: int = 0
    photos_foreign: int = 0

    def describe(self) -> str:
        if not self.imported and not self.skipped_dupes:
            return "Новых сообщений нет."
        lines = [f"Принято сообщений: {self.imported}"
                 + (f", пропущено повторов: {self.skipped_dupes}"
                    if self.skipped_dupes else "")]
        if self.photos_saved or self.photos_skipped:
            lines.append(
                f"  фото: с текстом — {self.photos_saved}"
                + (f", без текста (не храню) — {self.photos_skipped}"
                   if self.photos_skipped else "")
                + (f", текст не про работу — {self.photos_foreign}"
                   if self.photos_foreign else ""))
        for thread, info in self.threads.items():
            lines.append(f"  обсуждение {thread}: станок №{info['machine']}, "
                         f"смена {info['date']}")
        for date, count in sorted(self.days.items()):
            lines.append(f"  смена {date}: +{count} сообщ.")
        lines += [f"  ⚠️ {w}" for w in self.warnings]
        return "\n".join(lines)


def assign_headers(messages: List[Incoming],
                   threads: Optional[Dict[str, Dict[str, str]]] = None,
                   now: Optional[datetime] = None) -> Dict[str, Dict[str, str]]:
    """Разметить сообщения станком и датой по заголовкам их обсуждений.

    `threads` — карта, известная с прошлых опросов (дополняется на месте).
    Сообщение-заголовок помечается `is_header=True`: в накопитель оно не
    попадает, данных в нём нет.

    Заголовком считается ТОЛЬКО корень обсуждения (у сообщения нет ссылки на
    ветку либо она равна его собственному id). Это защита от потери данных:
    рабочий может написать в ответе «данные по 121 машине», и без проверки
    корня такое сообщение приняли бы за заголовок и выбросили.

    Если в чате обсуждений нет вообще (плоский поток), заголовок работает как
    «контекст до следующего заголовка» — сообщения после него считаются
    относящимися к его станку.
    """
    threads = threads if threads is not None else {}

    for msg in messages:
        is_root = msg.thread_id is None or msg.thread_id == msg.message_id
        if not is_root:
            continue
        parsed = parse_thread_header(msg.text, now)
        if not parsed:
            continue
        machine, date = parsed
        threads[msg.thread_id or msg.message_id] = {"machine": machine,
                                                    "date": date}
        msg.is_header = True
        msg.machine, msg.date = machine, date

    flat_context: Dict[str, str] = {}
    for msg in messages:
        if msg.is_header:
            flat_context = {"machine": msg.machine or "", "date": msg.date or ""}
            continue
        info = threads.get(msg.thread_id or "")
        if info is None and msg.thread_id is None:
            info = flat_context or None
        info = info or {}
        msg.machine = info.get("machine") or None
        msg.date = info.get("date") or None
    return threads


def import_messages(messages: List[Incoming],
                    now: Optional[datetime] = None,
                    dry_run: bool = False) -> ImportResult:
    """Сложить сообщения обсуждений в накопитель смены.

    Заголовки в накопитель не идут (в них нет данных), но задают станок и
    дату для всех ответов своей ветки. Сообщение без обсуждения не теряется:
    оно ложится в смену по дате из текста — как при приёме из Telegram.
    """
    state = load_state()
    result = ImportResult()
    threads = assign_headers(messages, state["threads"], now)
    # Повторы считаем и в --dry-run: иначе предварительный прогон покажет
    # больше сообщений, чем реально примется.
    seen = set(state["seen"])

    for msg in messages:
        if msg.is_header:
            result.headers += 1
            key = msg.thread_id or msg.message_id
            result.threads[key] = {"machine": msg.machine or "?",
                                   "date": msg.date or "?"}
            warning = _machine_warning(msg.machine or "")
            if warning and warning not in result.warnings:
                result.warnings.append(warning)
            continue
        if msg.message_id in seen:
            result.skipped_dupes += 1
            continue
        if not msg.text and not msg.file_ids:
            continue

        photos: List[bytes] = []
        for file_id in msg.file_ids:
            try:
                blob = _download_file(file_id)
            except YandexSourceError as e:
                # Фото не скачалось — сообщение всё равно сохраняем, иначе
                # потеряем текст. О пропуске честно пишем в предупреждения.
                warning = f"Вложение {file_id} не скачано: {e}"
                if warning not in result.warnings:
                    result.warnings.append(warning)
                continue
            if blob:
                photos.append(blob)

        if msg.thread_id and not msg.machine:
            warning = (f"Обсуждение {msg.thread_id}: заголовок со станком не "
                       f"найден — сообщения уйдут в смену без номера станка. "
                       f"Проверьте, что руководитель написал «NN машина "
                       f"информация за смену ДД.ММ.ГГГГ».")
            if warning not in result.warnings:
                result.warnings.append(warning)

        if dry_run:
            date = msg.date or shift_store.detect_shift_date(msg.text, now)
        else:
            accepted = shift_store.add_message(
                text=msg.text, photos=photos, author=msg.author,
                date=msg.date, now=now, machine=msg.machine,
                thread=msg.thread_id)
            date = accepted.date
            state["seen"].append(msg.message_id)
            # Фото из чата фильтрует OCR: «показать материал» отбрасывается,
            # посторонний текст выносится отдельно (services/photo_ocr.py).
            result.photos_saved += accepted.photos_saved
            result.photos_skipped += accepted.photos_skipped
            result.photos_foreign += accepted.photos_foreign
            if accepted.ocr_warning and accepted.ocr_warning \
                    not in result.warnings:
                result.warnings.append(accepted.ocr_warning)
        seen.add(msg.message_id)
        result.imported += 1
        result.days[date] = result.days.get(date, 0) + 1

    if not dry_run:
        state["threads"] = threads
        save_state(state)
    return result


# ──────────────────── две функции под реальный доступ ────────────────────
# ЗДЕСЬ И ТОЛЬКО ЗДЕСЬ появится обращение к Яндексу. Всё остальное в проекте
# уже готово и протестировано.

def _require_credentials() -> Tuple[str, str, str]:
    s = _settings()
    token = (getattr(s, "yandex_token", "") or "").strip()
    chat_id = (getattr(s, "yandex_chat_id", "") or "").strip()
    base_url = (getattr(s, "yandex_base_url", "") or "").strip()
    missing = [name for name, value in (("YANDEX_TOKEN", token),
                                        ("YANDEX_CHAT_ID", chat_id),
                                        ("YANDEX_BASE_URL", base_url))
               if not value]
    if missing:
        raise YandexSourceError(
            "Приём из Мессенджера не настроен: не заполнено "
            + ", ".join(missing)
            + ". Заполните в .env (секреты храните только там) и добавьте "
              "бота в рабочий чат.")
    return token, chat_id, base_url


def _api_get(path: str, params: Optional[Dict[str, Any]] = None) -> Any:
    """ЗАГЛУШКА. Здесь будет один HTTP-запрос к API Мессенджера.

    Когда появится доступ, тело станет примерно таким (stdlib, без pip —
    как в services/ai_extract.py):

        token, chat_id, base_url = _require_credentials()
        query = urllib.parse.urlencode({**(params or {}), "chat_id": chat_id})
        req = urllib.request.Request(
            f"{base_url.rstrip('/')}{path}?{query}",
            headers={"Authorization": f"OAuth {token}"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))

    Точные path/заголовок авторизации/имя курсора берём из документации,
    когда её дадут: сейчас гадать смысла нет, а разбор ответа уже терпим к
    именам полей (см. normalize_updates).
    """
    _require_credentials()
    raise YandexSourceError(
        "Запрос к API Мессенджера ещё не подключён (нужен доступ и точный "
        "адрес метода). Пока пользуйтесь --file: сохранённый ответ API или "
        "выгрузку чата в JSON модуль разбирает уже сейчас.")


def _download_file(file_id: str) -> Optional[bytes]:
    """ЗАГЛУШКА скачивания вложения (фото журнала, скрин).

    Появится вместе с доступом: GET по ссылке из сообщения с тем же
    Authorization, вернуть `resp.read()`. Возврат None означает «вложения
    нет» и не считается ошибкой.
    """
    raise YandexSourceError("скачивание вложений ещё не подключено")


def poll_once(now: Optional[datetime] = None) -> ImportResult:
    """Один опрос чата: забрать новое и сложить в накопитель.

    Вызывать по таймеру (например, раз в 2 минуты) или один раз перед
    формированием отчёта — на расчёт это не влияет, накопитель один и тот же.
    """
    state = load_state()
    payload = _api_get("/messages", {"since": state.get("cursor")})
    return import_messages(normalize_updates(payload), now=now)


def handle_webhook(payload: Any, now: Optional[datetime] = None) -> ImportResult:
    """Если Мессенджер умеет присылать события сам — точка входа для вебхука."""
    return import_messages(normalize_updates(payload), now=now)


def import_from_file(path: str, now: Optional[datetime] = None,
                     dry_run: bool = False) -> ImportResult:
    """Разобрать сохранённый ответ API / выгрузку чата из файла JSON."""
    with open(path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    return import_messages(normalize_updates(payload), now=now, dry_run=dry_run)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Приём переписки из Яндекс Мессенджера в накопитель смены")
    parser.add_argument("--file", help="JSON: сохранённый ответ API или "
                                       "выгрузка чата")
    parser.add_argument("--poll", action="store_true",
                        help="опросить API (заработает после доступа)")
    parser.add_argument("--dry-run", action="store_true",
                        help="показать, что было бы принято, и ничего не писать")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")

    try:
        if args.file:
            result = import_from_file(args.file, dry_run=args.dry_run)
        elif args.poll:
            result = poll_once()
        else:
            parser.error("укажите --file или --poll")
            return 2
    except YandexSourceError as e:
        print(f"❌ {e}")
        return 1
    print(result.describe())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

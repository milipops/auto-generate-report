"""Разбор ПО ОДНОМУ СТАНКУ ЗА ВЫЗОВ ИИ (сеть не нужна).

Проверяем, что:
  * номера станков находятся в тексте без ИИ;
  * на каждый станок уходит СВОЙ запрос со всей перепиской и своей
    инструкцией «разбери только №NNN»;
  * чужие станки, если модель их всё же вернула, отбрасываются (иначе станки
    задваиваются между вызовами);
  * падение одного станка не ломает остальные.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import ai_extract


TEXT = (
    "251 машина. Камбаров. Об.сч. 250955 в 07:00, 254030 в 19:00.\n"
    "151 машина. Вашурина. Об.сч. 111000 в 07:10, 112000 в 18:50.\n"
    "98 машина. Дроздова. Об.сч. 21136950 → 21140500.\n"
)


class _S:
    ai_enabled = True
    ai_api_key = "test"
    ai_api_format = "openai"
    ai_model = "test-model"
    ai_max_tokens = 1000
    ai_timeout = 10
    ai_retries = 0
    ai_per_machine = True


def _patch(monkey_calls, answers):
    """Подменить сетевой вызов: возвращаем заготовленный JSON по инструкции."""
    def fake_call(text, images, instruction=None):
        monkey_calls.append((text, instruction))
        for num, payload in answers.items():
            if f"№{num}" in (instruction or ""):
                return json.dumps(payload)
        return json.dumps({"date": None, "machines": []})
    return fake_call


def test_detect_machines_without_ai():
    found = ai_extract.detect_machines(TEXT)
    assert found == ["251", "151", "98"], found
    print("✅ станки найдены без ИИ:", found)


def test_one_call_per_machine():
    calls = []
    answers = {
        "251": {"date": "27.08.2026", "machines": [{"machine": "251",
                                                    "operator": "Камбаров"}]},
        "151": {"date": None, "machines": [{"machine": "151",
                                            "operator": "Вашурина"}]},
        "98": {"date": None, "machines": [{"machine": "98",
                                           "operator": "Дроздова"}]},
    }
    old_call, old_settings = ai_extract._call_openai, ai_extract._settings
    ai_extract._call_openai = _patch(calls, answers)
    ai_extract._settings = lambda: _S()
    try:
        data = ai_extract.extract_structured(TEXT)
    finally:
        ai_extract._call_openai = old_call
        ai_extract._settings = old_settings

    assert len(calls) == 3, len(calls)
    # в каждом вызове — вся переписка и указание на свой станок
    for (text, instruction), num in zip(calls, ("251", "151", "98")):
        assert text == TEXT
        assert f"№{num}" in instruction, instruction
    nums = [m["machine"] for m in data["machines"]]
    assert nums == ["251", "151", "98"], nums
    assert data["date"] == "27.08.2026", data["date"]
    print("✅ по одному запросу на станок:", nums)


def test_foreign_machines_dropped():
    """Модель вернула лишний станок — он не должен попасть в день дважды."""
    calls = []
    answers = {
        "251": {"machines": [{"machine": "251"}, {"machine": "151"}]},
        "151": {"machines": [{"machine": "151"}]},
        "98": {"machines": [{"machine": "98"}]},
    }
    old_call, old_settings = ai_extract._call_openai, ai_extract._settings
    ai_extract._call_openai = _patch(calls, answers)
    ai_extract._settings = lambda: _S()
    try:
        data = ai_extract.extract_structured(TEXT)
    finally:
        ai_extract._call_openai = old_call
        ai_extract._settings = old_settings
    nums = [m["machine"] for m in data["machines"]]
    assert nums == ["251", "151", "98"], nums
    print("✅ лишний станок из ответа отброшен:", nums)


def test_one_machine_failure_does_not_break_day():
    calls = []

    def fake_call(text, images, instruction=None):
        calls.append(instruction)
        if "№151" in (instruction or ""):
            raise ai_extract.AIExtractError("обрыв связи")
        return json.dumps({"machines": [{"machine": "251"}]}
                          if "№251" in (instruction or "")
                          else {"machines": [{"machine": "98"}]})

    old_call, old_settings = ai_extract._call_openai, ai_extract._settings
    old_pause = ai_extract.MACHINE_RETRY_PAUSE_S
    ai_extract._call_openai = fake_call
    ai_extract._settings = lambda: _S()
    ai_extract.MACHINE_RETRY_PAUSE_S = 0.0     # здесь важна ветка, а не пауза
    try:
        data = ai_extract.extract_structured(TEXT)
    finally:
        ai_extract._call_openai = old_call
        ai_extract._settings = old_settings
        ai_extract.MACHINE_RETRY_PAUSE_S = old_pause
    nums = [m["machine"] for m in data["machines"]]
    assert nums == ["251", "98"], nums
    assert data["warnings"] and "151" in data["warnings"][0], data["warnings"]
    print("✅ упавший станок не ломает день:", nums, data["warnings"])


def test_machine_from_topic_header_gets_its_own_call():
    """Номер из заголовка темы — станок, даже если его нет в справочнике норм.

    Прогон 21–22.09.2026: «91 машина(плановый пуск 23.09.2026г.)» была и в
    переписке, и в накопителе, но в отчёт не попала НИ ОДНОЙ строкой:
    detect_machines брал номера только из справочника норм, а №91 в нём нет, —
    и ИИ о нём просто не спрашивали. Теперь шапка раздела («обсуждение по станку
    №91», её кладёт shift_store.day_text) тоже даёт станок; считается он как
    «нет нормы», но не молчит.
    """
    text = ("--- сообщение 3, прислал Мастер, обсуждение по станку №91 "
            "(номер из заголовка темы, не из текста) ---\n"
            "91 машина(плановый пуск 23.09.2026г.)\n") + TEXT
    assert "91" in ai_extract.detect_machines(text)

    calls = []
    answers = {num: {"date": None, "machines": [{"machine": num,
                                                 "operator": "Кто-то"}]}
               for num in ("251", "151", "98", "91")}
    old_call, old_settings = ai_extract._call_openai, ai_extract._settings
    ai_extract._call_openai = _patch(calls, answers)
    ai_extract._settings = lambda: _S()
    try:
        data = ai_extract.extract_structured(text)
    finally:
        ai_extract._call_openai = old_call
        ai_extract._settings = old_settings
    nums = [m["machine"] for m in data["machines"]]
    assert sorted(nums) == ["151", "251", "91", "98"], nums
    assert len(calls) == 4, [i for _t, i in calls]
    print("✅ станок из заголовка темы спрашиваем отдельно:", nums)


# ─────────── отказ шлюза: повторять с паузой, а не в пустую ───────────

def test_gateway_failure_counts_as_retryable():
    """502 и обрыв соединения — «сервер сейчас не отвечает», не «данных нет».

    Прогон 21.09.2026 20:56: станок №98 ответил HTTP 502 и двумя обрывами
    соединения. Все три попытки прошли за 0,2 с, потому что ни одна из этих
    ошибок не считалась повторяемой, — и сборка длиной 35 минут вышла без
    данных по станку.
    """
    assert isinstance(ai_extract._http_error(502, ""), ai_extract.AIRetryableError)
    assert isinstance(ai_extract._http_error(429, ""), ai_extract.AIRetryableError)
    assert isinstance(ai_extract._network_error("EOF occurred in violation "
                                                "of protocol"),
                      ai_extract.AIRetryableError)
    # Неверный ключ и отсутствие ответа по таймауту повтором не лечатся.
    assert not isinstance(ai_extract._http_error(401, "bad key"),
                          ai_extract.AIRetryableError)
    assert not isinstance(ai_extract._http_error(404, "no model"),
                          ai_extract.AIRetryableError)


def test_reask_waits_for_the_gateway():
    """Между переспросами станка есть растущая пауза — шлюз успевает встать."""
    class Clock:
        def __init__(self):
            self.waited = []

        def sleep(self, seconds):
            self.waited.append(seconds)

    state = {"tries": 0}
    clock = Clock()

    def fake_call(text, images, instruction=None):
        if "№151" not in (instruction or ""):
            num = "251" if "№251" in (instruction or "") else "98"
            return json.dumps({"machines": [{"machine": num}]})
        state["tries"] += 1
        if state["tries"] < 3:
            raise ai_extract.AIRetryableError("HTTP 502 от ИИ: ")
        return json.dumps({"machines": [{"machine": "151"}]})

    old = (ai_extract._call_openai, ai_extract._settings, ai_extract.time,
           ai_extract.MACHINE_RETRY_PAUSE_S)
    ai_extract._call_openai = fake_call
    ai_extract._settings = lambda: _S()
    ai_extract.time = clock
    ai_extract.MACHINE_RETRY_PAUSE_S = 0.05
    try:
        data = ai_extract.extract_structured(TEXT)
    finally:
        (ai_extract._call_openai, ai_extract._settings, ai_extract.time,
         ai_extract.MACHINE_RETRY_PAUSE_S) = old
    nums = [m["machine"] for m in data["machines"]]
    assert sorted(nums) == ["151", "251", "98"], nums
    assert state["tries"] == 3, state          # 151 дождалась третьего захода
    assert clock.waited == [0.05, 0.10], clock.waited
    assert not data["warnings"], data["warnings"]
    print("✅ переспрос станка идёт с паузой:", clock.waited)


if __name__ == "__main__":
    test_detect_machines_without_ai()
    test_one_call_per_machine()
    test_foreign_machines_dropped()
    test_one_machine_failure_does_not_break_day()
    test_machine_from_topic_header_gets_its_own_call()

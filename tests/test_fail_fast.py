"""generate(): fail fast on the free tier, skip models known to be out, report progress. A fake client; no real AI calls."""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import extract


class FakeModels:
    def __init__(self, behaviour):
        self.behaviour, self.calls = behaviour, []

    def generate_content(self, model, contents, config):
        self.calls.append(model)
        b = self.behaviour.get(model, "ok")
        if b == "ok":
            return f"answer from {model}"
        if b == "slow":                                  # a slow, busy model (busy-wait: time.sleep is stubbed out)
            end = time.time() + 0.2
            while time.time() < end:
                pass
            raise Exception("503 UNAVAILABLE slow")
        raise Exception(b)


def setup(behaviour, monkeypatch):
    fake = type("C", (), {})()
    fake.models = FakeModels(behaviour)
    monkeypatch.setattr(extract, "_client", lambda: fake)
    monkeypatch.setattr(extract, "_SKIP", {})
    monkeypatch.setattr(extract.time, "sleep", lambda s: None)
    return fake.models


PER_DAY = "429 RESOURCE_EXHAUSTED quotaId GenerateRequestsPerDayPerProjectPerModel-FreeTier"


def test_out_for_the_day_is_skipped_until_reset(monkeypatch):
    m = setup({extract.MODEL: PER_DAY}, monkeypatch)
    said = []
    with extract.progress(said.append):
        resp, used = extract.generate("q", None)
    assert used == extract.FALLBACKS[0] and m.calls == [extract.MODEL, extract.FALLBACKS[0]]
    assert any("trying" in s for s in said)
    m.calls.clear()
    extract.generate("q", None)                                   # second question: the used-up model is not asked again
    assert m.calls == [extract.FALLBACKS[0]]


def test_busy_main_model_gets_one_quick_retry_then_moves_on(monkeypatch):
    m = setup({extract.MODEL: "503 UNAVAILABLE high demand"}, monkeypatch)
    resp, used = extract.generate("q", None)
    assert m.calls == [extract.MODEL, extract.MODEL, extract.FALLBACKS[0]] and used == extract.FALLBACKS[0]


def test_every_model_out_fails_at_once_with_a_quota_message(monkeypatch):
    m = setup({x: PER_DAY for x in [extract.MODEL] + extract.FALLBACKS}, monkeypatch)
    try:
        extract.generate("q", None)
    except Exception as e:
        assert "RESOURCE_EXHAUSTED" in str(e)
    m.calls.clear()
    try:
        extract.generate("q", None)                               # nothing left to try: fails without any request
    except Exception as e:
        assert "every model" in str(e) and m.calls == []


def test_time_budget_stops_the_chain(monkeypatch):
    setup({x: "slow" for x in [extract.MODEL] + extract.FALLBACKS}, monkeypatch)
    try:
        extract.generate("q", None, budget=0.3)
        assert False, "should have timed out"
    except TimeoutError as e:
        assert "timed out" in str(e)


def test_other_errors_are_not_hidden(monkeypatch):
    setup({extract.MODEL: "400 INVALID_ARGUMENT bad schema"}, monkeypatch)
    try:
        extract.generate("q", None)
        assert False
    except Exception as e:
        assert "INVALID_ARGUMENT" in str(e)

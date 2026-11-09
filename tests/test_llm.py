import json

from shrek import llm
from shrek.models import UNCATEGORIZED


class _Resp:
    def __init__(self, text):
        self.text = text


class _FakeModels:
    def __init__(self, payload):
        self.payload = payload

    def generate_content(self, **kwargs):
        return _Resp(self.payload)


class _FakeClient:
    payload = "[]"

    def __init__(self, api_key):
        self.models = _FakeModels(type(self).payload)


ROWS = [
    {"id": 1, "description": "bus ticket", "amount": 30.0, "currency": "INR"},
    {"id": 2, "description": "mystery", "amount": 10.0, "currency": "INR"},
]


def test_categorize_maps_ids_and_filters_invalid(monkeypatch):
    _FakeClient.payload = json.dumps(
        [
            {"id": 1, "category": "transport"},
            {"id": 2, "category": "not-a-category"},
            {"id": 99, "category": "rent"},
        ]
    )
    monkeypatch.setattr("google.genai.Client", _FakeClient)
    assert llm.categorize(ROWS, key="k") == {1: "transport"}


def test_parse_expense_invalid_category_becomes_miscellaneous(monkeypatch):
    _FakeClient.payload = json.dumps(
        {
            "type": "expense",
            "amount": 10.0,
            "currency": "INR",
            "category": "not-a-category",
            "description": "x",
        }
    )
    monkeypatch.setattr("google.genai.Client", _FakeClient)
    entry = llm.parse_expense("x 10", key="k")
    assert entry.category == UNCATEGORIZED


def test_categorize_without_key_returns_none():
    assert llm.categorize(ROWS, key="") is None


def test_categorize_failure_returns_none(monkeypatch):
    class Boom:
        def __init__(self, api_key):
            raise RuntimeError("no network")

    monkeypatch.setattr("google.genai.Client", Boom)
    assert llm.categorize(ROWS, key="k") is None

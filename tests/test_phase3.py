import tempfile
from app.storage import Store
from app import decision_engine as engine

def test_decisions_cache_and_exact_refs(monkeypatch):
    store = Store(tempfile.mktemp(suffix=".sqlite3"))
    package = {"packageId": "p1", "document": "Paid and reconciled [DOC:A] [DOC:B] [DOC:C]"}
    calls = []
    def fake_model(packages, revision):
        calls.append(1)
        return [{"packageId": "p1", "action": "settle_invoice", "facts": {"vendorName": "Example", "invoiceNumber": "INV1", "amountMinor": 1200, "currency": "INR"}, "evidenceRefs": ["[DOC:A]", "[DOC:B]", "[DOC:C]"], "rationale": "Settle invoice because [DOC:A] and [DOC:B] support a reconciled and authorized payment."}]
    monkeypatch.setattr(engine, "ask_model", fake_model)
    first = engine.decide_batch(store, [package], "r1")
    second = engine.decide_batch(store, [package], "r1")
    assert first == second and len(calls) == 1

def test_rejects_invented_reference():
    package = {"packageId": "p1", "text": "[A] [B] [C]"}
    raw = {"packageId": "p1", "action": "hold_invoice", "facts": {"vendorName": "V", "invoiceNumber": "I", "amountMinor": 1, "currency": "INR"}, "evidenceRefs": ["[A]", "[B]", "[FAKE]"], "rationale": "Hold invoice because [A] and [B] indicate that the required verification has not yet completed."}
    import pytest
    with pytest.raises(ValueError):
        engine.validate_decision(package, raw)

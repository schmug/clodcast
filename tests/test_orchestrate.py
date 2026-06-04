# tests/test_orchestrate.py
from __future__ import annotations

import orchestrate


def test_extract_last_json_single_line():
    assert orchestrate.extract_last_json('noise\n{"ok": true, "segment": "x"}') == {
        "ok": True, "segment": "x"}


def test_extract_last_json_multiline_fallback():
    out = orchestrate.extract_last_json('pre\n{\n  "ok": false,\n  "reason": "no"\n}\n')
    assert out == {"ok": False, "reason": "no"}


def test_extract_last_json_none_when_absent():
    assert orchestrate.extract_last_json("just prose, no json") is None
    assert orchestrate.extract_last_json("") is None

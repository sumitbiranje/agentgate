from __future__ import annotations

from agentgate.redact import MARKER, Redactor

from .conftest import make_trace


def test_builtin_patterns() -> None:
    r = Redactor()
    samples = [
        "key sk-ant-api03-abcdefghijklmnop",
        "openai sk-proj-ABCDEFGHIJKLMNOPQRSTUV",
        "aws AKIAABCDEFGHIJKLMNOP",
        "gh ghp_" + "a" * 36,
        "slack xoxb-1234567890-abc",
        "Authorization: Bearer abcdefghijklmnopqrstuvwxyz",
        "jwt eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NSJ9.c2lnbmF0dXJlMTIz",
        "-----BEGIN RSA PRIVATE KEY-----\nMIIE\n-----END RSA PRIVATE KEY-----",
    ]
    for s in samples:
        out = r.text(s)
        assert MARKER in out, s
    assert r.text("nothing secret here") == "nothing secret here"


def test_custom_patterns_and_keys() -> None:
    r = Redactor(patterns=[r"ORD-\d+"], keys=["ssn"])
    data = {
        "note": "see ORD-1234",
        "SSN": "123",
        "nested": [{"Api-Key": "abc"}],
        "token": "",
        "input_tokens": 5,
    }
    assert r.value(data) == {
        "note": f"see {MARKER}",
        "SSN": MARKER,
        "nested": [{"Api-Key": MARKER}],
        "token": "",
        "input_tokens": 5,
    }
    assert r.value(("a", 1)) == ("a", 1)


def test_no_builtins() -> None:
    r = Redactor(use_builtins=False)
    assert r.text("sk-ant-api03-abcdefghijklmnop") == "sk-ant-api03-abcdefghijklmnop"
    assert r.value({"password": "x"}) == {"password": "x"}


def test_trace_redaction_keeps_ids() -> None:
    t = make_trace(
        "case-sk-ant-xxxxxxxxxxxxxxxx",
        [("login", {"password": "hunter2"})],
        "your key is sk-ant-api03-abcdefghijklmnop",
    )
    out = Redactor().trace(t)
    assert out.case_id == t.case_id
    assert out.tool_calls[0].arguments == {"password": MARKER}
    assert "sk-ant" not in out.final_output

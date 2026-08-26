import gspread
import pytest

from gsheet_sheets_client import SheetsClient, _RATE_LIMIT_MAX_ATTEMPTS


class _FakeResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code

    def json(self):
        return {"error": {"code": self.status_code, "message": "rate limited", "status": "RESOURCE_EXHAUSTED"}}


def _rate_limit_error() -> gspread.exceptions.APIError:
    return gspread.exceptions.APIError(_FakeResponse(429))


def _other_error() -> gspread.exceptions.APIError:
    return gspread.exceptions.APIError(_FakeResponse(500))


def test_call_retries_on_429_and_eventually_succeeds(monkeypatch):
    monkeypatch.setattr("gsheet_sheets_client.time.sleep", lambda _seconds: None)
    attempts = []

    def flaky():
        attempts.append(1)
        if len(attempts) < 3:
            raise _rate_limit_error()
        return "ok"

    result = SheetsClient._call(flaky)

    assert result == "ok"
    assert len(attempts) == 3


def test_call_gives_up_after_max_attempts_on_persistent_429(monkeypatch):
    monkeypatch.setattr("gsheet_sheets_client.time.sleep", lambda _seconds: None)
    attempts = []

    def always_rate_limited():
        attempts.append(1)
        raise _rate_limit_error()

    with pytest.raises(gspread.exceptions.APIError):
        SheetsClient._call(always_rate_limited)

    assert len(attempts) == _RATE_LIMIT_MAX_ATTEMPTS


def test_call_does_not_retry_non_429_errors(monkeypatch):
    monkeypatch.setattr("gsheet_sheets_client.time.sleep", lambda _seconds: None)
    attempts = []

    def server_error():
        attempts.append(1)
        raise _other_error()

    with pytest.raises(gspread.exceptions.APIError):
        SheetsClient._call(server_error)

    assert len(attempts) == 1, "khong duoc retry loi khac 429"


def test_call_passes_through_args_and_kwargs(monkeypatch):
    def add(a, b, c=0):
        return a + b + c

    assert SheetsClient._call(add, 1, 2, c=3) == 6

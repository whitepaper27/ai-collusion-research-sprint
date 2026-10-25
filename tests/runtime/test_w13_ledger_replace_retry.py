"""R-SPD-06: a transient Windows sharing violation on the ledger rename is retried, not fatal.

A live control episode ended as SETUP_ERROR when `os.replace` on `_spend.json` raised
PermissionError ("Access is denied") because another process briefly held the file.
"""

import os

import pytest

from ethwall.spend import ledger as L


def test_transient_permission_errors_are_retried(tmp_path, monkeypatch):
    """R-SPD-06: two transient failures, then success: the file is replaced."""
    src, dst = tmp_path / "a.tmp", tmp_path / "a.json"
    src.write_text("new")
    dst.write_text("old")
    real, calls = os.replace, {"n": 0}

    def flaky(a, b):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise PermissionError(5, "Access is denied")
        return real(a, b)

    monkeypatch.setattr(L.os, "replace", flaky)
    monkeypatch.setattr(L.time, "sleep", lambda s: None)
    L._replace_with_retry(src, dst)
    assert dst.read_text() == "new" and calls["n"] == 3


def test_persistent_permission_error_is_raised(tmp_path, monkeypatch):
    """R-SPD-06: a permanent failure still surfaces after the bounded retries."""
    monkeypatch.setattr(
        L.os, "replace", lambda a, b: (_ for _ in ()).throw(PermissionError(5, "x"))
    )
    monkeypatch.setattr(L.time, "sleep", lambda s: None)
    with pytest.raises(PermissionError):
        L._replace_with_retry(tmp_path / "a", tmp_path / "b", attempts=3)

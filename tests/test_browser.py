import socket

import pytest

from calsync.browser import LoginError, _free_port, find_browser


def test_explicit_browser_path_is_used(tmp_path):
    fake = tmp_path / "my-chrome"
    fake.write_text("")
    assert find_browser(str(fake)) == str(fake)


def test_unknown_browser_gives_a_clear_error():
    with pytest.raises(LoginError, match="Browser not found"):
        find_browser("definitely-not-a-browser-xyz")


def test_auto_detection_error_when_nothing_is_installed(monkeypatch):
    monkeypatch.setattr("calsync.browser._candidate_paths", lambda: [])
    with pytest.raises(LoginError, match="No Chrome"):
        find_browser("auto")


def test_auto_detection_picks_first_existing_candidate(monkeypatch, tmp_path):
    present = tmp_path / "chrome"
    present.write_text("")
    monkeypatch.setattr("calsync.browser._candidate_paths", lambda: ["/nope", str(present)])
    assert find_browser("auto") == str(present)


def test_free_port_is_bindable():
    port = _free_port()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", port))

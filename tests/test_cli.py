import json
from datetime import date

from calsync.browser import token_expiry
from calsync.cli import academic_year, main

from .conftest import raw_session


def test_academic_year_boundaries():
    assert academic_year(date(2026, 10, 1)) == (date(2026, 9, 1), date(2027, 8, 31))
    assert academic_year(date(2027, 3, 1)) == (date(2026, 9, 1), date(2027, 8, 31))
    assert academic_year(date(2027, 9, 1)) == (date(2027, 9, 1), date(2028, 8, 31))


def test_preview_from_file_does_not_need_google_or_a_browser(tmp_path, capsys):
    data = tmp_path / "raw.json"
    data.write_text(json.dumps([
        raw_session(1, code="CM", unit="Maths"),
        raw_session(2, code="BEN", unit="Aéro"),
    ]))
    code = main(["--preview", "--from-file", str(data), "--start", "2026-09-01", "--end", "2027-08-31"])
    out = capsys.readouterr().out
    assert code == 0
    assert "2 sessions" in out and "critical" in out and "BEN · Aéro" in out


def test_missing_or_malformed_input_file_gives_a_clean_error(tmp_path, capsys):
    assert main(["--preview", "--from-file", str(tmp_path / "nope.json")]) == 1
    assert "Cannot read" in capsys.readouterr().err
    bad = tmp_path / "bad.json"
    bad.write_text('{"not": "a list"}')
    assert main(["--preview", "--from-file", str(bad)]) == 1
    assert "JSON list" in capsys.readouterr().err


def test_invalid_range_is_rejected(capsys):
    assert main(["--start", "2027-01-01", "--end", "2026-01-01", "--preview"]) == 2


def test_token_expiry_reads_jwt_exp_and_tolerates_garbage():
    import base64

    payload = base64.urlsafe_b64encode(json.dumps({"exp": 1790000000}).encode()).rstrip(b"=").decode()
    assert token_expiry(f"Bearer aaa.{payload}.sig").timestamp() == 1790000000
    assert token_expiry("Bearer not-a-jwt") is None

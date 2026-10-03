"""3 Okt 2026 (MASTER): SETIAP penolakan proforma karena status SO (SO_BILLABLE_STATUSES) membawa detail
{code: "SO_NOT_BILLABLE", message} -- bentuk sama dengan SO_NOT_ACCEPTING_DEPOSIT; FE membaca detail.code."""
import ast
from pathlib import Path

SUMBER = Path(__file__).parents[2] / "app" / "routers" / "proformas.py"


def test_penolakan_status_so_berkode_stabil():
    pohon = ast.parse(SUMBER.read_text())
    jaga = [n for n in ast.walk(pohon) if isinstance(n, ast.If) and "SO_BILLABLE_STATUSES" in ast.unparse(n.test)]
    assert len(jaga) == 2, len(jaga)  # buat + terbitkan
    for n in jaga:
        [r] = [x for x in n.body if isinstance(x, ast.Raise)]
        detail = {k.arg: k.value for k in r.exc.keywords}["detail"]
        assert isinstance(detail, ast.Dict), ast.unparse(detail)
        kv = {ast.literal_eval(k): v for k, v in zip(detail.keys, detail.values)}
        assert ast.literal_eval(kv["code"]) == "SO_NOT_BILLABLE" and "message" in kv

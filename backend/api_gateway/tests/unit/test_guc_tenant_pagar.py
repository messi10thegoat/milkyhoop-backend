"""BF-1c3: pengecualian set_config TERBUKA (bukan jalan memutar). set_config mentah tetap terlarang di bankfeed_aksi (test_bankfeed_aksi); GUC app.tenant_id BUKAN pagar tenant
(BYPASSRLS) -- hanya agar penulis modul yang membacanya bekerja seperti dari rute modulnya. Pemasangnya dibatasi: SATU panggilan, di jalur tulis, sesudah kunci, hanya untuk jenis tertentu."""
import ast
import inspect
from pathlib import Path

from app.services import bankfeed_aksi as BA
from app.services import guc_tenant as GUC

APP = Path(BA.__file__).resolve().parents[1]


def test_set_config_mentah_tetap_terlarang_di_bankfeed_aksi():
    assert "set_config" not in inspect.getsource(BA)


def test_pasang_tenant_dipanggil_hanya_sekali_di_tulis_sesudah_kunci_dan_sebelum_penentu():
    pohon = ast.parse(inspect.getsource(BA))
    panggilan = [(f.name, c.lineno) for f in ast.walk(pohon) if isinstance(f, (ast.AsyncFunctionDef, ast.FunctionDef)) for c in ast.walk(f)
                 if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute) and c.func.attr == "pasang_tenant"]
    assert [n for n, _ in panggilan] == ["tulis"], panggilan                       # tak ada di cocokkan/pratinjau/rencana/lainnya
    tulis = next(f for f in ast.walk(pohon) if isinstance(f, ast.AsyncFunctionDef) and f.name == "tulis")
    kunci = next(c.lineno for c in ast.walk(tulis) if isinstance(c, ast.Constant) and isinstance(c.value, str) and c.value.startswith("SELECT pg_advisory_xact_lock"))
    penentu = next(c.lineno for c in ast.walk(tulis) if isinstance(c, ast.Subscript) and isinstance(c.value, ast.Name) and c.value.id == "RENCANA")
    assert kunci < panggilan[0][1] < penentu
    jaga = next(n for n in ast.walk(tulis) if isinstance(n, ast.If) and any(isinstance(c, ast.Call) and getattr(c.func, "attr", "") == "pasang_tenant" for c in ast.walk(n)))
    assert "JENIS_GUC" in ast.unparse(jaga.test) and "catat" in ast.unparse(jaga.test)       # hanya aksi catat jenis penulis-pembaca-GUC
    assert BA.JENIS_GUC == ("uang_muka_pelanggan", "terima_pembayaran", "bayar_tagihan")


def test_guc_tenant_hanya_dipakai_bankfeed_aksi_dan_hanya_berisi_pasang_tenant():
    pemakai = sorted(str(p.relative_to(APP)) for p in APP.rglob("*.py") if p.name != "guc_tenant.py" and "guc_tenant" in p.read_text(encoding="utf-8"))
    assert pemakai == ["services/bankfeed_aksi.py"], pemakai
    fungsi = [n.name for n in ast.parse(inspect.getsource(GUC)).body if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef))]
    assert fungsi == ["pasang_tenant"]


def test_setiap_sql_bankfeed_aksi_tetap_menyaring_tenant_id():
    daftar = {n: v for n, v in vars(BA).items() if n.startswith("SQL_") and isinstance(v, str)}
    assert daftar and all("tenant_id" in v for v in daftar.values()), [n for n, v in daftar.items() if "tenant_id" not in v]

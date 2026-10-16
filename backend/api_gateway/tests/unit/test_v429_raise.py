"""V429: pesan RAISE EXCEPTION DB yang bisa sampai ke pengguna Penjualan -> Indonesia (hanya literal; badan identik).
Bukti badan identik + apply/rollback/penjaga drift di DB scratch: scripts/gate_v429_raise.sh."""
import re
from pathlib import Path

MIG = Path("/app/backend/migrations")
BARU = (MIG / "V429__pesan_raise_db_indonesia.sql").read_text()
LAMA = (MIG / "V429__pesan_raise_db_indonesia_ROLLBACK.sql").read_text()
INGGRIS = ("Cannot ", "Invalid ", "Please allocate", "Use reversal", "Use void", "line/header imbalance",
           "Non-UUID", "Financial history")


def _pesan(sql):
    return re.findall(r"RAISE EXCEPTION\s+'([^']*)'", sql)


def test_sepuluh_fungsi_sama_di_kedua_arah():
    f = lambda s: sorted(re.findall(r"CREATE OR REPLACE FUNCTION public\.(\w+)", s))
    assert f(BARU) == f(LAMA) and len(f(BARU)) == 10


def test_tak_ada_pesan_inggris_tersisa_di_v429():
    for p in _pesan(BARU):
        assert not any(k in p for k in INGGRIS), p


def test_placeholder_sama_per_pesan_dan_urutan():
    lama, baru = _pesan(LAMA), _pesan(BARU)
    assert len(lama) == len(baru)
    for a, b in zip(lama, baru):
        if a.startswith("V429"):  # pesan penjaga drift (ada di kedua berkas)
            continue
        assert a.count("%") == b.count("%"), (a, b)
        m = re.match(r"Law [\d/]+", a)
        assert m is None or b.startswith(m.group()), (a, b)


def test_keluarga_periode_dan_salary_tak_disentuh():
    for nama in ("prevent_closed_period_journal", "trg_protect_system_salary_components"):
        assert nama not in BARU and nama not in LAMA


def test_penjaga_drift_di_kedua_berkas():
    for s in (BARU, LAMA):
        assert s.lstrip().startswith("--") and "DO $jaga$" in s and s.index("DO $jaga$") < s.index("CREATE OR REPLACE FUNCTION public.")

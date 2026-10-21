"""Pengiriman pill perlu_difaktur + summary NK total_applied (21 Okt 2026). Predikat SQL dibuktikan pada data sintetis di harness nyata
(uji-nyata-pengiriman-perlu-difaktur); di sini: bentuk, prioritas pill, kosakata, dan penghubung ke router."""
import inspect
import os

os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")

from decimal import Decimal  # noqa: E402

from app.services import pengiriman_daftar as PG  # noqa: E402
from app.services import nota_kredit_daftar as NKD  # noqa: E402
from app.services import so_aksi  # noqa: E402
from app.routers import deliveries as DL, credit_notes as CN  # noqa: E402


def test_kosakata_dan_urutan_pill():
    assert PG.AKSI == ("perlu_resi", "belum_dicatat", "perlu_difaktur")
    assert PG.AKSI_SERAH == ("perlu_resi", "belum_dicatat")


def test_prioritas_pill_tunggal():
    assert PG.gabung_aksi("perlu_resi", True) == "perlu_resi"
    assert PG.gabung_aksi("belum_dicatat", True) == "belum_dicatat"
    assert PG.gabung_aksi(None, True) == "perlu_difaktur"
    assert PG.gabung_aksi(None, False) is None


def test_flag_sama_dengan_definisi_tagih_so():
    assert PG.FLAG_TAGIH_SEBELUM_KIRIM == so_aksi.FLAG_TAGIH_SEBELUM_KIRIM


def test_predikat_memakai_sumber_yang_sama_dengan_so():
    sql = PG.sql_aksi("perlu_difaktur", "f")
    assert PG.sql_perlu_difaktur("f") == sql
    assert "f.asal = 'so'" in sql and "f.status = 'posted'" in sql and "f.voided_at IS NULL" in sql
    assert "COALESCE(pds.perlu_kirim, pdp.track_inventory, false)" in sql           # = so_aksi.SQL_BARIS
    assert "si2.status NOT IN ('draft', 'void')" in sql                              # = sj_so.PRED_FAKTUR_TERBIT
    assert "f3.asal = 'so'" in sql                                                   # jalur asal-so = so_kirim.SQL_TERKIRIM_PER_BARIS
    assert "$1" in sql and "tenant_id" in sql                                        # Law 24


def test_router_daftar_detail_summary_menyambung():
    src = inspect.getsource(DL)
    assert '"perlu_resi", "belum_dicatat", "perlu_difaktur"' in src                  # Literal ?aksi=
    assert "_pg.gabung_aksi(i[\"aksi\"], r[\"id\"] in _pd)" in src                   # baris
    assert "_pg.gabung_aksi(detail[\"aksi\"]" in src                                 # detail
    assert "tagih_sesudah_kirim" in src and "AKSI_SERAH" in src                      # summary


def test_summary_nk_total_applied_dari_jurnal_bukan_cache():
    src = inspect.getsource(CN.get_credit_notes_summary)
    assert "jumlah_terpakai(await _nkd.rincian_nk_many" in src
    assert '"total_applied": float(_total_terpakai)' in src
    assert 'row["total_applied"]' not in src


def test_jumlah_terpakai():
    r = {"a": {"terpakai": Decimal("100000")}, "b": {"terpakai": Decimal("10000.50")}, "c": {"terpakai": Decimal("0")}}
    assert NKD.jumlah_terpakai(r) == Decimal("110000.50")
    assert NKD.jumlah_terpakai({}) == Decimal("0")

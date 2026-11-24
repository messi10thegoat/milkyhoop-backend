"""Stok Opname (V509, putusan pemilik 24 Nov 2026): state machine, kunci gudang, void, hukum-hukum (Iron Laws).
Suite unit = nol DB/HTTP: penentu dijalankan dengan sambungan palsu + monkeypatch pembaca; DDL dibaca sebagai kebenaran.
Uji trigger di DB nyata: scripts/uji_v509_lokal.sh (klaster Postgres sementara; 2 sisi + sabotase)."""
import asyncio
import os
import re
import uuid
from datetime import date
from decimal import Decimal as D
from pathlib import Path

os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")

import pytest  # noqa: E402

from app.routers import stock_opnames as SO  # noqa: E402
from app.services import inventory_helpers as IH  # noqa: E402
from app.services import stok_sesuai as SS  # noqa: E402
from app.services.bayar_keluar_rencana import Blok  # noqa: E402
from app.services.galat_berkode import GalatBerkode  # noqa: E402

DISINI = Path(__file__).parent
MIG = DISINI.parents[2] / "migrations" / "V509__stok_opname.sql"
ROLLBACK = DISINI.parents[2] / "migrations" / "V509__stok_opname_ROLLBACK.sql"
ROUTER = DISINI.parents[1] / "app" / "routers" / "stock_opnames.py"
HELPER = DISINI.parents[1] / "app" / "services" / "inventory_helpers.py"
SA_ROUTER = DISINI.parents[1] / "app" / "routers" / "stock_adjustments.py"
TID, OID, GID, P1, P2, P3 = "t-1", uuid.UUID(int=1), uuid.UUID(int=2), uuid.UUID(int=11), uuid.UUID(int=12), uuid.UUID(int=13)


def jalan(c):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(c)


def opname(status="hitung", adj=None, nomor="OPN-2611-0001"):
    return {"id": OID, "tenant_id": TID, "opname_number": nomor, "warehouse_id": GID, "opname_date": date(2026, 11, 24), "status": status, "adjustment_id": adj}


def garis(pid, sistem, fisik, nama="Barang"):
    return {"product_id": pid, "product_name": nama, "unit": "pcs", "system_quantity": sistem, "physical_quantity": fisik, "note": None}


@pytest.fixture
def siapkan(monkeypatch):
    """Pasang pembaca palsu: opname, baris, stok ledger SEKARANG, periode terbuka, harga pokok 100."""
    def _pasang(o, baris, kini):
        async def muat(conn, tid, oid, kunci=False): return o
        async def barisf(conn, oid): return baris
        async def stok(conn, tid, gid, pids): return {p: D(str(kini.get(p, 0))) for p in pids}
        async def periode(conn, tid, tgl, bl): return None
        async def hpp(conn, tid, pid): return D("100")
        monkeypatch.setattr(SO, "_muat", muat); monkeypatch.setattr(SO, "_baris", barisf); monkeypatch.setattr(SO, "_stok_banyak", stok)
        monkeypatch.setattr(SS, "periode", periode); monkeypatch.setattr(SS, "harga_pokok", hpp)
    return _pasang


class KonnLock:
    """fetchval(guard) -> nomor opname yang menahan gudang (atau None); mencatat apa yang ditulis."""
    def __init__(self, nomor=None): self.nomor, self.ditulis, self.args = nomor, [], None
    async def fetchval(self, sql, *a):
        self.args = a
        return self.nomor
    async def execute(self, sql, *a): self.ditulis.append(sql)
    async def fetchrow(self, *a, **k): return None
    async def fetch(self, *a, **k): return []


# ----------------------------------------------------------------------------- state machine: posting
def test_selisih_dari_ledger_saat_post_bukan_snapshot(siapkan):
    siapkan(opname(), [garis(P1, D("10"), D("8"))], {P1: 7})     # snapshot 10, fisik 8, ledger kini 7 -> selisih +1 (bukan -2)
    h = jalan(SO.rencana_posting(None, TID, OID, Blok(False)))
    (b, s), = h["items"]
    assert s == D("1") and h["baris"][0]["selisih"] == D("1") and h["baris"][0]["bergerak_sejak_snapshot"] is True
    assert h["total_nilai_bertanda"] == D("100")


def test_baris_belum_dihitung_tidak_disesuaikan(siapkan):
    siapkan(opname(), [garis(P1, D("10"), None), garis(P2, D("5"), D("3"))], {P1: 10, P2: 5})
    h = jalan(SO.rencana_posting(None, TID, OID, Blok(False)))
    assert [str(b["product_id"]) for b, _ in h["items"]] == [str(P2)] and h["belum_dihitung"] == 1
    assert h["baris"][0]["dihitung"] is False and h["baris"][0]["selisih"] is None and "TIDAK disesuaikan" in h["catatan"]


def test_selisih_nol_dilewati_semua_nol_tanpa_sa(siapkan):
    siapkan(opname(), [garis(P1, D("10"), D("10"))], {P1: 10})
    h = jalan(SO.rencana_posting(None, TID, OID, Blok(False)))
    assert h["items"] == [] and h["tanpa_selisih"] is True and h["total_nilai_bertanda"] == 0


def test_total_bertanda_naik_dikurangi_turun(siapkan):
    siapkan(opname(), [garis(P1, D("0"), D("5")), garis(P2, D("0"), D("1"))], {P1: 0, P2: 3})   # P1 +5 (500), P2 -2 (200)
    h = jalan(SO.rencana_posting(None, TID, OID, Blok(False)))
    assert h["total_nilai_bertanda"] == D("300") and h["baris_selisih"] == 2


@pytest.mark.parametrize("status", ["draft", "posted", "void", "batal"])
def test_post_hanya_dari_hitung(siapkan, status):
    siapkan(opname(status), [garis(P1, D("1"), D("1"))], {P1: 1})
    with pytest.raises(GalatBerkode) as e:
        jalan(SO.rencana_posting(None, TID, OID, Blok(False)))
    assert e.value.code == "OPNAME_BUKAN_HITUNG" and e.value.status_code == 409


def test_post_pratinjau_mengumpulkan_blok_bukan_melempar(siapkan):
    siapkan(opname("posted"), [], {})
    bl = Blok(True)
    jalan(SO.rencana_posting(None, TID, OID, bl))
    assert [b["code"] for b in bl.daftar] == ["OPNAME_BUKAN_HITUNG"]


def test_opname_tak_ada_404(siapkan):
    siapkan(None, [], {})
    with pytest.raises(GalatBerkode) as e:
        jalan(SO.rencana_posting(None, TID, OID, Blok(False)))
    assert e.value.status_code == 404 and e.value.code == "OPNAME_TIDAK_DITEMUKAN"


# ----------------------------------------------------------------------------- state machine: mulai / batal / void
class KonnMulai(KonnLock):
    def __init__(self, lain=None, baris=3): super().__init__(); self.lain, self.baris = lain, baris
    async def fetchval(self, sql, *a):
        return self.lain if "status = 'hitung'" in sql else self.baris


def _gudang_palsu(monkeypatch):
    async def g(conn, tid, wid, bl): return {"id": GID, "name": "Gudang A", "is_active": True}
    monkeypatch.setattr(SS, "gudang", g)


def test_mulai_hanya_dari_draf(siapkan, monkeypatch):
    siapkan(opname("hitung"), [], {}); _gudang_palsu(monkeypatch)
    with pytest.raises(GalatBerkode) as e:
        jalan(SO.rencana_mulai(KonnMulai(), TID, OID, Blok(False)))
    assert e.value.code == "OPNAME_BUKAN_DRAF"


def test_mulai_ditolak_bila_gudang_sudah_dihitung_opname_lain(siapkan, monkeypatch):
    siapkan(opname("draft"), [], {}); _gudang_palsu(monkeypatch)
    with pytest.raises(GalatBerkode) as e:
        jalan(SO.rencana_mulai(KonnMulai(lain="OPN-2611-0009"), TID, OID, Blok(False)))
    assert e.value.code == "GUDANG_SUDAH_DIHITUNG" and "OPN-2611-0009" in e.value.detail and e.value.status_code == 409


def test_mulai_tanpa_baris_ditolak_dan_normal_mengunci(siapkan, monkeypatch):
    siapkan(opname("draft"), [], {}); _gudang_palsu(monkeypatch)
    with pytest.raises(GalatBerkode) as e:
        jalan(SO.rencana_mulai(KonnMulai(baris=0), TID, OID, Blok(False)))
    assert e.value.code == "OPNAME_TANPA_BARIS"
    h = jalan(SO.rencana_mulai(KonnMulai(baris=3), TID, OID, Blok(False)))
    assert h["jumlah_baris"] == 3 and "DIKUNCI" in h["catatan"]


@pytest.mark.parametrize("status", ["draft", "posted", "void", "batal"])
def test_batal_hanya_dari_hitung(siapkan, status):
    siapkan(opname(status), [], {})
    with pytest.raises(GalatBerkode) as e:
        jalan(SO.rencana_batal(None, TID, OID, Blok(False)))
    assert e.value.code == "OPNAME_BUKAN_HITUNG"


@pytest.mark.parametrize("status", ["draft", "hitung", "void", "batal"])
def test_void_hanya_dari_posted(siapkan, status):
    siapkan(opname(status), [], {})
    with pytest.raises(GalatBerkode) as e:
        jalan(SO.rencana_void(None, TID, OID, "x", Blok(False)))
    assert e.value.code == "OPNAME_BUKAN_POSTED"


def test_void_tanpa_sa_hanya_ubah_status_dan_dengan_sa_memakai_penentu_batal_sa(siapkan, monkeypatch):
    siapkan(opname("posted", adj=None), [], {})
    h = jalan(SO.rencana_void(None, TID, OID, "x", Blok(False)))
    assert h["sa_rencana"] is None and "tanpa selisih" in h["catatan"]
    SA_ID, panggil = uuid.UUID(int=99), []
    siapkan(opname("posted", adj=SA_ID), [], {})
    async def batal_sa(conn, tid, sa_id, alasan, bl, kunci=False): panggil.append((sa_id, alasan, kunci)); return {"sa": {}, "baris": []}
    monkeypatch.setattr(SS, "rencana_batal", batal_sa)
    h = jalan(SO.rencana_void(None, TID, OID, "salah hitung", Blok(False), kunci=True))
    assert panggil == [(SA_ID, "salah hitung", True)] and h["sa_rencana"] is not None      # SATU penentu void = jalur void SA


# ----------------------------------------------------------------------------- KUNCI GUDANG: helper lapis 1
def test_helper_menolak_gudang_dihitung_409_berkode():
    c = KonnLock("OPN-2611-0001")
    with pytest.raises(GalatBerkode) as e:
        jalan(IH.assert_gudang_tidak_terkunci(c, TID, GID, "SALES_INVOICE", uuid.UUID(int=5)))
    assert e.value.status_code == 409 and e.value.code == "GUDANG_TERKUNCI" and "OPN-2611-0001" in e.value.detail


def test_helper_lolos_bila_tak_dihitung_dan_tanpa_gudang_tak_menanyakan_db():
    assert jalan(IH.assert_gudang_tidak_terkunci(KonnLock(None), TID, GID, "SALES_INVOICE", uuid.UUID(int=5))) is None
    c = KonnLock("OPN-X")
    assert jalan(IH.assert_gudang_tidak_terkunci(c, TID, None)) is None and c.args is None


def test_helper_meneruskan_source_type_dan_id_untuk_pengecualian_sa_milik_opname():
    c = KonnLock(None)
    sid = uuid.UUID(int=77)
    jalan(IH.assert_gudang_tidak_terkunci(c, TID, GID, "STOCK_ADJUSTMENT", sid))
    assert c.args == (TID, GID, "STOCK_ADJUSTMENT", str(sid))


class KonnInline(KonnLock):
    """Meniru jalur record_inventory_*: apa pun yang menulis ledger sebelum guard = gagal."""
    pass


def test_outbound_dan_inbound_inline_ditolak_sebelum_menulis_apa_pun():
    for fn, kw in ((IH.record_inventory_outbound, {"receipt_date": None}), (IH.record_inventory_inbound, {})):
        c = KonnInline("OPN-2611-0001")
        args = dict(conn=c, tenant_id=TID, product_id=P1, product_code="P1", product_name="Barang", warehouse_id=GID, quantity=1,
                    source_type="SALES_INVOICE", source_id=uuid.UUID(int=5), source_number="F-1", user_id=None, notes="n")
        import inspect
        ok = set(inspect.signature(fn).parameters)
        args = {k: v for k, v in args.items() if k in ok}
        for k in ok:
            if k not in args and inspect.signature(fn).parameters[k].default is inspect.Parameter.empty:
                args[k] = D("1") if k in ("unit_cost", "quantity") else None
        with pytest.raises(GalatBerkode) as e:
            jalan(fn(**args))
        assert e.value.code == "GUDANG_TERKUNCI" and c.ditulis == []


def test_reversal_ditolak_bila_gudang_baris_asli_sedang_dihitung():
    class K(KonnLock):
        async def fetch(self, sql, *a):
            return [{"id": uuid.UUID(int=1), "product_id": P1, "product_code": "P1", "product_name": "B", "quantity_in": D("0"), "quantity_out": D("2"),
                     "unit_cost": D("10"), "total_cost": D("20"), "warehouse_id": GID, "source_number": "F", "batch_id": None}]
    c = K("OPN-2611-0001")
    with pytest.raises(GalatBerkode) as e:
        jalan(IH.record_inventory_reversal(c, TID, "SALES_INVOICE", uuid.UUID(int=5), uuid.UUID(int=6), reversal_date=date(2026, 11, 24)))
    assert e.value.code == "GUDANG_TERKUNCI" and c.ditulis == []


# ----------------------------------------------------------------------------- DDL sebagai kebenaran (lapis 2 + skema)
SQL = MIG.read_text()


def test_ddl_indeks_parsial_satu_opname_hitung_per_gudang():
    assert re.search(r"CREATE UNIQUE INDEX uq_opname_gudang_terkunci ON stock_opnames \(tenant_id, warehouse_id\) WHERE status = 'hitung'", SQL)


def test_ddl_status_dan_nomor_dan_rls():
    assert "CHECK (status IN ('draft', 'hitung', 'posted', 'void', 'batal'))" in SQL
    assert "UNIQUE (tenant_id, opname_number)" in SQL and "'OPN-'" in SQL
    assert SQL.count("ENABLE ROW LEVEL SECURITY") == 2 and SQL.count("CREATE POLICY") == 2
    assert "physical_quantity IS NULL OR physical_quantity >= 0" in SQL


def test_ddl_trigger_before_insert_ledger_dengan_pengecualian_sa_milik_opname():
    assert "CREATE TRIGGER trg_gudang_terkunci BEFORE INSERT ON inventory_ledger" in SQL
    assert "o.status = 'hitung'" in SQL and "NEW.source_type = 'STOCK_ADJUSTMENT'" in SQL and "o.adjustment_id" in SQL
    assert "P0409" in SQL and "GUDANG_TERKUNCI" in SQL


def test_helper_dan_trigger_memakai_predikat_sama():
    h = HELPER.read_text()
    for frag in ("o.status = 'hitung'", "STOCK_ADJUSTMENT", "o.adjustment_id"):
        assert frag in h and frag in SQL


def test_rollback_melepas_semua():
    r = ROLLBACK.read_text()
    for frag in ("DROP TRIGGER IF EXISTS trg_gudang_terkunci", "DROP FUNCTION IF EXISTS trg_tolak_gerak_gudang_terkunci", "DROP TABLE IF EXISTS stock_opname_lines",
                 "DROP TABLE IF EXISTS stock_opnames", "DROP FUNCTION IF EXISTS generate_stock_opname_number", "DROP TABLE IF EXISTS stock_opname_sequences"):
        assert frag in r


def test_ddl_aditif_tanpa_menyentuh_data_lama():
    assert not re.search(r"\b(UPDATE|DELETE FROM|ALTER TABLE)\b", SQL.replace("ENABLE ROW LEVEL SECURITY", ""))      # hanya CREATE baru; trigger di tabel lama = CREATE TRIGGER


# ----------------------------------------------------------------------------- Iron Laws pada penulis
SRC = ROUTER.read_text()


def test_penulis_post_satu_transaksi_lock_pertama_dan_jalur_sa_bersama():
    pos = SRC[SRC.index("async def post_opname"):SRC.index('@router.post("/{opname_id}/batal/preview")')]
    assert pos.index("conn.transaction()") < pos.index("pg_advisory_xact_lock") < pos.index("rencana_posting")           # Law 13 + 23
    assert "SA._buat_draf" in pos and "SA._tulis_posting" in pos and "INSERT INTO journal_entries" not in pos and "INSERT INTO inventory_ledger" not in pos   # jalur SA, bukan jurnal baru
    assert pos.index("UPDATE stock_opnames SET adjustment_id") < pos.index("SA._tulis_posting")                          # tautkan SEBELUM gerak (pengecualian kunci)
    assert pos.index("SA._tulis_posting") < pos.index("status = 'posted'")                                                # kunci lepas SESUDAH gerak
    assert "STOCK:{p}:{o['warehouse_id']}" in SRC                                                                         # Law 13 granularitas (barang, gudang)
    assert "idem_buat.mulai_aksi" in pos and "idem_buat.simpan" in pos                                                     # Law 14


def test_penulis_void_memakai_penulis_void_sa_dan_lock_sama_dengan_void_sa_langsung():
    v = SRC[SRC.index("async def void_opname"):]
    assert "SA._tulis_void" in v and "STOCK_ADJ_VOID:{o0['adjustment_id']}" in v and "INSERT INTO journal" not in v
    assert "async def _tulis_void" in SA_ROUTER.read_text() and "await _tulis_void(conn, ctx, adjustment_id, r, body.reason)" in SA_ROUTER.read_text()


def test_tak_ada_float_dan_sumber_type_baru():
    assert "float(" not in SRC                                                   # Law 25
    assert "STOCK_OPNAME" not in SRC and "journal_source_types" not in SRC and "journal_source_types" not in SQL   # tak mendaftar source_type baru (Law 6)


def test_flag_gelap_dijaga_di_setiap_penulis_dan_pembaca():
    for nama in ("buat_opname", "hapus_opname", "isi_baris", "mulai_hitung", "post_opname", "batal_opname", "void_opname", "daftar_opname", "detail_opname"):
        blok = SRC[SRC.index(f"async def {nama}("):]
        blok = blok[:blok.index("\n@router") if "\n@router" in blok else len(blok)]
        assert "_flag(conn" in blok, nama
    assert 'FLAG = "stok_opname_v1"' in SRC and "_pratinjau" in SRC and "await _flag(conn, ctx[\"tenant_id\"])" in SRC


def test_flag_mati_menolak_404():
    class K:
        async def fetch(self, sql, *a): return []
    with pytest.raises(GalatBerkode) as e:
        jalan(SO._flag(K(), TID))
    assert e.value.status_code == 404 and e.value.code == "FITUR_BELUM_AKTIF"
    class KAktif:
        async def fetch(self, sql, *a): return [{"feature": "stok_opname_v1"}]
    assert jalan(SO._flag(KAktif(), TID)) is None


def test_izin_rute_dipetakan_ke_modul_stock_adjust():
    mw = (DISINI.parents[1] / "app" / "middleware" / "permission_middleware.py").read_text()
    for frag in ('^/api/stock-opnames/[^/]+/(post|post/preview)$", ["POST"], "stock_adjust", "P"', '^/api/stock-opnames/[^/]+/(void|void/preview)$", ["POST"], "stock_adjust", "V"',
                 '"stock_adjust", "U"', '^/api/stock-opnames", ["GET"], "stock_adjust", "R"', '^/api/stock-opnames", ["POST"], "stock_adjust", "C"'):
        assert frag in mw
    assert mw.index("/api/stock-opnames/[^/]+/(post|post/preview)") < mw.index('^/api/stock-opnames", ["POST"]')    # khusus di atas generik

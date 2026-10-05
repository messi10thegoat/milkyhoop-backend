"""U7 (5 Okt 2026, pemilik: grapgrap = "No. SPK"): SEMUA permukaan BE memakai label setelan tenant, bukan teks tetap "Kode order".

Penjaga: (1) tenant berlabel "No. SPK" -> galat validasi, laporan impor, PDF (label_cetak), DTO (kode_untuk_dokumen, penawaran),
riwayat (DIRENDER SAAT BACA: baris lama ikut berganti) semuanya memuat "No. SPK" dan tak ada "Kode order" yang bocor;
(2) tenant bawaan ("Kode order") tetap berbunyi seperti sebelumnya; (3) PENJAGA STATIK: tak ada string literal "kode order"
(huruf besar/kecil) di seluruh app/ selain daftar putih (konstanta bawaan, default skema, galat internal) -> teks tetap
baru di mana pun = merah."""
import ast
import asyncio
import uuid
from datetime import date
from pathlib import Path

import pytest

from app.services import kode_order as KO
from app.services import so_riwayat as SR

APP = Path(__file__).resolve().parents[2] / "app"
SPK = "No. SPK"


def _j(c):
    return asyncio.run(c)


# ── (1) galat validasi ───────────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("masukan,potongan", [(None, "wajib berupa teks"), ("   ", "tidak boleh dikosongkan"),
                                              ("x" * 200, "maksimal")])
def test_galat_validasi_memakai_label_tenant(masukan, potongan):
    with pytest.raises(KO.KodeOrderGalat) as e:
        KO.normal_kode(masukan, SPK)
    assert str(e.value).startswith(SPK) and potongan in str(e.value) and "Kode order" not in str(e.value)


def test_bawaan_tak_berubah():
    with pytest.raises(KO.KodeOrderGalat) as e:
        KO.normal_kode("  ")
    assert str(e.value) == "Kode order tidak boleh dikosongkan."
    assert KO.label_kalimat("Kode order") == "kode order" and KO.label_kalimat(SPK) == SPK


# ── tiruan koneksi untuk impor / ganti / riwayat / label ─────────────────────────────────────────────────────────

class _Conn:
    def __init__(self, label=SPK, fetchval=None, audit=(), so=None):
        self.label, self._fetchval, self.audit, self.so, self.sql = label, fetchval, list(audit), so, []

    async def fetchrow(self, sql, *a):
        self.sql.append(sql)
        if "FROM order_code_settings" in sql:
            return {"enabled": True, "template": "{SEQ}-{MM}-{YY}", "min_digits": 3, "reset": "yearly",
                    "trigger": "so_confirmed", "allow_override": True, "label": self.label, "title_label": "Judul order"}
        if "FROM sales_orders" in sql and self.so:
            return self.so
        return None

    async def fetchval(self, sql, *a):
        self.sql.append(sql)
        return self._fetchval(sql, a) if self._fetchval else None

    async def fetch(self, sql, *a):
        self.sql.append(sql)
        if "FROM audit_logs" in sql:
            return list(self.audit)
        return []

    async def execute(self, *a, **k):
        return "OK"


def test_ganti_kode_kosong_memakai_label_tenant():
    c = _Conn()
    with pytest.raises(KO.KodeOrderGalat) as e:
        _j(KO.ganti_kode(c, "t1", uuid.uuid4(), "  ", None))
    assert str(e.value) == f"{SPK} tidak boleh dikosongkan." and "Kode order" not in str(e.value)


def test_impor_laporan_memakai_label_tenant(monkeypatch):
    so_a = {"id": uuid.uuid4(), "order_number": "SO-1", "order_code": "009-10-26"}

    async def cari(conn, tid, b):
        return so_a, None
    monkeypatch.setattr(KO, "_cari_so", cari)
    # fetchval = cek "kode dipakai pesanan lain": baris ke-2 memakai kode yang sudah dipakai SO-7
    c = _Conn(fetchval=lambda sql, a: "SO-7" if a[1] == "005-10-26" else None)
    baris = [{"order_number": "SO-1", "order_code": None},                       # kosong -> galat validasi
             {"order_number": "SO-1", "order_code": "005-10-26"},                # dipakai pesanan lain
             {"order_number": "SO-1", "order_code": "005-10-26"},                # ganda di berkas
             {"order_number": "SO-1", "order_code": "010-10-26"}]                # SO sudah memakai kode lain
    h = _j(KO.impor(c, "t1", baris, True, None))
    pesan = [r["message"] for r in h["rows"]]
    assert all(SPK in m for m in pesan[:3]), pesan
    assert not any("Kode order" in m or m.startswith("Kode ") for m in pesan), pesan
    assert "sudah memakai No. SPK 009-10-26" in pesan[3] or "No. SPK" in pesan[3], pesan[3]


# ── riwayat: dirender SAAT BACA ──────────────────────────────────────────────────────────────────────────────────

def test_teks_riwayat_kode_dirender_dengan_label_sekarang():
    f = SR.teks_riwayat_kode
    assert f("ORDER_CODE_ISSUED", SPK, {"new": "001-10-26"}, "Kode order 001-10-26 terbit") == "No. SPK 001-10-26 terbit"
    assert f("ORDER_CODE_ISSUED", SPK, {"new": "001-10-26"}, "Kode order 001-10-26 terbit (manual)") == "No. SPK 001-10-26 terbit (manual)"
    assert f("ORDER_CODE_OVERRIDDEN", SPK, {"old": "006-10-26", "new": "005-10-26"}, "x") == "No. SPK: 006-10-26 → 005-10-26"
    assert f("ORDER_CODE_OVERRIDDEN", SPK, {"old": None, "new": "040-09-26"}, "x") == "No. SPK: — → 040-09-26"
    assert f("ORDER_CODE_IMPORTED", SPK, {"new": "101-09-26"}, "x") == "No. SPK 101-09-26 diimpor"
    # tanpa nilai baru -> teks tersimpan apa adanya (tak dikarang)
    assert f("ORDER_CODE_ISSUED", SPK, {}, "Kode order terbit") == "Kode order terbit"


def test_riwayat_so_audit_kode_order_memakai_label_tenant_sekarang():
    from datetime import datetime, timezone
    so_id = uuid.uuid4()
    at = datetime(2026, 10, 4, 3, 0, tzinfo=timezone.utc)
    baris = [
        {"id": "a1", "createdAt": at, "eventType": "ORDER_CODE_ISSUED", "userId": "u1", "entity_type": "sales_orders",
         "entity_id": so_id, "entity_number": "SO-1", "source": "api:kode_order",
         "metadata": {"ringkas": "Kode order 001-10-26 terbit", "new": "001-10-26", "old": None}},
        {"id": "a2", "createdAt": at, "eventType": "ORDER_CODE_OVERRIDDEN", "userId": "u1", "entity_type": "sales_orders",
         "entity_id": so_id, "entity_number": "SO-1", "source": "api:kode_order",
         "metadata": {"ringkas": "Kode order: 001-10-26 → 002-10-26", "old": "001-10-26", "new": "002-10-26"}},
        {"id": "a3", "createdAt": at, "eventType": "SALES_ORDER_UPDATED", "userId": "u1", "entity_type": "sales_orders",
         "entity_id": so_id, "entity_number": "SO-1", "source": "api", "metadata": {"ringkas": "Pesanan diubah"}},
    ]
    c = _Conn(audit=baris)
    k = SR._Kumpul()
    keluar, _ = _j(SR._selesaikan(c, "t1", k, {"sales_order": [so_id]}, {"sales_order": True}, 50))
    teks = {e["jenis"]: e["ringkas"] for e in keluar}
    assert teks["ORDER_CODE_ISSUED"] == "No. SPK 001-10-26 terbit"
    assert teks["ORDER_CODE_OVERRIDDEN"] == "No. SPK: 001-10-26 → 002-10-26"
    assert teks["SALES_ORDER_UPDATED"] == "Pesanan diubah"  # peristiwa lain tak disentuh
    assert sum("FROM order_code_settings" in s for s in c.sql) == 1  # label dibaca SEKALI


def test_riwayat_tanpa_peristiwa_kode_tak_membaca_setelan():
    from datetime import datetime, timezone
    so_id = uuid.uuid4()
    baris = [{"id": "a3", "createdAt": datetime(2026, 10, 4, tzinfo=timezone.utc), "eventType": "SALES_ORDER_UPDATED",
              "userId": None, "entity_type": "sales_orders", "entity_id": so_id, "entity_number": "SO-1", "source": "api",
              "metadata": {"ringkas": "Pesanan diubah"}}]
    c = _Conn(audit=baris)
    _j(SR._selesaikan(c, "t1", SR._Kumpul(), {"sales_order": [so_id]}, {"sales_order": True}, 50))
    assert not any("FROM order_code_settings" in s for s in c.sql)


# ── PDF + DTO ────────────────────────────────────────────────────────────────────────────────────────────────────

def test_label_cetak_pdf_memakai_label_tenant():
    c = _Conn(so={"order_code": "002-10-26", "order_title": "KAOS"})
    h = _j(KO.label_cetak(c, "t1", uuid.uuid4()))
    assert h["order_code_cetak"] == "No. SPK 002-10-26 · KAOS" and h["order_code_pendek"] == "No. SPK 002-10-26"


def test_dto_dokumen_anak_dan_penawaran_membawa_label_tenant():
    q = uuid.uuid4()
    c = _Conn()
    h = _j(KO.so_hasil_penawaran(c, "t1", [q]))
    assert h[str(q)]["order_code_label"] == SPK
    k = _j(KO.kode_untuk_dokumen(c, "t1", "proforma", [uuid.uuid4()]))
    assert all(v["order_code_label"] == SPK for v in k.values())


# ── (3) PENJAGA STATIK: tak ada teks tetap "kode order" di app/ ──────────────────────────────────────────────────

# (berkas, teks persis) yang SAH: konstanta bawaan, default skema (fallback FE lama), galat internal bukan-untuk-pengguna
PUTIH = {
    ("services/kode_order.py", "Kode order"),                                  # LABEL_BAWAAN
    ("services/kode_order.py", "kode order"),                                  # label_kalimat(): lower()
    ("services/kode_order.py", "kode order: 1000 nomor berturut-turut sudah terpakai"),   # RuntimeError internal
    ("services/kode_order.py", "kode order: SO berubah di tengah penerbitan"),            # RuntimeError internal
    ("schemas/sales_orders.py", "Kode order"),                                 # default skema order_code_label (2x)
}


def _teks_tetap():
    ketemu = []
    for p in sorted(APP.rglob("*.py")):
        rel = str(p.relative_to(APP))
        pohon = ast.parse(p.read_text(encoding="utf-8"))
        docs = set()
        for n in ast.walk(pohon):
            if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                b = n.body
                if b and isinstance(b[0], ast.Expr) and isinstance(getattr(b[0], "value", None), ast.Constant):
                    docs.add(id(b[0].value))
        for n in ast.walk(pohon):
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs:
                if "kode order" in n.value.lower():
                    ketemu.append((rel, n.value))
        for n in ast.walk(pohon):  # bagian literal f-string (JoinedStr) juga
            if isinstance(n, ast.JoinedStr):
                for v in n.values:
                    if isinstance(v, ast.Constant) and "kode order" in str(v.value).lower() and id(v) not in docs:
                        ketemu.append((rel, str(v.value)))
    return ketemu


def test_tak_ada_teks_tetap_kode_order_di_seluruh_app():
    liar = [(f, t) for f, t in _teks_tetap() if (f, t) not in PUTIH]
    assert not liar, "teks tetap 'kode order' (pakai label setelan tenant): " + "; ".join(f"{f}: {t[:60]!r}" for f, t in liar[:8])


def test_penjaga_statik_bisa_merah_kontrol():
    """Kontrol: pendeteksi MENEMUKAN teks tetap pada kode dummy (nol temuan = bukan bukti alat hidup)."""
    pohon = ast.parse('def f():\n    raise ValueError("Kode order tidak ada")\n    return f"Pesanan sudah berkode {x}; kode order salah"')
    ada = [n.value for n in ast.walk(pohon) if isinstance(n, ast.Constant) and isinstance(n.value, str)
           and "kode order" in n.value.lower()]
    assert len(ada) >= 2

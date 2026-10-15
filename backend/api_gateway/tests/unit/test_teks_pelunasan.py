"""PELUNASAN-TEKS (15 Okt 2026, putusan MASTER #2): teks yang menyebut SATU penerimaan memakai label jenis efektifnya
("Uang muka" | "Pelunasan") -- riwayat faktur/proforma, ringkas audit, pesan galat, memo jurnal BARU, Rekap, anomali, ekspor,
deskripsi alat agen. Konsep SALDO tetap "uang muka". Jurnal lama tak disentuh."""
import asyncio
import inspect
import re
import uuid
from pathlib import Path

import pytest

from app.services import deposit_jenis as DJ

APP = Path(DJ.__file__).resolve().parent.parent


class C:
    def __init__(self, jenis):
        self.jenis = jenis
        self.sql = []

    async def fetchval(self, sql, *a):
        self.sql.append((sql, a))
        return self.jenis

    async def fetch(self, sql, *a):
        self.sql.append((sql, a))
        return [{"id": i, "jenis": self.jenis} for i in a[1]]


@pytest.mark.parametrize("jenis,biasa,kapital", [("PELUNASAN", "Pelunasan", "Pelunasan"), ("DP", "Uang muka", "Uang Muka"),
                                                 (None, "Uang muka", "Uang Muka")])
def test_label_deposit(jenis, biasa, kapital):
    c = C(jenis)
    assert asyncio.run(DJ.label_deposit(c, "t", uuid.uuid4())) == biasa
    assert asyncio.run(DJ.label_deposit(c, "t", uuid.uuid4(), kapital=True)) == kapital
    sql, a = c.sql[0]
    assert "cdl.tenant_id = $2" in sql and a[1] == "t" and DJ.sql_jenis_efektif("cdl") in sql


def test_label_banyak_dan_frasa():
    i1, i2 = uuid.uuid4(), uuid.uuid4()
    assert asyncio.run(DJ.label_deposit_banyak(C("PELUNASAN"), "t", [i1, None, i2])) == {i1: "Pelunasan", i2: "Pelunasan"}
    assert asyncio.run(DJ.label_deposit_banyak(C("DP"), "t", [])) == {}
    assert DJ.frasa_daftar(["Uang muka DP-1 (sisa Rp1)", "Pelunasan DP-2 (sisa Rp2)"]) == \
        "Uang muka DP-1 (sisa Rp1), pelunasan DP-2 (sisa Rp2)"
    assert DJ.frasa_daftar(["Pelunasan DP-2"]) == "Pelunasan DP-2" and DJ.frasa_daftar([]) == ""


def test_ekspor_menandai_pelunasan():
    from app.services import bulk_export as BE
    r = {"status": "posted", "amount_applied": 0, "amount_refunded": 0}
    assert BE._dp({**r, "jenis": "PELUNASAN"}).endswith(" · Pelunasan")
    assert "Pelunasan" not in BE._dp({**r, "jenis": "DP"})
    assert 'sql_jenis_efektif("t")' in (APP / "services" / "bulk_export.py").read_text()


# ---- tak ada teks per-penerimaan yang memaku "Uang muka {nomor}" di berkas yang dipoles
_POLA = re.compile(r'f"(?:Uang muka|Penerapan uang muka|Terima Uang Muka|Refund Uang Muka|Aplikasi Uang Muka) \{'
                   r'|sisa uang muka \{')
BERKAS = ["routers/customer_deposits.py", "services/so_riwayat.py", "routers/anomalies.py", "routers/receive_payments.py",
          "services/rekap_pesanan.py"]
# SENGAJA tetap (konsep SALDO / kelebihan bayar selalu jenis DP): baris yang memuat salah satu frasa ini
SAH = ("dari kelebihan bayar ini", "dicatat sebagai uang muka")


@pytest.mark.parametrize("berkas", BERKAS)
def test_tak_ada_label_dipaku_per_penerimaan(berkas):
    salah = [ln for ln in (APP / berkas).read_text().splitlines() if _POLA.search(ln) and not any(s in ln for s in SAH)]
    assert salah == [], salah


def test_memo_jurnal_baru_kapital_dan_riwayat_berlabel():
    src = (APP / "routers" / "customer_deposits.py").read_text()
    for awal in ("Terima ", "Aplikasi ", "Refund "):
        assert f'f"{awal}{{await label_deposit(conn, ctx[\'tenant_id\'], deposit_id, kapital=True)}}' in src, awal
    rw = (APP / "services" / "so_riwayat.py").read_text()
    assert rw.count('k.label_dp[str(') >= 2 and rw.count("lb = label_jenis(") >= 2
    rk = (APP / "services" / "rekap_pesanan.py").read_text()
    assert '"jenis": label_jenis(d["jenis"])' in rk and 'f"Pengembalian {label_jenis(d[\'jenis\']).lower()}"' in rk


def test_daftar_penerimaan_berlabel_per_butir():
    for berkas in ("routers/sales_orders.py", "routers/sales_invoices.py", "services/so_pelunasan.py"):
        t = (APP / berkas).read_text()
        assert "label_deposit_banyak(" in t and "frasa_daftar(" in t, berkas
    assert '"Uang muka " + ", ".join' not in (APP / "services" / "so_pelunasan.py").read_text()


def test_alat_agen_menyebut_jenis():
    t = (APP / "services" / "unified_agent" / "tool_registry.py").read_text()
    assert t.count("PELUNASAN = pelunasan") == 2

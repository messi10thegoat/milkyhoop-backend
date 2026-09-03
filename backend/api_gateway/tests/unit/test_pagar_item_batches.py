"""Pagar tenant pada pembacaan `item_batches` di jalur faktur penjualan.

`batch_id` datang dari badan permintaan dan TIDAK dipasangkan dengan apa pun
yang sudah diverifikasi. Bandingkan jalur fulfillment (:1413/:1434) yang
memasangkannya dengan `warehouse_id` ber-tenant sehingga terlindung SECARA
KEBETULAN -- perlindungan yang hilang tanpa suara kalau pemeriksaan gudang itu
kelak dipindah.

DIUKUR dengan menanam dua baris lalu ROLLBACK (3 Sep 2026):
    tanpa pagar     -> 'BATCH-TENANT-LAIN exp=2027-01-31' terbaca oleh
                       pemanggil kaos-biru
    dengan pagar    -> nol baris
    kontrol positif -> batch sendiri tetap terbaca

`item_batches` masih 0 baris; kosong itu PENUNDAAN, bukan perlindungan.
"""

import re
from pathlib import Path

SUMBER = Path(__file__).resolve().parents[2] / "app/routers/sales_invoices.py"


def _jendela(teks: str, mulai: int, n: int = 200) -> str:
    return teks[mulai : mulai + n]


def test_semua_pembacaan_item_batches_menyaring_tenant():
    teks = SUMBER.read_text(encoding="utf-8")
    tanpa_pagar = []
    for m in re.finditer(r"FROM item_batches", teks):
        # Jendela, BUKAN pola yang berhenti di tanda kutip: SQL-nya dipecah
        # menjadi literal bersebelahan, dan pola yang berhenti di kutip akan
        # memotong WHERE-nya lalu melaporkan pelanggaran palsu.
        j = _jendela(teks, m.start())
        if "tenant_id" not in j:
            tanpa_pagar.append(j[:100].replace("\n", " "))
    assert not tanpa_pagar, (
        "pembacaan item_batches tanpa saringan tenant: " + repr(tanpa_pagar)
    )


def test_kedua_situs_mengoper_tenant_id_sebagai_parameter():
    """Saringan di SQL tak berarti kalau parameternya tak dioper."""
    teks = SUMBER.read_text(encoding="utf-8")
    situs = [m.start() for m in re.finditer(r"FROM item_batches", teks)]
    assert len(situs) == 2, f"jumlah situs berubah: {len(situs)}"
    for i in situs:
        j = _jendela(teks, i, 400)
        assert 'ctx["tenant_id"]' in j, "tenant_id tidak dioper ke fetchrow"

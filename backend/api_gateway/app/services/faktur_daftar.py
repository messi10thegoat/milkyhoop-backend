"""Data daftar Faktur Penjualan untuk paket listpage-polish (LISTPAGE-DATA, 16 Okt 2026, MASTER).

Server menghitung, FE hanya memformat (satu sumber):
  jatuh_tempo_hari    = due_date - hari ini (hari kalender, tanggal BISNIS tenant), None bila tak terbuka / tanpa tanggal.
  jatuh_tempo_terbuka = predikat YANG SAMA dengan bagian "belum lunas" dari is_overdue / ?status=overdue
                        (routers/sales_invoices._syarat_terbuka_ar). is_overdue == terbuka AND jatuh_tempo_hari < 0.
  documents           = [{type}] satu per dokumen, urut Faktur · Kwitansi · Nota kredit (paket 06-TEKS). type = kind
                        panel /documents (routers/dokumen.susun_dokumen): invoice / receipt / credit_note.
     Kwitansi per faktur = himpunan kelompok "Pembayaran" susun_dokumen DIBATASI ke faktur ini: penerimaan posted
     (alokasi aktif) atas faktur ini + uang muka ber-jurnal tak void yang DITERAPKAN (aplikasi aktif) ke faktur ini.
     Uang muka SO yang belum diterapkan ke faktur ini bukan dokumen faktur (milik pesanan). Satu kwitansi per nomor.
     Nota kredit = credit_notes.original_invoice_id = faktur ini, status bukan draft/void (= kelompok "Nota Kredit").
     Faktur sendiri selalu satu entri (baris daftar = faktur itu; draf/batal tetap dokumennya sendiri).
Filter tenant EKSPLISIT di tiap kueri.
"""
from datetime import date
from typing import Optional

URUT_DOK_FAKTUR = ("invoice", "receipt", "credit_note")

SQL_KWITANSI_FAKTUR = """
    SELECT invoice_id, count(DISTINCT id) AS n FROM (
        SELECT rpa.invoice_id, rpa.payment_id AS id
        FROM receive_payment_allocations rpa
        JOIN receive_payments rp ON rp.id = rpa.payment_id AND rp.tenant_id = $1 AND rp.status = 'posted'
        WHERE rpa.invoice_id = ANY($2::uuid[]) AND rpa.status = 'active'
        UNION
        SELECT cda.invoice_id, cd.id
        FROM customer_deposit_applications cda
        JOIN customer_deposits cd ON cd.id = cda.deposit_id AND cd.tenant_id = $1
        WHERE cda.tenant_id = $1 AND cda.status = 'active' AND cd.status <> 'void' AND cd.journal_id IS NOT NULL
          AND cda.invoice_id = ANY($2::uuid[])
    ) x GROUP BY 1
"""
SQL_NK_FAKTUR = """
    SELECT original_invoice_id AS invoice_id, count(*) AS n FROM credit_notes
    WHERE tenant_id = $1 AND original_invoice_id = ANY($2::uuid[]) AND status NOT IN ('draft', 'void')
    GROUP BY 1
"""


def dokumen_faktur(n_kwitansi: int, n_nota_kredit: int) -> list:
    n = {"invoice": 1, "receipt": n_kwitansi, "credit_note": n_nota_kredit}
    return [{"type": k} for k in URUT_DOK_FAKTUR for _ in range(n[k])]


def jatuh_tempo_hari(terbuka: bool, due_date: Optional[date], hari_ini: date) -> Optional[int]:
    if not terbuka or due_date is None:
        return None
    return (due_date - hari_ini).days


def jatuh_tempo_keadaan(status: str, terbuka: bool, due_date: Optional[date], hari_ini: date) -> str:
    """KEADAAN-LIST (16 Okt 2026): enum dari fakta YANG SAMA dengan jatuh_tempo_terbuka/_hari. 'telat' (terbuka, hari < 0 ==
    is_overdue) | 'hari_ini' | 'akan' | 'selesai' (tak terbuka: lunas / sisa 0 -- lunas lewat tempo = selesai, BUKAN telat) |
    'tanpa' (draf / void / tanpa tanggal). Selalu terisi, tak pernah null."""
    if status in ("draft", "void") or due_date is None:
        return "tanpa"
    if not terbuka:
        return "selesai"
    d = (due_date - hari_ini).days
    return "telat" if d < 0 else "hari_ini" if d == 0 else "akan"


async def dokumen_per_faktur(conn, tenant_id: str, ids: list) -> dict:
    """-> {invoice_id: [{type}]} untuk faktur di halaman (<= 100). Dua kueri ber-batch."""
    if not ids:
        return {}
    kw = {r["invoice_id"]: r["n"] for r in await conn.fetch(SQL_KWITANSI_FAKTUR, tenant_id, ids)}
    nk = {r["invoice_id"]: r["n"] for r in await conn.fetch(SQL_NK_FAKTUR, tenant_id, ids)}
    return {i: dokumen_faktur(kw.get(i, 0), nk.get(i, 0)) for i in ids}

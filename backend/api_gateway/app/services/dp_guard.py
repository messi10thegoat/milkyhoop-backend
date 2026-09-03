"""Guard uang muka — SATU definisi, dipakai beberapa router.

Dokumen penjualan yang dipindahkan ke keadaan terminal (batal / tolak /
hapus) bisa meninggalkan UANG MUKA YATIM: baris `customer_deposits` beserta
jurnalnya menunjuk dokumen yang sudah tak berlaku, tanpa jalur menutupnya.

Yang membuat ini tidak kentara, dan sebabnya guard ini ada di satu tempat:
`customer_deposits` menempel ke TIGA dokumen lewat tiga kolom berbeda
(`quote_id`, `sales_order_id`, `proforma_id`), sehingga tiap jalur terminal
di tiap modul adalah pintu bocor tersendiri.

"AKTIF" DIDEFINISIKAN DARI SKEMA, BUKAN INGATAN (diukur 2026-09-03):
- `chk_cust_deposit_status` mengizinkan draft/posted/partial/applied/void.
  TIDAK ADA status 'refunded' — refund dicatat sebagai NOMINAL di kolom
  `amount_refunded` (+ tabel `customer_deposit_refunds`).
- Karena itu predikatnya DUA klausa:
      status <> 'void'  AND  (amount - COALESCE(amount_refunded,0)) > 0
- Klausa status BUKAN hiasan: mem-void deposit TIDAK mengisi
  `amount_refunded` (terukur — deposit ter-void tetap `sisa` penuh). Tanpa
  klausa itu, setiap deposit ter-void akan lolos sebagai "masih aktif".
- 'draft' dihitung AKTIF: belum berjurnal, tapi tetap baris yatim.

Guard tidak menghapus atau mengubah apa pun. Ia hanya menolak 400 dan
menyebut NOMOR deposit yang menghalangi, supaya pengguna tahu urutan yang
benar: tutup uang mukanya dulu.
"""

from fastapi import HTTPException

# Kolom penambat yang boleh dipakai. Nama kolom masuk ke SQL, jadi ia WAJIB
# datang dari daftar tertutup ini — tidak pernah dari masukan pemanggil.
KOLOM_TAMBATAN = {
    "quote_id",
    "sales_order_id",
    "proforma_id",
}


async def tolak_bila_ada_uang_muka_aktif(
    conn, kolom: str, dokumen_id, tenant_id: str, aksi: str, label: str
):
    """400 bila dokumen ini masih memegang uang muka aktif.

    `kolom` : salah satu KOLOM_TAMBATAN.
    `aksi`  : kata kerja untuk kalimat pesan ("dibatalkan", "dihapus", ...).
    `label` : nama dokumen dalam kalimat ("Penawaran", "Pesanan penjualan").
              Sengaja parameter, bukan kata generik "Dokumen" -- pesan yang
              menyebut jenis dokumennya lebih menolong pengguna, dan pesan
              Penawaran yang sudah dipakai tidak boleh turun mutunya.
    """
    if kolom not in KOLOM_TAMBATAN:
        raise ValueError(f"kolom tambatan tidak dikenal: {kolom!r}")

    baris = await conn.fetch(
        f"""
        SELECT deposit_number
        FROM customer_deposits
        WHERE {kolom} = $1
          AND tenant_id = $2
          AND status <> 'void'
          AND (amount - COALESCE(amount_refunded, 0)) > 0
        ORDER BY deposit_number
        """,
        dokumen_id,
        tenant_id,
    )
    if not baris:
        return
    nomor = ", ".join(r["deposit_number"] for r in baris)
    raise HTTPException(
        status_code=400,
        detail=(
            f"{label} memiliki uang muka aktif ({nomor}) dan tidak bisa {aksi}. "
            "Batalkan atau refund uang muka itu lebih dulu."
        ),
    )

"""Detail KONTAK (Pelanggan / Vendor) -- B2 (4 Nov 2026, kontrak CONVERSATIONAL; desain disetujui MASTER): teks server + aksi
[{kunci, label, utama?, bahaya?, boleh, alasan?}] (pola medan aksi SO). boleh/alasan dari pemeriksa YANG SAMA dengan penulisnya
(hapus = smart_delete.cek_jejak, saldo awal = pemeriksa rute saldo awal). Angka saldo / lewat tempo = services/kontak_daftar.
"""
from typing import Optional

TIPE = {"BADAN": "Badan usaha", "ORANG_PRIBADI": "Orang pribadi"}


def tipe_text(kode: Optional[str]) -> Optional[str]:
    return TIPE.get((kode or "").upper()) if kode else None


def termin_text(hari) -> str:
    n = int(hari or 0)
    return "Tunai" if n <= 0 else f"{n} hari"  # BUG-026: tanpa "Net" (Inggris)


AGREGAT = {
    # K2b (putusan MASTER 4 Nov 2026): agregat dokumen di detail kontak hanya untuk yang boleh BACA modul dokumennya
    "customer": ("sales_invoice", ("total_transactions", "total_value", "total_penjualan", "jumlah_faktur", "last_invoice_date",
                                   "jumlah_dokumen", "last_transaction_at")),
    "vendor": ("purchase_invoice", ("total_pembelian", "jumlah_tagihan", "last_bill_date", "jumlah_dokumen")),
}


async def saring_agregat(jenis: str, data: dict, boleh) -> dict:
    """Pola omitted B4: tanpa izin BACA modul dokumen -> medan agregat DIBUANG (null di JSON, FE "—"), bukan 0 palsu;
    data["omitted"] = [modul]. boleh(modul) = dashboard_izin.boleh_baca (sama dengan riwayat)."""
    modul, kunci = AGREGAT[jenis]
    if await boleh(modul):
        data["omitted"] = []
        return data
    for k in kunci:
        data[k] = None
    data["omitted"] = [modul]
    return data


def aksi_detail(is_active: bool, alasan_hapus: Optional[str], saldo_awal_ada: Optional[str]) -> list:
    """Urutan tampil tetap; boleh=False membawa alasan berkalimat (sama dengan galat penulisnya)."""
    def a(kunci, label, boleh=True, alasan=None, **kw):
        return {"kunci": kunci, "label": label, **kw, "boleh": boleh, **({"alasan": alasan} if alasan else {})}
    out = [a("ubah", "Ubah", utama=True),
           a("saldo_awal", "Saldo awal", boleh=not saldo_awal_ada,
             alasan=f"Saldo awal sudah dicatat ({saldo_awal_ada}). Batalkan dulu untuk menggantinya." if saldo_awal_ada else None),
           a("gabung", "Gabungkan")]
    out.append(a("nonaktifkan", "Nonaktifkan") if is_active else a("aktifkan", "Aktifkan"))
    out.append(a("hapus", "Hapus", bahaya=True, boleh=alasan_hapus is None, alasan=alasan_hapus))
    return out


def aksi_tersedia(aksi: list) -> dict:
    """Peta aksi_tersedia bentuk bersama (schemas/aksi_tersedia) dari daftar aksi_detail YANG SAMA -- satu penentu, dua bentuk
    (daftar `aksi` lama untuk FE lama). Urutan kunci = urutan tampil; label milik resep FE."""
    from ..schemas.aksi_tersedia import aksi as entri
    return {x["kunci"]: entri(x["boleh"], x.get("alasan")) for x in aksi}


# Dokumen yang menyimpan nama kontak sebagai snapshot (diukur information_schema; pelanggan 25 Sep, vendor 9 Nov 2026: tabel ber-vendor_name
# dengan tenant_id + vendor_id uuid). Nama baru hanya dipakai dokumen baru; dokumen lama tetap mencetak nama saat dibuat.
GANTI_NAMA = {
    "customer": {"tabel": "customers", "nama": "nama", "fk": "customer_id", "snap": "customer_name", "dok": (
        ("sales_invoices", "faktur_penjualan"),
        ("sales_orders", "pesanan_penjualan"),
        ("quotes", "penawaran"),
        ("proformas", "proforma"),
        ("customer_deposits", "uang_muka"),
        ("receive_payments", "penerimaan"),
        ("credit_notes", "nota_kredit"),
        ("sales_receipts", "kwitansi_penjualan"),
    )},
    "vendor": {"tabel": "vendors", "nama": "name", "fk": "vendor_id", "snap": "vendor_name", "dok": (
        ("bills", "faktur_pembelian"),
        ("purchase_orders", "pesanan_pembelian"),
        ("bill_payments_v2", "pembayaran_keluar"),
        ("vendor_credits", "nota_debit"),
        ("expenses", "biaya"),
    )},
}


async def dampak_ganti_nama(conn, tid: str, jenis: str, pihak_id) -> Optional[dict]:
    """GET /{customers|vendors}/{id}/rename-impact (BACA SAJA). per_jenis[k].total = semua dokumen (draf/batal ikut: semuanya mencetak
    nama tersimpan); nama_berbeda = yang namanya SUDAH beda dari nama sekarang. None = kontak tak ada di tenant ini."""
    g = GANTI_NAMA[jenis]
    pihak = await conn.fetchrow(f"SELECT id, {g['nama']} AS nama FROM {g['tabel']} WHERE id = $1 AND tenant_id = $2", pihak_id, tid)
    if not pihak:
        return None
    per_jenis = {}
    for tabel, kunci in g["dok"]:
        r = await conn.fetchrow(
            f"SELECT count(*) AS total, "  # nosec B608 - tabel dari konstanta
            f"count(*) FILTER (WHERE {g['snap']} IS DISTINCT FROM $3) AS nama_berbeda "
            f"FROM {tabel} WHERE tenant_id = $1 AND {g['fk']} = $2",
            tid, pihak_id, pihak["nama"])
        per_jenis[kunci] = {"total": r["total"], "nama_berbeda": r["nama_berbeda"]}
    total = sum(v["total"] for v in per_jenis.values())
    return {
        f"{jenis}_id": str(pihak_id),
        "nama": pihak["nama"],
        "total_dokumen": total,
        "nama_berbeda": sum(v["nama_berbeda"] for v in per_jenis.values()),
        "per_jenis": per_jenis,
        "pesan": (f"Nama baru hanya dipakai dokumen baru. {total} dokumen yang sudah ada "
                  "tetap memakai nama saat dokumen itu dibuat.") if total else None,
    }


async def pratinjau_hapus(conn, tid: str, jenis: str, pihak_id, jejak: list) -> Optional[dict]:
    """POST /{customers|vendors}/{id}/delete/preview (BACA SAJA): alasan = smart_delete.cek_jejak YANG SAMA dengan DELETE.
    Blok berkode HTTP_400 = kosakata blok_dari_galat (DELETE menolak dengan 400 + kalimat yang sama). None = kontak tak ada."""
    from .smart_delete import cek_jejak
    g = GANTI_NAMA[jenis]
    pihak = await conn.fetchrow(f"SELECT id, {g['nama']} AS nama FROM {g['tabel']} WHERE id = $1 AND tenant_id = $2", pihak_id, tid)
    if not pihak:
        return None
    alasan = await cek_jejak(conn, str(pihak_id), pihak["nama"], jejak)
    return {"ok": alasan is None, "blocks": [{"code": "HTTP_400", "message": alasan}] if alasan else [],
            "rencana": {"id": str(pihak_id), "nama": pihak["nama"]}}


def nilai_json(v):
    """Nilai kolom untuk pratinjau ubah: Decimal -> JSON number (uang_json), tanggal -> ISO, UUID -> teks; lainnya apa adanya."""
    from datetime import date
    from decimal import Decimal
    from uuid import UUID
    if isinstance(v, Decimal):
        from ..schemas.uang_json import _uang_json
        return _uang_json(v)
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, UUID):
        return str(v)
    return v

"""T208 — pencarian entitas master lewat chat harus DETERMINISTIK.

Sebelum tiket ini, "apakah ada pelanggan bernama Sharon?" tidak pernah memanggil
tool pencarian: extractor LLM mengembalikan intent yang berubah-ubah
(query_customer_sales conf 1.00 di satu sesi, 0.70 di sesi lain,
query_vendor_ap untuk frasa vendor), tool_calls kosong, jawaban buntu
"Permintaan itu belum bisa saya proses lewat jalur ini."

Berkas ini mengunci DUA sisi sekaligus:
  - POSITIF: kalimat pencarian menghasilkan intent + nama yang benar,
  - NEGATIF: kalimat yang BUKAN pencarian tidak boleh tertangkap (piutang tetap
    ke jalur AR, "buat pelanggan" tetap ke jalur create, "daftar pelanggan"
    tetap ke jalur daftar).
Yang negatif sama pentingnya: regex yang terlalu lapar akan membajak rute
keuangan tanpa suara.
"""

import pytest

from app.services.unified_agent.entity_extractor import (
    classify_entity_search,
    classify_query_intent,
)


@pytest.mark.parametrize(
    "teks,intent,nama",
    [
        ("apakah ada pelanggan bernama Sharon?", "search_customer", "Sharon"),
        ("adakah customer Sharon", "search_customer", "Sharon"),
        ("cari pelanggan Sharon", "search_customer", "Sharon"),
        ("ada ga customer sharon", "search_customer", "sharon"),
        ("ada gak pelanggan Sharon", "search_customer", "Sharon"),
        (
            "Carikan pelanggan dengan nama Sharon Vanesha",
            "search_customer",
            "Sharon Vanesha",
        ),
        ("temukan klien Sharon", "search_customer", "Sharon"),
        ("LIHAT PELANGGAN SHARON", "search_customer", "SHARON"),
        (
            "apakah ada pelanggan namanya Sharon di sistem",
            "search_customer",
            "Sharon",
        ),
        ("cek vendor bernama Sharon", "search_vendor", "Sharon"),
        ("apakah ada pemasok bernama PT Maju", "search_vendor", "PT Maju"),
        ("cari supplier Zzxy Fiktif", "search_vendor", "Zzxy Fiktif"),
        ("apakah ada barang bernama Dryfit?", "search_item", "Dryfit"),
        ("cek item Dryfit", "search_item", "Dryfit"),
        ("cari produk Kaos Biru 30s", "search_item", "Kaos Biru 30s"),
    ],
)
def test_kalimat_pencarian_terklasifikasi_deterministik(teks, intent, nama):
    assert classify_entity_search(teks) == (intent, nama)


@pytest.mark.parametrize(
    "teks,intent,nama",
    [
        ("apakah ada pelanggan bernama Sharon?", "search_customer", "Sharon"),
        ("cek item Dryfit", "search_item", "Dryfit"),
    ],
)
def test_classify_query_intent_meneruskan_hasil_pencarian(teks, intent, nama):
    """Blok pencarian harus menang di DALAM classify_query_intent, bukan cuma
    berdiri sendiri — ia dipasang paling atas, sebelum semua blok keuangan."""
    got_intent, got_name, _ = classify_query_intent(teks)
    assert (got_intent, got_name) == (intent, nama)


@pytest.mark.parametrize(
    "teks",
    [
        # Keuangan — harus tetap ke rute AR/AP/penjualan seperti sebelumnya.
        "berapa piutang Sharon?",
        "cek piutang pelanggan Sharon",
        "siapa pelanggan dengan piutang terbesar",
        "berapa total pembelian pelanggan Debora bulan ini",
        "cek stok barang Dryfit",
        "cari faktur INV-001",
        # CRUD — harus tetap ke rute create/delete.
        "buat pelanggan Sharon",
        "buatkan pelanggan baru bernama Sharon",
        "hapus pelanggan Sharon",
        "edit vendor PT Maju",
        # Daftar / tanpa nama — rute daftar, bukan pencarian.
        "daftar pelanggan",
        "lihat pelanggan",
        "pelanggan mana yang telat bayar",
        "ada berapa pelanggan aktif",
        # Bukan permintaan sama sekali.
        "",
        "halo",
    ],
)
def test_kalimat_bukan_pencarian_tidak_tertangkap(teks):
    assert classify_entity_search(teks) == (None, None)


def test_kalimat_bukan_pencarian_tidak_mengubah_intent_lama():
    """Kontrol positif untuk sisi negatif: kalimat piutang tetap punya intent
    keuangan (bukan None, bukan search_*)."""
    intent, _, _ = classify_query_intent("siapa pelanggan dengan piutang terbesar")
    assert intent is not None
    assert not str(intent).startswith("search_")

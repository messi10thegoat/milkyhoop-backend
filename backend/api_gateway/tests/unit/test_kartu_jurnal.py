"""T184 -- KARTU JURNAL HARUS MEMPERLIHATKAN BARISNYA.

Yang diuji: untuk `create_journal_entry` SAJA, kartu yang dibangun SEBELUM
tombol ditekan memuat tiap baris jurnal (NAMA AKUN, arah, nominal), total
debit, total kredit, dan status seimbang -- semuanya DIHITUNG LOKAL dari
`payload["lines"]`, nol panggilan jaringan.

Keadaan sebelum tiket ini (terukur di produksi): header hanya
[Tanggal, Keterangan]; journal_lines=None, journal_balanced=None.

Batas yang TIDAK boleh diklaim lebih:
- Ini menguji fungsi murni pembangun kartu. Ia TIDAK menguji bahwa LLM
  mengarang `lines` dengan bentuk itu, dan TIDAK menguji resolusi akun
  (dua account_id berbeda untuk "Bank" pada dua percakapan -- ronde lain).
- Ia TIDAK menguji renderer FE. Yang sudah diverifikasi terpisah: slot
  `journal_lines`/`journal_balanced` HIDUP di bundel terpasang
  main.82ce0f51.js, dan slot `warnings` digambar tanpa syarat.
"""
import re
import sys

import pytest

sys.path.insert(0, "/app/backend/api_gateway")

from app.services.unified_agent.direct_action_registry import (  # noqa: E402
    build_confirmation_table,
    build_review_card_payload,
)

# BUKTI MERAH HARUS BERUPA KEGAGALAN ASSERTION, BUKAN ModuleNotFoundError.
#
# Berkas ini dijalankan juga pada pohon 4bec354a (tanpa tambalan T184) untuk
# membuktikan harness BISA merah. Kalau simbol baru diimpor langsung di
# puncak, seluruh berkas gugur saat COLLECTION -- dan "error impor" tidak
# membuktikan apa pun tentang perilaku kartu. Jadi impornya dibuat lunak:
# tanpa tambalan, `bangun_pratinjau_jurnal_dari_lines` mengembalikan None,
# yang PERSIS keadaan produksi hari ini (journal_preview=None -> kartu kosong),
# dan tiap tes gagal pada assertion-nya sendiri, menyebut apa yang hilang.
try:
    from app.services.unified_agent.direct_action_registry import (  # noqa: E402
        bangun_pratinjau_jurnal_dari_lines as _pembangun_lokal,
    )

    ADA_PEMBANGUN_LOKAL = True
except ImportError:
    _pembangun_lokal = None
    ADA_PEMBANGUN_LOKAL = False


def bangun_pratinjau_jurnal_dari_lines(payload):
    """Tanpa tambalan T184: None -- keadaan produksi terukur."""
    if _pembangun_lokal is None:
        return None
    return _pembangun_lokal(payload)

# UUID mentah TIDAK BOLEH sampai ke layar. Kartu quick_stock_adjustment sudah
# pernah memuntahkannya; regex ini yang menahannya di sini.
RE_UUID = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)

AKUN_BEBAN = "9365af44-3762-4b1f-bc0e-dfaa61b3e804"
AKUN_BANK = "e9e9544b-073c-45a7-a75a-371063732908"


def _jurnal(lines=None):
    """Amplop create_journal_entry, DISALIN dari dump pending_actions.action_plan
    produksi (31 Agt 2026) -- bukan dikarang. Termasuk kenyataan bahwa satu
    sisi bisa TIDAK memuat kunci `debit`/`credit` sama sekali."""
    return {
        "entry_date": "2026-08-31",
        "posting_date": "2026-08-31",
        "description": "Jurnal Manual Beban Sewa",
        "lines": lines
        if lines is not None
        else [
            {"debit": 2000000, "credit": 0, "account_id": AKUN_BEBAN,
             "description": "Beban Sewa"},
            {"debit": 0, "credit": 2000000, "account_id": AKUN_BANK,
             "description": "Bank"},
        ],
    }


def _kartu(payload):
    """Bangun kartu lewat JALUR YANG SAMA dengan tool_executor: pratinjau
    lokal -> build_review_card_payload."""
    pratinjau = bangun_pratinjau_jurnal_dari_lines(payload)
    return build_review_card_payload("create_journal_entry", payload, pratinjau)


def _teks_kartu(kartu) -> str:
    """Seluruh teks yang bisa dibaca user dari kartu, dijadikan satu."""
    potongan = []
    for f in kartu.get("header") or []:
        potongan += [str(f.get("label")), str(f.get("value"))]
    for jl in kartu.get("journal_lines") or []:
        potongan += [str(jl.get("dir")), str(jl.get("account")), str(jl.get("amount"))]
    for w in kartu.get("warnings") or []:
        potongan.append(str(w.get("message")))
    for n in kartu.get("impact_notes") or []:
        potongan.append(str(n))
    potongan.append(str(kartu.get("title")))
    return "\n".join(potongan)


# ═══════════════ GATE A.1 -- seimbang ═══════════════

def test_lines_seimbang_kartu_memuat_baris_dan_balanced_true():
    kartu = _kartu(_jurnal())
    assert kartu["journal_lines"], "journal_lines kosong -- ini penyakit T184"
    assert kartu["journal_balanced"] is True


def test_lines_dua_baris_kartu_menampilkan_DUA_baris_bukan_nol():
    kartu = _kartu(_jurnal())
    assert len(kartu["journal_lines"]) == 2, kartu["journal_lines"]
    assert [jl["dir"] for jl in kartu["journal_lines"]] == ["Dr", "Cr"]
    assert [jl["amount"] for jl in kartu["journal_lines"]] == [2000000.0, 2000000.0]


def test_baris_memakai_NAMA_AKUN_bukan_uuid():
    kartu = _kartu(_jurnal())
    assert [jl["account"] for jl in kartu["journal_lines"]] == ["Beban Sewa", "Bank"]


def test_sisi_tanpa_kunci_debit_atau_credit_tetap_terbaca():
    """Terukur di produksi: baris kredit yang tak memuat kunci "debit" sama
    sekali. `or 0` sendirian tidak cukup kalau kuncinya absen."""
    kartu = _kartu(_jurnal([
        {"debit": 2000000, "account_id": AKUN_BEBAN, "description": "Beban Sewa"},
        {"credit": 2000000, "account_id": AKUN_BANK, "description": "Bank"},
    ]))
    assert len(kartu["journal_lines"]) == 2
    assert kartu["journal_balanced"] is True


def test_total_debit_dan_kredit_terbaca_di_kartu():
    teks = _teks_kartu(_kartu(_jurnal()))
    assert "Total debit Rp 2.000.000" in teks, teks
    assert "total kredit Rp 2.000.000" in teks, teks
    assert "SEIMBANG" in teks


# ═══════════════ GATE A.2 -- TIDAK seimbang ═══════════════

def test_lines_tidak_seimbang_balanced_false():
    kartu = _kartu(_jurnal([
        {"debit": 2000000, "credit": 0, "account_id": AKUN_BEBAN,
         "description": "Beban Sewa"},
        {"debit": 0, "credit": 1500000, "account_id": AKUN_BANK,
         "description": "Bank"},
    ]))
    assert kartu["journal_balanced"] is False


def test_tidak_seimbang_TERLIHAT_di_teks_kartu():
    """Lencana FE hanya digambar untuk journal_balanced=True; False = NOL teks.
    Jadi kalimatnya harus ada di slot `warnings`, yang digambar tanpa syarat."""
    kartu = _kartu(_jurnal([
        {"debit": 2000000, "credit": 0, "account_id": AKUN_BEBAN,
         "description": "Beban Sewa"},
        {"debit": 0, "credit": 1500000, "account_id": AKUN_BANK,
         "description": "Bank"},
    ]))
    teks = _teks_kartu(kartu)
    assert "TIDAK SEIMBANG" in teks, teks
    assert "selisih Rp 500.000" in teks, teks
    # dan bukan sekadar ada -- harus berkelas peringatan, bukan info
    assert any(
        w["type"] == "warning" and "TIDAK SEIMBANG" in w["message"]
        for w in kartu["warnings"]
    ), kartu["warnings"]
    # barisnya TETAP ditampilkan (ronde ini menampilkan+menandai, tidak menolak)
    assert len(kartu["journal_lines"]) == 2


def test_teks_konfirmasi_juga_menyebut_status_seimbang():
    p = _jurnal()
    teks = build_confirmation_table(
        "create_journal_entry", p, bangun_pratinjau_jurnal_dari_lines(p)
    )
    assert "Dr. Beban Sewa" in teks, teks
    assert "Cr. Bank" in teks, teks
    assert "Total Debit  Rp 2.000.000" in teks, teks
    assert "Status: SEIMBANG" in teks, teks


# ═══════════════ GATE A.3 -- NOL UUID MENTAH ═══════════════

@pytest.mark.parametrize(
    "lines",
    [
        None,  # kasus normal
        # description kosong -> harus jujur, JANGAN menambal dengan account_id
        [{"debit": 1000, "credit": 0, "account_id": AKUN_BEBAN, "description": ""},
         {"debit": 0, "credit": 1000, "account_id": AKUN_BANK}],
    ],
)
def test_nol_uuid_mentah_di_teks_kartu(lines):
    kartu = _kartu(_jurnal(lines))
    teks = _teks_kartu(kartu)
    ketemu = RE_UUID.findall(teks)
    assert ketemu == [], f"UUID bocor ke layar: {ketemu}\n---\n{teks}"


def test_akun_tanpa_nama_berkata_jujur_bukan_uuid():
    kartu = _kartu(_jurnal([
        {"debit": 1000, "credit": 0, "account_id": AKUN_BEBAN, "description": ""},
        {"debit": 0, "credit": 1000, "account_id": AKUN_BANK},
    ]))
    assert [jl["account"] for jl in kartu["journal_lines"]] == [
        "(akun belum dikenali)",
        "(akun belum dikenali)",
    ]


def test_kontrol_positif_regex_uuid_BISA_menangkap():
    """Kalau regex-nya salah, tes "nol UUID" di atas hijau tanpa arti."""
    assert RE_UUID.findall(f"Dr. {AKUN_BEBAN} Rp 1.000") == [AKUN_BEBAN]


# ═══════════════ GATE A.4 -- RADIUS: aksi lain TIDAK BERGERAK ═══════════════

def _bill():
    return {
        "vendor_id": "11111111-1111-1111-1111-111111111111",
        "vendor_name": "Knitto Textile",
        "bill_date": "2026-08-31",
        "due_date": "2026-09-30",
        "items": [{"product_name": "Kain Katun", "qty": 10, "price": 50000,
                   "product_id": "22222222-2222-2222-2222-222222222222"}],
    }


def _jual():
    return {
        "customer_id": "55555555-5555-5555-5555-555555555555",
        "customer_name": "Toko Melati",
        "quote_date": "2026-08-31",
        "invoice_date": "2026-08-31",
        "order_date": "2026-08-31",
        "due_date": "2026-09-30",
        "items": [{"description": "Kain Katun", "quantity": 2, "unit_price": 1000,
                   "item_id": "44444444-4444-4444-4444-444444444444"}],
    }


# Pratinjau jurnal palsu berbentuk SEPERTI yang dibalas endpoint pratinjau,
# supaya cabang journal_lines pada aksi-aksi ini benar-benar DILALUI. Tanpa
# ini "byte-identik" hanya membuktikan cabang yang tak pernah dieksekusi.
_PV = [
    {"account_name": "Persediaan", "debit": 500000, "credit": 0},
    {"account_name": "Utang Usaha", "debit": 0, "credit": 500000},
]

AKSI_TAK_BOLEH_BERGERAK = [
    ("create_bill", _bill()),
    ("create_sales_invoice", _jual()),
    ("create_quote", _jual()),
    ("create_sales_order", _jual()),
]


@pytest.mark.parametrize("action_key,payload", AKSI_TAK_BOLEH_BERGERAK)
def test_aksi_lain_nol_catatan_saldo_tambahan(action_key, payload):
    """Empat aksi ini memakai renderer yang SAMA. Radius T184 = satu aksi:
    kartu mereka tidak boleh menumbuhkan catatan total/seimbang."""
    kartu = build_review_card_payload(action_key, payload, list(_PV))
    assert kartu is not None
    # cabang benar-benar dilalui (kontrol positif untuk tes ini sendiri)
    assert kartu["journal_lines"] and len(kartu["journal_lines"]) == 2
    assert kartu["journal_balanced"] is True
    for w in kartu.get("warnings") or []:
        assert "Total debit" not in w["message"], w
        assert "TIDAK SEIMBANG" not in w["message"], w

    teks = build_confirmation_table(action_key, payload, list(_PV))
    assert "Total Debit" not in teks, teks
    assert "Status: SEIMBANG" not in teks, teks


@pytest.mark.parametrize(
    "action_key,payload",
    [
        # Nama field DIINTROSPEKSI dari DIRECT_ACTIONS, bukan ditebak: menebak
        # "nama"/"telepon" (penamaan tabel customers) menghasilkan header
        # KOSONG dan tes yang hijau/merah karena alasan yang salah.
        ("create_customer", {"name": "Toko Melati", "phone": "0812"}),
        ("create_vendor", {"name": "Knitto Textile", "phone": "0812"}),
        ("create_item", {"name": "Kain Katun", "item_type": "product",
                         "base_unit": "Pcs", "sales_price": 50000}),
    ],
)
def test_master_data_tetap_kartu_normal(action_key, payload):
    kartu = build_review_card_payload(action_key, payload, None)
    assert kartu is not None
    assert kartu["header"], "kartu master data kehilangan headernya"
    assert kartu["journal_lines"] is None
    assert kartu["journal_balanced"] is None


# ═══════════════ GATE A.5 -- pembangun lokal: bentuk & batas ═══════════════

def test_pembangun_lokal_menerima_lines_berbentuk_STRING_json():
    """LLM kadang mengirim `lines` sebagai string JSON, bukan array."""
    p = _jurnal()
    p["lines"] = (
        '[{"debit":2000000,"credit":0,"account_id":"%s","description":"Beban Sewa"},'
        '{"debit":0,"credit":2000000,"account_id":"%s","description":"Bank"}]'
        % (AKUN_BEBAN, AKUN_BANK)
    )
    assert len(bangun_pratinjau_jurnal_dari_lines(p)) == 2


_RUSAK = [
    {},                                   # `lines` tidak ada sama sekali
    {"lines": None},
    {"lines": []},
    {"lines": "bukan json"},
    {"lines": {"debit": 1}},              # dict, bukan array
    {"lines": [1, 2, 3]},                 # array of skalar
    {"lines": [{"debit": "abc", "credit": "xyz", "description": "Bank"}]},
]


@pytest.mark.parametrize("payload", _RUSAK)
def test_pembangun_lokal_tidak_meledak_pada_bentuk_rusak(payload):
    assert bangun_pratinjau_jurnal_dari_lines(payload) is None


def test_kontrol_positif_pembangun_lokal_BISA_mengembalikan_isi():
    """Tanpa ini, "semua bentuk rusak -> None" bisa hijau karena fungsinya
    selalu mengembalikan None."""
    assert bangun_pratinjau_jurnal_dari_lines(_jurnal()) is not None


def test_pembangun_lokal_nol_panggilan_jaringan():
    """Q1(d): keseimbangan dihitung LOKAL. Kalau suatu saat fungsi ini
    menumbuhkan panggilan HTTP, tes ini yang meledak lebih dulu."""
    import inspect

    # inspeksi FUNGSI ASLI, bukan pembungkus di berkas tes ini -- kalau salah
    # sasaran, tes ini hijau selamanya tanpa pernah melihat kode produksi.
    assert ADA_PEMBANGUN_LOKAL, "tambalan T184 belum terpasang di pohon ini"
    sumber = inspect.getsource(_pembangun_lokal)
    assert "account_id" not in sumber.split('"""')[-1], (
        "account_id tersentuh di badan fungsi -- risiko UUID bocor"
    )
    for terlarang in ("httpx", "requests", "await ", "async "):
        assert terlarang not in sumber, terlarang


# ═══════════════ KONTROL: harness BISA GAGAL ═══════════════

def test_kontrol_harness_bisa_gagal():
    """Kalau blok ini hijau, seluruh berkas ini tidak membuktikan apa pun."""
    kartu = _kartu(_jurnal())
    with pytest.raises(AssertionError):
        assert kartu["journal_lines"] is None, "sengaja salah"
    with pytest.raises(AssertionError):
        assert "Beban Sewa" not in _teks_kartu(kartu), "sengaja salah"


# ═══════════════ GATE A.6 -- PEMASANGAN (wiring) ═══════════════

def test_pembangun_lokal_memang_terpasang():
    """Fungsi murni yang benar tapi tak pernah dipanggil = nol perbaikan."""
    assert ADA_PEMBANGUN_LOKAL, (
        "bangun_pratinjau_jurnal_dari_lines tidak ada di direct_action_registry "
        "-- tambalan T184 belum terpasang di pohon ini"
    )


def test_tool_executor_memanggil_pembangun_lokal_untuk_jurnal_umum():
    """Gate A.1-A.5 menguji fungsi murni. Tes ini menguji bahwa jalur produksi
    (`_execute_propose_direct`) benar-benar melewatinya -- kalau tidak,
    kartu di layar tetap kosong walau semua tes lain hijau.

    Diperiksa di tingkat SUMBER, bukan dieksekusi: memanggil
    `_execute_propose_direct` sungguhan menyeret DB + LLM + httpx, yang bukan
    suite unit. Batasnya disebut supaya tak diklaim lebih.
    """
    import inspect

    from app.services.unified_agent import tool_executor as te

    sumber = inspect.getsource(te)
    assert "bangun_pratinjau_jurnal_dari_lines(payload)" in sumber, (
        "tool_executor tidak pernah memanggil pembangun pratinjau lokal"
    )
    # dan panggilan itu harus berada di cabang create_journal_entry
    assert (
        'elif action_key == "create_journal_entry":' in sumber
    ), "cabang create_journal_entry tidak ada di blok JOURNAL PREVIEW"


# ═══════════════ GATE A.7 -- jurnal SEPIHAK / NOL ═══════════════

def test_jurnal_sepihak_tidak_menyuruh_user_melapor_ke_tim():
    """Renderer bersama berkata "laporkan ke tim MilkyHoop" untuk jurnal
    sepihak -- benar untuk faktur (baris disusun server), SALAH untuk jurnal
    manual (baris diketik user). Ia menyembunyikan perbaikan satu kalimat di
    balik tiket dukungan."""
    kartu = _kartu(_jurnal([
        {"debit": 2000000, "credit": 0, "account_id": AKUN_BEBAN,
         "description": "Beban Sewa"},
        {"debit": 1000000, "credit": 0, "account_id": AKUN_BANK,
         "description": "Bank"},
    ]))
    teks = _teks_kartu(kartu)
    assert "laporkan ke tim MilkyHoop" not in teks, teks
    assert "hanya punya sisi debit" in teks, teks
    assert "sisi kredit-nya belum ada" in teks, teks
    # jurnal yang tak layak tampil TIDAK boleh memasang lencana Balance
    assert kartu["journal_balanced"] is None
    assert kartu["journal_lines"] is None


def test_jurnal_semua_nol_berkata_jujur():
    kartu = _kartu(_jurnal([
        {"debit": 0, "credit": 0, "account_id": AKUN_BEBAN,
         "description": "Beban Sewa"},
        {"debit": 0, "credit": 0, "account_id": AKUN_BANK, "description": "Bank"},
    ]))
    teks = _teks_kartu(kartu)
    assert "bernilai Rp 0" in teks, teks
    assert RE_UUID.findall(teks) == []


def test_radius_pesan_sepihak_aksi_lain_TIDAK_berubah():
    """Kalimat "laporkan ke tim MilkyHoop" HARUS tetap ada untuk aksi yang
    barisnya disusun server."""
    kartu = build_review_card_payload(
        "create_sales_invoice",
        _jual(),
        [{"account_name": "Piutang Usaha", "debit": 500000, "credit": 0}],
    )
    assert any(
        "laporkan ke tim MilkyHoop" in w["message"]
        for w in kartu.get("warnings") or []
    ), kartu.get("warnings")

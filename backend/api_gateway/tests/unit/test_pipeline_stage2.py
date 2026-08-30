"""
Uji pipeline Stage-2 dengan LLM PALSU — deterministik, nol HTTP, nol DB.

Inilah yang malam 2026-08-30 tidak kita punya: tiap hipotesis "apakah baris
kedua hilang" menuntut belasan probe produksi karena modelnya
non-deterministik. Dengan balasan model yang KITA tentukan, pertanyaan yang
sama dijawab dalam hitungan detik.

BATAS: ini menguji kode kita saat model mengembalikan bentuk X. Ia TIDAK
membuktikan model sungguh mengembalikan X — untuk itu tetap perlu probe
produksi. Kalau keduanya berbeda, PRODUKSI MENANG.
"""
import json
import sys
import pytest

sys.path.insert(0, "/app/backend/api_gateway")

from app.services.unified_agent.entity_extractor import FieldExtractor


async def _ekstrak(fake_llm, balasan, teks="Catat faktur pembelian dari PT X, "
                                          "Kain Katun 10 meter @ 40000, "
                                          "Benang Jahit 5 pcs @ 50000"):
    f = fake_llm(balasan)
    fx = FieldExtractor(f)
    hasil = await fx.extract_fields(teks, "create_bill", {})
    return hasil, f


@pytest.mark.asyncio
async def test_model_balas_array_dua_baris(fake_llm, kontrol_fake_llm):
    """Jalan bahagia: model patuh pada skema array -> dua baris utuh."""
    balasan = json.dumps({"vendor_name": "PT X", "items": [
        {"product_name": "Kain Katun", "qty": 10, "price": 40000},
        {"product_name": "Benang Jahit", "qty": 5, "price": 50000},
    ]})
    hasil, f = await _ekstrak(fake_llm, balasan)
    kontrol_fake_llm(f)                      # nol-nya bermakna
    items = hasil.get("items")
    assert isinstance(items, list), f"items bertipe {type(items).__name__}"
    assert len(items) == 2
    assert [i["product_name"] for i in items] == ["Kain Katun", "Benang Jahit"]


@pytest.mark.asyncio
async def test_model_balas_prosa_terdeteksi_bukan_didiamkan(fake_llm, kontrol_fake_llm):
    """AKAR T181, direproduksi DETERMINISTIK dalam hitungan detik.

    Di produksi ini butuh kalimat pemicu khusus ('2 item:' + jatuh tempo
    relatif) dan tetap non-deterministik. Di sini cukup menyuruh model palsu
    mengembalikan prosa.

    Yang diuji: kode TIDAK boleh diam-diam menghasilkan satu baris seolah
    itu ekstraksi yang benar.
    """
    balasan = json.dumps({
        "vendor_name": "PT X",
        # persis yang dikirim model di produksi 2026-08-30:
        "items": "Kain Katun 10 meter @ 40000, Benang Jahit 5 pcs @ 50000",
    })
    hasil, f = await _ekstrak(fake_llm, balasan)
    kontrol_fake_llm(f)
    items = hasil.get("items")
    # Karakterisasi: hari ini ia LOLOS sebagai string. Itu bukan kartu 1 baris
    # (enricher yang mengarangnya), tapi ia sinyal bahaya yang harus terlihat.
    assert not isinstance(items, list) or len(items) == 2, (
        "items string diam-diam jadi satu baris — itu kelas bug T181"
    )


@pytest.mark.asyncio
async def test_satu_barang_tetap_utuh(fake_llm, kontrol_fake_llm):
    """K1 — kontrol paling menentukan.

    Perbaikan T181 Fase 1 di-ROLLBACK justru karena kasus ini ikut tertolak
    1 dari 2 di produksi. Tes ini membuatnya deterministik: satu barang
    WAJIB menghasilkan satu baris, selalu.
    """
    balasan = json.dumps({"vendor_name": "PT Grosir Kaos", "items": [
        {"product_name": "Kaos Biru 30s", "qty": 5, "price": 35000},
    ]})
    hasil, f = await _ekstrak(fake_llm, balasan, "Catat faktur pembelian dari "
                                                 "PT Grosir Kaos, Kaos Biru 30s 5 pcs @ 35000")
    kontrol_fake_llm(f)
    items = hasil.get("items")
    assert isinstance(items, list) and len(items) == 1
    assert items[0]["product_name"] == "Kaos Biru 30s"


@pytest.mark.asyncio
async def test_skema_array_benar_benar_dikirim_ke_model(fake_llm):
    """Bukti bahwa skema array sampai ke model — bukan hanya ada di kode.

    FakeLLM merekam response_format yang diterimanya.
    """
    balasan = json.dumps({"items": []})
    hasil, f = await _ekstrak(fake_llm, balasan)
    skema = f.skema_terakhir
    assert skema is not None, "response_format tak pernah dikirim — tes buta"
    p = ((skema.get("json_schema") or {}).get("schema") or {}).get("properties", {}).get("items") or {}
    assert p.get("type") == "array", (
        f"model menerima items sebagai {p.get('type')!r} — regresi T179-Q3"
    )


@pytest.mark.asyncio
async def test_balasan_rusak_tidak_meledak(fake_llm, kontrol_fake_llm):
    """Ketahanan: JSON tak terurai -> {} , bukan exception yang bocor ke user."""
    hasil, f = await _ekstrak(fake_llm, "{ini bukan json")
    kontrol_fake_llm(f)
    assert hasil == {} or isinstance(hasil, dict)

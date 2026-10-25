"""Gerbang T1 inti Milkybook (rancangan §11.6): tulis tanpa Setuju = 0; payload Setuju == pratinjau;
kunci tetap per kartu; filter izin + turun-kelas; penjaga angka; riwayat append-only."""
import json
import os
import re

import pytest

from app.milkybook.inti import harga_sasaran, penjaga_angka
from app.milkybook.inti.audit import AuditMemori
from app.milkybook.inti.kartu import StatusKartu, badan_kanonik, hash_payload
from app.milkybook.inti.pelaksana import JawabanBE
from app.milkybook.inti.putaran import CATATAN_ANGKA_DISAMARKAN, Putaran, Sesi
from app.milkybook.inti.registri import Kelas, Paket, Registri, Tool

from .conftest import BEPalsu, ModelPalsu, izin_semua, panggil, teks

OBJ = lambda props, req=(): {"type": "object", "properties": props, "required": list(req), "additionalProperties": False}  # noqa: E731

T_CARI = Tool("cari_x", Kelas.READ, "cari", OBJ({"q": {"type": "string"}}, ["q"]), "GET", "/api/x")
T_HITUNG = Tool("hitung_x", Kelas.PREVIEW, "hitung", OBJ({"jumlah": {"type": "number"}}, ["jumlah"]), "POST", "/api/x/calculate")
T_BUAT = Tool("buat_x", Kelas.WRITE, "buat", OBJ({"jumlah": {"type": "number"}}, ["jumlah"]), "POST", "/api/x",
              pratinjau="hitung_x", butuh=("G1",), halaman="/x/baru", judul_kartu="Buat X")
T_UBAH_PR = Tool("pratinjau_ubah_x", Kelas.PREVIEW, "pratinjau ubah",
                 OBJ({"id": {"type": "string"}, "jumlah": {"type": "number"}}, ["id", "jumlah"]), "POST", "/api/x/{id}/edit/preview")
T_UBAH = Tool("ubah_x", Kelas.WRITE, "ubah", OBJ({"id": {"type": "string"}, "jumlah": {"type": "number"}}, ["id", "jumlah"]),
              "PATCH", "/api/x/{id}", pratinjau="pratinjau_ubah_x", halaman="/x/{id}/ubah")


def registri():
    return Registri([Paket("contoh", [T_CARI, T_HITUNG, T_BUAT, T_UBAH_PR, T_UBAH], "Kartu pengetahuan contoh.")])


def be_standar():
    return BEPalsu({
        ("GET", "/api/x"): JawabanBE(200, {"items": [{"id": "a1", "nama": "Kaos", "harga": 45000}]}),
        ("POST", "/api/x/calculate"): lambda **k: JawabanBE(200, {"total": json.loads(k["badan"])["jumlah"] * 45000}),
        ("POST", "/api/x"): JawabanBE(201, {"id": "baru1", "total": 9000000}),
        ("POST", "/api/x/a1/edit/preview"): JawabanBE(200, {"total": 90000, "blocks": []}),
        ("PATCH", "/api/x/a1"): JawabanBE(200, {"id": "a1"}),
    })


def putaran(naskah, gap=("G1",)):
    m = ModelPalsu(naskah)
    return Putaran(registri(), m, AuditMemori(), model_nama="claude-opus-5-5", gap_mendarat=gap), m


def sesi():
    return Sesi("s1", "kaos-biru-konveksi", "u1")


# ---------------------------------------------------------------- registri

async def test_write_butuh_gap_belum_mendarat_turun_jadi_nav():
    tools = {t.nama: t for t in await registri().tools_untuk(izin_semua, gap_mendarat=())}
    assert tools["buat_x"].kelas == Kelas.NAV
    assert tools["ubah_x"].kelas == Kelas.WRITE  # tak butuh gap
    tools2 = {t.nama: t for t in await registri().tools_untuk(izin_semua, gap_mendarat=("G1",))}
    assert tools2["buat_x"].kelas == Kelas.WRITE


async def test_filter_izin_membuang_tool_dan_write_tanpa_izin_pratinjau():
    async def izin(jalur, metode):
        return {j: not (metode == "POST" and j.endswith("/edit/preview")) for j in jalur}

    nama = [t.nama for t in await registri().tools_untuk(izin, ("G1",))]
    assert "ubah_x" not in nama and "pratinjau_ubah_x" not in nama
    assert "buat_x" in nama and "cari_x" in nama


async def test_nav_turunan_tetap_butuh_izin_aksi_asli():
    async def izin(jalur, metode):
        return {j: not (metode == "POST" and j == "/api/x") for j in jalur}

    nama = [t.nama for t in await registri().tools_untuk(izin, ())]
    assert "buat_x" not in nama


def test_paket_menolak_write_tanpa_pratinjau_dan_skema_tak_strict():
    with pytest.raises(ValueError):
        Paket("p", [Tool("w", Kelas.WRITE, "w", OBJ({}), "POST", "/api/w", halaman="/w")])
    with pytest.raises(ValueError):
        Paket("p", [Tool("r", Kelas.READ, "r", {"type": "object", "properties": {}}, "GET", "/api/r")])
    with pytest.raises(ValueError):
        Paket("p", [Tool("r", Kelas.READ, "r", OBJ({"n": {"type": "number", "minimum": 0}}), "GET", "/api/r")])


def test_definisi_api_tanpa_strict_skema_tertutup():
    for t in (T_BUAT, T_HITUNG, T_CARI):
        d = t.untuk_api()
        assert "strict" not in d and d["input_schema"]["additionalProperties"] is False


# ---------------------------------------------------------------- tulis hanya lewat Setuju

async def test_write_dari_model_tidak_menulis_hanya_kartu():
    p, m = putaran([panggil(("buat_x", {"jumlah": 200})), teks("Silakan tinjau kartunya.")])
    be, s = be_standar(), sesi()
    ev = await p.giliran(s, "buat 200", be, izin_semua)
    assert [c["jalur"] for c in be.tulis()] == ["/api/x/calculate"]  # pratinjau saja
    kartu = [e for e in ev if e["jenis"] == "kartu"]
    assert len(kartu) == 1 and kartu[0]["pratinjau"] == {"total": 9000000}
    hasil_tool = m.permintaan[1]["messages"][-1]["content"][0]
    assert "menunggu_persetujuan" in hasil_tool["content"]


async def test_setuju_mengirim_badan_identik_dan_kunci_kartu():
    p, _ = putaran([panggil(("buat_x", {"jumlah": 200})), teks("Tinjau."), teks("Penawaran tersimpan.")])
    be, s = be_standar(), sesi()
    ev = await p.giliran(s, "buat 200", be, izin_semua)
    k = s.kartu[[e for e in ev if e["jenis"] == "kartu"][0]["kartu_id"]]
    await p.setujui(s, k.kartu_id, be, izin_semua)
    tulis = [c for c in be.tulis() if c["jalur"] == "/api/x"]
    assert len(tulis) == 1
    assert tulis[0]["badan"] == k.badan == badan_kanonik({"jumlah": 200})
    assert tulis[0]["header"]["X-Idempotency-Key"] == k.kunci
    assert k.status == StatusKartu.SELESAI
    assert k.payload_hash == hash_payload({"jumlah": 200})


async def test_payload_setuju_sama_dengan_payload_pratinjau():
    p, _ = putaran([panggil(("buat_x", {"jumlah": 7})), teks("Tinjau."), teks("ok")])
    be, s = be_standar(), sesi()
    ev = await p.giliran(s, "buat 7", be, izin_semua)
    kid = [e for e in ev if e["jenis"] == "kartu"][0]["kartu_id"]
    await p.setujui(s, kid, be, izin_semua)
    pr = [c for c in be.panggilan if c["jalur"] == "/api/x/calculate"][0]
    tulis = [c for c in be.panggilan if c["jalur"] == "/api/x"][0]
    assert pr["badan"] == tulis["badan"]


async def test_setuju_dua_kali_nol_tulis_kedua():
    p, _ = putaran([panggil(("buat_x", {"jumlah": 1})), teks("Tinjau."), teks("ok")])
    be, s = be_standar(), sesi()
    ev = await p.giliran(s, "x", be, izin_semua)
    kid = [e for e in ev if e["jenis"] == "kartu"][0]["kartu_id"]
    await p.setujui(s, kid, be, izin_semua)
    ev2 = await p.setujui(s, kid, be, izin_semua)
    assert ev2[0]["jenis"] == "galat"
    assert len([c for c in be.tulis() if c["jalur"] == "/api/x"]) == 1


async def test_5xx_diulang_dengan_kunci_sama_maks_dua_kali():
    hit = {"n": 0}

    def tulis(**k):
        hit["n"] += 1
        return JawabanBE(503, {"detail": "sibuk"})

    p, _ = putaran([panggil(("buat_x", {"jumlah": 1})), teks("Tinjau."), teks("gagal")])
    be, s = be_standar(), sesi()
    be.rute[("POST", "/api/x")] = tulis
    ev = await p.giliran(s, "x", be, izin_semua)
    kid = [e for e in ev if e["jenis"] == "kartu"][0]["kartu_id"]
    await p.setujui(s, kid, be, izin_semua)
    kunci = {c["header"]["X-Idempotency-Key"] for c in be.tulis() if c["jalur"] == "/api/x"}
    assert hit["n"] == 3 and len(kunci) == 1
    assert s.kartu[kid].status == StatusKartu.GAGAL


async def test_5xx_lalu_sukses_dan_replay_dikenali():
    urutan = [JawabanBE(502, {}), JawabanBE(201, {"id": "z"}, {"x-idempotent-replay": "true"})]
    p, m = putaran([panggil(("buat_x", {"jumlah": 1})), teks("Tinjau."), teks("ok")])
    be, s = be_standar(), sesi()
    be.rute[("POST", "/api/x")] = lambda **k: urutan.pop(0)
    ev = await p.giliran(s, "x", be, izin_semua)
    kid = [e for e in ev if e["jenis"] == "kartu"][0]["kartu_id"]
    await p.setujui(s, kid, be, izin_semua)
    assert s.kartu[kid].status == StatusKartu.SELESAI
    assert "sudah tersimpan sebelumnya" in m.permintaan[-1]["messages"][-1]["content"]


async def test_4xx_basi_tanpa_coba_ulang():
    p, _ = putaran([panggil(("buat_x", {"jumlah": 1})), teks("Tinjau."), teks("basi")])
    be, s = be_standar(), sesi()
    be.rute[("POST", "/api/x")] = JawabanBE(400, {"code": "QUOTE_BASI"})
    ev = await p.giliran(s, "x", be, izin_semua)
    kid = [e for e in ev if e["jenis"] == "kartu"][0]["kartu_id"]
    await p.setujui(s, kid, be, izin_semua)
    assert len([c for c in be.tulis() if c["jalur"] == "/api/x"]) == 1
    assert s.kartu[kid].status == StatusKartu.BASI


async def test_usulan_baru_membuat_kartu_lama_basi_dan_kunci_baru():
    p, _ = putaran([panggil(("buat_x", {"jumlah": 1})), teks("Tinjau."),
                    panggil(("buat_x", {"jumlah": 2})), teks("Tinjau lagi.")])
    be, s = be_standar(), sesi()
    ev1 = await p.giliran(s, "x", be, izin_semua)
    ev2 = await p.giliran(s, "ganti 2", be, izin_semua)
    k1 = [e for e in ev1 if e["jenis"] == "kartu"][0]["kartu_id"]
    k2 = [e for e in ev2 if e["jenis"] == "kartu"][0]["kartu_id"]
    assert s.kartu[k1].status == StatusKartu.BASI and s.kartu[k2].status == StatusKartu.MENUNGGU
    assert s.kartu[k1].kunci != s.kartu[k2].kunci
    await p.setujui(s, k1, be, izin_semua)
    assert not [c for c in be.tulis() if c["jalur"] == "/api/x"]


async def test_dua_write_satu_langkah_yang_kedua_ditolak():
    p, m = putaran([panggil(("buat_x", {"jumlah": 1}), ("ubah_x", {"id": "a1", "jumlah": 2})), teks("Tinjau.")])
    be, s = be_standar(), sesi()
    ev = await p.giliran(s, "x", be, izin_semua)
    assert len([e for e in ev if e["jenis"] == "kartu"]) == 1
    hasil = m.permintaan[1]["messages"][-1]["content"]
    assert hasil[1].get("is_error") is True


async def test_pratinjau_berblok_tidak_melahirkan_kartu():
    p, _ = putaran([panggil(("ubah_x", {"id": "a1", "jumlah": 2})), teks("Tidak bisa diubah.")])
    be, s = be_standar(), sesi()
    be.rute[("POST", "/api/x/a1/edit/preview")] = JawabanBE(200, {"blocks": [{"code": "QUOTE_SUDAH_DIKONVERSI"}]})
    ev = await p.giliran(s, "ubah", be, izin_semua)
    assert not [e for e in ev if e["jenis"] == "kartu"] and not s.kartu


async def test_tool_di_luar_daftar_efektif_ditolak_tanpa_panggilan_be():
    async def izin(jalur, metode):
        return {j: metode == "GET" for j in jalur}

    p, m = putaran([panggil(("buat_x", {"jumlah": 1})), teks("Kamu tidak punya izin.")])
    be, s = be_standar(), sesi()
    await p.giliran(s, "x", be, izin)
    assert be.panggilan == []
    assert m.permintaan[1]["messages"][-1]["content"][0]["is_error"] is True
    assert all(t["name"] != "buat_x" for t in m.permintaan[0]["tools"])


async def test_nav_turunan_tidak_memanggil_be():
    p, _ = putaran([panggil(("buat_x", {"jumlah": 1})), teks("Buka halaman itu.")], gap=())
    be, s = be_standar(), sesi()
    ev = await p.giliran(s, "x", be, izin_semua)
    assert be.panggilan == [] and [e for e in ev if e["jenis"] == "nav"][0]["halaman"] == "/x/baru"


async def test_masukan_kunci_asing_ditolak():
    p, m = putaran([panggil(("cari_x", {"q": "a", "tenant_id": "lain"})), teks("ok")])
    be, s = be_standar(), sesi()
    await p.giliran(s, "x", be, izin_semua)
    assert be.panggilan == []
    assert "tidak dikenal" in m.permintaan[1]["messages"][-1]["content"][0]["content"]


async def test_parameter_jalur_disaring():
    p, m = putaran([panggil(("ubah_x", {"id": "../../admin", "jumlah": 1})), teks("ok")])
    be, s = be_standar(), sesi()
    await p.giliran(s, "x", be, izin_semua)
    assert be.panggilan == []


# ---------------------------------------------------------------- penjaga angka

async def test_penjaga_angka_memaksa_koreksi_sekali():
    p, m = putaran([panggil(("cari_x", {"q": "kaos"})), teks("Harga kaos Rp 50.000."), teks("Harga kaos Rp 45.000.")])
    be, s = be_standar(), sesi()
    ev = await p.giliran(s, "harga kaos?", be, izin_semua)
    teks_ev = [e for e in ev if e["jenis"] == "teks"]
    assert [e["teks"] for e in teks_ev] == ["Harga kaos Rp 45.000."]
    assert "[penjaga]" in m.permintaan[2]["messages"][-1]["content"]


async def test_penjaga_angka_kedua_kali_angka_disamarkan():
    # MASTER 25 Okt: dulu "lolos-bertanda" (angka karangan tetap tampil); kini angka itu disamarkan + catatan.
    p, _ = putaran([teks("Totalnya Rp 1.234.567 untuk QT-2610-0001."), teks("Totalnya Rp 1.234.567 untuk QT-2610-0001.")])
    be, s = be_standar(), sesi()
    ev = await p.giliran(s, "berapa?", be, izin_semua)
    t = [e for e in ev if e["jenis"] == "teks"][0]
    assert t["angka_tak_terverifikasi"] == ["Rp 1.234.567"]
    assert t["teks"] == "Totalnya … untuk QT-2610-0001.\n\n" + CATATAN_ANGKA_DISAMARKAN
    assert "1.234.567" not in t["teks"]
    assert any(b.peristiwa == "penjaga_angka" and b.hasil.startswith("disamarkan:") for b in p.audit.baris)


@pytest.mark.parametrize("jawaban,sumber,hasil", [
    ("Total Rp 9.000.000, diskon Rp 50.000.", [{"total": 9000000}], "Total Rp 9.000.000, diskon …."),
    ("QT-2610-0001 tanggal 24 Okt 2026 total 300.000", [], "QT-2610-0001 tanggal 24 Okt 2026 total …"),
    ("Diskon 16,67% dan 3 pcs", [{"discount_percent": "16.67"}], "Diskon 16,67% dan 3 pcs"),
    ("100.000 × 3 = 300.000 jadi", [{"harga": 100000}], "100.000 × 3 = … jadi"),
])
def test_samarkan(jawaban, sumber, hasil):
    assert penjaga_angka.samarkan(jawaban, sumber) == hasil


async def test_teks_pengantar_bersama_tool_tidak_ditampilkan():
    # eval nyata kaos 25 Okt: "Harga master 100.000 × 3 = 300.000 ..." tampil sebelum tool -> kini hanya jawaban akhir.
    p, m = putaran([panggil(("cari_x", {"q": "kaos"}), teks_awal="Harga 45.000 × 3 = 135.000, saya cek dulu."),
                    teks("Harga kaos Rp 45.000.")])
    be, s = be_standar(), sesi()
    ev = await p.giliran(s, "harga kaos?", be, izin_semua)
    assert [e["teks"] for e in ev if e["jenis"] == "teks"] == ["Harga kaos Rp 45.000."]
    assert not any(e.get("angka_tak_terverifikasi") for e in ev if e["jenis"] == "teks")
    model = [b for b in p.audit.baris if b.peristiwa == "model"]
    assert model[0].hasil.endswith(";pengantar_disembunyikan:42") and len(model[0].payload_hash or "") == 64
    assert model[1].payload_hash is None and model[1].hasil == "end_turn"
    # riwayat model tetap utuh (teks pengantar ada di messages untuk giliran berikutnya)
    assert any(k.get("text") == "Harga 45.000 × 3 = 135.000, saya cek dulu." for k in m.permintaan[1]["messages"][-2]["content"])


@pytest.mark.parametrize("jawaban,sumber,lolos", [
    ("Total Rp 9.000.000", [{"total": 9000000}], True),
    ("Total Rp 9.000.000", [{"total": "9000000.00"}], True),
    ("PPN 11%", [{"tax_rate": 11}], True),
    ("PPN 11%", [{"tax_rate": "0.11"}], True),
    ("Margin 37,8%", [{"harga": 45000, "hpp": 28000}], False),
    ("Harga 45 ribu", ["harga 45 ribu ya"], True),
    ("Harga 45 ribu", [{"harga": 45000}], True),
    ("QT-2610-0001 berlaku sampai 30 Okt 2026, 200 pcs", [{}], True),
    ("Kurang Rp 500", [{}], False),
    ("Selisih 1.500.000", [{"a": 10000000, "b": 8500000}], False),
    ("Belum ada surat jalan bulan Oktober 2026.", [{}], True),          # positif palsu PNW-032
    ("Jatuh tempo 31-10-2026 dan tahun 2027 nanti.", [{}], True),
])
def test_penjaga_angka_kasus(jawaban, sumber, lolos):
    assert (penjaga_angka.periksa(jawaban, sumber) == []) is lolos


# ---------------------------------------------------------------- riwayat & teks sumber

async def test_riwayat_append_only_dan_thinking_dipertahankan():
    p, m = putaran([panggil(("cari_x", {"q": "a"})), teks("ok"), panggil(("cari_x", {"q": "b"})), teks("ok2")])
    be, s = be_standar(), sesi()
    await p.giliran(s, "satu", be, izin_semua)
    await p.giliran(s, "dua", be, izin_semua)
    for a, b in zip(m.permintaan, m.permintaan[1:]):
        assert b["messages"][: len(a["messages"])] == a["messages"]
    assert m.permintaan[1]["messages"][1]["content"][0]["type"] == "thinking"


async def test_system_dan_tools_stabil_antar_giliran_untuk_cache():
    p, m = putaran([teks("a"), teks("b")])
    be, s = be_standar(), sesi()
    await p.giliran(s, "1", be, izin_semua)
    await p.giliran(s, "2", be, izin_semua)
    assert m.permintaan[0]["system"] == m.permintaan[1]["system"]
    assert m.permintaan[0]["tools"] == m.permintaan[1]["tools"]


def test_hash_payload_identik_dengan_be_teks_sumber():
    akar = os.path.join(os.path.dirname(__file__), "..", "..", "app")
    be_src = open(os.path.join(akar, "utils", "idempotency.py")).read()
    mb_src = open(os.path.join(akar, "milkybook", "inti", "kartu.py")).read()
    pola = re.compile(r'json\.dumps\(payload, sort_keys=True, default=str, separators=\(",", ":"\)\)\.encode\(\)')
    assert pola.search(be_src) and pola.search(mb_src)
    from app.utils.idempotency import hash_payload as be_hash  # noqa: F401 -- impor bila tersedia
    assert be_hash({"b": 1, "a": [1, "x"]}) == hash_payload({"b": 1, "a": [1, "x"]})


async def test_audit_tanpa_payload_mentah():
    p, _ = putaran([panggil(("buat_x", {"jumlah": 3})), teks("Tinjau.")])
    a = p.audit
    await p.giliran(sesi(), "buat untuk PT Rahasia", be_standar(), izin_semua)
    teks_audit = json.dumps(a.sebagai_dict())
    assert "PT Rahasia" not in teks_audit and '"jumlah"' not in teks_audit
    assert any(b["peristiwa"] == "kartu_lahir" and b["kunci_idempotensi"] for b in a.sebagai_dict())


async def test_null_dari_model_dibuang_sebelum_payload():
    from app.milkybook.inti.registri import buang_null
    assert buang_null({"a": "x", "b": None, "d": [{"e": None, "f": 1}]}) == {"a": "x", "d": [{"f": 1}]}
    p2, m = putaran([panggil(("buat_x", {"jumlah": 3, "catatan": None})), teks("Tinjau.")])
    be, s2 = be_standar(), sesi()
    ev = await p2.giliran(s2, "x", be, izin_semua)
    # null dibuang SEBELUM validasi: kunci bernilai null tidak pernah sampai ke payload.
    assert [e for e in ev if e["jenis"] == "kartu"][0]["payload"] == {"jumlah": 3}


async def test_tutup_kartu_tinjau_dari_model_nol_tulis_dan_setuju_ditolak():
    p, m = putaran([panggil(("buat_x", {"jumlah": 1})), teks("Tinjau."), panggil(("tutup_kartu_tinjau", {})), teks("Usulan ditutup.")])
    be, s = be_standar(), sesi()
    ev = await p.giliran(s, "buat", be, izin_semua)
    kid = [e for e in ev if e["jenis"] == "kartu"][0]["kartu_id"]
    ev2 = await p.giliran(s, "tidak jadi", be, izin_semua)
    assert s.kartu[kid].status == StatusKartu.DITOLAK
    assert any(e["jenis"] == "kartu_status" and e["status"] == "ditolak" for e in ev2)
    await p.setujui(s, kid, be, izin_semua)
    assert not [c for c in be.tulis() if c["jalur"] == "/api/x"]
    assert any(t["name"] == "tutup_kartu_tinjau" for t in m.permintaan[0]["tools"])


# ---------------------------------------------------------------- penjaga tanggal
from datetime import date as _date  # noqa: E402

from app.milkybook.inti import penjaga_tanggal  # noqa: E402

HARI = _date(2026, 10, 24)


@pytest.mark.parametrize("teks,lolos", [
    ("Berlaku sampai 26 Oktober 2026, besok.", False),          # temuan K9 Haiku
    ("Berlaku sampai 26 Oktober 2026, tinggal 2 hari lagi.", True),
    ("Berlaku sampai 26 Okt 2026, 2 hari lagi.", True),
    ("Berlaku sampai 2026-10-25, besok.", True),
    ("Tanggal berlaku 19 Okt 2026 sudah lewat 5 hari.", True),
    ("Tanggal berlaku 19 Okt 2026 sudah lewat 3 hari.", False),
    ("Kirim lusa, 26 Okt.", True),
    ("Besok saya ingatkan lagi.", True),                         # tanpa tanggal di kalimat: tak bisa diperiksa
    ("Jatuh tempo 24-10-2026, hari ini juga.", True),
])
def test_penjaga_tanggal_kasus(teks, lolos):
    assert (penjaga_tanggal.periksa(teks, HARI) == []) is lolos


async def test_penjaga_tanggal_memaksa_koreksi_dan_hitung_tanggal_server():
    p, m = putaran([teks("Penawaran berlaku sampai 26 Oktober 2026, besok."),
                    panggil(("hitung_tanggal", {"sampai": "2026-10-26"})),
                    teks("Penawaran berlaku sampai 26 Oktober 2026, 2 hari lagi.")])
    be, s = be_standar(), sesi()
    s.hari_ini = HARI
    ev = await p.giliran(s, "kapan habis?", be, izin_semua)
    assert [e["teks"] for e in ev if e["jenis"] == "teks"] == ["Penawaran berlaku sampai 26 Oktober 2026, 2 hari lagi."]
    assert "tanggal relatif" in m.permintaan[1]["messages"][-1]["content"]
    hasil = m.permintaan[2]["messages"][-1]["content"][0]["content"]
    assert '"selisih_hari": 2' in hasil and '"hari_sampai": "Senin"' in hasil
    assert any(t["name"] == "hitung_tanggal" for t in m.permintaan[0]["tools"])


async def test_hitung_tanggal_tambah_hari():
    p, m = putaran([panggil(("hitung_tanggal", {"tambah_hari": 14})), teks("Berlaku sampai 7 Nov 2026.")])
    be, s = be_standar(), sesi()
    s.hari_ini = HARI
    await p.giliran(s, "berlaku 2 minggu", be, izin_semua)
    assert '"sampai": "2026-11-07"' in m.permintaan[1]["messages"][-1]["content"][0]["content"]


def test_harga_sasaran_opsi_satu_baris():
    # eval nyata kaos 25 Okt: "totalnya pas Rp250.000" dari 1 barang Rp100.000 -> model bertanya tanpa opsi
    h = harga_sasaran.hitung([{"harga_satuan": 100000, "jumlah": 3}], 250000)
    o = {x["jenis"]: x for x in h["opsi"]}
    assert h["subtotal_master"] == "300000" and h["dpp_sasaran"] == "250000"
    assert o["diskon_dokumen_rupiah"]["discount_value"] == "50000"
    assert o["diskon_persen_baris"]["discount_percent"] == "16.67"
    assert o["harga_satuan_baru"]["unit_price"] == "83333"
    assert "jumlah_pas" not in o                                    # 2,5 pcs bukan bilangan bulat


def test_harga_sasaran_jumlah_pas_dan_termasuk_pajak():
    o = {x["jenis"]: x for x in harga_sasaran.hitung([{"harga_satuan": 100000, "jumlah": 1}], 300000)["opsi"]}
    assert o["jumlah_pas"]["quantity"] == "3" and "diskon_dokumen_rupiah" not in o   # target > master: tanpa diskon
    h = harga_sasaran.hitung([{"harga_satuan": 1000000, "jumlah": 1}], 1110000, 11, True)
    assert h["dpp_sasaran"] == "1000000" and h["opsi"] == []         # sudah pas: tak ada opsi


@pytest.mark.parametrize("masukan", [([], 1000), ([{"harga_satuan": 1}], 1000), ([{"harga_satuan": 1, "jumlah": 0}], 1000),
                                     ([{"harga_satuan": 1, "jumlah": 1}], 0), ([{"harga_satuan": True, "jumlah": 1}], 1)])
def test_harga_sasaran_tolak_masukan_tak_sah(masukan):
    with pytest.raises(ValueError):
        harga_sasaran.hitung(*masukan)


async def test_hitung_harga_sasaran_tool_inti_dan_angka_sah():
    p, m = putaran([panggil(("hitung_harga_sasaran", {"baris": [{"harga_satuan": 100000, "jumlah": 3}], "target_total": 250000})),
                    teks("Pilihan: diskon Rp50.000, diskon 16,67%, atau harga satuan Rp83.333.")])
    be, s = be_standar(), sesi()
    ev = await p.giliran(s, "3 pcs totalnya pas 250 ribu", be, izin_semua)
    assert any(t["name"] == "hitung_harga_sasaran" for t in m.permintaan[0]["tools"])
    assert {"jenis": "tool", "nama": "hitung_harga_sasaran", "kelas": "INTI"} in ev   # terlihat penilai eval + UI
    assert '"discount_value": "50000"' in m.permintaan[1]["messages"][-1]["content"][0]["content"]
    assert [e["teks"] for e in ev if e["jenis"] == "teks"] == ["Pilihan: diskon Rp50.000, diskon 16,67%, atau harga satuan Rp83.333."]
    assert be.panggilan == []                                        # inti: tanpa panggilan BE


async def test_hitung_harga_sasaran_masukan_salah_jadi_galat_tool():
    p, m = putaran([panggil(("hitung_harga_sasaran", {"baris": [], "target_total": 250000})), teks("Maaf.")])
    await p.giliran(sesi(), "x", be_standar(), izin_semua)
    blok = m.permintaan[1]["messages"][-1]["content"][0]
    assert blok.get("is_error") is True and "tidak sah" in blok["content"]


@pytest.mark.parametrize("isi", [
    {"blocks": [{"code": "X"}]},
    {"success": True, "data": {"ok": False, "blocks": [{"code": "ALASAN_WAJIB"}]}},   # void penawaran quotes.py:1750
    {"success": True, "data": {"ok": False, "blocks": []}},
    {"can_proceed": False, "blocks": []},                                            # proforma _pratinjau
    {"success": True, "data": {"can_cancel": False}},
])
async def test_pratinjau_menolak_dalam_segala_bentuk_tanpa_kartu(isi):
    p, _ = putaran([panggil(("ubah_x", {"id": "a1", "jumlah": 2})), teks("Tidak bisa.")])
    be, s = be_standar(), sesi()
    be.rute[("POST", "/api/x/a1/edit/preview")] = JawabanBE(200, isi)
    ev = await p.giliran(s, "ubah", be, izin_semua)
    assert not [e for e in ev if e["jenis"] == "kartu"] and not s.kartu


async def test_pratinjau_lolos_bentuk_data_bersarang_melahirkan_kartu():
    p, _ = putaran([panggil(("ubah_x", {"id": "a1", "jumlah": 2})), teks("Tinjau.")])
    be, s = be_standar(), sesi()
    be.rute[("POST", "/api/x/a1/edit/preview")] = JawabanBE(200, {"success": True, "data": {"ok": True, "blocks": []}})
    ev = await p.giliran(s, "ubah", be, izin_semua)
    assert len([e for e in ev if e["jenis"] == "kartu"]) == 1


def test_aturan_harga_sasaran_ada_di_system():
    """Putusan MASTER 24 Okt (PNW-056): model tak boleh menurunkan harga satuan dari total sasaran."""
    from app.milkybook.inti.putaran import ATURAN_SISTEM
    assert "HARGA SASARAN" in ATURAN_SISTEM and "JANGAN menghitung harga satuan" in ATURAN_SISTEM


def test_aturan_jangan_menghilangkan_permintaan_ada_di_system():
    """Temuan eval 24 Okt (Sonnet PNW-059): saldo awal 2 jt dibuang tanpa disebut."""
    from app.milkybook.inti.putaran import ATURAN_SISTEM
    assert "JANGAN MENGHILANGKAN PERMINTAAN DIAM-DIAM" in ATURAN_SISTEM


# ---------------------------------------------------------------- PATCH hanya medan yang berubah (PNW-015)
from app.milkybook.inti.putaran import buang_medan_tak_berubah  # noqa: E402

LAMA = {"id": "a1", "discount_type": "fixed", "discount_value": 0, "notes": "x",
        "items": [{"id": "L1", "description": "Kaos", "quantity": 10, "unit_price": "85000.00", "tax_rate": 0}]}


@pytest.mark.parametrize("usul,harap", [
    ({"discount_type": "percentage", "discount_value": 5,
      "items": [{"id": "L1", "description": "Kaos", "quantity": 10, "unit_price": 85000, "tax_rate": 0}]},
     {"discount_type": "percentage", "discount_value": 5}),                                  # PNW-015
    ({"items": [{"id": "L1", "description": "Kaos", "quantity": 15, "unit_price": 85000}]},
     {"items": [{"id": "L1", "description": "Kaos", "quantity": 15, "unit_price": 85000}]}),  # baris berubah: utuh
    ({"items": [{"id": "L1", "description": "Kaos", "quantity": 10, "unit_price": 85000},
                {"description": "Topi", "quantity": 5, "unit_price": 30000}]}, None),          # baris baru: utuh
    ({"notes": "x", "discount_value": 0}, {}),
])
def test_buang_medan_tak_berubah(usul, harap):
    assert buang_medan_tak_berubah(usul, LAMA) == (usul if harap is None else harap)


async def test_patch_tanpa_perubahan_tidak_melahirkan_kartu_dan_pratinjau_menerima_payload_ringkas():
    from dataclasses import replace as _r
    ubah = _r(T_UBAH, pembanding="detail_x")
    detail = Tool("detail_x", Kelas.READ, "d", OBJ({"id": {"type": "string"}}, ["id"]), "GET", "/api/x/{id}")
    reg = Registri([Paket("contoh", [T_CARI, T_HITUNG, T_BUAT, T_UBAH_PR, ubah, detail])])
    be = be_standar()
    be.rute[("GET", "/api/x/a1")] = JawabanBE(200, {"success": True, "data": {"id": "a1", "jumlah": 2}})
    m = ModelPalsu([panggil(("ubah_x", {"id": "a1", "jumlah": 2})), teks("Tidak ada yang berubah."),
                    panggil(("ubah_x", {"id": "a1", "jumlah": 3})), teks("Tinjau.")])
    p = Putaran(reg, m, AuditMemori(), gap_mendarat=("G1",))
    s = sesi()
    ev = await p.giliran(s, "ubah jadi 2", be, izin_semua)
    assert not [e for e in ev if e["jenis"] == "kartu"]
    ev2 = await p.giliran(s, "jadi 3", be, izin_semua)
    k = [e for e in ev2 if e["jenis"] == "kartu"][0]
    assert k["payload"] == {"jumlah": 3}
    pr = [c for c in be.panggilan if c["jalur"] == "/api/x/a1/edit/preview"][-1]
    assert pr["badan"] == badan_kanonik({"jumlah": 3})


# ---------------------------------------------------------------- cache prefiks bersama
def test_breakpoint_eksplisit_di_akhir_system_ttl_1h_dan_system_tanpa_isi_volatil():
    from app.milkybook.inti.model import sistem_berbreakpoint
    sys_ = [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]
    out = sistem_berbreakpoint(sys_, "1h")
    assert out[-1]["cache_control"] == {"type": "ephemeral", "ttl": "1h"} and "cache_control" not in out[0]
    assert "cache_control" not in sys_[-1]  # tidak memutasi asli
    assert sistem_berbreakpoint(sys_, "5m")[-1]["cache_control"] == {"type": "ephemeral"}
    # prefiks harus byte-identik lintas sesi/tenant: system tak memuat tanggal/tenant/pengguna
    p = Putaran(registri(), None, AuditMemori())
    t = p.system[0]["text"]
    import re as _re
    assert not _re.search(r"\b20\d\d-\d\d-\d\d\b", t) and "kaos-biru" not in t and "Hari ini" not in t


async def test_pemanggil_anthropic_mengirim_breakpoint_dan_cache_otomatis():
    from types import SimpleNamespace
    from app.milkybook.inti import model as M
    tangkap = {}

    class Klien:
        class beta:
            class messages:
                @staticmethod
                async def create(**kw):
                    tangkap.update(kw)
                    return SimpleNamespace(content=[], usage=None, stop_reason="end_turn", model=kw["model"])
        messages = beta.messages

    pm = M.PemanggilAnthropic.__new__(M.PemanggilAnthropic)
    pm.model, pm.max_tokens, pm.ttl_prefiks, pm._client = "claude-sonnet-5-5", 100, "1h", Klien
    await pm(system=[{"type": "text", "text": "x"}], messages=[], tools=[], effort="medium")
    assert tangkap["system"][-1]["cache_control"]["ttl"] == "1h" and tangkap["cache_control"] == {"type": "ephemeral"}


def test_biaya_tulis_cache_1h_dua_kali_harga_masuk():
    from app.milkybook.inti.model import biaya_usd
    u = {"cache_creation_input_tokens": 1_000_000, "cache_creation": {"ephemeral_1h_input_tokens": 1_000_000, "ephemeral_5m_input_tokens": 0}}
    assert abs(biaya_usd("claude-sonnet-5-5", u) - 4.0) < 1e-9
    assert abs(biaya_usd("claude-sonnet-5-5", {"cache_creation_input_tokens": 1_000_000}) - 2.5) < 1e-9


# ---------------------------------------------------------------- hibrida baca (model murah) / tulis
def _hibrida(naskah_baca, naskah_tulis):
    mb, mt = ModelPalsu(naskah_baca), ModelPalsu(naskah_tulis)
    p = Putaran(registri(), mt, AuditMemori(), gap_mendarat=("G1",), pemanggil_baca=mb)
    return p, mb, mt


async def test_hibrida_model_baca_tak_pernah_menerima_tool_write():
    p, mb, mt = _hibrida([panggil(("cari_x", {"q": "a"})), teks("Ini daftarnya.")], [])
    await p.giliran(sesi(), "ada apa saja?", be_standar(), izin_semua)
    nama = {t["name"] for t in mb.permintaan[0]["tools"]}
    assert "buat_x" not in nama and "ubah_x" not in nama and "tutup_kartu_tinjau" not in nama
    assert "perlu_penulis" in nama and "cari_x" in nama and "hitung_x" in nama
    assert mt.permintaan == [] and "Mode baca" in mb.permintaan[0]["system"][0]["text"]


async def test_hibrida_model_baca_memanggil_write_paksa_ditolak_nol_tulis():
    p, mb, mt = _hibrida([panggil(("buat_x", {"jumlah": 1})), teks("Tidak bisa.")], [])
    be = be_standar()
    await p.giliran(sesi(), "buat", be, izin_semua)
    assert be.panggilan == [] and mt.permintaan == []
    assert mb.permintaan[1]["messages"][-1]["content"][0]["is_error"] is True


async def test_hibrida_perlu_penulis_dialihkan_ke_model_tulis_riwayat_utuh():
    p, mb, mt = _hibrida([panggil(("perlu_penulis", {"alasan": "buat penawaran"}))],
                         [panggil(("buat_x", {"jumlah": 2})), teks("Tinjau kartunya.")])
    be, s = be_standar(), sesi()
    ev = await p.giliran(s, "buat 2", be, izin_semua)
    assert len([e for e in ev if e["jenis"] == "kartu"]) == 1
    assert "buat_x" in {t["name"] for t in mt.permintaan[0]["tools"]}
    # model tulis melihat riwayat model baca (append-only): pesan pertama sama, lalu tool_use perlu_penulis
    assert mt.permintaan[0]["messages"][0] == mb.permintaan[0]["messages"][0]
    assert any(b.get("name") == "perlu_penulis" for b in mt.permintaan[0]["messages"][1]["content"])
    assert mt.permintaan[0]["messages"][-1]["content"][0]["content"].startswith("[sistem] Giliran ini dialihkan")
    assert s.mode_tulis is True


async def test_hibrida_kartu_menunggu_dan_lanjutan_setuju_pakai_model_tulis():
    p, mb, mt = _hibrida([panggil(("perlu_penulis", {"alasan": "x"}))],
                         [panggil(("buat_x", {"jumlah": 2})), teks("Tinjau."), teks("Ganti apa?"), teks("Tersimpan.")])
    be, s = be_standar(), sesi()
    ev = await p.giliran(s, "buat", be, izin_semua)
    n_baca = len(mb.permintaan)
    await p.giliran(s, "eh tunggu", be, izin_semua)          # kartu masih menunggu -> model tulis
    assert len(mb.permintaan) == n_baca
    kid = [e for e in ev if e["jenis"] == "kartu"][0]["kartu_id"]
    be.rute[("POST", "/api/x")] = JawabanBE(201, {"id": "b"})
    await p.setujui(s, kid, be, izin_semua)                  # lanjutan sesudah Setuju -> model tulis
    assert len(mb.permintaan) == n_baca and s.mode_tulis is False  # selesai tanpa usulan baru -> kembali ke baca


async def test_tanpa_pemanggil_baca_perilaku_lama():
    p, m = putaran([teks("ok")])
    await p.giliran(sesi(), "x", be_standar(), izin_semua)
    assert "perlu_penulis" not in {t["name"] for t in m.permintaan[0]["tools"]}



async def test_hibrida_teks_model_baca_saat_alih_tidak_ditampilkan():
    p, mb, mt = _hibrida([panggil(("perlu_penulis", {"alasan": "x"}), teks_awal="Sudah saya teruskan ke asisten penulis.")],
                         [panggil(("buat_x", {"jumlah": 2})), teks("Kartu Tinjau sudah tampil.")])
    ev = await p.giliran(sesi(), "buat 2", be_standar(), izin_semua)
    semua = " ".join(e.get("teks", "") for e in ev if e["jenis"] == "teks")
    assert "teruskan" not in semua and "Kartu Tinjau sudah tampil." in semua


# ---------------------------------------------------------------- syarat MASTER hibrida bawaan (24 Okt)
async def test_saklar_baca_mati_semua_giliran_ke_model_tulis_tanpa_deploy():
    mb, mt = ModelPalsu([teks("baca")]), ModelPalsu([teks("tulis"), teks("tulis2")])
    nyala = {"v": False}
    p = Putaran(registri(), mt, AuditMemori(), gap_mendarat=("G1",), pemanggil_baca=mb, saklar_baca=lambda: nyala["v"])
    s = sesi()
    await p.giliran(s, "daftar?", be_standar(), izin_semua)
    assert mb.permintaan == [] and len(mt.permintaan) == 1
    nyala["v"] = True                                    # dibaca SETIAP giliran: nyala lagi tanpa membuat Putaran baru
    await p.giliran(s, "daftar lagi?", be_standar(), izin_semua)
    assert len(mb.permintaan) == 1


def test_konfigurasi_env_kosong_berarti_sonnet_saja():
    from app.milkybook.inti import konfigurasi as K
    assert K.model_baca_dari_env({}) == "claude-haiku-5-5"
    assert K.model_baca_dari_env({"MILKYBOOK_MODEL_BACA": ""}) is None
    assert K.model_baca_dari_env({"MILKYBOOK_MODEL_BACA": "  "}) is None
    env = {"MILKYBOOK_MODEL_BACA": "claude-haiku-5-5"}
    sk = K.saklar_baca_dari_env(env)
    assert sk() is True
    env["MILKYBOOK_MODEL_BACA"] = ""
    assert sk() is False                                 # mundur cepat terbaca tanpa membuat ulang objek


async def test_audit_satu_baris_per_giliran_model_eskalasi_biaya():
    def hasil(model, *a, **k):
        h = panggil(*a, **k) if a else teks("ok")
        h.model = model
        return h
    mb = ModelPalsu([hasil("claude-haiku-5-5", ("perlu_penulis", {"alasan": "x"}))])
    mt = ModelPalsu([hasil("claude-sonnet-5-5", ("buat_x", {"jumlah": 1})), hasil("claude-sonnet-5-5")])
    p = Putaran(registri(), mt, AuditMemori(), gap_mendarat=("G1",), pemanggil_baca=mb)
    await p.giliran(sesi(), "buat", be_standar(), izin_semua)
    g = [b for b in p.audit.sebagai_dict() if b["peristiwa"] == "giliran"]
    assert len(g) == 1 and g[0]["eskalasi"] is True and g[0]["kelas"] == "BACA"
    assert g[0]["model"] == "claude-haiku-5-5,claude-sonnet-5-5" and g[0]["biaya_usd"] > 0


def test_sql_audit_jumlah_kolom_sama_dengan_placeholder_dan_migrasi():
    import os as _o
    from app.milkybook.inti import audit as A
    kolom = A._SQL.split("(", 1)[1].split(")", 1)[0].split(",")
    assert len(kolom) == A._SQL.count("$")
    mig = open(_o.path.join(_o.path.dirname(A.__file__), "..", "..", "..", "..", "migrations", "V465__asisten_aksi.sql")).read()
    assert "CREATE TABLE IF NOT EXISTS asisten_aksi" in mig
    for k in kolom:
        assert k.strip() in mig, k


def test_buat_putaran_dari_env_hibrida_bawaan_dan_mundur(monkeypatch):
    from app.milkybook.inti import konfigurasi as K, model as M

    class P:
        def __init__(self, model, api_key=None, **k):
            self.model, self.api_key = model, api_key

    monkeypatch.setattr(M, "PemanggilAnthropic", P)
    p = K.buat_putaran_dari_env(registri(), AuditMemori(), env={"MILKYBOOK_ANTHROPIC_API_KEY": "x"})
    assert p.pemanggil.model == "claude-sonnet-5-5" and p.pemanggil_baca.model == "claude-haiku-5-5" and p.effort == "medium"
    p2 = K.buat_putaran_dari_env(registri(), AuditMemori(), env={"MILKYBOOK_MODEL_BACA": ""})
    assert p2.pemanggil_baca is None



# ---------------------------------------------------------------- penjaga kartu (eval 25 Okt PNW-008)

async def test_penjaga_kartu_klaim_tanpa_tool_tulis_dipaksa_koreksi():
    klaim = "Penawaran sudah saya siapkan untuk Tinjau. Belum tersimpan sampai Anda menekan Setuju."
    p, m = putaran([teks(klaim), panggil(("buat_x", {"jumlah": 2})), teks("Silakan tinjau kartunya.")])
    be, s = be_standar(), sesi()
    ev = await p.giliran(s, "buat 2", be, izin_semua)
    assert "kartu Tinjau, padahal belum ada kartu" in m.permintaan[1]["messages"][-1]["content"]
    assert [e["teks"] for e in ev if e["jenis"] == "teks"] == ["Silakan tinjau kartunya."]
    assert any(e["jenis"] == "kartu" for e in ev)
    assert any(b.peristiwa == "penjaga_angka" and b.hasil == "klaim_kartu" for b in p.audit.baris)


async def test_penjaga_kartu_kedua_kali_diberi_catatan_jujur():
    from app.milkybook.inti.putaran import CATATAN_TANPA_KARTU
    klaim = "Kartu Tinjau sudah tampil, silakan tekan Setuju."
    p, _ = putaran([teks(klaim), teks(klaim)])
    ev = await p.giliran(sesi(), "buat", be_standar(), izin_semua)
    t = [e for e in ev if e["jenis"] == "teks"][0]
    assert t["teks"].endswith(CATATAN_TANPA_KARTU) and t["kartu_tak_ada"] is True


async def test_penjaga_kartu_tidak_salah_tangkap():
    # (a) kartu memang lahir di giliran ini; (b) kalimat lampau sesudah Setuju; (c) kartu menunggu dari giliran lalu
    p, m = putaran([panggil(("buat_x", {"jumlah": 2})), teks("Kartu Tinjau sudah tampil. Tekan Setuju bila benar."),
                    teks("Tersimpan setelah Anda menekan Setuju."), teks("Kartu tadi masih ada di kartu Tinjau; tekan Setuju.")])
    be, s = be_standar(), sesi()
    ev = await p.giliran(s, "buat 2", be, izin_semua)
    kid = next(e["kartu_id"] for e in ev if e["jenis"] == "kartu")
    ev2 = await p.setujui(s, kid, be, izin_semua)
    assert [e["teks"] for e in ev2 if e["jenis"] == "teks"] == ["Tersimpan setelah Anda menekan Setuju."]
    p2, m2 = putaran([panggil(("buat_x", {"jumlah": 2})), teks("Tinjau."), teks("Kartu tadi masih ada di kartu Tinjau; tekan Setuju.")])
    s2 = sesi()
    await p2.giliran(s2, "buat 2", be_standar(), izin_semua)
    ev3 = await p2.giliran(s2, "yang tadi gimana?", be_standar(), izin_semua)
    assert [e["teks"] for e in ev3 if e["jenis"] == "teks"] == ["Kartu tadi masih ada di kartu Tinjau; tekan Setuju."]
    assert not any("[penjaga]" in json.dumps(x) for x in m.permintaan[-1]["messages"][-2:])


async def test_hibrida_jawaban_atas_pertanyaan_penulis_tetap_ke_penulis_satu_giliran():
    # eval 25 Okt PNW-008: penulis bertanya (barang baru? pajak?) -> "lanjutkan" dulu jatuh ke model baca tanpa tool tulis.
    p, mb, mt = _hibrida([panggil(("perlu_penulis", {"alasan": "buat"})), teks("Ini daftarnya.")],
                         [teks("Barangnya dibuat baru atau ditulis bebas?"), panggil(("buat_x", {"jumlah": 2})), teks("Tinjau."),
                          teks("Baik.")])
    be, s = be_standar(), sesi()
    await p.giliran(s, "buat penawaran", be, izin_semua)                  # eskalasi -> penulis bertanya
    assert s.mode_tulis and s.tulis_tertahan
    n_baca = len(mb.permintaan)
    ev = await p.giliran(s, "lanjutkan, tulis bebas", be, izin_semua)    # jawaban -> penulis, kartu lahir
    assert len(mb.permintaan) == n_baca and any(e["jenis"] == "kartu" for e in ev)
    k = s.kartu_menunggu()
    await p.tolak(s, k.kartu_id)
    await p.giliran(s, "ok", be, izin_semua)                              # penulis tanpa eskalasi, tanpa tulis -> lepas
    assert not s.mode_tulis and not s.tulis_tertahan
    await p.giliran(s, "ada apa saja?", be, izin_semua)                    # kembali ke model baca
    assert len(mb.permintaan) > n_baca


async def test_hibrida_model_baca_mengklaim_kartu_dialihkan_ke_penulis():
    p, mb, mt = _hibrida([teks("Penawaran sudah saya siapkan untuk Tinjau. Tekan Setuju.")],
                         [panggil(("buat_x", {"jumlah": 2})), teks("Silakan tinjau kartunya.")])
    be, s = be_standar(), sesi()
    ev = await p.giliran(s, "lanjut", be, izin_semua)
    assert [e["teks"] for e in ev if e["jenis"] == "teks"] == ["Silakan tinjau kartunya."]  # klaim model baca tak tampil
    assert any(e["jenis"] == "kartu" for e in ev) and s.mode_tulis
    assert any(b.hasil == "klaim_kartu:alih" for b in p.audit.baris)


# ---------------------------------------------------------------- #11 pagar biaya per tenant (putusan pemilik 25 Okt)

def test_batas_bulan_bawaan_pemilik_dan_env():
    from app.milkybook.inti.konfigurasi import batas_bulan_dari_env
    b = batas_bulan_dari_env({})
    assert b("grapgrap-manado") == 15 and b("kaos-biru-konveksi") == 5 and b("tenant-lain") == 5
    b = batas_bulan_dari_env({"MILKYBOOK_BATAS_BULAN_USD": "bawaan=0, kaos-biru-konveksi = 2, rusak=xx"})
    assert b("kaos-biru-konveksi") == 2 and b("tenant-lain") is None and b("rusak") is None


def _audit_terpakai(tenant, usd, bulan_lalu=False):
    from app.milkybook.inti.audit import AuditMemori, BarisAudit, awal_bulan_utc
    a = AuditMemori()
    w = awal_bulan_utc() - 3600 if bulan_lalu else awal_bulan_utc() + 60
    a.baris.append(BarisAudit(tenant, "u", "s", "giliran", biaya_usd=usd, waktu=w))
    a.baris.append(BarisAudit("tenant-lain", "u", "s", "giliran", biaya_usd=99, waktu=w))
    return a


def _putaran_batas(audit, naskah, batas=5.0):
    m = ModelPalsu(naskah)
    return Putaran(registri(), m, audit, gap_mendarat=("G1",), batas_bulan=lambda t: batas), m


async def test_lewat_batas_giliran_ditolak_tanpa_model_tenant_lain_dan_bulan_lalu_tak_terhitung():
    from app.milkybook.inti.putaran import PESAN_BATAS_BIAYA
    p, m = _putaran_batas(_audit_terpakai("kaos-biru-konveksi", 5.0), [teks("tak boleh")])
    s = sesi()
    ev = await p.giliran(s, "hai", be_standar(), izin_semua)
    assert ev[0] == {"jenis": "galat", "kode": "batas_biaya", "pesan": PESAN_BATAS_BIAYA} and ev[-1]["jenis"] == "selesai"
    assert m.permintaan == [] and s.messages == []
    assert any(b.peristiwa == "model" and b.hasil.startswith("batas_biaya:5.0000/5.00") for b in p.audit.baris)
    p2, m2 = _putaran_batas(_audit_terpakai("kaos-biru-konveksi", 4.99), [teks("Halo.")])
    assert (await p2.giliran(sesi(), "hai", be_standar(), izin_semua))[0]["teks"] == "Halo."
    p3, _ = _putaran_batas(_audit_terpakai("kaos-biru-konveksi", 50, bulan_lalu=True), [teks("Halo.")])
    assert (await p3.giliran(sesi(), "hai", be_standar(), izin_semua))[0]["teks"] == "Halo."


async def test_lewat_batas_setuju_tetap_menulis_penjelasan_dilewati():
    audit = _audit_terpakai("kaos-biru-konveksi", 0)
    p, m = _putaran_batas(audit, [panggil(("buat_x", {"jumlah": 2})), teks("Tinjau."), teks("tak boleh")])
    be, s = be_standar(), sesi()
    ev = await p.giliran(s, "buat", be, izin_semua)
    kid = next(e["kartu_id"] for e in ev if e["jenis"] == "kartu")
    from app.milkybook.inti.audit import BarisAudit, awal_bulan_utc
    audit.baris.append(BarisAudit("kaos-biru-konveksi", "u", "s", "giliran", biaya_usd=6.0, waktu=awal_bulan_utc() + 120))
    n = len(m.permintaan)
    ev2 = await p.setujui(s, kid, be, izin_semua)
    assert ev2[0]["jenis"] == "kartu_status" and ev2[0]["status"] == "selesai"
    assert ev2[1]["kode"] == "batas_biaya" and len(m.permintaan) == n
    assert [c["metode"] for c in be.panggilan].count("POST") >= 1


async def test_lewat_batas_galat_baca_fail_open():
    class AuditRusak(AuditMemori):
        async def biaya_bulan_ini(self, t):
            return None
    p, _ = _putaran_batas(AuditRusak(), [teks("Halo.")], batas=0.0001)
    assert (await p.giliran(sesi(), "hai", be_standar(), izin_semua))[0]["teks"] == "Halo."


def test_sql_biaya_per_tenant_giliran_bulan_ini():
    from app.milkybook.inti.audit import _SQL_BIAYA
    sql = " ".join(_SQL_BIAYA.split())
    assert "tenant_id = $1" in sql and "peristiwa = 'giliran'" in sql and "date_trunc('month'" in sql

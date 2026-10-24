"""Gerbang T1 inti Milkybook (rancangan §11.6): tulis tanpa Setuju = 0; payload Setuju == pratinjau;
kunci tetap per kartu; filter izin + turun-kelas; penjaga angka; riwayat append-only."""
import json
import os
import re

import pytest

from app.milkybook.inti import penjaga_angka
from app.milkybook.inti.audit import AuditMemori
from app.milkybook.inti.kartu import StatusKartu, badan_kanonik, hash_payload
from app.milkybook.inti.pelaksana import JawabanBE
from app.milkybook.inti.putaran import Putaran, Sesi
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


def test_definisi_api_strict():
    d = T_BUAT.untuk_api()
    assert d["strict"] is True and d["input_schema"]["additionalProperties"] is False


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


async def test_penjaga_angka_kedua_kali_ditandai_bukan_disembunyikan():
    p, _ = putaran([teks("Totalnya Rp 1.234.567."), teks("Totalnya Rp 1.234.567.")])
    be, s = be_standar(), sesi()
    ev = await p.giliran(s, "berapa?", be, izin_semua)
    t = [e for e in ev if e["jenis"] == "teks"][0]
    assert t["angka_tak_terverifikasi"] == ["Rp 1.234.567"]


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

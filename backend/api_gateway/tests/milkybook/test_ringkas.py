"""(c) Peringkasan otomatis (syarat MASTER 25 Okt). Nol DB / Redis / LLM sungguhan.

Dikunci: ringkasan hanya prosa (angka disamarkan), nomor dokumen + status kartu dari data SERVER, isi hasil tool tidak
dikirim ke peringkas, dua ucapan terakhir utuh, riwayat sah (awal user, berselang), biaya peringkas ikut baris giliran
(pagar biaya), galat peringkas = riwayat utuh dan giliran tetap jalan."""
import asyncio
import json

from app.milkybook.inti import ringkas
from app.milkybook.inti.audit import AuditMemori
from app.milkybook.inti.model import GalatModel, HasilModel
from app.milkybook.inti.pelaksana import JawabanBE
from app.milkybook.inti.putaran import Putaran, Sesi

from .conftest import ModelPalsu, izin_semua, panggil
from .test_inti import be_standar, registri


def t(x, n=10):
    return HasilModel([{"type": "text", "text": x}], "end_turn", {"input_tokens": n, "output_tokens": 5}, "claude-sonnet-5-5")


def _be():
    be = be_standar()
    be.rute[("GET", "/api/x")] = JawabanBE(200, {"items": [{"id": "a1", "quote_number": "QUO-2610-0039", "total": 777000}]})
    return be


def _jalan(naskah, galat_ringkas=False):
    m = ModelPalsu(naskah)
    audit = AuditMemori()
    p = Putaran(registri(), m, audit, gap_mendarat=("G1",), ringkas_pada=100)
    s = Sesi("s1", "t1", "u1", konteks="Hari ini 2026-10-25.")
    be = _be()

    async def alur():
        await p.giliran(s, "cari penawaran", be, izin_semua)
        await p.giliran(s, "buat 2", be, izin_semua)
        await p.giliran(s, "harganya 45000 ya", be, izin_semua)
        return await p.giliran(s, "lanjut", be, izin_semua)
    return m, audit, s, asyncio.run(alur())


NASKAH = [panggil(("cari_x", {"q": "x"})), t("Ketemu."), panggil(("buat_x", {"jumlah": 2})), t("Tinjau."),
          t("Baik.", n=500), t("Pengguna mencari penawaran Flowry dan minta harga 45000 per kaos."), t("Oke.", n=3000)]


def test_ringkas_prosa_angka_disamarkan_fakta_dari_server_dua_ucapan_utuh():
    m, audit, s, ev = _jalan(list(NASKAH))
    assert ev[0] == {"jenis": "diringkas", "token_sebelum": 500}
    pembuka = s.messages[0]["content"][0]["text"]
    assert pembuka.startswith("[ringkasan]") and "[konteks] Hari ini 2026-10-25." in pembuka
    assert "45000" not in pembuka and "minta harga … per kaos" in pembuka
    assert "QUO-2610-0039" in pembuka  # dari hasil tool (server), bukan dari ringkasan model
    (k,) = s.kartu.values()
    assert f"kartu {k.kartu_id} (buat_x): menunggu" in pembuka
    assert s.messages[0]["content"][1] == {"type": "text", "text": "buat 2"}  # ucapan ke-2 dari belakang utuh
    assert [x["role"] for x in s.messages][:2] == ["user", "assistant"]
    teks_semua = json.dumps(s.messages, ensure_ascii=False)
    assert "harganya 45000 ya" in teks_semua and "lanjut" in teks_semua and 'cari penawaran"' not in teks_semua  # ucapan pertama dibuang
    # permintaan peringkas: tanpa tool, prompt ringkas, isi hasil tool TIDAK dikirim
    req = m.permintaan[5]
    assert req["tools"] == [] and req["system"][0]["text"] == ringkas.PROMPT_RINGKAS
    isi = req["messages"][0]["content"]
    assert "\n\ncari penawaran\nAsisten: (memakai alat cari_x)" in isi and "(memakai alat cari_x)" in isi
    assert "QUO-2610-0039" not in isi and "777000" not in isi
    # panggilan sesudah ringkas memakai riwayat baru; meteran dari panggilan itu, bukan dari peringkas
    assert m.permintaan[6]["messages"][0]["content"][0]["text"] == pembuka
    assert s.konteks_token == 3000
    g = [b for b in audit.baris if b.peristiwa == "giliran"][-1]
    assert g.biaya_usd > 0 and any(b.hasil and b.hasil.startswith("ringkas:4/") for b in audit.baris)


def test_biaya_peringkas_ikut_baris_giliran():
    m, audit, s, ev = _jalan(list(NASKAH))
    giliran = [b.biaya_usd for b in audit.baris if b.peristiwa == "giliran"]
    assert round(sum(giliran), 6) == round(s.biaya_usd, 6)  # pagar biaya #11 menjumlah baris giliran


class ModelGalatRingkas(ModelPalsu):
    async def __call__(self, *, system, messages, tools, effort):
        if system and system[0]["text"] == ringkas.PROMPT_RINGKAS:
            self.permintaan.append({"system": system, "messages": messages, "tools": tools})
            raise GalatModel("kelebihan_beban", "529")
        return await super().__call__(system=system, messages=messages, tools=tools, effort=effort)


def test_galat_peringkas_riwayat_utuh_giliran_tetap_jalan():
    naskah = [x for i, x in enumerate(NASKAH) if i != 5]
    m = ModelGalatRingkas(naskah)
    p = Putaran(registri(), m, AuditMemori(), gap_mendarat=("G1",), ringkas_pada=100)
    s = Sesi("s1", "t1", "u1", konteks="Hari ini 2026-10-25.")
    be = _be()

    async def alur():
        for x in ("cari penawaran", "buat 2", "harganya 45000 ya"):
            await p.giliran(s, x, be, izin_semua)
        return await p.giliran(s, "lanjut", be, izin_semua)
    ev = asyncio.run(alur())
    assert not any(e["jenis"] == "diringkas" for e in ev) and [e["teks"] for e in ev if e["jenis"] == "teks"] == ["Oke."]
    assert "cari penawaran" in json.dumps(s.messages, ensure_ascii=False)


def test_tanpa_pemicu_bila_di_bawah_ambang_atau_terlalu_pendek():
    assert ringkas.titik_potong([{"role": "user", "content": "a"}, {"role": "assistant", "content": []}]) is None
    msgs = [{"role": "user", "content": "a"}, {"role": "assistant", "content": [{"type": "text", "text": "x"}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "1", "content": "{}"}]},
            {"role": "assistant", "content": []}, {"role": "user", "content": "b"}, {"role": "assistant", "content": []},
            {"role": "user", "content": "[sistem] Kartu disetujui"}, {"role": "assistant", "content": []},
            {"role": "user", "content": "c"}]
    assert ringkas.titik_potong(msgs) == 4  # tool_result & [sistem] bukan ucapan
    m = ModelPalsu([t("a", n=50), t("b", n=50), t("c", n=50), t("d", n=50)])
    p = Putaran(registri(), m, AuditMemori(), ringkas_pada=100)
    s = Sesi("s1", "t1", "u1")

    async def alur():
        for x in "wxyz":
            await p.giliran(s, x, be_standar(), izin_semua)
    asyncio.run(alur())
    assert len(m.permintaan) == 4 and s.messages[0]["content"] == "w"

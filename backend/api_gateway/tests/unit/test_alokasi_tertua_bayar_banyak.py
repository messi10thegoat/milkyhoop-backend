"""Bayar banyak faktur (4 Nov 2026, desain disetujui MASTER): SATU penentu alokasi tertua-dulu (services/alokasi_tertua).

Kolam bill_ids / invoice_ids di pratinjau /bill-payments + /receive-payments, pelunasan SO, re-cap kartu chat, bangun alokasi
chat. Urutan = jatuh tempo (kosong akhir), tanggal, nomor, id. Decimal (Law 9): sen tak hilang (dulu int() di unified_chat)."""
import asyncio
import inspect
from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

from app.services import alokasi_tertua as AT
from app.services import bayar_keluar_rencana as BKR
from app.services import so_pelunasan as SP

APP = Path(AT.__file__).resolve().parents[1]  # modul chat TIDAK diimpor (impor memicu klien LLM): dibaca sebagai teks

D = Decimal


def _k(*rem, manual=None):
    m = manual or {}
    return [{"remaining": D(str(r)), "manual": (D(str(m[i])) if i in m else None)} for i, r in enumerate(rem)]


# ── penentu murni ───────────────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("rem,total,harap", [
    ((300000, 500000, 0), 450000, (300000, 150000, 0)),
    ((300000, 500000, 0), 900000, (300000, 500000, 0)),     # kelebihan tak dialokasi (pemanggil = uang muka/catatan)
    ((100000.50, 200), "100000.75", ("100000.50", "0.25")),  # sen terjaga
    ((0, 0), 1000, (0, 0)),
    ((5000,), 0, (0,)),
])
def test_selesaikan_kolam_tertua(rem, total, harap):
    assert AT.selesaikan(_k(*rem), total) == [D(str(h)) for h in harap]


def test_manual_dipakai_apa_adanya_kolam_dapat_sisanya():
    # manual 200 di baris tengah; kolam mendapat total - 200 tertua-dulu
    assert AT.selesaikan(_k(300, 999, 500, manual={1: 200}), 600) == [D("300"), D("200"), D("100")]
    # manual melebihi total -> kolam 0 (penentu tulis yang menolak, bukan penentu ini)
    assert AT.selesaikan(_k(300, 0, manual={1: 700}), 600) == [D("0"), D("700")]


def test_kunci_jatuh_tempo_kosong_paling_akhir_lalu_tanggal_nomor():
    a = AT.kunci_tertua(date(2026, 9, 15), date(2026, 9, 1), "INV-2", "b")
    b = AT.kunci_tertua(None, date(2026, 8, 1), "INV-1", "a")
    c = AT.kunci_tertua(date(2026, 9, 15), date(2026, 9, 1), "INV-1", "c")
    assert sorted([a, b, c]) == [c, a, b]
    # date dan teks ISO (baris API chat) terurut sama
    assert AT.kunci_tertua("2026-09-15", "2026-09-01", "X", "1") == AT.kunci_tertua(date(2026, 9, 15), date(2026, 9, 1), "X", "1")


def test_kontrol_merah_kunci_dibalik_mengubah_hasil():
    rows = [{"id": "b", "remaining": D("100"), "manual": None, "k": AT.kunci_tertua(date(2026, 10, 1), None, "B", "b")},
            {"id": "a", "remaining": D("100"), "manual": None, "k": AT.kunci_tertua(date(2026, 9, 1), None, "A", "a")}]

    def dibayar(urut):
        return [r["id"] for r, x in zip(urut, AT.selesaikan(urut, 100)) if x > 0]
    assert dibayar(sorted(rows, key=lambda r: r["k"])) == ["a"]                # jatuh tempo lebih awal dibayar dulu
    assert dibayar(sorted(rows, key=lambda r: r["k"], reverse=True)) == ["b"]  # kunci dibalik -> hasil berbeda


# ── kolam (pratinjau) ───────────────────────────────────────────────────────────────────────────────────────────

A, B, C, X = (str(uuid4()) for _ in range(4))
INFO = {A: {"nomor": "F-A", "jatuh_tempo": date(2026, 9, 30), "tanggal": date(2026, 9, 1), "galat": None},
        B: {"nomor": "F-B", "jatuh_tempo": date(2026, 9, 10), "tanggal": date(2026, 9, 5), "galat": None},
        C: {"nomor": "F-C", "jatuh_tempo": None, "tanggal": date(2026, 8, 1), "galat": None},
        X: {"galat": ("LAIN", "Faktur F-X milik pihak lain.")}}
SISA = {A: D("400"), B: D("250.50"), C: D("1000"), X: D("1")}


def _kolam(manual, ids, total):
    async def muat(ids):
        return {i: INFO[i] for i in ids}

    async def sisa(i):
        return SISA[i]
    return asyncio.run(AT.selesaikan_kolam(manual, ids, muat, sisa, total))


def test_kolam_urut_jatuh_tempo_dedup_dan_galat():
    baris, galat = _kolam([], [A, C, B, A.upper(), X], D("700"))
    assert [b["nomor"] for b in baris] == ["F-B", "F-A", "F-C"]          # jatuh tempo; kosong akhir; duplikat dibuang
    assert [b["applied"] for b in baris] == [D("250.50"), D("400"), D("49.50")]
    assert galat == [("LAIN", "Faktur F-X milik pihak lain.")]


def test_kolam_tak_mengulang_baris_manual():
    baris, _ = _kolam([(A, D("100"))], [A, B], D("300"))
    assert [(b["nomor"], b["source"], b["applied"]) for b in baris] == [("F-B", "otomatis", D("200")), ("F-A", "manual", D("100"))]


def test_kolam_tagihan_payload_terselesaikan(monkeypatch):
    from app.schemas.bill_payments import BillPaymentPreviewRequest
    vid, lain = uuid4(), uuid4()

    class Conn:
        async def fetch(self, sql, tid, ids):
            assert "FROM bills WHERE tenant_id = $1" in sql and tid == "t"
            meta = {A: ("F-A", date(2026, 9, 30), vid), B: ("F-B", date(2026, 9, 10), vid), X: ("F-X", None, lain)}
            return [{"id": i, "invoice_number": meta[str(i)][0], "vendor_id": meta[str(i)][2], "due_date": meta[str(i)][1],
                     "issue_date": None} for i in ids if str(i) in meta]

    async def sisa(conn, tid, i):
        return {A: D("400"), B: D("250")}[str(i)]
    monkeypatch.setattr(BKR, "sisa_tagihan", sisa)
    p = BillPaymentPreviewRequest(vendor_id=str(vid), payment_date=date(2026, 11, 4), bank_account_id=str(uuid4()),
                                  total_amount=D("300"), bill_ids=[A, B, X, "bukan-uuid"])
    tulis, baris, galat = asyncio.run(BKR.kolam_tagihan(Conn(), "t", p))
    assert type(tulis).__name__ == "CreateBillPaymentRequest" and "bill_ids" not in tulis.model_dump()
    assert [(a.bill_id, a.amount_applied) for a in tulis.allocations] == [(B, D("250")), (A, D("50"))]
    assert [c for c, _ in galat] == ["TAGIHAN_VENDOR_LAIN", "TAGIHAN_TIDAK_DITEMUKAN"]


# ── satu penentu: salinan lama dihapus ──────────────────────────────────────────────────────────────────────────

def test_salinan_lama_dihapus():
    assert not hasattr(SP, "alokasi_tertua_dulu")
    assert "AT.selesaikan(" in inspect.getsource(SP.rencana_pelunasan_so)
    te = (APP / "services/unified_agent/tool_executor.py").read_text(encoding="utf-8")
    assert "min(_ra_out, _ra_remaining)" not in te and "min(outstanding, remaining)" not in te
    assert te.count("_alokasi_tertua(") == 2 and "alokasi_chat as _alokasi_tertua" in te  # 2 pemakai, penentu dari modul bersama
    uc = (APP / "routers/unified_chat.py").read_text(encoding="utf-8")
    assert "_remaining = int(_new_total)" not in uc and "angka_json, selesaikan" in uc


def test_catatan_kelebihan_satu_sumber():
    sp = Path(SP.__file__).read_text(encoding="utf-8")
    assert sp.count("AT.catatan_lebih_pelanggan(") == 2 and "dicatat sebagai uang muka pelanggan" not in sp
    v = AT.catatan_lebih_vendor(D("12500"))
    assert v["message"] == "Lebih Rp 12.500 dicatat di akun Uang Muka Pembelian; belum bisa dipakai ke faktur lain."


# ── kesetaraan salinan chat lama vs penentu baru ────────────────────────────────────────────────────────────────

def _lama_recap(total, allocs):  # salinan verbatim logika lama unified_chat (int)
    rem, out = int(total), []
    for a in allocs:
        cap = int(a.get("amount_applied") or 0)
        give = max(0, min(cap, rem))
        if give > 0:
            out.append(dict(a, amount_applied=give))
            rem -= give
    return out


def _baru_recap(total, allocs):
    appl = AT.selesaikan([{"remaining": a.get("amount_applied"), "manual": None} for a in allocs], total)
    return [dict(a, amount_applied=AT.angka_json(x)) for a, x in zip(allocs, appl) if x > 0]


@pytest.mark.parametrize("total,caps", [(500000, [300000, 400000]), (100, [50, 50, 50]), (1000000, [200000])])
def test_recap_chat_sama_untuk_angka_bulat(total, caps):
    allocs = [{"invoice_id": str(i), "amount_applied": c} for i, c in enumerate(caps)]
    assert _baru_recap(total, allocs) == _lama_recap(total, allocs)


def test_recap_chat_sen_terjaga_kontrol_merah_lama():
    allocs = [{"invoice_id": "a", "amount_applied": 100.75}, {"invoice_id": "b", "amount_applied": 50.5}]
    assert [a["amount_applied"] for a in _baru_recap(151.25, allocs)] == [100.75, 50.5]
    assert [a["amount_applied"] for a in _lama_recap(151.25, allocs)] != [100.75, 50.5]  # lama membuang sen


def test_bangun_alokasi_chat_tertua_dan_lewati_sisa_nol():
    rows = [{"inv": {"id": "2", "invoice_number": "INV-2", "due_date": "2026-10-01", "outstanding_amount": "300"}},
            {"inv": {"id": "1", "invoice_number": "INV-1", "due_date": "2026-09-01", "outstanding_amount": 200}},
            {"inv": {"id": "3", "invoice_number": "INV-3", "due_date": None, "outstanding_amount": None}},
            {"inv": {"id": "4", "invoice_number": "INV-4", "due_date": "2026-11-01", "outstanding_amount": "bukan"}}]
    for r in rows:
        r.update(remaining=AT.sisa_atau_nol(r["inv"]["outstanding_amount"]), manual=None)
    built, nomor = AT.alokasi_chat(rows, 350.0, lambda b: (b["inv"]["id"], b["inv"]["invoice_number"], b["inv"]["due_date"], None))
    assert built == [{"invoice_id": "1", "amount_applied": 200}, {"invoice_id": "2", "amount_applied": 150}]
    assert nomor == ["INV-1", "INV-2"]


def test_pratinjau_tanpa_kolam_payload_tanpa_kunci_baru():
    src_bp = inspect.getsource(__import__("app.routers.bill_payments", fromlist=["x"]).preview_create_bill_payment)
    src_rp = inspect.getsource(__import__("app.routers.receive_payments", fromlist=["x"]).preview_create_receive_payment)
    assert 'exclude={"bill_ids"}' in src_bp and "if payload.bill_ids:" in src_bp
    assert 'exclude={"invoice_ids"}' in src_rp and "if body.invoice_ids:" in src_rp

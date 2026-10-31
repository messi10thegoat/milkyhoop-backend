"""KONTRAK sent.state bundel /documents (31 Okt 2026, putusan MASTER A): SETIAP dokumen bundel (SO, PO, FPB, PK, UMP, ND, GRN, ...) memberi
sent.state dalam {none, sent, viewed} -- satu-satunya himpunan yang diterima pembaca FE bersama (bacaBundel). GRN sempat memberi 'not_sent'
(5d76e56c, 24 Okt) -> panel dokumen GRN gagal dimuat di FE. Pemindai: setiap literal "state" di app/ yang berada di kamus sent (kunci
"sent", atau fungsi _belum_kirim / overlay dokumen_bagikan) wajib anggota himpunan; 'not_sent' tak boleh muncul di mana pun."""
import inspect
import pathlib
import re
from datetime import date
from uuid import uuid4

from app.services import dokumen_bagikan as DB
from app.services import grn_daftar as GD

SAH = {"none", "sent", "viewed"}
APP = pathlib.Path(GD.__file__).resolve().parents[1]
LIT = re.compile(r'"state"\s*:\s*"(\w+)"(?:\s+if\s+[^,{}]*?else\s+"(\w+)")?')


def _sumber():
    for p in sorted(APP.rglob("*.py")):
        if "__pycache__" not in p.parts:
            yield p, p.read_text(encoding="utf-8")


def test_dok_goods_receipt_sent_none():
    r = {"id": uuid4(), "receipt_number": "GRN-1", "receipt_date": date(2026, 10, 31), "amount": 5, "status": "posted"}
    assert GD.dok_goods_receipt(r)["sent"] == {"state": "none", "sent_at": None, "viewed_at": None}


def test_pemindai_literal_state_kamus_sent_dalam_himpunan():
    temu = []
    for p, s in _sumber():
        for m in LIT.finditer(s):
            awal = s.rfind("\n", 0, m.start()) + 1
            baris = s[awal:s.find("\n", m.end())]
            fungsi = s[:m.start()].rsplit("\ndef ", 1)[-1].split("(", 1)[0] if "\ndef " in s[:m.start()] else ""
            if '"sent"' in baris or fungsi in ("_belum_kirim",) or p.name == "dokumen_bagikan.py":
                temu.append((p.name, {m.group(1), m.group(2)} - {None}))
    assert len(temu) >= 8, temu   # kontrol: pemindai benar-benar melihat produsen (po, nd, pk x2, bundel x2, tagihan, grn, bagikan)
    salah = [(n, v) for n, v in temu if not v <= SAH]
    assert salah == [], salah


def test_tak_ada_not_sent_di_app():
    assert [p.name for p, s in _sumber() if "not_sent" in s] == []


def test_overlay_dokumen_bagikan_hanya_sent_atau_viewed():
    src = inspect.getsource(DB)
    assert '"state": "viewed" if r["viewed_at"] else "sent"' in src

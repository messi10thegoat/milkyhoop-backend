"""Audit uang muka (MASTER GO 5 Okt 2026): DEPOSIT_VOIDED / APPLIED / REFUNDED / APPLICATION_REVERSED / UPDATED lewat
catat_riwayat di transaksi YANG SAMA; riwayat TANPA baris ganda dengan padanan PER BARIS (kunci = id penerapan/refund).

Jebakan yang dikunci: padanan per DOKUMEN (jenis_audit, id_uang_muka) membuang SEMUA baris kolom "diterapkan" begitu satu
penerapan beraudit -- penerapan lama tanpa audit HILANG dari riwayat. Hapus draf = trigger trg_log_deletion
(DOCUMENT_DELETED) + set_config app.user_id (juga di jalur void-atas-draf)."""
import asyncio
import inspect
from datetime import datetime, timezone

from app.routers import customer_deposits as CD
from app.services import so_riwayat as SR

DP = "11111111-1111-1111-1111-111111111111"


def _rata(f):
    return " ".join(inspect.getsource(f).split())


class _Konn:
    def __init__(self, audit):
        self.audit = audit

    async def fetch(self, sql, *a):
        return self.audit if "FROM audit_logs" in sql else []


def _audit(ev, kunci, jam):
    return {"id": f"a{jam}", "createdAt": datetime(2026, 10, 5, jam, tzinfo=timezone.utc), "eventType": ev,
            "userId": "u1", "entity_type": "customer_deposit", "entity_id": DP, "entity_number": "DEP-1",
            "metadata": {"ringkas": f"{ev} {kunci}", **({"kunci": kunci} if kunci else {})}, "source": "api"}


def _jalan(monkeypatch, audit, kolom):
    async def _zona(conn, tid):
        return timezone.utc
    monkeypatch.setattr(SR, "zona_tenant", _zona)
    k = SR._Kumpul()
    for jam, jenis, kunci in kolom:
        k.tambah(datetime(2026, 10, 5, jam, tzinfo=timezone.utc), jenis, f"kolom {jenis} {kunci}", None,
                 "customer_deposit", DP, "DEP-1", kunci=kunci)
    ev, _ = asyncio.run(SR._selesaikan(_Konn(audit), "t1", k, {"customer_deposit": [DP]}, {"customer_deposit": True}, 200))
    return [(e["jenis"], e["sumber"], e["ringkas"]) for e in ev]


def test_padanan_per_baris_tak_membuang_penerapan_lain(monkeypatch):
    ev = _jalan(monkeypatch, [_audit("DEPOSIT_APPLIED", "A", 3)],
                [(1, "UANG_MUKA_DITERAPKAN", "A"), (2, "UANG_MUKA_DITERAPKAN", "B")])
    assert ("DEPOSIT_APPLIED", "audit", "DEPOSIT_APPLIED A") in ev
    assert ("UANG_MUKA_DITERAPKAN", "dokumen", "kolom UANG_MUKA_DITERAPKAN B") in ev    # lama tanpa audit TETAP ada
    assert not any(j == "UANG_MUKA_DITERAPKAN" and r.endswith(" A") for j, s, r in ev)        # padanan A tak dobel
    assert len(ev) == 2


def test_lepas_dan_refund_per_baris(monkeypatch):
    ev = _jalan(monkeypatch, [_audit("DEPOSIT_APPLICATION_REVERSED", "A", 5), _audit("DEPOSIT_REFUNDED", "R1", 6)],
                [(4, "UANG_MUKA_DILEPAS", "A"), (4, "UANG_MUKA_DILEPAS", "B"),
                 (6, "UANG_MUKA_DIKEMBALIKAN", "R1"), (7, "UANG_MUKA_DIKEMBALIKAN", "R2")])
    jenis = sorted((j, r.split()[-1]) for j, s, r in ev)
    assert jenis == [("DEPOSIT_APPLICATION_REVERSED", "A"), ("DEPOSIT_REFUNDED", "R1"),
                     ("UANG_MUKA_DIKEMBALIKAN", "R2"), ("UANG_MUKA_DILEPAS", "B")]


def test_void_padanan_per_dokumen(monkeypatch):
    ev = _jalan(monkeypatch, [_audit("DEPOSIT_VOIDED", None, 9)], [(8, "UANG_MUKA_DIBATALKAN", None)])
    assert [j for j, s, r in ev] == ["DEPOSIT_VOIDED"]


def test_kunci_tidak_bocor_ke_keluaran(monkeypatch):
    async def _zona(conn, tid):
        return timezone.utc
    monkeypatch.setattr(SR, "zona_tenant", _zona)
    k = SR._Kumpul()
    k.tambah(datetime(2026, 10, 5, 1, tzinfo=timezone.utc), "UANG_MUKA_DITERAPKAN", "x", None, "customer_deposit", DP,
             "DEP-1", kunci="A")
    ev, _ = asyncio.run(SR._selesaikan(_Konn([]), "t1", k, {}, {}, 200))
    assert set(ev[0]) == {"at", "jenis", "ringkas", "aktor", "dokumen", "sumber"}


def test_semua_kolom_penerapan_refund_membawa_kunci():
    src = inspect.getsource(SR)
    for jenis in ("UANG_MUKA_DITERAPKAN", "UANG_MUKA_DILEPAS", "UANG_MUKA_DIKEMBALIKAN"):
        n_panggil = src.count(f'"{jenis}",')
        assert n_panggil >= 1
    # setiap k.tambah kolom penerapan/refund memakai kunci baris
    import re
    for m in re.finditer(r'k\.tambah\([^)]*?"UANG_MUKA_(DITERAPKAN|DILEPAS|DIKEMBALIKAN)".*?\)\n', src, re.S):
        assert "kunci=" in m.group(0), m.group(0)[:120]


def test_penulis_mencatat_di_transaksi_yang_sama():
    for f, ev in ((CD.void_deposit_core, "DEPOSIT_VOIDED"), (CD.apply_deposit_core, "DEPOSIT_APPLIED"),
                  (CD.refund_deposit_core, "DEPOSIT_REFUNDED"),
                  (CD.reverse_deposit_application_core, "DEPOSIT_APPLICATION_REVERSED"),
                  (CD.update_customer_deposit, "DEPOSIT_UPDATED")):
        s = _rata(f)
        assert "await catat_riwayat(conn," in s and f'"{ev}"' in s, f.__name__
        assert "ctx[\"user_id\"]" in s[s.index("await catat_riwayat"):], f.__name__     # beraktor
    for f, kunci in ((CD.apply_deposit_core, '"kunci": str(app_id)'), (CD.refund_deposit_core, '"kunci": str(refund_id)'),
                     (CD.reverse_deposit_application_core, '"kunci": str(application_id)')):
        assert kunci in _rata(f), f.__name__
    # void: catat SEBELUM return; draf -> aktor ke trigger DOCUMENT_DELETED sebelum DELETE
    v = _rata(CD.void_deposit_core)
    assert v.index("set_config('app.user_id', $1, true)") < v.index("DELETE FROM customer_deposits")
    assert v.index('"DEPOSIT_VOIDED"') < v.rindex("return {")
    # update: hanya bila ada medan yang BENAR-BENAR berubah
    u = _rata(CD.update_customer_deposit)
    assert "if berubah:" in u and u.index("lama = await conn.fetchrow(") < u.index("await conn.execute(query, *params)")


def test_ringkas_rupiah_baku_dan_label_medan_indonesia():
    for f in (CD.void_deposit_core, CD.apply_deposit_core, CD.refund_deposit_core, CD.reverse_deposit_application_core):
        s = inspect.getsource(f)
        seg = s[s.index("await catat_riwayat"):]
        assert "tg.rp(" in seg.split("\n\n")[0], f.__name__
    assert set(SR.label_medan(["amount", "deposit_date", "account_id", "reference", "customer_id",
                               "payment_method"]).split(", ")) == \
        {"nominal", "pelanggan", "tanggal", "rekening", "referensi", "cara bayar"}
    for ev in ("DEPOSIT_VOIDED", "DEPOSIT_APPLIED", "DEPOSIT_REFUNDED", "DEPOSIT_APPLICATION_REVERSED", "DEPOSIT_UPDATED"):
        assert ev in SR.RINGKAS_AUDIT

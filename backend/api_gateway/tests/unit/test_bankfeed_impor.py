"""BF-1a impor + daftar mutasi bank (KONTRAK-bankfeed §3, §8): pratinjau READ ONLY == tulis (penentu SATU), NOL jurnal, sidik anti-duplikat, jalur berkas
G2 (milik pengguna yang sama, <= 24 jam, csv), hash isi, izin/rute. Berkas golden penuh diuji harness di luar repo (/root/fixtures/bankfeed_golden)."""
import asyncio
import hashlib
import inspect
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D

import pytest
from fastapi import HTTPException

from app.routers import mutasi_bank as MR
from app.services import bankfeed_daftar as BD
from app.services import bankfeed_impor as BI
from app.services import bankfeed_parser as P
from tests.unit.test_bankfeed_parser import BARIS, klikbca

T = "t-uji"
REK, DOK, USER = uuid.uuid4(), uuid.uuid4(), str(uuid.uuid4())
ISI = klikbca(BARIS)
BARIS_SAH = P.baca(ISI, "x.csv").baris


def _rek(**kw):
    d = {"id": REK, "account_name": "BCA", "account_number": "8888882185", "account_type": "bank", "is_active": True}
    d.update(kw)
    return d


def _dok(**kw):
    d = {"id": DOK, "file_name": "mutasi.csv", "file_type": "text/csv", "file_extension": "csv", "file_size": 2000, "storage_type": "s3",
         "file_path": "k/mutasi.csv", "uploaded_at": datetime.now(timezone.utc) - timedelta(minutes=5), "uploaded_by": USER}
    d.update(kw)
    return d


class _K:
    def __init__(self, rek="ada", dok="ada", ada=(), lama=(), saldo=D("1000000.00"), sisip=None):
        self.rek = _rek() if rek == "ada" else rek
        self.dok = _dok() if dok == "ada" else dok
        self.ada, self.lama, self.saldo, self.sisip = set(ada), list(lama), saldo, sisip
        self.sql, self.tulis = [], []

    async def fetchrow(self, sql, *a):
        self.sql.append(sql)
        if "journal_lines" in sql:
            return {"id": REK, "account_name": "BCA", "account_type": "bank", "coa_id": uuid.uuid4(), "is_active": True, "saldo": self.saldo}
        if "FROM documents" in sql:
            return self.dok
        if "FROM bank_accounts" in sql:
            return self.rek
        raise AssertionError(sql)

    async def fetch(self, sql, *a):
        self.sql.append(sql)
        if "SELECT sidik FROM mutasi_bank" in sql:
            return [{"sidik": s} for s in a[2] if s in self.ada]
        if "FROM mutasi_bank WHERE" in sql:
            return self.lama
        if sql.lstrip().startswith("INSERT INTO mutasi_bank "):
            self.tulis.append((sql, a))
            n = len(a[3]) if self.sisip is None else self.sisip
            return [{"id": uuid.uuid4()} for _ in range(n)]
        raise AssertionError(sql)

    async def execute(self, sql, *a):
        self.sql.append(sql)
        self.tulis.append((" ".join(sql.split()), a))


@pytest.fixture(autouse=True)
def _berkas(monkeypatch):
    async def baca(row):
        return ISI
    monkeypatch.setattr(BI, "baca_isi_dokumen", baca)
    audit = []

    async def catat(conn, tid, jenis, eid, nomor, event, uid, ringkas, meta=None, source="api"):
        audit.append((jenis, event, ringkas, meta))
    monkeypatch.setattr(BI, "catat_riwayat", catat)
    return audit


def _rencana(k, **body):
    b = {"document_id": DOK, "peta_kolom": None, "hash_isi": None}
    b.update(body)
    return asyncio.run(BI.rencana(k, T, USER, REK, b))


# ---------------------------------------------------------------- pratinjau / rencana
def test_rencana_ok_hitung_baru_dilewati_rentang_saldo_dan_payload():
    h, dalam = _rencana(_K())
    assert h["ok"] and h["blocks"] == [] and h["format"] == "klikbca_csv"
    assert (h["baru"], h["duplikat"]) == (3, 0) and h["dilewati"] == [{"alasan": "PEND (belum dibukukan bank)", "n": 1}]
    assert h["rentang"] == {"dari": "2026-08-01", "sampai": "2026-08-03"}
    assert h["saldo_berkas"] == {"awal": "1000000.00", "akhir": "1419975.00"} and h["saldo_buku_pada"] == {"tanggal": "2026-08-03", "nilai": "1000000.00"}
    assert len(h["contoh"]) == 3 and h["tebakan"] == {"yakin": 0, "periksa": 0, "kurang_data": 3}
    assert h["payload"] == {"document_id": str(DOK), "peta_kolom": None, "hash_isi": hashlib.sha256(ISI).hexdigest()}
    assert len(dalam["baris"]) == 3 and dalam["dilewati_n"] == 1 and "Baris PEND" in " ".join(h["catatan"])


def test_impor_ulang_semua_baris_ada_nol_baru():
    sid = P.beri_sidik(T, REK, BARIS_SAH)
    h, dalam = _rencana(_K(ada=sid))
    assert h["ok"] and h["baru"] == 0 and h["duplikat"] == 3 and dalam["baris"] == []


def test_tumpang_tindih_sebagian_hanya_baris_baru():
    sid = P.beri_sidik(T, REK, BARIS_SAH)
    h, _ = _rencana(_K(ada=sid[:2]))
    assert h["baru"] == 1 and h["duplikat"] == 2


def test_mungkin_duplikat_lintas_format_tak_dibuang_diam_diam():
    b = BARIS_SAH[0]
    lama = [{"id": uuid.uuid4(), "tanggal": b.tanggal, "arah": b.arah, "nominal": b.nominal, "saldo_berkas": b.saldo, "keterangan": "SETORAN (PDF)"}]
    h, dalam = _rencana(_K(lama=lama))
    assert h["baru"] == 3 and len(h["mungkin_duplikat"]) == 1 and h["mungkin_duplikat"][0]["lama"]["keterangan"] == "SETORAN (PDF)"
    assert [x["status"] for x in dalam["baris"]] == ["mungkin_duplikat", "untuk_ditinjau", "untuk_ditinjau"] and dalam["baris"][0]["dup"] == lama[0]["id"]


def test_rekening_berkas_beda_diblok_dan_nomor_kosong_dicatat():
    h, _ = _rencana(_K(rek=_rek(account_number="1234567890")))
    assert [b["code"] for b in h["blocks"]] == ["REKENING_BERKAS_BEDA"] and not h["ok"] and "2185" in h["blocks"][0]["message"]
    h2, _ = _rencana(_K(rek=_rek(account_number=None)))
    assert h2["ok"] and "belum diisi" in " ".join(h2["catatan"])


def test_rekening_tak_ada_404_dan_nonaktif_diblok():
    with pytest.raises(HTTPException) as e:
        _rencana(_K(rek=None))
    assert e.value.status_code == 404 and e.value.detail["code"] == "REKENING_TAK_ADA"
    assert "REKENING_NONAKTIF" in [b["code"] for b in _rencana(_K(rek=_rek(is_active=False)))[0]["blocks"]]


@pytest.mark.parametrize("dok,kode", [
    (None, "BERKAS_TAK_DITEMUKAN"),
    (_dok(uploaded_by=str(uuid.uuid4())), "BERKAS_BUKAN_MILIK_ANDA"),
    (_dok(uploaded_at=datetime.now(timezone.utc) - timedelta(hours=25)), "BERKAS_KEDALUWARSA"),
    (_dok(file_extension="pdf", file_name="e.pdf"), "PDF_BELUM_DIDUKUNG"),
    (_dok(file_extension="xlsx", file_name="e.xlsx"), "FORMAT_TAK_DIDUKUNG"),
    (_dok(file_size=11 * 1024 * 1024), "BERKAS_TERLALU_BESAR"),
])
def test_jalur_berkas_g2_diverifikasi(dok, kode, monkeypatch):
    async def tak_boleh(row):
        raise AssertionError("berkas tak boleh dibaca bila verifikasi gagal")
    monkeypatch.setattr(BI, "baca_isi_dokumen", tak_boleh)
    h, dalam = _rencana(_K(dok=dok))
    assert [b["code"] for b in h["blocks"]] == [kode] and not h["ok"] and dalam is None


def test_blok_parser_diteruskan_dan_tak_menyentuh_basis_data_sidik(monkeypatch):
    async def rusak(row):
        return klikbca(BARIS, kredit="999.00")
    monkeypatch.setattr(BI, "baca_isi_dokumen", rusak)
    k = _K()
    h, dalam = _rencana(k)
    assert h["blocks"][0]["code"] == "BERKAS_TAK_SEIMBANG" and not h["ok"] and dalam["baris"] == []
    assert not [s for s in k.sql if "FROM mutasi_bank" in s]


def test_pratinjau_memakai_transaksi_read_only():
    jejak = {}

    class Tx:
        def __init__(self, ro):
            jejak["ro"] = ro

        async def __aenter__(self):
            return self

        async def __aexit__(self, *e):
            return False

    class Kon(_K):
        def transaction(self, readonly=False):
            return Tx(readonly)

    class Pool:
        def acquire(self):
            k = Kon()

            class Ctx:
                async def __aenter__(s):
                    return k

                async def __aexit__(s, *e):
                    return False
            return Ctx()
    r = asyncio.run(BI.pratinjau(Pool(), T, USER, REK, {"document_id": DOK, "peta_kolom": None, "hash_isi": None}))
    assert jejak["ro"] is True and r["success"] and r["data"]["ok"]


# ---------------------------------------------------------------- tulis
CTX = {"tenant_id": T, "user_id": USER}


def _tulis(k, **body):
    b = {"document_id": DOK, "peta_kolom": None, "hash_isi": hashlib.sha256(ISI).hexdigest()}
    b.update(body)
    return asyncio.run(BI.tulis(k, CTX, REK, b))


def test_tulis_kunci_dulu_lalu_impor_baris_lampiran_audit_nol_jurnal(_berkas):
    k = _K()
    h = _tulis(k)
    assert h["baru"] == 3 and h["duplikat"] == 0 and h["dilewati"][0]["n"] == 1
    sql = [s for s, _ in k.tulis]
    assert "pg_advisory_xact_lock" in sql[0] and k.tulis[0][1] == (f"MUTASI_IMPOR:{T}:{REK}",)
    assert sql[1].startswith("INSERT INTO mutasi_bank_impor") and any("INSERT INTO mutasi_bank " in s for s in sql)
    assert any("document_attachments" in s and "'mutasi_bank_impor'" in s for s in sql)
    assert _berkas and _berkas[0][0] == "mutasi_bank_impor" and _berkas[0][1] == "BANK_FEED_IMPOR"
    assert "journal" not in " ".join(s for s, _ in k.tulis).lower()   # NOL tulisan jurnal (membaca saldo buku dari jurnal itu boleh)
    kolom = next(a for s, a in k.tulis if s.lstrip().startswith("INSERT INTO mutasi_bank "))
    assert kolom[0] == T and kolom[1] == REK and len(kolom[3]) == 3 and kolom[9][0] == P.beri_sidik(T, REK, BARIS_SAH)[0]
    assert kolom[13] == ["untuk_ditinjau"] * 3 and all(m.startswith("'") for m in kolom[11])   # baris_mentah = jejak ke berkas asli


def test_tulis_hash_beda_409_tanpa_menulis_baris():
    k = _K()
    with pytest.raises(HTTPException) as e:
        _tulis(k, hash_isi="0" * 64)
    assert e.value.status_code == 409 and e.value.detail["code"] == "BERKAS_BERUBAH"
    assert not [s for s, _ in k.tulis if s.startswith("INSERT")]


def test_tulis_diblok_422_impor_ditolak_tanpa_menulis(monkeypatch):
    k = _K(rek=_rek(account_number="1234567890"))
    with pytest.raises(HTTPException) as e:
        _tulis(k)
    assert e.value.status_code == 422 and e.value.detail["code"] == "IMPOR_DITOLAK" and e.value.detail["blocks"][0]["code"] == "REKENING_BERKAS_BEDA"
    assert not [s for s, _ in k.tulis if s.startswith("INSERT")]


def test_tulis_baris_hilang_karena_bentrok_serentak_409():
    with pytest.raises(HTTPException) as e:
        _tulis(_K(sisip=2))
    assert e.value.status_code == 409 and e.value.detail["code"] == "IMPOR_BENTROK"


def test_tulis_impor_ulang_nol_baru_tetap_tercatat_riwayat_impornya():
    k = _K(ada=P.beri_sidik(T, REK, BARIS_SAH))
    h = _tulis(k)
    assert h["baru"] == 0 and h["duplikat"] == 3
    assert not any("INSERT INTO mutasi_bank " in s for s, _ in k.tulis) and any(s.startswith("INSERT INTO mutasi_bank_impor") for s, _ in k.tulis)


# ---------------------------------------------------------------- daftar
def test_ringkas_keterangan_buang_kode_pertahankan_huruf_dan_potong():
    assert BD.ringkas_keterangan("TRSF E-BANKING DB 0108/FTSCY/WS95031 3900000.00 N001") == "TRSF E-BANKING DB 3900000.00 N001"
    panjang = BD.ringkas_keterangan("X" * 200)
    assert len(panjang) == 80 and panjang.endswith("…")


def test_syarat_selalu_bertenant_dan_membuang_induk_pisah():
    s, p = BD._syarat(REK, T, date(2026, 8, 1), date(2026, 8, 31), "bca")
    assert s.startswith("tenant_id = $1 AND bank_account_id = $2 AND status <> 'dipisah'") and p[0] == T and p[1] == REK and "%bca%" in p
    assert "ESCAPE" in s


def test_syarat_q_meloloskan_karakter_like():
    _, p = BD._syarat(REK, T, None, None, "10%_x")
    assert "%10\\%\\_x%" in p


class _KD:
    def __init__(self, rows=(), cacah=(), jumlah=None, ada=True):
        self.rows, self.cacah, self.jumlah, self.ada, self.sql = list(rows), list(cacah), jumlah or {"masuk": D("0"), "keluar": D("0"), "n": 0}, ada, []

    async def fetchrow(self, sql, *a):
        self.sql.append((sql, a))
        if "FROM bank_accounts" in sql:
            return {"?column?": 1} if self.ada else None
        return self.jumlah

    async def fetch(self, sql, *a):
        self.sql.append((sql, a))
        if "FROM bank_transactions bt" in sql or "FROM chart_of_accounts" in sql or "FROM mutasi_bank_tautan" in sql:   # penentu tebak (BF-1b): tak ada kandidat/akun pada tes ini
            return []
        return self.cacah if "GROUP BY status" in sql else self.rows


def test_daftar_cacah_total_status_text_dan_tebakan_kosong():
    rows = [{"id": uuid.uuid4(), "tanggal": date(2026, 8, 1), "keterangan": "TRSF 0108/FTSCY/WS95031 A", "arah": "masuk", "nominal": D("500000.00"),
             "status": "untuk_ditinjau", "cara": None, "diputus_oleh": None, "diputus_pada": None, "rekonsiliasi_id": None}]
    k = _KD(rows, [{"status": "untuk_ditinjau", "n": 5}, {"status": "dicatat", "n": 2}], {"masuk": D("500000.00"), "keluar": D("0"), "n": 5})
    h = asyncio.run(BD.daftar(k, T, REK, "untuk_ditinjau", None, None, None, 50, 0))
    assert h["cacah"] == {"untuk_ditinjau": 5, "mungkin_duplikat": 0, "dicatat": 2, "dikecualikan": 0}
    assert {x: h["total"][x] for x in ("masuk", "keluar", "jumlah")} == {"masuk": "500000.00", "keluar": "0", "jumlah": 5} and h["has_more"] is True   # 1 dari 5 baris dimuat
    assert h["total"]["masuk_tampil"] == "Rp 500.000" and h["total"]["keluar_tampil"]       # BF-1e: teks milik server
    r = h["data"][0]
    assert r["status_text"] == "Untuk ditinjau" and r["keterangan_ringkas"] == "TRSF A" and r["nominal_tampil"] == "Rp 500.000"
    assert r["tebakan"]["jenis"] == "kosong" and r["tebakan"]["keyakinan"] == "kurang_data" and r["tebakan"]["kalimat"] is None and r["tebakan"]["blok"] is None
    assert r["tebakan"]["layak_massal"] is False and r["diputus_text"] is None
    assert r["jenis_tersedia"] and set(r["aksi_tersedia"]) == {"cocokkan", "catat", "kecualikan"} and r["dokumen_tertaut"] == []       # BF-1e (G1): diisi penentu
    sql_baris = [s for s, a in k.sql if "ORDER BY" in s][0]
    assert "status = $" in sql_baris


def test_daftar_rekening_tak_ada_404():
    with pytest.raises(HTTPException) as e:
        asyncio.run(BD.daftar(_KD(ada=False), T, REK, None, None, None, None, 50, 0))
    assert e.value.status_code == 404


def test_daftar_has_more_menurut_total():
    rows = [{"id": uuid.uuid4(), "tanggal": date(2026, 8, 1), "keterangan": "A", "arah": "keluar", "nominal": D("1"), "status": "dicatat", "cara": None,
             "diputus_oleh": None, "diputus_pada": None}]
    assert asyncio.run(BD.daftar(_KD(rows, jumlah={"masuk": D("0"), "keluar": D("1"), "n": 7}), T, REK, None, None, None, None, 1, 0))["has_more"] is True


# ---------------------------------------------------------------- penjaga struktur
def test_semua_sql_bertenant_dan_tanpa_float_atau_set_config():
    for modul in (BI, BD):
        src = inspect.getsource(modul)
        assert "float(" not in src and "set_config" not in src and "journal" not in src.lower().replace("jurnal", ""), modul.__name__
    for nama, nilai in vars(BI).items():
        if nama.startswith("SQL_"):
            assert "tenant_id" in nilai, nama


def test_rute_bf1a_terdaftar_bersekema_dan_dipasang():
    paths = {(r.path, tuple(sorted(r.methods))): r for r in MR.router.routes}
    for kunci in (("/{bank_account_id}/mutasi-bank", ("GET",)), ("/{bank_account_id}/mutasi-bank/impor/preview", ("POST",)),
                  ("/{bank_account_id}/mutasi-bank/impor", ("POST",))):
        assert kunci in paths and paths[kunci].response_model is not None, kunci
    assert paths[("/{bank_account_id}/mutasi-bank/impor", ("POST",))].status_code == 201
    main = open(inspect.getsourcefile(MR).replace("routers/mutasi_bank.py", "main.py")).read()
    assert re.search(r'include_router\(mutasi_bank\.router, prefix="/api/bank-accounts"', main)
    src = inspect.getsource(MR)
    assert "request.json()" not in src and "set_config" not in src


def test_izin_kas_bank_baca_dan_buat_serta_inventaris():
    from app.middleware.permission_middleware import izin_untuk_jalur
    r = f"/api/bank-accounts/{REK}/mutasi-bank"
    assert izin_untuk_jalur(r, "GET") == ("kas_bank", "R")
    assert izin_untuk_jalur(r + "/impor", "POST") == ("kas_bank", "C") and izin_untuk_jalur(r + "/impor/preview", "POST") == ("kas_bank", "C")
    inv = open(inspect.getsourcefile(MR).replace("app/routers/mutasi_bank.py", "tests/unit/inventaris_rute_tulis.txt")).read()
    for baris in ("POST /api/bank-accounts/{bank_account_id}/mutasi-bank/impor", "POST /api/bank-accounts/{bank_account_id}/mutasi-bank/impor/preview"):
        assert re.search(rf"^{re.escape(baris)}$", inv, re.M), baris


def test_sql_baris_anti_duplikat_dijaga_basis_data_bukan_hanya_pemeriksaan_di_kode():
    # fake DB di tes ini tak menegakkan keunikan; penjaga SERENTAK ada di DB: indeks unik (tenant, rekening, sidik) + ON CONFLICT DO NOTHING
    assert "ON CONFLICT (tenant_id, bank_account_id, sidik) DO NOTHING RETURNING id" in BI.SQL_BARIS
    assert re.search(r"INSERT INTO mutasi_bank \(tenant_id, bank_account_id,", BI.SQL_BARIS)   # tenant tertulis eksplisit pada tiap baris

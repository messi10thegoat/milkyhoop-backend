"""Penentu tebakan Bank Feed (BF-1b; KONTRAK-bankfeed §4 langkah 2 cocok + 5 bawaan BCA + 6 kosong). SATU penentu: dipakai daftar, /calon, dan (BF-1c/2) massal.
Bagian MURNI (tanpa DB) ada di atas; bagian DB (`kandidat_bt`, `tebak`) di bawah. Aturan (yang pertama menang):
- COCOK: bank_transactions POSTED rekening ini, arah SAMA (dari JURNAL, bukan tanda amount), nominal PERSIS, belum bertaut aktif, tak direkonsiliasi, BUKAN bagian
  pasangan batal (predikat SATU kasbank_ringkas.sql_bt_tanpa_batal), tanggal bt − tanggal bank ∈ [−90, +20] hari (P5). Satu kandidat + nama cocok + ≤3 hari → yakin;
  selain itu periksa. Dua kandidat nominal sama → periksa (tak dipilih diam). Gabungan 2–4 bt = 1 baris / 1 bt = 2–3 baris (nama cocok, ±10 hari) → HANYA periksa.
  Yakin hanya untuk dokumen PEMBAYARAN (SUMBER_YAKIN; biaya/manual/penyesuaian/saldo awal paling tinggi periksa) DAN dokumen yang tak diperebutkan baris lain.
  Selisih biaya Rp2.500 BUKAN toleransi: tebak = nominal persis; hanya /calon yang menampilkan, selalu periksa.
- BAWAAN BCA (putusan F2): BIAYA ADM→5-20850 Biaya Administrasi Bank · BUNGA→4-90000 Pendapatan Lain-lain · PAJAK BUNGA→5-80000 Beban Pajak; akun di-resolve runtime
  (Law 27); tak ada → blok AKUN_BAWAAN_TAK_ADA (tak membuat CoA diam-diam); keyakinan periksa.
- Selain itu kosong/kurang_data. K4: layak_massal = yakin DAN unik dua arah (1:1, bukan gabungan, tanpa mungkin_dobel_dengan, status untuk_ditinjau)."""
import re
from datetime import date
from decimal import Decimal
from itertools import combinations
from typing import Optional

from . import teks_galat as tg
from .bank_text_normalizer import normalize_bank_text

MUNDUR_HARI, MAJU_HARI = 90, 20          # P5: tanggal bt − tanggal bank ∈ [−90, +20]
YAKIN_HARI = 3                           # yakin hanya bila ≤ 3 hari
GABUNGAN_HARI = 10
MAKS_DOBEL = 5                           # id baris saingan yang dikirim ke FE (sisanya hanya jumlahnya)
GABUNGAN_MAKS_BT, GABUNGAN_MAKS_BARIS = 4, 3
GABUNGAN_MAKS_KANDIDAT = 12              # batas kombinasi (2^12 terkecil) — kandidat lebih banyak = tak digabung
SELISIH_BI_FAST = Decimal("2500")
# "Yakin" hanya bila dokumen kandidat adalah dokumen PEMBAYARAN yang membawa pihak jelas. Biaya (EXPENSE), entri manual (BANK_TRANSACTION), penyesuaian,
# saldo awal dan sumber tak dikenal tetap boleh jadi kandidat tetapi paling tinggi PERIKSA (default tolak). Terukur golden: 3/3 dokumen biaya TAK punya
# pasangan bank di jawaban benar, sedangkan 19/19 "yakin" benar berasal dari dokumen pembayaran/uang muka/penerimaan.
SUMBER_YAKIN = frozenset({"RECEIVE_PAYMENT", "PAYMENT_RECEIVED", "CUSTOMER_DEPOSIT", "BILL_PAYMENT", "PAYMENT_BILL", "PAYMENT_MADE", "VENDOR_DEPOSIT"})
AKUN_BAWAAN = {"admin": "5-20850", "bunga": "4-90000", "pajak_bunga": "5-80000"}
NAMA_AKUN_BAWAAN = {"admin": "Biaya Administrasi Bank", "bunga": "Pendapatan Lain-lain", "pajak_bunga": "Beban Pajak"}

# kata BCA/umum yang BUKAN nama (melengkapi NOISE_WORDS bank_text_normalizer); kata badan usaha juga bukan nama
_BUKAN_NAMA = frozenset({"trsf", "e", "banking", "bank", "ftscy", "bif", "bifast", "fast", "bi", "dr", "tarikan", "kartu", "debit", "qr", "transaksi",
                         "tanggal", "wsid", "cdm", "pt", "cv", "ud", "toko", "pd", "fa", "payment", "pembayaran", "bayar", "terima", "uang"})
_POLA_BAWAAN = re.compile(r"^(PAJAK BUNGA|BUNGA|BIAYA ADM)\b")


def dalam_rentang(tgl_bank: date, tgl_bt: date) -> bool:
    return -MUNDUR_HARI <= (tgl_bt - tgl_bank).days <= MAJU_HARI


def _token_nama(teks) -> list:
    hasil = []
    for t in normalize_bank_text(str(teks or "")).split():
        if t in _BUKAN_NAMA or len(t) < 4 or t.isdigit() or re.fullmatch(r"ws\d+", t):
            continue
        hasil.append(t)
    return hasil


def nama_cocok(keterangan, nama_kontak) -> bool:
    """Token awalan 4 huruf: ada token kontak dan token keterangan yang awalan 4 hurufnya sama (nama bank terpotong 18 karakter tetap cocok)."""
    a, b = {t[:4] for t in _token_nama(keterangan)}, {t[:4] for t in _token_nama(nama_kontak)}
    return bool(a & b)


def bawaan_bca(keterangan, arah: str) -> Optional[dict]:
    s = " ".join(str(keterangan or "").upper().split())
    m = _POLA_BAWAAN.match(s)
    if not m:
        return None
    kunci, arah_harus = {"BIAYA ADM": ("admin", "keluar"), "BUNGA": ("bunga", "masuk"), "PAJAK BUNGA": ("pajak_bunga", "keluar")}[m.group(1)]
    if arah != arah_harus:
        return None
    return {"kunci": kunci, "kode": AKUN_BAWAAN[kunci]}


def _kosong(**ekstra) -> dict:
    t = {"jenis": "kosong", "keyakinan": "kurang_data", "kalimat": None, "dasar": None, "bank_transaction_ids": [], "dokumen": [], "gabungan": False,
         "jumlah_calon": 0, "akun": None, "kontak": None, "blok": None, "nama_cocok": None, "mungkin_dobel_dengan": None, "mungkin_dobel_jumlah": 0}
    t.update(ekstra)
    return t


def _dokumen(bts) -> list:
    return [{"jenis": b.get("jenis"), "id": b["id"], "nomor": b.get("nomor")} for b in bts]


def _jarak(baris, b) -> int:
    return (b["tanggal"] - baris["tanggal"]).days


def skor_calon(baris, b) -> dict:
    """Skor SATU kandidat (untuk /calon dan tebak): keyakinan turun dengan jarak/nama; selisih Rp2.500 selalu periksa (kalimat jujur)."""
    hari, cocok_nama = _jarak(baris, b), nama_cocok(baris.get("keterangan"), b.get("kontak_nama"))
    selisih = b["nominal"] - baris["nominal"]
    if selisih != 0:
        if selisih == SELISIH_BI_FAST:
            kal = f"Nominal beda {tg.rp(SELISIH_BI_FAST)} dari {b.get('nomor')}; mungkin biaya transfer atau salah input — periksa dulu."
        else:
            kal = f"Nominal beda {tg.rp(abs(selisih))} dari {b.get('nomor')} — periksa dulu."
        return {"id": b["id"], "nomor": b.get("nomor"), "tanggal": b["tanggal"], "nominal": b["nominal"], "selisih_hari": hari,
                "selisih_nominal": selisih, "nama_cocok": cocok_nama, "keyakinan": "periksa", "kalimat": kal}
    layak = b.get("sumber") in SUMBER_YAKIN
    yakin = cocok_nama and abs(hari) <= YAKIN_HARI and layak
    if yakin:
        kal = f"Cocok dengan {b.get('nomor')}: nominal sama, nama cocok, {abs(hari)} hari."
    elif not cocok_nama:
        kal = f"Nominal sama dengan {b.get('nomor')}, tetapi nama tidak cocok — periksa dulu."
    elif not layak and abs(hari) <= YAKIN_HARI:
        kal = f"Nominal, nama, dan tanggal cocok dengan {b.get('nomor')}, tetapi itu entri biaya/manual — konfirmasi dulu, periksa."
    else:
        arah_hari = "sesudah" if hari > 0 else "sebelum"
        kal = f"Nominal dan nama cocok dengan {b.get('nomor')}, tetapi {abs(hari)} hari {arah_hari} tanggal bank — periksa dulu."
    return {"id": b["id"], "nomor": b.get("nomor"), "tanggal": b["tanggal"], "nominal": b["nominal"], "selisih_hari": hari, "selisih_nominal": None,
            "nama_cocok": cocok_nama, "keyakinan": "yakin" if yakin else "periksa", "kalimat": kal}


def _subset_unik(target: Decimal, cocok, ukuran_min: int, ukuran_maks: int, nilai):
    """Subset UNIK (urut id) dari `cocok` yang jumlahnya == target dengan ukuran [min, maks]; >1 subset atau tak ada -> None."""
    if len(cocok) > GABUNGAN_MAKS_KANDIDAT:
        return None
    ketemu = []
    for n in range(ukuran_min, ukuran_maks + 1):
        for sub in combinations(cocok, n):
            if sum((nilai(x) for x in sub), Decimal(0)) == target:
                ketemu.append(sub)
                if len(ketemu) > 1:
                    return None
    return ketemu[0] if ketemu else None


def putuskan(baris, kandidat, akun: Optional[dict] = None) -> dict:
    """Tebakan SATU baris bank. baris {id, tanggal, nominal, arah, keterangan}; kandidat [{id, tanggal, nominal, arah, kontak_nama, nomor, jenis}];
    akun = {kunci: {id, kode, nama}} hasil resolve runtime (None/{} bila tak ada)."""
    ada = [b for b in kandidat if b["arah"] == baris["arah"] and dalam_rentang(baris["tanggal"], b["tanggal"])]
    persis = [b for b in ada if b["nominal"] == baris["nominal"]]
    if len(persis) == 1:
        s = skor_calon(baris, persis[0])
        return _kosong(jenis="cocok", keyakinan=s["keyakinan"], kalimat=s["kalimat"], dasar="nominal_nama_tanggal", bank_transaction_ids=[persis[0]["id"]],
                       dokumen=_dokumen(persis), jumlah_calon=1, nama_cocok=s["nama_cocok"])
    if len(persis) > 1:
        return _kosong(jenis="cocok", keyakinan="periksa", kalimat=f"{len(persis)} dokumen bernominal sama — pilih yang benar.", dasar="nominal_ganda",
                       bank_transaction_ids=[b["id"] for b in persis], dokumen=_dokumen(persis), jumlah_calon=len(persis))
    gab = [b for b in ada if nama_cocok(baris.get("keterangan"), b.get("kontak_nama")) and abs(_jarak(baris, b)) <= GABUNGAN_HARI and b["nominal"] < baris["nominal"]]
    sub = _subset_unik(baris["nominal"], sorted(gab, key=lambda b: (b["tanggal"], str(b["id"]))), 2, GABUNGAN_MAKS_BT, lambda b: b["nominal"])
    if sub:
        return _kosong(jenis="cocok", keyakinan="periksa", kalimat=f"Gabungan {len(sub)} dokumen ({', '.join(str(b.get('nomor')) for b in sub)}) = nominal bank — periksa dulu.",
                       dasar="gabungan", bank_transaction_ids=[b["id"] for b in sub], dokumen=_dokumen(sub), gabungan=True, jumlah_calon=len(sub))
    kunci = bawaan_bca(baris.get("keterangan"), baris["arah"])
    if kunci:
        a = (akun or {}).get(kunci["kunci"])
        if not a:
            return _kosong(blok={"code": "AKUN_BAWAAN_TAK_ADA", "message": f"Akun bawaan {kunci['kode']} ({NAMA_AKUN_BAWAAN[kunci['kunci']]}) tidak ada atau nonaktif di tenant ini; tambahkan akunnya lebih dulu."})
        return _kosong(jenis="catat", keyakinan="periksa", kalimat=f"Bawaan bank: {a.get('nama')} ({a.get('kode')}) — periksa dulu.", dasar="bawaan_bca",
                       akun={"id": a.get("id"), "kode": a.get("kode"), "nama": a.get("nama")})
    return _kosong()


def gabungan_baris(b, barisan) -> dict:
    """1 bt = 2–3 baris bank (nama cocok, ±10 hari, arah sama): {mutasi_id: tebakan periksa}; subset tak unik / tak ada -> {}."""
    cocok = [r for r in barisan if r["arah"] == b["arah"] and r["nominal"] < b["nominal"] and abs(_jarak(r, b)) <= GABUNGAN_HARI
             and nama_cocok(r.get("keterangan"), b.get("kontak_nama"))]
    sub = _subset_unik(b["nominal"], sorted(cocok, key=lambda r: (r["tanggal"], str(r["id"]))), 2, GABUNGAN_MAKS_BARIS, lambda r: r["nominal"])
    if not sub:
        return {}
    return {r["id"]: _kosong(jenis="cocok", keyakinan="periksa", kalimat=f"{b.get('nomor')} = gabungan {len(sub)} baris bank — periksa dulu.", dasar="gabungan_baris",
                             bank_transaction_ids=[b["id"]], dokumen=_dokumen([b]), gabungan=True, jumlah_calon=1) for r in sub}


def turunkan_persaingan(peta: dict, status: dict) -> dict:
    """Dokumen yang sama dipilih >1 baris untuk_ditinjau yang SAMA-SAMA bernama cocok (cocok satu dokumen, bukan gabungan) = salah satunya pasti dobel/bayar ganda:
    TAK satu pun yakin (turun ke periksa) dan ditandai `mungkin_dobel_dengan` (paling banyak MAKS_DOBEL id + `mungkin_dobel_jumlah`). Pembayar lain bernominal
    sama tetapi nama tak cocok BUKAN saingan (nominal bulat sering sama). Dihitung atas SEMUA baris yang ditebak, bukan satu halaman."""
    pilih = {}
    for mid, t in peta.items():
        if (status.get(mid) == "untuk_ditinjau" and t.get("jenis") == "cocok" and not t.get("gabungan") and t.get("nama_cocok")
                and len(t.get("bank_transaction_ids") or []) == 1):
            pilih.setdefault(t["bank_transaction_ids"][0], []).append(mid)
    hasil = dict(peta)
    for baris in pilih.values():
        if len(baris) < 2:
            continue
        for mid in baris:
            t = dict(hasil[mid])
            lain = sorted(x for x in baris if x != mid)
            t["mungkin_dobel_dengan"], t["mungkin_dobel_jumlah"] = lain[:MAKS_DOBEL], len(lain)
            if t["keyakinan"] == "yakin":
                t["keyakinan"] = "periksa"
                t["kalimat"] = f"{len(baris)} baris bank bernama sama cocok dengan dokumen yang sama ({t['dokumen'][0]['nomor']}) — salah satunya mungkin ganda; periksa dulu."
            hasil[mid] = t
    return hasil


def tandai_layak_massal(peta: dict, status: dict) -> dict:
    """K4: layak_massal = yakin DAN unik dua arah. Lawan dua arah dihitung HANYA di antara baris untuk_ditinjau yang memilih dokumen itu."""
    pilih = {}
    for mid, t in peta.items():
        if status.get(mid) == "untuk_ditinjau":
            for bid in t.get("bank_transaction_ids") or []:
                pilih.setdefault(bid, []).append(mid)
    hasil = {}
    for mid, t in peta.items():
        ids = t.get("bank_transaction_ids") or []
        if status.get(mid) != "untuk_ditinjau":
            alasan = "Hanya baris Untuk ditinjau yang bisa diproses massal."
        elif t.get("jenis") != "cocok" or t.get("keyakinan") != "yakin":
            alasan = "Hanya tebakan yakin yang bisa diproses massal."
        elif t.get("gabungan") or t.get("jumlah_calon") != 1 or len(ids) != 1:
            alasan = "Gabungan atau lebih dari satu calon: putuskan satu per satu."
        elif t.get("mungkin_dobel_dengan"):
            alasan = "Ada kemungkinan dobel: putuskan satu per satu."
        elif len(pilih.get(ids[0], [])) != 1:
            alasan = "Dokumen yang sama dipilih lebih dari satu baris: putuskan satu per satu."
        else:
            alasan = None
        hasil[mid] = {**t, "layak_massal": alasan is None, "layak_massal_text": alasan or "Cocok satu-satu; boleh diproses massal."}
    return hasil


# ====================================================================== bagian DB (BACA SAJA; NOL tulisan)
from datetime import timedelta  # noqa: E402

from fastapi import HTTPException  # noqa: E402

from . import kasbank_ringkas as KR  # noqa: E402

# Kandidat = bank_transactions POSTED rekening ini; arah + nominal dari JURNAL (debit−kredit pada CoA rekening; Iron Law journal-derived, bukan tanda amount);
# predikat batal SATU (KR.sql_bt_tanpa_batal) + status POSTED + belum bertaut aktif + belum direkonsiliasi; pihak/nomor dari KR.SQL_JOIN_DOKUMEN (satu definisi
# dengan register bank). Tenant eksplisit di tiap SQL.
SQL_KANDIDAT = f"""
SELECT bt.id, bt.transaction_date AS tanggal, abs(j.net) AS nominal, CASE WHEN j.net > 0 THEN 'masuk' ELSE 'keluar' END AS arah,
       COALESCE(dok.pihak_nama, NULLIF(btrim(bt.payee_payer), '')) AS kontak_nama, COALESCE(dok.nomor_dokumen, bt.transaction_number, bt.reference_number) AS nomor, bt.transaction_type AS jenis,
       je.source_type AS sumber
FROM bank_transactions bt
JOIN bank_accounts ba ON ba.id = bt.bank_account_id AND ba.tenant_id = bt.tenant_id
JOIN journal_entries je ON je.id = bt.journal_id AND je.tenant_id = bt.tenant_id AND je.status = 'POSTED'
JOIN LATERAL (SELECT sum(jl.debit - jl.credit) AS net FROM journal_lines jl WHERE jl.journal_id = je.id AND jl.account_id = ba.coa_id) j ON j.net <> 0
{KR.SQL_JOIN_DOKUMEN}
WHERE bt.tenant_id = $1 AND bt.bank_account_id = $2 AND bt.status = 'POSTED' AND COALESCE(bt.is_reconciled, false) = false
  AND bt.transaction_date BETWEEN $3 AND $4 AND {KR.sql_bt_tanpa_batal("bt", "$1")}
  AND NOT EXISTS (SELECT 1 FROM mutasi_bank_tautan t WHERE t.tenant_id = $1 AND t.bank_transaction_id = bt.id AND t.dilepas_pada IS NULL)
ORDER BY bt.transaction_date, bt.id"""
SQL_AKUN = ("SELECT account_code, id, name FROM chart_of_accounts WHERE tenant_id = $1 AND account_code = ANY($2::text[]) "
            "AND is_active = true AND is_header = false")
SQL_MUTASI_SATU = ("SELECT id, tanggal, keterangan, arah, nominal, status FROM mutasi_bank WHERE tenant_id = $1 AND bank_account_id = $2 AND id = $3 "
                   "AND status <> 'dipisah'")
SQL_SEMUA_DITEBAK = ("SELECT id, tanggal, keterangan, arah, nominal, status FROM mutasi_bank WHERE tenant_id = $1 AND bank_account_id = $2 "
                     "AND status IN ('untuk_ditinjau', 'mungkin_duplikat') ORDER BY tanggal, baris_ke, id LIMIT $3")
STATUS_DITEBAK = ("untuk_ditinjau", "mungkin_duplikat")
BATAS_SEMUA = 20000
BATAS_CALON = 50


def _kandidat(r) -> dict:
    return {"id": str(r["id"]), "tanggal": r["tanggal"], "nominal": Decimal(str(r["nominal"])), "arah": r["arah"], "kontak_nama": r["kontak_nama"],
            "nomor": r["nomor"], "jenis": r["jenis"], "sumber": r["sumber"]}


async def kandidat_bt(conn, tid: str, rekening_id, dari: date, sampai: date) -> list:
    return [_kandidat(r) for r in await conn.fetch(SQL_KANDIDAT, tid, rekening_id, dari, sampai)]


async def akun_bawaan(conn, tid: str) -> dict:
    """{kunci: {id, kode, nama}} untuk akun bawaan BCA yang ADA, aktif, bukan header di tenant ini (Law 27: resolve runtime)."""
    kunci_dari_kode = {v: k for k, v in AKUN_BAWAAN.items()}
    return {kunci_dari_kode[r["account_code"]]: {"id": str(r["id"]), "kode": r["account_code"], "nama": r["name"]}
            for r in await conn.fetch(SQL_AKUN, tid, list(AKUN_BAWAAN.values()))}


async def tebak(conn, tid: str, rekening_id, baris: list) -> dict:
    """{mutasi_id: Tebakan} untuk baris [{id, tanggal, nominal, arah, keterangan, status}]; hanya status untuk_ditinjau/mungkin_duplikat ditebak.
    BACA SAJA. Penentu SAMA untuk daftar, /calon, dan (BF-1c/2) massal."""
    target = [b for b in baris if b["status"] in STATUS_DITEBAK]
    if not target:
        return {}
    kand = await kandidat_bt(conn, tid, rekening_id, min(b["tanggal"] for b in target) - timedelta(days=MUNDUR_HARI),
                             max(b["tanggal"] for b in target) + timedelta(days=MAJU_HARI))
    akun = await akun_bawaan(conn, tid) if any(bawaan_bca(b["keterangan"], b["arah"]) for b in target) else {}
    peta = {b["id"]: putuskan(b, kand, akun) for b in target}
    terpilih = {i for t in peta.values() for i in t["bank_transaction_ids"]}
    kosong = [b for b in target if peta[b["id"]]["jenis"] == "kosong" and not peta[b["id"]]["blok"]]
    for k in (k for k in kand if k["id"] not in terpilih):
        for mid, t in gabungan_baris(k, kosong).items():
            if peta[mid]["jenis"] == "kosong":
                peta[mid] = t
    status = {b["id"]: b["status"] for b in target}
    return tandai_layak_massal(turunkan_persaingan(peta, status), status)


async def tebak_halaman(conn, tid: str, rekening_id, halaman: list) -> dict:
    """Tebakan untuk baris SATU halaman daftar, tetapi persaingan/K4 dihitung atas SEMUA baris yang belum diputus di rekening itu (bukan hanya halaman):
    keyakinan satu baris tak boleh berubah hanya karena pengguna pindah halaman. BACA SAJA."""
    if not any(b["status"] in STATUS_DITEBAK for b in halaman):
        return {}
    semua = [{"id": str(r["id"]), "tanggal": r["tanggal"], "nominal": Decimal(str(r["nominal"])), "arah": r["arah"], "keterangan": r["keterangan"], "status": r["status"]}
             for r in await conn.fetch(SQL_SEMUA_DITEBAK, tid, rekening_id, BATAS_SEMUA)]
    ada = {b["id"] for b in semua}
    semua += [b for b in halaman if b["id"] not in ada and b["status"] in STATUS_DITEBAK]
    peta = await tebak(conn, tid, rekening_id, semua)
    return {b["id"]: peta[b["id"]] for b in halaman if b["id"] in peta}


def ringkas_tebakan(t: dict) -> dict:
    """Bentuk untuk respons daftar (FE hanya memformat; semua teks dari server)."""
    return {k: t.get(k) for k in ("jenis", "keyakinan", "kalimat", "dasar", "bank_transaction_ids", "dokumen", "gabungan", "akun", "blok",
                                  "layak_massal", "layak_massal_text", "mungkin_dobel_dengan", "mungkin_dobel_jumlah")}


async def calon(conn, tid: str, rekening_id, mutasi_id, q: Optional[str] = None, limit: int = 20) -> dict:
    """G1 (READ ONLY): calon dokumen untuk SATU baris, berskor. Bawaan = nominal persis atau selisih Rp2.500 (periksa); ?q= (nomor/nama/nominal) melebar
    ke seluruh rentang P5 (−90/+20) arah sama. Tak menulis apa pun."""
    m = await conn.fetchrow(SQL_MUTASI_SATU, tid, rekening_id, mutasi_id)
    if not m:
        raise HTTPException(status_code=404, detail={"code": "MUTASI_TAK_ADA", "message": "Mutasi bank tidak ditemukan."})
    baris = {"id": str(m["id"]), "tanggal": m["tanggal"], "nominal": Decimal(str(m["nominal"])), "arah": m["arah"], "keterangan": m["keterangan"]}
    kand = [k for k in await kandidat_bt(conn, tid, rekening_id, m["tanggal"] - timedelta(days=MUNDUR_HARI), m["tanggal"] + timedelta(days=MAJU_HARI))
            if k["arah"] == baris["arah"]]
    kata = (q or "").strip().lower()
    if kata:
        digit = re.sub(r"\D", "", kata)
        kand = [k for k in kand if kata in str(k["nomor"] or "").lower() or kata in str(k["kontak_nama"] or "").lower()
                or (digit and digit in str(int(k["nominal"])))]
    else:
        kand = [k for k in kand if k["nominal"] == baris["nominal"] or abs(k["nominal"] - baris["nominal"]) == SELISIH_BI_FAST]
    skor = sorted((skor_calon(baris, k) for k in kand), key=lambda s: (s["selisih_nominal"] is not None and s["selisih_nominal"] != 0, abs(s["selisih_hari"]), str(s["id"])))
    data = [{**s, "tanggal": s["tanggal"].isoformat(), "nominal": str(s["nominal"]), "selisih_nominal": None if s["selisih_nominal"] in (None, 0) else str(s["selisih_nominal"])}
            for s in skor[:max(1, min(limit, BATAS_CALON))]]
    return {"data": data, "jumlah": len(skor)}

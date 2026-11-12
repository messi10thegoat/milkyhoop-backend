# Konfig gerbang unit BUG-185 (penangan 422 BERSAMA: input bytes dibuang; main.py = kunci bersama -> gerbang PENUH). Tiap sabotase WAJIB membuat tes merah.
W=/root/mh-v422; CABANG=feat/validasi-422-biner; NAMA_JENDELA=validasi422
POLA_UNIT='validasi|galat|lampiran|expense|biaya|izin_rute|inventaris_rute|pagar_rute|permission'
HARNESS=scripts/ops/uji-validasi422.sh
PENANDA_BERKAS=services/galat_validasi.py; PENANDA='async def validasi_handler'
A=backend/api_gateway/app; T=tests/unit/test_validasi_422_biner.py; GV=$A/services/galat_validasi.py; MN=$A/main.py
SAB=(
 "s1 input bytes dikembalikan (tak dibersihkan)|$GV|s#    return \[{\*\*e, \"input\": _aman(e\[\"input\"\])} if \"input\" in e else e for e in errors\]#    return list(errors)#|$T"
 "s2 galat validasi JSON biasa kehilangan loc/msg/type|$GV|s#    return \[{\*\*e, \"input\": _aman(e\[\"input\"\])} if \"input\" in e else e for e in errors\]#    return [{\"input\": _aman(e.get(\"input\"))} for e in errors]#|$T"
 "s3 main.py kembali ke penangan lama|$MN|s#_validasi_handler = _galat_validasi.validasi_handler#_validasi_handler = lambda r, e: _JSONResp(status_code=422, content=_galat_validasi.badan(_jsonable(e.errors())))#|$T"
 "s4 bytes didecode replace (bocor isi biner)|$GV|s#        return f\"<biner {len(v)} byte>\"#        return bytes(v).decode(\"utf-8\", \"replace\")#|$T"
)

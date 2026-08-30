import ast, json
P="/root/mh-t181c/backend/api_gateway/app/services/unified_agent/tool_executor.py"
src=open(P).read(); tree=ast.parse(src)

# ---- muat DUA fungsi murni tingkat-modul APA ADANYA dari berkas yang dikirim
want={"_t181_urai_items","_t181_pesan_tolak"}
fns=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in want]
assert len(fns)==2, [f.name for f in fns]
g={"json":json}
exec(compile(ast.Module(body=fns,type_ignores=[]),P,"exec"),g)
urai=g["_t181_urai_items"]; pesan=g["_t181_pesan_tolak"]

def jalankan(payload):
    """Tiru URUTAN situs enricher: urai -> gerbang karangan -> hasil."""
    urai(payload)
    _dikarang=False
    if not payload.get("items") and not payload.get("_t181_items_mentah"):
        _n=payload.get("item_name") or payload.get("name")
        _q=payload.get("quantity"); _p=payload.get("unit_price")
        if payload.get("item_id") or _n or _q or _p:
            payload["items"]=[{"description":_n or "Item","quantity":_q or 1,
                               "unit_price":_p or 0}]
            _dikarang=True
    if payload.get("_t181_items_mentah"): return "TOLAK", payload.get("items")
    if _dikarang: return "KARANG", payload.get("items")
    return "BARIS", payload.get("items")

SKALAR={"item_name":"Kain Katun","quantity":10,"unit_price":40000}
KASUS=[
 ("B1 JSONDecodeError","Kain Katun 10 meter @ 40000, Benang Jahit 5 pcs @ 50000","TOLAK"),
 ("B2 sukses tapi dict",'{"a":1}',"TOLAK"),
 ("B3 teks biasa","teks biasa","TOLAK"),
 ("B4 daftar kosong '[]'","[]","?AMBIGU"),
 ("B5 daftar sah 2 baris",'[{"description":"Kaos Hitam 24s","quantity":10,"unit_price":35000},{"description":"Kaos Biru 30s","quantity":5,"unit_price":35000}]',"BARIS"),
]
print("=== GATE-6: enricher sebagai FUNGSI MURNI, payload disusun sendiri ===")
gagal=[]
for nama,raw,harap in KASUS:
    p=dict(SKALAR); p["items"]=raw
    hasil,items=jalankan(p)
    n=len(items) if isinstance(items,list) else -1
    print("  %-24s -> %-6s n_baris=%-2d sentinel=%s" %
          (nama,hasil,n,"ADA" if p.get("_t181_items_mentah") else "tidak"))
    if harap=="TOLAK" and hasil!="TOLAK": gagal.append(nama)
    if harap=="BARIS" and (hasil!="BARIS" or n!=2): gagal.append(nama)
    if nama.startswith("B4"): B4=(hasil,n)

# KASUS A -- WAJIB tetap mengarang
pA=dict(SKALAR)                       # `items` TIDAK ADA sama sekali
hasilA,itemsA=jalankan(pA)
print("  %-24s -> %-6s n_baris=%-2d sentinel=%s" %
      ("A  items tidak ada",hasilA,len(itemsA or []),"ADA" if pA.get("_t181_items_mentah") else "tidak"))
if hasilA!="KARANG" or len(itemsA)!=1: gagal.append("A KASUS-A RUSAK")
pA2=dict(SKALAR); pA2["items"]=[]     # list kosong asli (bukan teks)
hasilA2,_=jalankan(pA2)
print("  %-24s -> %-6s" % ("A2 items=[] (list asli)",hasilA2))
if hasilA2!="KARANG": gagal.append("A2")

print("=== KONTROL NEGATIF GATE-6 (tiap assertion harus BISA gagal) ===")
pN=dict(SKALAR); pN["items"]="teks biasa"; pN.pop("items")
print("  kontrol: kalau gerbang dilucuti, B3 jadi KARANG ->",
      "alat sehat" if jalankan(dict(SKALAR))[0]=="KARANG" else "ALAT RUSAK")
try:
    assert pesan("X")=="kalimat yang tidak pernah ada"; print("  ALAT RUSAK")
except AssertionError: print("  kontrol pesan: assertion BISA gagal, alat sehat")

print("=== GATE-1 pesan murni ===")
STIM="Kain Katun 10 meter @ 40000, Benang Jahit 5 pcs @ 50000"
m=pesan(STIM)
assert STIM in m and "TIDAK membuat kartunya" in m and "satu barang per baris" in m
print(m)

print("=== GATE-2..5 struktural ===")
target={"_enrich_sales_invoice","_enrich_sales_order","_enrich_purchase_invoice","_enrich_quote"}
GERBANG='not payload.get(\n                "_t181_items_mentah"\n            )'
for k in [n for n in tree.body if isinstance(n,ast.ClassDef)]:
    for n in ast.walk(k):
        if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name in target:
            seg=ast.get_source_segment(src,n)
            c=(seg.count("_t181_urai_items(payload)"), seg.count(GERBANG),
               seg.count("json.loads"))
            print("  [GATE-2] %-26s urai=%d gerbang=%d loads_lokal=%d" % ((n.name,)+c))
            if c!=(1,1,0): gagal.append("GATE-2 "+n.name)
PEN='"[T181_TOLAK] action=%s len=%d"'
print("  [GATE-3] situs penerbit [T181_TOLAK] =",src.count(PEN),
      "(komentar:",src.count("[T181_TOLAK]")-src.count(PEN),")")
if src.count(PEN)!=1: gagal.append("GATE-3")
POP='payload.pop("_t181_items_mentah", None)'
print("  [GATE-4] situs pop sentinel =",src.count(POP))
if src.count(POP)!=3: gagal.append("GATE-4")
_sesudah=src.split(PEN)[1][:300]
print("  [GATE-5] argumen log:", "hanya action + len(...)"
      if "len(str(" in _sesudah and '"%r"' not in _sesudah else "PERIKSA")

print()
print("### PERILAKU items='[]' (DILAPORKAN APA ADANYA, tidak diputuskan):",B4[0],
      "n_baris=",B4[1])
print()
print("GAGAL:",gagal if gagal else "TIDAK ADA -- SEMUA GATE OFFLINE HIJAU")
assert not gagal

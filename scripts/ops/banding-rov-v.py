"""Pembanding ROW (v): argv = lama.json baru.json. Perubahan SAH saja:
 (a) status_text GRN: baru == lama + " · faktur draf" HANYA bila aksi == draf_tagihan;
 (b) bundel dokumen GRN: kelompok faktur + dokumen berstatus "draf" == draft_bills GRN itu (tak ada dokumen lain berubah);
 (c) status_text NK: "Terpakai" -> "Jadi saldo kredit" HANYA bila NK punya saldo kredit (customer_credit);
 selain itu IDENTIK."""
import json
import sys

lama, baru = (json.load(open(f)) for f in sys.argv[1:3])
hasil, beda_sah = [], {"a": 0, "b": 0, "c": 0}


def cek(nama, ok, info=""):
    hasil.append(bool(ok))
    if not ok or info.startswith("!"):
        print(("PASS " if ok else "FAIL ") + nama + (f" -- {info}" if info else ""))


def jalan(a, b, jalur, akar, tid):
    if isinstance(a, dict) and isinstance(b, dict):
        if set(a) != set(b):
            cek(f"{tid} {akar}{jalur} kunci sama", False, str(set(a) ^ set(b)))
            return
        for k in a:
            jalan(a[k], b[k], f"{jalur}.{k}", akar, tid)
        return
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        for i, (x, y) in enumerate(zip(a, b)):
            jalan(x, y, f"{jalur}[{i}]", akar, tid)
        return
    if a != b:
        cek(f"{tid} {akar}{jalur}", False, f"{a!r} -> {b!r}")


for tid, B in baru.items():
    L = lama[tid]
    cek(f"{tid} kunci sama", set(L) == set(B))
    draf = {no: [x["bill_number"] for x in g.get("draft_bills") or []] for no, g in B["grn_daftar"].items()}
    aksi = {no: g.get("aksi") for no, g in B["grn_daftar"].items()}
    for k in sorted(B):
        a, b = json.loads(json.dumps(L[k])), json.loads(json.dumps(B[k]))
        if k == "grn_daftar" or k.startswith("grn_detail:"):
            for no, x in ({k.split(":")[1]: b} if ":" in k else b).items():
                y = a if ":" in k else a[no]
                if ":" not in k and x.get("documents") != y.get("documents"):
                    tambah = x["documents"][len(y["documents"]):]
                    ok = (x["documents"][:len(y["documents"])] == y["documents"] and tambah == [{"type": "bill"}] * len(draf[no])
                          and x["doc_count"] == y["doc_count"] + len(draf[no]))
                    cek(f"{tid} {k} {no} (b) kolom Dok + faktur draf", ok, f"! doc_count {y['doc_count']} -> {x['doc_count']}")
                    y["documents"], y["doc_count"] = x["documents"], x["doc_count"]
                if x.get("status_text") != y.get("status_text"):
                    ok = aksi[no] == "draf_tagihan" and x["status_text"] == y["status_text"] + " · faktur draf"
                    cek(f"{tid} {k} {no} (a) teks dua dimensi", ok, f"! {y['status_text']!r} -> {x['status_text']!r}")
                    beda_sah["a"] += ok
                    y["status_text"] = x["status_text"]
        if k.startswith("grn_dok:"):
            no = k.split(":")[1]
            if a["anchor"].get("position_text") != b["anchor"].get("position_text"):
                ok = aksi[no] == "draf_tagihan" and b["anchor"]["position_text"] == a["anchor"]["position_text"] + " · faktur draf"
                cek(f"{tid} {k} (a) anchor", ok, f"! {a['anchor']['position_text']!r} -> {b['anchor']['position_text']!r}")
                a["anchor"]["position_text"] = b["anchor"]["position_text"]
            fb = [d for g in b.get("groups", []) if g["key"] == "faktur" for d in g["docs"]]
            fa = [d for g in a.get("groups", []) if g["key"] == "faktur" for d in g["docs"]]
            baru_d = [d for d in fb if d["id"] not in {x["id"] for x in fa}]
            if baru_d or draf[no]:
                ok = sorted(d["number"] for d in baru_d) == sorted(draf[no]) and all(d["status"] == "draf" for d in baru_d)
                cek(f"{tid} {k} (b) dokumen draf == draft_bills", ok, f"! +{[(d['number'], d['status']) for d in baru_d]} draft_bills={draf[no]}")
                beda_sah["b"] += ok and bool(baru_d)
            a["groups"] = [g for g in a.get("groups", []) if g["key"] != "faktur"]
            b["groups"] = [g for g in b.get("groups", []) if g["key"] != "faktur"]
            for x, y in ((fa, [d for d in fb if d not in baru_d]),):
                jalan(x, y, ".faktur", k, tid)
        if k == "nk_daftar" or k.startswith("nk_detail:") or k.startswith("nk_dok:"):
            def pas(x, y, nama, kredit):
                if x != y:
                    ok = (x, y) == ("Terpakai", "Jadi saldo kredit") and kredit
                    cek(f"{tid} {nama} (c) NK saldo kredit", ok, f"! {x!r} -> {y!r} kredit={kredit}")
                    beda_sah["c"] += ok
            if k == "nk_daftar":
                for no in b:
                    pas(a[no]["status_text"], b[no]["status_text"], f"nk_daftar {no}", bool(b[no].get("customer_credit")))
                    a[no]["status_text"] = b[no]["status_text"]
            elif k.startswith("nk_detail:"):
                da, db = a.get("data", a), b.get("data", b)
                pas(da.get("status_text"), db.get("status_text"), k, bool(db.get("customer_credit")))
                da["status_text"] = db.get("status_text")
            else:
                kr = bool(B.get("nk_detail:" + k.split(":")[1], {}).get("data", {}).get("customer_credit"))
                pas(a["anchor"].get("position_text"), b["anchor"].get("position_text"), k, kr)
                a["anchor"]["position_text"] = b["anchor"].get("position_text")
        jalan(a, b, "", k, tid)
    print(f"[info] {tid}: {len(B['grn_daftar'])} GRN, {len(B['nk_daftar'])} NK")
print(f"[info] perubahan sah: (a) teks GRN {beda_sah['a']} · (b) dokumen draf {beda_sah['b']} · (c) NK saldo kredit {beda_sah['c']}")
print(f"HASIL {sum(hasil)}/{len(hasil)} PASS")
sys.exit(0 if all(hasil) and beda_sah["b"] > 0 else 1)

"""UJI NYATA neto_tagihan_terposting (23 Okt 2026, aturan D GRN): BACA SAJA (koneksi default_transaction_read_only=on) kaos + grapgrap,
SEMUA tagihan tenant dikirim. Bukti: (1) hanya baris tagihan terposting non-void yang muncul (status_v2 NULL = draf lama -> keluar, == SQL);
(2) setiap baris tagihan terposting ada dan per tagihan Σ neto == grand_total - tax_amount TERSIMPAN (dasar post_bill); (3) dasar itu BEKU
sesudah posting: trigger trg_law19_bekukan_nominal terpasang di bills; (4) kredit hutang (peran AP_TRADE) jurnal posting == grand_total
tersimpan -- pengecualian PERSIS 3 benih uji lama ZSEARCHBILL-1..3 (grand_total 0, amount terisi, tanpa baris PO). MERAH=1: satu nilai neto
dimutasi -> WAJIB gagal."""
import asyncio, os, sys
from decimal import Decimal
sys.path.insert(0, "/app/backend/api_gateway"); os.environ.setdefault("OPENAI_API_KEY", "x")
import asyncpg

gagal = []


def cek(ok, l, i=""):
    print(("PASS " if ok else "FAIL ") + l + (f" | {i}" if i else ""), flush=True)
    if not ok: gagal.append(l)


async def main():
    from app.services import tagihan_aksi as TA
    from app.services.role_resolver import AccountRole, resolve_account_id_by_role
    c = await asyncpg.connect(os.environ["DATABASE_URL"], server_settings={"default_transaction_read_only": "on"})
    cek(bool(await c.fetchval("SELECT 1 FROM pg_trigger WHERE tgname = 'trg_law19_bekukan_nominal' AND tgrelid = 'bills'::regclass AND NOT tgisinternal")), "trigger trg_law19_bekukan_nominal terpasang di bills (Law 19)")
    for t in ("kaos-biru-konveksi", "grapgrap"):
        semua = await c.fetch("SELECT id, invoice_number, status_v2, grand_total, tax_amount, journal_id FROM bills WHERE tenant_id = $1", t)
        out = await TA.neto_tagihan_terposting(c, t, [b["id"] for b in semua])
        if os.environ.get("MERAH") == "1" and out:
            k0 = next(iter(out)); out[k0] += Decimal("1")
        baris = await c.fetch("SELECT bi.id, bi.bill_id FROM bill_items bi JOIN bills b ON b.id = bi.bill_id WHERE b.tenant_id = $1", t)
        pemilik = {r["id"]: r["bill_id"] for r in baris}
        terpost = {b["id"]: b for b in semua if b["status_v2"] is not None and b["status_v2"] not in ("draft", "void")}  # == SQL NOT IN (NULL -> keluar)
        cek(all(pemilik.get(i) in terpost for i in out), f"{t}: hanya baris tagihan terposting non-void")
        hilang = {i for i, bid in pemilik.items() if bid in terpost} - set(out)
        cek(not hilang, f"{t}: setiap baris tagihan terposting ada", f"hilang {[(str(i), str(pemilik[i])) for i in hilang]}")
        try:
            ap = await resolve_account_id_by_role(c, t, AccountRole.AP_TRADE)
        except Exception as e:  # noqa: BLE001 -- peran tak dipetakan (grapgrap) -> cek jurnal dilewati, dilaporkan
            ap = None; print(f"INFO {t}: AP_TRADE tak dipetakan ({type(e).__name__}) -> cek kredit hutang dilewati")
        beda, beda_j, nj = [], [], 0
        for bid, b in terpost.items():
            n = sum((v for i, v in out.items() if pemilik.get(i) == bid), Decimal("0"))
            ada_baris = any(x == bid for x in pemilik.values())
            if ada_baris and n != Decimal(str(b["grand_total"] or 0)) - Decimal(str(b["tax_amount"] or 0)):
                beda.append((b["invoice_number"], str(n), str(b["grand_total"]), str(b["tax_amount"])))
            if b["journal_id"] and ap:
                nj += 1
                kr = await c.fetchval("SELECT COALESCE(SUM(credit), 0) FROM journal_lines WHERE journal_id = $1 AND account_id = $2", b["journal_id"], ap)
                if Decimal(str(kr)) != Decimal(str(b["grand_total"] or 0)):
                    beda_j.append((b["invoice_number"], str(kr), str(b["grand_total"])))
        cek(not beda, f"{t}: Σ neto == grand_total - tax_amount tersimpan ({len(terpost)} tagihan terposting)", f"{len(beda)} beda: {beda}")
        # Pengecualian TERUKUR (23 Okt): 3 tagihan benih uji 2 Sep (ZSEARCHBILL-1..3) diposting jalur lama dgn grand_total 0 & amount terisi
        # (jurnal mengkredit hutang = amount). Tanpa baris PO -> tak pernah dihargai aturan D. Daftar PERSIS: beda lain / beda baru = MERAH.
        dikenal = {"ZSEARCHBILL-1", "ZSEARCHBILL-2", "ZSEARCHBILL-3"} if t == "kaos-biru-konveksi" else set()
        ber_po = {r["bill_id"] for r in await c.fetch(
            "SELECT DISTINCT bi.bill_id FROM bill_items bi JOIN bills b ON b.id = bi.bill_id WHERE b.tenant_id = $1 AND bi.purchase_order_item_id IS NOT NULL", t)}
        nomor_po = {terpost[b]["invoice_number"] for b in ber_po if b in terpost}
        cek({x[0] for x in beda_j} == dikenal and not (nomor_po & dikenal), f"{t}: kredit hutang jurnal posting == grand_total tersimpan ({nj} tagihan "
            f"ber-jurnal; pengecualian persis {sorted(dikenal)}, tanpa baris PO)", f"{len(beda_j)} beda: {beda_j}")
        print(f"INFO {t}: tagihan terposting ber-baris PO = {len(nomor_po)}")
    await c.close()
    print(f"RINGKAS gagal={len(gagal)} {gagal[:6]}"); sys.exit(1 if gagal else 0)

asyncio.run(main())

#!/usr/bin/env python3
"""Probe ekstraksi [DocSimple] di luar jalur chat — mengukur hasil MENTAH gpt-4o-mini
atas satu gambar, N kali (stokastik), dengan prompt `legacy` (diambil dari
unified_chat.py yang ditunjuk) atau `new` (app.services.unified_agent.ocr_extract).

Jalankan DI DALAM kontainer gateway (punya OPENAI_API_KEY + openai + PIL):
  docker exec milkyhoop-dev-api_gateway python /tmp/wt/probe_ocr.py --image /tmp/wt/nota.jpg \
      --mode new --n 3 --caption "..." --module /tmp/wt/ocr_extract.py [--legacy-from <unified_chat.py>]
Keluaran: satu baris JSON per percobaan + ringkasan. Meniru persis resize 1280/q85/detail=high.
"""
import argparse, base64, io, json, os, re, sys, time
from datetime import date

from PIL import Image
from openai import OpenAI

ap = argparse.ArgumentParser()
ap.add_argument("--image", required=True)
ap.add_argument("--mode", choices=["legacy", "new"], required=True)
ap.add_argument("--n", type=int, default=3)
ap.add_argument("--caption", default="Bukti pembayaran dari Ferrenlita Pesan Pisang, tolong catat.")
ap.add_argument("--legacy-from", default="/app/backend/api_gateway/app/routers/unified_chat.py")
ap.add_argument("--max-tokens", type=int, default=None)
ap.add_argument("--module", default=None, help="path ocr_extract.py (default: sebelah skrip ini)")
a = ap.parse_args()

if a.mode == "legacy":
    src = open(a.legacy_from).read()
    m = re.search(r'_ocr_prompt = f"""(.*?)"""', src, re.S)
    assert m, "prompt legacy tidak ditemukan"
    prompt = eval('f"""' + m.group(1) + '"""', {"text": a.caption})
    post = None
    max_tokens = a.max_tokens or 1000
else:
    # impor lewat PATH berkas supaya modul dari WORKTREE yang dipakai, bukan /app (main tree)
    import importlib.util as _ilu
    _mod_path = a.module or os.path.join(os.path.dirname(os.path.abspath(__file__)), "ocr_extract.py")
    _spec = _ilu.spec_from_file_location("ocr_extract_probe", _mod_path)
    _m = _ilu.module_from_spec(_spec); _spec.loader.exec_module(_m)
    build_ocr_prompt, postprocess_ocr = _m.build_ocr_prompt, _m.postprocess_ocr
    print(json.dumps({"module": _mod_path}), flush=True)
    prompt = build_ocr_prompt(a.caption)
    post = postprocess_ocr
    max_tokens = a.max_tokens or 1200

img = Image.open(a.image)
if img.mode in ("RGBA", "LA", "P"):
    img = img.convert("RGB")
if max(img.size) > 1280:
    img.thumbnail((1280, 1280), Image.LANCZOS)
buf = io.BytesIO(); img.save(buf, format="JPEG", quality=85, optimize=True)
b64 = base64.b64encode(buf.getvalue()).decode()
client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

rows = []
for i in range(a.n):
    t0 = time.perf_counter()
    r = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "high"}},
        ]}],
        max_tokens=max_tokens, temperature=0.1,
    )
    ms = int((time.perf_counter() - t0) * 1000)
    txt = r.choices[0].message.content or "{}"
    if txt.startswith("```"):
        txt = txt.split("\n", 1)[-1].rsplit("```", 1)[0]
    try:
        d = json.loads(txt)
    except Exception as e:
        d = {"_parse_error": str(e), "_raw": txt[:300]}
    raw = {k: d.get(k) for k in ("doc_type", "vendor_name", "customer_name", "document_date", "total_amount", "grand_total", "line_items", "confidence")}
    if post:
        post(d, date.today(), a.caption)
    fin = {k: d.get(k) for k in ("doc_type", "vendor_name", "customer_name", "document_date", "total_amount", "total_source", "total_flag", "date_flag", "extraction_notes")}
    u = r.usage
    row = {"i": i + 1, "ms": ms, "finish": r.choices[0].finish_reason,
           "extracted_text": (d.get("extracted_text") or "")[:500], "_err": d.get("_parse_error"), "_raw_head": (d.get("_raw") or "")[:700], "prompt_tokens": u.prompt_tokens, "completion_tokens": u.completion_tokens, "raw": raw, "final": fin}
    rows.append(row)
    print(json.dumps(row, ensure_ascii=False, default=str), flush=True)

# harga gpt-4o-mini (USD/1M): input 0.15, output 0.60
cost = sum(r["prompt_tokens"] * 0.15 + r["completion_tokens"] * 0.60 for r in rows) / 1e6 / len(rows)
print(json.dumps({"summary": {"mode": a.mode, "n": len(rows),
      "avg_ms": sum(r["ms"] for r in rows) // len(rows),
      "avg_usd_per_call": round(cost, 6),
      "totals": [r["final"]["total_amount"] for r in rows],
      "dates": [r["final"]["document_date"] for r in rows],
      "vendors": [r["final"]["vendor_name"] for r in rows]}}, ensure_ascii=False, default=str))

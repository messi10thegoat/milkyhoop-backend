from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import time
from preprocessing import preprocess_image_cv
from decoder import decode_barcode
from cache import cache_lookup, cache_store

app = FastAPI(
    title="MilkyHoop Barcode Service",
    description="pyzbar-based barcode decoder with OpenCV preprocessing",
    version="1.0.0"
)

# CORS for milkyhoop.com
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://milkyhoop.com", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
async def health_check():
    return {"status": "ok", "service": "barcode-decoder"}

@app.post("/decode")
async def decode_image(file: UploadFile = File(...)):
    """
    Decode barcode from uploaded image (ROI, compressed WEBP/JPEG).

    Strategy:
    1. Try fast path first (minimal preprocessing) - target <200ms
    2. If no result, retry with slow path (full preprocessing) - target <400ms

    Returns: {ok: bool, results: [{text, type, rect}], cached: bool, latency_ms: float, path: str}
    """
    start = time.time()
    content = await file.read()

    # Log incoming image size for debugging
    print(f"[DECODE] Received image: {len(content)} bytes, content-type: {file.content_type}")

    # Check cache (short-term dedupe)
    cached = cache_lookup(content)
    if cached is not None:
        elapsed = (time.time() - start) * 1000
        return JSONResponse({
            'ok': True,
            'cached': True,
            'results': cached,
            'latency_ms': elapsed,
            'path': 'cache'
        })

    # Try FAST PATH first
    try:
        processed_fast = preprocess_image_cv(content, use_fast_path=True)
        results = decode_barcode(processed_fast)

        if results:
            # Success with fast path
            cache_store(content, results)
            elapsed = (time.time() - start) * 1000
            print(f"[DECODE] SUCCESS fast path: {results[0]['text']} ({elapsed:.1f}ms)")
            return JSONResponse({
                'ok': True,
                'cached': False,
                'results': results,
                'latency_ms': elapsed,
                'path': 'fast'
            })
        else:
            print(f"[DECODE] Fast path: no results, trying slow path...")
    except Exception as e:
        # Fast path failed, will try slow path
        print(f"[DECODE] Fast path error: {e}")

    # Fast path failed or no results - try SLOW PATH
    try:
        processed_slow = preprocess_image_cv(content, use_fast_path=False)
        results = decode_barcode(processed_slow)

        # Cache result even if empty (avoid repeated processing)
        cache_store(content, results)

        elapsed = (time.time() - start) * 1000
        if results:
            print(f"[DECODE] SUCCESS slow path: {results[0]['text']} ({elapsed:.1f}ms)")
        else:
            print(f"[DECODE] FAIL: no barcode found ({elapsed:.1f}ms)")
        return JSONResponse({
            'ok': True,
            'cached': False,
            'results': results,
            'latency_ms': elapsed,
            'path': 'slow'
        })
    except Exception as e:
        print(f"[DECODE] ERROR: {e}")
        raise HTTPException(status_code=400, detail=f"Image processing error: {e}")

@app.post("/decode-batch")
async def decode_batch(files: list[UploadFile] = File(...)):
    """
    Batch decode multiple images (for bulk registration).
    """
    results = []
    for file in files:
        content = await file.read()
        try:
            processed = preprocess_image_cv(content)
            decoded = decode_barcode(processed)
            results.append({
                'filename': file.filename,
                'ok': True,
                'results': decoded
            })
        except Exception as e:
            results.append({
                'filename': file.filename,
                'ok': False,
                'error': str(e)
            })

    return JSONResponse({'batch_results': results})

# HIGH: 522 `logger.info` tak pernah terlihat — stdlib logging tak pernah dikonfigurasi

**Tanggal:** 2026-08-06 **Severity:** HIGH (observability, bukan korupsi data)
**Status:** OPEN — **JANGAN diperbaiki sekarang**, perubahan logging global berisiko luas
**Kelas bukti:** `[CODE]` + `[LOG]` produksi

## Fakta

`main.py:182` mengonfigurasi **structlog** saja:
```python
structlog.configure(processors=[...], logger_factory=structlog.PrintLoggerFactory(file=sys.stdout))
logger = structlog.get_logger()
```

**Nol `logging.basicConfig()`, nol `dictConfig()`, nol `--log-config` uvicorn** untuk **stdlib
logging**. Modul lain memakai `logging.getLogger(__name__)`, yang tanpa konfigurasi default ke level
**WARNING** — sehingga seluruh `logger.info`/`logger.debug` **dibuang diam-diam**.

### Bukti dari log produksi
| | Jumlah |
|---|---|
| baris structlog `[info     ]` (dari `main.py`) | **58** — muncul |
| baris stdlib logging INFO | **0** — tak pernah muncul |

### Cakupan
```
logger.info di app/routers/  : 357
logger.info di app/services/ : 165
                        TOTAL: 522 call site yang tak pernah terlihat
```

**WARNING dan ERROR tetap muncul** (lewat `lastResort` handler ke stderr) — terbukti: traceback
`Kulakan product search error` dan `Redis connection failed` keduanya terlihat. Jadi celahnya
**INFO/DEBUG saja**, bukan seluruh logging. Itu mempersempit dampaknya, tapi 522 titik tetap besar:
semua jejak operasi normal (siapa memposting apa, idempotency hit, keputusan routing) hilang.

## Koreksi terhadap hipotesis awal

Hipotesis yang diajukan: `/ready` 503 selama >1 minggu tanpa alarm disebabkan kode inisialisasi Redis
yang tak pernah jalan di `startup_event`. **TIDAK TERKONFIRMASI.**

- `startup_event` hanya memuat **dua** item: `auth_client.connect()` dan `summary_poller_loop`.
  **Nol Redis.**
- Redis di-inisialisasi **lazy** di `/ready` sendiri (`main.py:813 from .services.redis_client import
  get_redis`).
- Sebab `/ready` 503 terlihat jelas di log dan **selalu terlihat**:
  ```
  Redis connection failed: Port could not be cast to integer value as 'x66dii8PjJ7ADL094'
  ```
  Yaitu **URL Redis salah bentuk — password ter-parse sebagai port**. (Bandingkan memory
  `redis-misconf-capdrop-20260725`: password redis memang `x66dii8…`.)

**Jadi `/ready` 503 lolos seminggu BUKAN karena lognya dibuang** — lognya ADA, terlihat, dan
BERULANG sejak awal. Yang tidak ada adalah **PEMBACANYA**.

⚠️ **JANGAN salah baca temuan ini sebagai "log tak terlihat".** Justru sebaliknya: sinyalnya hadir
sepanjang waktu di tempat yang benar, dan tetap tak ada yang bertindak. Itu **MEMPERKUAT** tiket
observability gap, bukan melemahkannya — karena membuktikan bahwa **menambah visibilitas tidak akan
menyelesaikannya**. Masalahnya bukan sinyal yang hilang, melainkan **ketiadaan pembaca**:
nol alerting, nol yang memantau, nol yang menagih.

Konsekuensi praktis: memperbaiki konfigurasi logging (tiket ini) akan menambah 522 baris INFO ke
aliran yang **sudah tidak dibaca siapa pun**. Tanpa alerting, itu menambah kebisingan, bukan
kemampuan melihat. Karena itu keduanya berakar berbeda dan tidak boleh digabung.

## Hubungan dengan observability gap (silang-rujuk, BUKAN gabung)

| | Akar | Perbaikan |
|---|---|---|
| **Tiket ini** | stdlib logging tak dikonfigurasi → 522 INFO hilang | konfigurasi logging |
| **Observability gap** (`/ready` 503 >1 minggu, nol alarm) | nol alerting; log ada tapi tak dibaca | alerting/monitoring |

Keduanya memperburuk satu sama lain, tapi memperbaiki salah satu **tidak** memperbaiki yang lain.

## Kenapa jangan diperbaiki sekarang

Menyalakan 522 `logger.info` sekaligus di produksi = lonjakan volume log mendadak, biaya penyimpanan,
dan kemungkinan membocorkan data sensitif yang selama ini "aman karena tak terlihat" (mis. `logger.info`
yang mencetak payload). **Audit isi pesan dulu**, baru nyalakan bertahap per-modul.

## Workaround yang sudah dipakai
`services/idempotency_cleanup.py` memakai `print()` alih-alih `logger.info()` supaya syarat "log tiap
siklus" benar-benar terpenuhi. **Itu menutup instance, bukan kelas** — dan alasannya ditulis di
komentar modul supaya tak dikira gaya penulisan sembarangan.

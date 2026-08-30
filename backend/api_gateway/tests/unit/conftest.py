"""
Fixture untuk uji FUNGSI MURNI pipeline chat.

Kenapa ada: sebelum ini satu-satunya cara menguji "apakah baris kedua hilang"
adalah HTTP -> LLM -> DB, ~10 menit per hipotesis, dan modelnya
non-deterministik sehingga tiap hipotesis butuh belasan probe.
Dengan LLM palsu, pertanyaan yang sama dijawab dalam hitungan detik dan
DETERMINISTIK.

Batas yang harus disadari (jangan diklaim lebih):
- Ini menguji KODE KITA saat model mengembalikan bentuk X.
- Ini TIDAK menguji apakah model sungguh mengembalikan bentuk X.
  Untuk itu tetap perlu probe produksi.
- Aturan yang berlaku: kalau harness dan produksi berbeda, PRODUKSI MENANG.
  (Terukur 2026-08-30: harness dengan collected={} melaporkan "items tidak ada
  18/18" sementara produksi 4/4 mengirim string yang gagal parse. Harness
  yang menang saat itu memakan satu ronde penuh.)
"""
import sys
import pytest

sys.path.insert(0, "/app/backend/api_gateway")

from app.services.llm.llm_client import LLMResponse  # noqa: E402


class FakeLLM:
    """LLM palsu: mengembalikan isi yang KITA tentukan, mencatat apa yang diminta.

    Dipakai dengan menyuntikkannya ke EntityExtractor / FieldExtractor, yang
    keduanya sudah menerima llm_client lewat konstruktor (dependency injection
    yang sudah ada di kode, bukan yang kita tambahkan).
    """

    def __init__(self, *balasan: str):
        # tiap panggilan chat() mengambil balasan berikutnya; yang terakhir
        # diulang kalau panggilan lebih banyak dari balasan yang disiapkan
        self._balasan = list(balasan) or ["{}"]
        self.panggilan = []          # rekaman: apa yang dikirim ke model
        self.n = 0

    async def chat(self, messages, tools=None, model="", temperature=0.1,
                   max_tokens=4096, response_format=None, **_):
        self.panggilan.append({
            "messages": messages,
            "model": model,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "response_format": response_format,
        })
        i = min(self.n, len(self._balasan) - 1)
        self.n += 1
        return LLMResponse(content=self._balasan[i], model=model or "fake")

    # -- pembacaan yang sering dipakai tes --
    @property
    def skema_terakhir(self):
        """responseSchema yang dikirim ke model pada panggilan terakhir."""
        if not self.panggilan:
            return None
        return self.panggilan[-1].get("response_format")


@pytest.fixture
def fake_llm():
    """Pabrik FakeLLM: fake_llm('{"a":1}') atau fake_llm(balasan1, balasan2)."""
    return FakeLLM


@pytest.fixture
def kontrol_fake_llm():
    """Kontrol positif: membuktikan FakeLLM benar-benar dipakai, bukan dilewati.

    Dipakai tes yang melaporkan NOL — supaya nol itu berarti 'tidak terjadi',
    bukan 'tidak pernah terpicu'.
    """
    def _cek(f: FakeLLM):
        assert f.n > 0, (
            "FakeLLM tidak pernah dipanggil — tes ini tidak menguji apa pun. "
            "Nol yang dihasilkannya TIDAK bermakna."
        )
        return True
    return _cek

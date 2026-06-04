from goldset.trace import has_trace


def test_trace_present_when_source_and_period():
    txt = "Berdasarkan data April–Mei 2026 (2 bulan terakhir): laba kotor Rp 77.752.904. Asumsi margin tetap."
    assert has_trace(txt) is True


def test_no_trace_when_period_missing():
    assert has_trace("Laba kotormu sekitar Rp 77 juta.") is False


def test_no_trace_when_source_missing():
    assert has_trace("Bulan Mei 2026 angkanya bagus.") is False

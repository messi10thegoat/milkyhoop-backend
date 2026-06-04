import re

_SOURCE = re.compile(r"\b(berdasarkan|sumber|basis|dari\s+data|asumsi|periode)\b", re.I)
_PERIOD = re.compile(
    r"\b(jan(uari)?|feb(ruari)?|mar(et)?|apr(il)?|mei|jun(i)?|jul(i)?|agu(stus)?|sep(tember)?|okt(ober)?|nov(ember)?|des(ember)?)\b"
    r"|\b20\d{2}\b|\bbulan\s+(ini|lalu|depan|terakhir)\b|\b\d+\s+bulan\b",
    re.I,
)


def has_trace(text):
    t = text or ""
    return bool(_SOURCE.search(t)) and bool(_PERIOD.search(t))

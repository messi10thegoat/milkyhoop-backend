#!/bin/bash
# pdf_text.sh <pdf-path> — print the RENDERED text (Unicode) of a WeasyPrint PDF.
# WeasyPrint embeds SUBSETTED fonts (glyph IDs, not ASCII), so zlib/grep on raw bytes CANNOT read
# the text and would FALSELY pass. A real extractor (pdfminer.six) maps glyphs back to Unicode.
#
# Runs in one of two places, in order:
#   1) the running test gateway `mh-test-gw` (pdfminer pre-installed by test_gateway.sh up) — fast;
#   2) a throwaway container from the gateway image (pip-installs pdfminer; needs internet).
# FAIL-HARD contract (owner directive, anti-silent-fallback): if NEITHER path yields text, print a
# clear error to stderr and EXIT NON-ZERO. Callers must treat empty output / non-zero as a hard
# failure ("cannot verify"), never as "no match / pass".
set -uo pipefail
PDF=${1:?usage: pdf_text.sh <pdf-path>}
NAME=mh-test-gw
cp "$PDF" /tmp/_pdf_text_in.pdf 2>/dev/null || { echo "pdf_text.sh: cannot read $PDF" >&2; exit 2; }
PY='from pdfminer.high_level import extract_text;import sys;sys.stdout.write(extract_text("/tmp/_pdf_text_in.pdf"))'

OUT=""
if docker ps --format '{{.Names}}' | grep -qx "$NAME" \
   && docker exec "$NAME" python -c 'import pdfminer' >/dev/null 2>&1; then
  docker cp /tmp/_pdf_text_in.pdf "$NAME":/tmp/_pdf_text_in.pdf >/dev/null 2>&1
  OUT=$(docker exec "$NAME" python -c "$PY" 2>/dev/null || true)
fi

if [ -z "$OUT" ]; then
  IMG=$(docker inspect milkyhoop-dev-api_gateway --format '{{.Config.Image}}' 2>/dev/null)
  if [ -n "$IMG" ]; then
    OUT=$(docker run --rm -v /tmp:/tmp "$IMG" sh -c \
      "python -m pip install -q --disable-pip-version-check pdfminer.six >/dev/null 2>&1; python -c '$PY'" 2>/dev/null || true)
  fi
fi

if [ -z "$OUT" ]; then
  echo "pdf_text.sh: NO PDF text extractor available (pdfminer.six). Bring up test_gateway.sh (pre-installs it) or provide internet for the throwaway container. FAILING HARD rather than pretending." >&2
  exit 3
fi
printf '%s' "$OUT"

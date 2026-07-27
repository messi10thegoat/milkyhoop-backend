#!/bin/bash
# pdf_text.sh <pdf-path> — print the RENDERED text of a WeasyPrint PDF (Unicode).
# WeasyPrint embeds subsetted fonts (glyph IDs, not ASCII), so zlib/grep on raw bytes CANNOT read
# the text and would falsely "pass". A real extractor (pdfminer.six) maps glyphs back to Unicode.
# Prefers the running test gateway (lib pre-installed by test_gateway.sh up); else a throwaway
# container from the gateway image (installs pdfminer on the fly — needs internet).
set -u
PDF=${1:?usage: pdf_text.sh <pdf-path>}
NAME=mh-test-gw
cp "$PDF" /tmp/_pdf_text_in.pdf 2>/dev/null || { echo "cannot read $PDF" >&2; exit 1; }
EXTRACT='from pdfminer.high_level import extract_text;import sys;sys.stdout.write(extract_text("/tmp/_pdf_text_in.pdf"))'

if docker ps --format '{{.Names}}' | grep -qx "$NAME" \
   && docker exec "$NAME" python -c 'import pdfminer' >/dev/null 2>&1; then
  docker cp /tmp/_pdf_text_in.pdf "$NAME":/tmp/_pdf_text_in.pdf >/dev/null 2>&1
  docker exec "$NAME" python -c "$EXTRACT" 2>/dev/null && exit 0
fi

IMG=$(docker inspect milkyhoop-dev-api_gateway --format '{{.Config.Image}}' 2>/dev/null)
docker run --rm -v /tmp:/tmp "$IMG" sh -c \
  "python -m pip install -q --disable-pip-version-check pdfminer.six 2>/dev/null; python -c '$EXTRACT'" 2>/dev/null

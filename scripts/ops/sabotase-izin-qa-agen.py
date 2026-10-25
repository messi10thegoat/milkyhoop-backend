import sys
p = sys.argv[1] + "/app/middleware/permission_middleware.py"; s = open(p).read()
lama = '(r"^/api/quotes/[^/]+/delete/preview$", ["POST"], "quote", "D")'
assert s.count(lama) == 1; open(p, "w").write(s.replace(lama, lama.replace('"D")', '"C")'))); print("terpasang izin C")

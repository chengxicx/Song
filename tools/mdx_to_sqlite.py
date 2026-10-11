import hashlib, html, re, sqlite3, sys
from mdict_utils.reader import MDX

src, out = sys.argv[1], sys.argv[2]
mdx = MDX(src)

TAG = re.compile(r"<[^>]+>")
def clean_key(k):
    if isinstance(k, bytes):
        k = k.decode("utf-8", "ignore")
    k = TAG.sub("", k)
    k = html.unescape(k)
    return k.replace("\r", "").replace("\n", "").strip()

def clean_def(d):
    if isinstance(d, bytes):
        d = d.decode("utf-8", "ignore")
    d = re.sub(r"(?i)<br\s*/?>", "\n", d)
    d = re.sub(r"(?i)</(p|div|li|tr|h\d)>", "\n", d)
    d = TAG.sub("", d)
    d = html.unescape(d)
    lines = [ln.strip() for ln in d.split("\n") if ln.strip()]
    return "\n".join(lines)[:800]

db = sqlite3.connect(out)
db.execute("PRAGMA journal_mode=OFF")
db.execute("PRAGMA synchronous=OFF")
db.execute("CREATE TABLE entries(eid INTEGER PRIMARY KEY, def TEXT)")
db.execute("CREATE TABLE terms(word TEXT NOT NULL, eid INTEGER NOT NULL)")
db.execute("CREATE INDEX idx_terms ON terms(word)")

def_hash = {}
n_e = n_t = 0
seen = set()
batch_e, batch_t = [], []

def flush():
    global n_e, n_t
    if batch_e:
        db.executemany("INSERT INTO entries VALUES(?,?)", batch_e)
        db.executemany("INSERT INTO terms VALUES(?,?)", batch_t)
        n_e += len(batch_e); n_t += len(batch_t)
        batch_e.clear(); batch_t.clear()

for key, definition in mdx.items():
    word = clean_key(key)
    if not word or word in seen:
        continue
    seen.add(word)
    text = clean_def(definition)
    if not text:
        continue
    dh = hashlib.md5(text.encode()).hexdigest()
    eid = def_hash.get(dh)
    if eid is None:
        eid = len(def_hash) + 1
        def_hash[dh] = eid
        batch_e.append((eid, text))
    batch_t.append((word, eid))
    if len(batch_e) >= 5000:
        flush()
flush()
db.commit()
db.execute("VACUUM")
print("entries:", n_e, "terms:", n_t)

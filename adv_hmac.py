import hashlib, json, os, sys, tempfile
sys.path.insert(0, r"C:\ProgramData\SentinelX\workspace\SENEX_WT_INTEGRATED_20260912")
from senecio_polymarket.backend import authority_seal as aseal

ID = {"source_commit":"a"*40,"source_tree":"b"*40,"build_digest":"sha256:"+"c"*64}
ROW = {"id":"1","ts":"2026-09-13T20:00:00+00:00","symbol":"BTCUSDT","prediction":"FLAT"}
CUR = {"ts":ROW["ts"],"id":ROW["id"]}
WC = "APPEND_ROWS_AND_MUTATE_DIRECTIONAL_ONLY_UNTIL_PROOF_QUALIFIED"
KEYA, KEYB = "A"*48, "B"*48
os.environ["SENEX_AUTHORITY_SEAL_DIR"] = tempfile.mkdtemp()

def setkey(k):
    if k is None:
        os.environ.pop("SENEX_AUTHORITY_SEAL_KEY", None)
    else:
        os.environ["SENEX_AUTHORITY_SEAL_KEY"] = k

def seal(scope, k):
    setkey(k)
    return aseal.save_authority_state(scope,[ROW],CUR,identity=ID,writer_contract=WC)

def load(scope, k):
    setkey(k)
    return aseal.load_authority_state(scope,identity=ID,writer_contract=WC)

results=[]
def check(name, should_reject, fn):
    try:
        fn()
        results.append((name,"FAIL" if should_reject else "PASS","accepted"))
    except aseal.AuthoritySealError as e:
        results.append((name,"PASS" if should_reject else "FAIL",str(e)[:70]))

seal("C1", None)
check("C1_legacy_sha_rejected_with_key", True, lambda: load("C1", KEYA))

seal("C2", KEYA)
p = aseal.authority_path("C2")
payload = json.loads(p.read_text())
payload["rows"][0]["prediction"] = "LONG"
payload["rows_hash"] = aseal._sha(payload["rows"])
u = dict(payload)
u.pop("seal_hash", None)
payload["seal_hash"] = "sha256:" + hashlib.sha256(aseal._canonical_json(u)).hexdigest()
p.write_text(json.dumps(payload))
check("C2_forged_sha_rejected_with_key", True, lambda: load("C2", KEYA))

seal("C3", KEYA)
check("C3_hmac_rejected_without_key", True, lambda: load("C3", None))

seal("C4", KEYA)
check("C4_key_rotation_rejected", True, lambda: load("C4", KEYB))

seal("C5", None)
assert load("C5", None)["seal_hash"].startswith("sha256:")
results.append(("C5_legacy_happy_path","PASS","sha256 OK"))
seal("C6", KEYA)
assert load("C6", KEYA)["seal_hash"].startswith("hmac-sha256:")
results.append(("C6_hmac_happy_path","PASS","hmac-sha256 OK"))

try:
    seal("C7", "x"*8)
    results.append(("C7_weak_key_rejected","FAIL","accepted weak key"))
except aseal.AuthoritySealError:
    results.append(("C7_weak_key_rejected","PASS","weak key rejected"))

for n,s,m in results:
    print(f"{n:42s} {s}  {m}")
fails = sum(1 for _,s,_ in results if s=="FAIL")
print(f"ADVERSARIALES = {len(results)-fails}/{len(results)} PASS")
sys.exit(1 if fails else 0)

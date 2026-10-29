#!/usr/bin/env python3
"""seeds.py - zero-dependency SEEDS action/trading tool for agents (Python 3.8+ stdlib only).

Builds calldata for every SEEDS action and prints the exact `baw contract-call` command for a
Binance Agentic Wallet, and reads chain state over public JSON-RPC.

Config (env or --deployment FILE.json with keys nft, game, market, vault, lens, content, care):
  SEEDS_RPC       default https://bsc-dataseed.bnbchain.org
  SEEDS_CHAIN_ID  default 56
  SEEDS_FROM      your agent wallet address (for the baw command)
  SEEDS_NFT / SEEDS_GAME / SEEDS_MARKET / SEEDS_VAULT / SEEDS_LENS / SEEDS_CONTENT / SEEDS_CARE

Write actions (print {to,inputData,value} + baw preview command; never sign anything):
  mint NONCE | mint-ref REFERRER NONCE  [--from ADDR; pre-flight runs when SEEDS_FROM/--from set] | pack | show-enter ID[,ID..] | show-request WEEK
  water ID[,ID..] (sends the water fee due: first 3 waters of a seed free, then 0.00002 BNB each) | fertilize ID | bloom ID[,ID..] | graft KEEP BURN | name ID "Text"
  gift ADDR | spin N | tickets N | claim-tickets ID[,ID..] | storm | draw WEEK | streak-prize WEEK
  claim | list ID PRICE_BNB | cancel ID | buy ID PRICE_BNB | buy-cheapest MAX_BNB [SCAN]
  offer ID|0 AMOUNT_BNB | accept-offer OFFER_ID TOKEN_ID | withdraw-offer OFFER_ID | withdraw
  refund-spin JOB | retry JOB
  Randomness (SeedsRandom; rng address = SEEDS_RNG or game.coordinator()):
  commit SECRET_TEXT   (game.commitNext(keccak256(SECRET_TEXT)) - your commitment for your next random job)
  rng-fallback REQ_ID  (anyone, once the operator is 200 blocks late) | rng-settle REQ_ID (re-targeted request)
  settle-pending WALLET [ID[,ID..]] [--req REQ_ID[,..]] [--blocks N]   (read-only: finds your pending bloom/pack/spin
      randomness and prints the ready rng-fallback / rng-settle tx once the operator is 200 blocks late;
      next-action WALLET ID embeds the same tx as `fallback` on WAIT_REVEAL; SEEDS_AUTO_FALLBACK=0 turns that off)
  Tamagotchi care (SeedsCare; owner OR its caretaker agent, one flower per call):
  feed ID | care-water ID | tend ID   (owner sends the water fee; a caretaker's fee comes from the care budget)
  Caretaker spending from the owner's care budget (caretaker agent only):
  care-fertilize ID | care-bloom ID | care-tickets OWNER N
  Owner-only caretaker settings:
  set-caretaker AGENT ALLOWANCE_BNB DAILY_CAP_BNB DAYS | revoke-caretaker
  deposit-care AMOUNT_BNB | withdraw-care AMOUNT_BNB|0
Reads (JSON to stdout):
  state ID | tokens ADDR | pot | floor | best-offer ID | listings [OFFSET] [LIMIT] | owed ADDR | pow-params ADDR | germination ID
  week | draw-info WEEK | show-info WEEK | referrer ADDR | job JOB | dashboard OUT.html | skill
  needs ID | care-status ID | caretaker OWNER | care-budget OWNER
  doctor [WALLET]   (read-only pre-plant check: rpc/chain, Agent ID, gas balance, planted, planting open, PoW speed; PASS/WARN/FAIL)
  nudges ID   (advisory only: fertilizer when the window is at risk, bloom at bud stage, a name plate once after bloom)
  rng-status | rng-request REQ_ID | next-action WALLET [ID]   (agent's first call; ID 0 = no flower yet;
      actions NONE/PLANT/WATER(now)/BLOOM/WAIT_REVEAL/WAIT_WATER(come back after wait_seconds))
Germination proof-of-work (multi-core, read-only; uses pycryptodome/eth_hash keccak if installed, else pure python):
  solve ADDR [--workers N] [--bits B] [--margin K]   (K extra bits of headroom; reads nft.powParams(ADDR), searches on N processes, default all CPUs)
  solve-bench BITS [--workers N] [--runs R]   (random inputs; prints mean seconds)
Flags: --deployment FILE  --json (machine output only)
"""
import json, os, sys, urllib.request

# ───────────────────────── keccak-256 (pure python) ─────────────────────────
_RC = [0x0000000000000001, 0x0000000000008082, 0x800000000000808A, 0x8000000080008000, 0x000000000000808B,
       0x0000000080000001, 0x8000000080008081, 0x8000000000008009, 0x000000000000008A, 0x0000000000000088,
       0x0000000080008009, 0x000000008000000A, 0x000000008000808B, 0x800000000000008B, 0x8000000000008089,
       0x8000000000008003, 0x8000000000008002, 0x8000000000000080, 0x000000000000800A, 0x800000008000000A,
       0x8000000080008081, 0x8000000000008080, 0x0000000080000001, 0x8000000080008008]
_ROT = [[0, 36, 3, 41, 18], [1, 44, 10, 45, 2], [62, 6, 43, 15, 61], [28, 55, 25, 21, 56], [27, 20, 39, 8, 14]]
_M = (1 << 64) - 1


def _rol(x, n):
    return ((x << n) | (x >> (64 - n))) & _M if n else x


def _f(A):
    for rc in _RC:
        C = [A[x][0] ^ A[x][1] ^ A[x][2] ^ A[x][3] ^ A[x][4] for x in range(5)]
        D = [C[(x - 1) % 5] ^ _rol(C[(x + 1) % 5], 1) for x in range(5)]
        A = [[A[x][y] ^ D[x] for y in range(5)] for x in range(5)]
        B = [[0] * 5 for _ in range(5)]
        for x in range(5):
            for y in range(5):
                B[y][(2 * x + 3 * y) % 5] = _rol(A[x][y], _ROT[x][y])
        A = [[B[x][y] ^ ((~B[(x + 1) % 5][y]) & B[(x + 2) % 5][y]) for y in range(5)] for x in range(5)]
        A[0][0] ^= rc
    return A


def keccak(data: bytes) -> bytes:
    rate = 136
    p = bytearray(data) + b"\x01"
    while len(p) % rate:
        p += b"\x00"
    p[-1] |= 0x80
    A = [[0] * 5 for _ in range(5)]
    for off in range(0, len(p), rate):
        blk = p[off:off + rate]
        for i in range(rate // 8):
            x, y = i % 5, i // 5
            A[x][y] ^= int.from_bytes(blk[8 * i:8 * i + 8], "little")
        A = _f(A)
    out = b"".join(A[i % 5][i // 5].to_bytes(8, "little") for i in range(4))
    return out


def selector(sig: str) -> bytes:
    return keccak(sig.encode())[:4]

# ───────────────────────── minimal ABI codec ─────────────────────────
def _split_types(s):
    out, depth, cur = [], 0, ""
    for ch in s:
        if ch == "," and depth == 0:
            out.append(cur); cur = ""
        else:
            depth += ch == "("; depth -= ch == ")"; cur += ch
    if cur:
        out.append(cur)
    return out


def _is_dynamic(t):
    if t in ("string", "bytes") or t.endswith("[]"):
        return True
    if t.startswith("("):
        return any(_is_dynamic(x) for x in _split_types(t[1:-1]))
    return False


def _enc_one(t, v):
    if t.endswith("[]"):
        inner = t[:-2]
        return (len(v)).to_bytes(32, "big") + encode([inner] * len(v), v)
    if t in ("string", "bytes"):
        b = v.encode() if isinstance(v, str) and t == "string" else (bytes.fromhex(v[2:]) if isinstance(v, str) else v)
        return len(b).to_bytes(32, "big") + b + b"\x00" * ((32 - len(b) % 32) % 32)
    if t == "address":
        return int(v, 16).to_bytes(32, "big")
    if t == "bool":
        return (1 if v else 0).to_bytes(32, "big")
    if t.startswith("uint"):
        return int(v).to_bytes(32, "big")
    if t.startswith("bytes"):
        return bytes.fromhex(v[2:]).ljust(32, b"\x00")
    raise ValueError(t)


def encode(types, values):
    head, tail = b"", b""
    hlen = 32 * len(types)
    for t, v in zip(types, values):
        if _is_dynamic(t):
            head += (hlen + len(tail)).to_bytes(32, "big")
            tail += _enc_one(t, v)
        else:
            head += _enc_one(t, v)
    return head + tail


def _dec_one(t, data, off):
    """decode type t whose head word is at data[off:]"""
    if _is_dynamic(t):
        ptr = int.from_bytes(data[off:off + 32], "big")
        return _dec_at(t, data, ptr)
    return _dec_at(t, data, off)


def _dec_at(t, data, pos):
    if t.endswith("[]"):
        n = int.from_bytes(data[pos:pos + 32], "big")
        return decode([t[:-2]] * n, data[pos + 32:])
    if t.startswith("("):
        return decode(_split_types(t[1:-1]), data[pos:])
    if t in ("string", "bytes"):
        n = int.from_bytes(data[pos:pos + 32], "big")
        b = data[pos + 32:pos + 32 + n]
        return b.decode("utf-8", "replace") if t == "string" else "0x" + b.hex()
    w = data[pos:pos + 32]
    if t == "address":
        return "0x" + w[12:].hex()
    if t == "bool":
        return w[-1] == 1
    if t.startswith("uint"):
        return int.from_bytes(w, "big")
    return "0x" + w.hex()


def decode(types, data):
    out, off = [], 0
    for t in types:
        out.append(_dec_one(t, data, off))
        off += 32
    return out

# ───────────────────────── config / rpc ─────────────────────────
CFG = {"rpc": os.environ.get("SEEDS_RPC", "https://bsc-dataseed.bnbchain.org"),
       "chain": int(os.environ.get("SEEDS_CHAIN_ID", "56")), "from": os.environ.get("SEEDS_FROM", "<YOUR_AGENT_WALLET>")}
# BNB Chain mainnet (chain 56) SEEDS deployment; env SEEDS_<NAME> or --deployment FILE overrides.
MAINNET = {"nft": "0x8d7E03C2dA2b532D4C59b46059443145147996D6", "game": "0xBE2C78e5c7aD8406A9dA36bd1d5f0F1Eea219D48", "market": "0xA52d300B25511cE5D40Ee06e94BdDDAb72C44b0a", "vault": "0xc79885D9B38344CA6bef3610B5Ec44feff24192B", "lens": "0xA97Dd8C78EBe6858fbBb043a45f4dE1350EB9A7E", "content": "0xd0DdDac59582e0919ad28662176F7befd7407227", "care": "0x5Bf6C48d396702cCaA0db98501436133A0bE7E6b", "rng": "0x57B5241f5B6f5fEFC9d0788ea2F511702891d99a"}
for k in ("nft", "game", "market", "vault", "lens", "content", "care", "rng"):
    CFG[k] = os.environ.get("SEEDS_" + k.upper(), MAINNET[k] if CFG["chain"] == 56 else "")

PRICES = {"bloom": 2 * 10**15, "fert": 5 * 10**14, "name": 5 * 10**14, "gift": 10**15, "graft": 10**15,
          "spin": 10**15, "ticket": 5 * 10**14, "pack": 3 * 10**15, "show": 10**15}


def bnb(s):
    from decimal import Decimal
    return int(Decimal(str(s)) * 10**18)


def rpc(method, params, url=None):
    req = urllib.request.Request(url or CFG["rpc"], data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode(),
                                 headers={"Content-Type": "application/json", "User-Agent": "seeds.py"})
    r = json.loads(urllib.request.urlopen(req, timeout=30).read())
    if "error" in r:
        raise SystemExit("rpc error: " + json.dumps(r["error"]))
    return r["result"]


def call(contract, sig, types, args, out):
    data = "0x" + (selector(sig) + encode(types, args)).hex()
    res = rpc("eth_call", [{"to": CFG[contract], "data": data}, "latest"])
    return decode(out, bytes.fromhex(res[2:]))


def ids(s):
    return [int(x) for x in str(s).split(",") if x]


def bloom_price():
    """live SeedsNFT.bloomPrice() (owner may only lower it); older deployments: BLOOM_PRICE() constant"""
    if "_bprice" not in CFG:
        for sig in ("bloomPrice()", "BLOOM_PRICE()"):
            try:
                CFG["_bprice"] = call("nft", sig, [], [], ["uint256"])[0]; break
            except SystemExit:
                pass
        else:
            CFG["_bprice"] = PRICES["bloom"]
    return CFG["_bprice"]


WATER_FEE_DEFAULT = 2 * 10**13  # 0.00002 BNB, SeedsNFT.WATER_FEE
DROP_NOTE = "+1 drop to the pot"


def water_fee_unit():
    """SeedsNFT.WATER_FEE, or 0 on a deployment without the water fee (older contracts)"""
    if "_wfee" not in CFG:
        try:
            CFG["_wfee"] = call("nft", "WATER_FEE()", [], [], ["uint256"])[0]
        except SystemExit:
            CFG["_wfee"] = 0
    return CFG["_wfee"]


def water_plan(id_list):
    """(fee_wei, paid_ids): the exact msg.value water(ids) needs from SEEDS_FROM right now.
    A water is paid when the flower is sprouted (not germinating/dormant), has used its 3 free waters,
    its 20h window is open, and SEEDS_FROM owns it (others' flowers are skipped and cost nothing)."""
    unit = water_fee_unit()
    if not unit:
        return 0, []
    now, seen, paid = _now(), set(), []
    frm = (CFG.get("from") or "").lower()
    for i in id_list:
        if i in seen:
            continue  # a repeated id is watered once
        seen.add(i)
        try:
            g = call("nft", "germination(uint256)", ["uint256"], [i], ["uint8", "uint40", "uint8", "uint40", "uint40"])
            st = dict(zip(STATE_K, call("nft", "state(uint256)", ["uint256"], [i], [STATE_T])[0]))
        except SystemExit:
            continue  # burned / nonexistent: skipped by the contract
        if g[0] != 0 or g[2] < 3:
            continue  # ritual or one of the first 3 waters: free
        if frm and st["owner"].lower() != frm:
            continue
        if now - st["lastWater"] < 20 * 3600:
            continue  # window not open: skipped (no fee)
        paid.append(i)
    return unit * len(paid), paid


def care_water_fee(i, tend=False):
    """fee the OWNER sends to care.water/tend (a caretaker sends 0: the fee comes from the care budget)"""
    try:
        fee = call("care", "waterFeeOf(uint256)", ["uint256"], [i], ["uint256"])[0]
    except SystemExit:
        return 0
    if not fee:
        return 0
    st = dict(zip(STATE_K, call("nft", "state(uint256)", ["uint256"], [i], [STATE_T])[0]))
    frm = (CFG.get("from") or "").lower()
    if frm and st["owner"].lower() != frm:
        return 0  # caretaker: paid from the owner's care budget
    if tend and _now() - st["lastWater"] < 20 * 3600:
        return 0  # tend will only feed
    return fee

# ───────────────────────── advisory nudges (read-only, never spend) ─────────────────────────
NUDGE_RISK_SECONDS = 4 * 3600  # "window at risk": under ~4h before the flower wilts
NUDGE_MAX_CHARGES = 3  # SeedsNFT.MAX_CHARGES
NUDGE_SEEN_FILE = os.environ.get("SEEDS_NUDGE_FILE", os.path.join(os.path.expanduser("~"), ".seeds_nudges.json"))


def live_price(sig, key):
    """read a price constant from chain (e.g. FERTILIZER_PRICE()); falls back to the documented price"""
    ck = "_p_" + key
    if ck not in CFG:
        try:
            CFG[ck] = call("nft", sig, [], [], ["uint256"])[0]
        except SystemExit:
            CFG[ck] = PRICES[key]
    return CFG[ck]


def nudge_logic(st, now, prices, seen=()):
    """pure: list of advisory nudges for one flower state dict (STATE_K keys).
    prices: {"fert","name","bloom"} in wei. seen: nudge keys already shown (one-time nudges)."""
    out = []
    if not st.get("bloomed") and not st.get("wilted"):
        left = st.get("wiltsAt", 0) - now
        if 0 < left < NUDGE_RISK_SECONDS and st.get("charges", 0) < NUDGE_MAX_CHARGES:
            out.append({"key": "fertilize", "action": "fertilize", "value_wei": prices["fert"],
                        "note": "watering window closes in %dh%02dm and you hold %d/3 fertilizer charges: fertilizer (%s BNB) is "
                                "streak insurance - it saves one missed window" % (left // 3600, left % 3600 // 60,
                                st.get("charges", 0), f"{prices['fert'] / 1e18:.4f}")})
        if st.get("stage") == 3 and not st.get("blooming"):
            out.append({"key": "bloom", "action": "bloom", "value_wei": prices["bloom"],
                        "note": "bud stage reached: bloom now for %s BNB (live bloomPrice) to reveal its rarity" % f"{prices['bloom'] / 1e18:.4f}"})
    if st.get("bloomed") and not st.get("name") and "name" not in seen:
        out.append({"key": "name", "action": "name", "value_wei": prices["name"], "once": True,
                    "note": "bloomed! give it a name plate for %s BNB (shown on the flower)" % f"{prices['name'] / 1e18:.4f}"})
    return out


def nudges(i):
    st = dict(zip(STATE_K, call("nft", "state(uint256)", ["uint256"], [i], [STATE_T])[0]))
    prices = {"fert": live_price("FERTILIZER_PRICE()", "fert"), "name": live_price("NAME_PRICE()", "name"), "bloom": bloom_price()}
    try:
        seen_all = json.load(open(NUDGE_SEEN_FILE))
    except (OSError, ValueError):
        seen_all = {}
    key = "%s:%d" % (CFG.get("nft", "").lower(), i)
    seen = seen_all.get(key, [])
    out = nudge_logic(st, _now(), prices, seen)
    once = [n["key"] for n in out if n.get("once")]
    if once:
        seen_all[key] = sorted(set(seen) | set(once))
        try:
            json.dump(seen_all, open(NUDGE_SEEN_FILE, "w"))
        except OSError:
            pass
    return {"id": i, "advisory": "suggestions only; nothing is sent - build the action yourself if you agree", "nudges": out}


# ───────────────────────── doctor: read-only pre-plant check ─────────────────────────
PLANT_OPENS_AT = int(os.environ.get("SEEDS_OPENS_AT", "1793451600"))  # announced Oct 31, 2026 13:00 UTC (off-chain; used only while paused)
# gas units measured on anvil (gas_costs.md): first-ever plant 368,619 + ritual waters 36,881 + 36,951 + 57,023
DOCTOR_PLANT_GAS = 368_619 + 36_881 + 36_951 + 57_023
DOCTOR_MARGIN = 1.5  # want 1.5x the estimate in the wallet
DOCTOR_GAS_FALLBACK = 10**9  # 1 gwei, only if eth_gasPrice fails


def _hms(s):
    s = int(max(0, s)); d, s = divmod(s, 86400); h, s = divmod(s, 3600); m = s // 60
    return ("%dd " % d if d else "") + "%dh %02dm" % (h, m)


def pow_bench(seconds=3.0):
    """single-core keccak rate (hashes/s) over ~`seconds`, with the same 192-byte input the solver hashes"""
    import time
    k, pre, n = _fast_keccak(), os.urandom(160), 0
    t0 = time.time()
    while time.time() - t0 < seconds:
        for _ in range(256):
            k(pre + n.to_bytes(32, "big")); n += 1
    return n / (time.time() - t0), k is keccak


def doctor(wallet=None, bench_seconds=3.0, cpus=None):
    """returns [(level, check, message)], level PASS/WARN/FAIL. Reads only; never signs or sends."""
    out = []
    add = lambda lv, c, m: out.append((lv, c, m))
    wallet = wallet or CFG["from"]
    if not wallet or not wallet.startswith("0x") or len(wallet) != 42:
        add("FAIL", "wallet", "give a wallet address: seeds.py doctor 0xYOURWALLET (or set SEEDS_FROM)")
        return out
    # 1. rpc + chain id
    try:
        cid = int(rpc("eth_chainId", []), 16)
    except BaseException as e:  # rpc() raises SystemExit on rpc errors
        add("FAIL", "rpc", "%s unreachable (%s)" % (CFG["rpc"], str(e)[:80]))
        return out
    if cid == CFG["chain"]:
        add("PASS", "rpc", "%s reachable, chain id %d matches" % (CFG["rpc"], cid))
    else:
        add("FAIL", "rpc", "chain id %d but config expects %d (check SEEDS_RPC / SEEDS_CHAIN_ID)" % (cid, CFG["chain"]))
    if not CFG["nft"]:
        add("FAIL", "config", "SEEDS_NFT not set (use --deployment FILE or SEEDS_NFT=...)")
        if _now() < PLANT_OPENS_AT:
            add("WARN", "planting", "contracts not configured yet; planting opens in %s (oct 31, 13:00 UTC)" % _hms(PLANT_OPENS_AT - _now()))
        return out
    # 2. Agent ID (same registry + agentKey() the contract's mint uses)
    bits, eligible = None, False
    try:
        reg = call("nft", "agentRegistry()", [], [], ["address"])[0]
        ok, _key = call("nft", "agentKey(address)", ["address"], [wallet], ["bool", "bytes32"])
        if ok:
            add("PASS", "agent-id", "wallet holds a .ai Agent ID in registry %s" % reg)
        else:
            add("FAIL", "agent-id", "no Agent ID in registry %s (mint reverts NoAgentId)" % reg)
        _c, _n, _a, _s, bits, eligible = call("nft", "powParams(address)", ["address"], [wallet],
                                              ["uint256", "address", "bytes32", "bytes32", "uint256", "bool"])
    except BaseException as e:
        add("FAIL", "agent-id", "could not read the Agent ID gate (%s)" % str(e)[:80])
    # 3. balance vs gas
    try:
        gp = int(rpc("eth_gasPrice", []), 16)
    except BaseException:
        gp = DOCTOR_GAS_FALLBACK
    try:
        bal = int(rpc("eth_getBalance", [wallet, "latest"]), 16)
        need = DOCTOR_PLANT_GAS * gp
        want = int(need * DOCTOR_MARGIN)
        msg = "%.6f BNB; plant + 3 ritual waters ~%.6f BNB at %.2f gwei (waters 1-3 are free, gas only)" % (
            bal / 1e18, need / 1e18, gp / 1e9)
        add("PASS" if bal >= want else "WARN" if bal >= need else "FAIL", "balance",
            msg + ("" if bal >= want else "; top up to >= %.6f BNB" % (want / 1e18)))
    except BaseException as e:
        add("FAIL", "balance", "could not read balance (%s)" % str(e)[:80])
    # 4. already planted?
    try:
        if call("nft", "walletClaimed(address)", ["address"], [wallet], ["bool"])[0]:
            add("FAIL", "planted", "this wallet already planted its free seed (mint reverts AlreadyClaimed)")
        elif bits is not None and not eligible:
            add("FAIL", "planted", "this Agent ID already planted from another wallet (or has no Agent ID)")
        else:
            add("PASS", "planted", "not planted yet")
    except BaseException as e:
        add("FAIL", "planted", "could not read walletClaimed (%s)" % str(e)[:80])
    # 5. planting open (whenLive = vault not paused)
    try:
        v = call("nft", "vault()", [], [], ["address"])[0]
        paused = decode(["bool"], bytes.fromhex(rpc("eth_call", [{"to": v, "data": "0x" + selector("paused()").hex()}, "latest"])[2:]))[0]
        if not paused:
            add("PASS", "planting", "planting is open")
        else:
            left = PLANT_OPENS_AT - _now()
            add("WARN" if left > 0 else "FAIL", "planting",
                "not open yet: opens in %s (oct 31, 13:00 UTC)" % _hms(left) if left > 0 else "vault is paused (planting halted)")
    except BaseException as e:
        add("FAIL", "planting", "could not read vault.paused() (%s)" % str(e)[:80])
    # 6. PoW speed
    if bits is None:
        try:
            bits = call("nft", "powBits()", [], [], ["uint256"])[0]
        except BaseException:
            bits = 25
    rate, pure = pow_bench(bench_seconds)
    cpus = int(cpus or os.cpu_count() or 1)
    exp = 2 ** bits
    est = lambda w: exp / (rate * w)
    rec = next((w for w in range(1, cpus + 1) if est(w) <= 120), cpus)
    m = "%d bits, %.0f hashes/s per core%s; expected solve ~%s with --workers %d (of %d CPUs)" % (
        bits, rate, " (pure-python keccak)" if pure else "", _hms(est(rec)) if est(rec) >= 60 else "%ds" % est(rec), rec, cpus)
    if est(rec) > 3600:
        add("WARN", "pow", m + "; too slow: pip install pycryptodome (about 100x faster), or use a bigger machine")
    else:
        add("PASS", "pow", m + "; run: seeds.py solve %s --workers %d" % (wallet, rec))
    return out


# ───────────────────────── actions ─────────────────────────
ACTIONS = {
    # name: (contract, signature, types, argfn(args)->values, valuefn(args)->wei)
    "mint": ("nft", "mint(uint256)", ["uint256"], lambda a: [int(a[0])], lambda a: 0),
    "mint-ref": ("nft", "mintWithReferrer(address,uint256)", ["address", "uint256"], lambda a: [a[0], int(a[1])], lambda a: 0),
    "pack": ("nft", "buyPack()", [], lambda a: [], lambda a: PRICES["pack"]),
    "show-enter": ("game", "enterShow(uint256[])", ["uint256[]"], lambda a: [ids(a[0])], lambda a: PRICES["show"] * len(ids(a[0]))),
    "show-request": ("game", "requestShow(uint256)", ["uint256"], lambda a: [int(a[0])], lambda a: 0),
    "water": ("nft", "water(uint256[])", ["uint256[]"], lambda a: [ids(a[0])], lambda a: water_plan(ids(a[0]))[0]),
    "fertilize": ("nft", "fertilize(uint256)", ["uint256"], lambda a: [int(a[0])], lambda a: PRICES["fert"]),
    "bloom": ("nft", "bloom(uint256[])", ["uint256[]"], lambda a: [ids(a[0])], lambda a: bloom_price() * len(ids(a[0]))),
    "graft": ("nft", "graft(uint256,uint256)", ["uint256", "uint256"], lambda a: [int(a[0]), int(a[1])], lambda a: PRICES["graft"]),
    "name": ("nft", "setName(uint256,string)", ["uint256", "string"], lambda a: [int(a[0]), a[1]], lambda a: PRICES["name"]),
    "gift": ("nft", "gift(address)", ["address"], lambda a: [a[0]], lambda a: PRICES["gift"]),
    "spin": ("game", "spin(uint256)", ["uint256"], lambda a: [int(a[0])], lambda a: PRICES["spin"] * int(a[0])),
    "tickets": ("game", "buyTickets(uint256)", ["uint256"], lambda a: [int(a[0])], lambda a: PRICES["ticket"] * int(a[0])),
    "claim-tickets": ("game", "claimTickets(uint256[])", ["uint256[]"], lambda a: [ids(a[0])], lambda a: 0),
    "storm": ("game", "storm()", [], lambda a: [], lambda a: 0),
    "draw": ("game", "requestDraw(uint256)", ["uint256"], lambda a: [int(a[0])], lambda a: 0),
    "streak-prize": ("game", "settleStreakPrize(uint256)", ["uint256"], lambda a: [int(a[0])], lambda a: 0),
    "refund-spin": ("game", "refundSpin(uint256)", ["uint256"], lambda a: [int(a[0])], lambda a: 0),
    "retry": ("game", "retry(uint256)", ["uint256"], lambda a: [int(a[0])], lambda a: 0),
    "commit": ("game", "commitNext(bytes32)", ["bytes32"], lambda a: ["0x" + keccak(a[0].encode()).hex()], lambda a: 0),
    "rng-fallback": ("rng", "fallbackFulfil(uint256)", ["uint256"], lambda a: [int(a[0])], lambda a: 0),
    "rng-settle": ("rng", "settle(uint256)", ["uint256"], lambda a: [int(a[0])], lambda a: 0),
    "claim": ("vault", "claim()", [], lambda a: [], lambda a: 0),
    "list": ("market", "list(uint256,uint256)", ["uint256", "uint256"], lambda a: [int(a[0]), bnb(a[1])], lambda a: 0),
    "cancel": ("market", "cancel(uint256)", ["uint256"], lambda a: [int(a[0])], lambda a: 0),
    "buy": ("market", "buy(uint256)", ["uint256"], lambda a: [int(a[0])], lambda a: bnb(a[1])),
    "buy-cheapest": ("market", "buyCheapest(uint256,uint256)", ["uint256", "uint256"],
                     lambda a: [bnb(a[0]), int(a[1]) if len(a) > 1 else 200], lambda a: bnb(a[0])),
    "offer": ("market", "makeOffer(uint256)", ["uint256"], lambda a: [int(a[0])], lambda a: bnb(a[1])),
    "accept-offer": ("market", "acceptOffer(uint256,uint256)", ["uint256", "uint256"], lambda a: [int(a[0]), int(a[1])], lambda a: 0),
    "withdraw-offer": ("market", "withdrawOffer(uint256)", ["uint256"], lambda a: [int(a[0])], lambda a: 0),
    "withdraw": ("market", "withdraw()", [], lambda a: [], lambda a: 0),
    # ── Tamagotchi care (SeedsCare) ──
    "feed": ("care", "feed(uint256)", ["uint256"], lambda a: [int(a[0])], lambda a: 0),
    "care-water": ("care", "water(uint256)", ["uint256"], lambda a: [int(a[0])], lambda a: care_water_fee(int(a[0]))),
    "tend": ("care", "tend(uint256)", ["uint256"], lambda a: [int(a[0])], lambda a: care_water_fee(int(a[0]), True)),
    "care-fertilize": ("care", "fertilize(uint256)", ["uint256"], lambda a: [int(a[0])], lambda a: 0),
    "care-bloom": ("care", "bloom(uint256)", ["uint256"], lambda a: [int(a[0])], lambda a: 0),
    "care-tickets": ("care", "buyTickets(address,uint256)", ["address", "uint256"], lambda a: [a[0], int(a[1])], lambda a: 0),
    "set-caretaker": ("care", "setCaretaker(address,uint256,uint256,uint256)", ["address", "uint256", "uint256", "uint256"],
                      lambda a: [a[0], bnb(a[1]), bnb(a[2]), _now() + int(float(a[3]) * 86400)], lambda a: 0),
    "revoke-caretaker": ("care", "revokeCaretaker()", [], lambda a: [], lambda a: 0),
    "deposit-care": ("care", "depositCareBudget()", [], lambda a: [], lambda a: bnb(a[0])),
    "withdraw-care": ("care", "withdrawCareBudget(uint256)", ["uint256"], lambda a: [bnb(a[0])], lambda a: 0),
}

CARE_STATUS_T = "(address,uint8,uint8,uint8,bool,bool,bool,uint16,uint40,uint40,uint40,uint40,uint40,uint40)"
CARE_STATUS_K = ["owner", "hunger", "thirst", "mood", "canWater", "canFeed", "wilted", "streak", "lastWater", "lastFed",
                 "nextWaterAt", "wiltsAt", "nextFeedAt", "starvesAt"]


def _now():
    try:
        return int(rpc("eth_getBlockByNumber", ["latest", False])["timestamp"], 16)
    except SystemExit:
        raise
    except Exception:
        import time
        return int(time.time())


def mood_name(m):
    return "Happy" if m >= 70 else "Okay" if m >= 40 else "Sad" if m >= 15 else "Miserable"

STATE_T = "(address,uint8,uint8,bool,bool,bool,bool,bool,uint8,uint8,uint16,uint16,uint40,uint40,uint40,uint40,uint32,string)"
STATE_K = ["owner", "stage", "rarity", "bloomed", "blooming", "wilted", "golden", "soulbound", "badges", "charges", "streak",
           "bestStreak", "born", "lastWater", "nextWaterAt", "wiltsAt", "transferNonce", "name"]


def _rng():
    """SeedsRandom address: SEEDS_RNG, else the game's coordinator()"""
    if not CFG["rng"] and CFG["game"]:
        CFG["rng"] = call("game", "coordinator()", [], [], ["address"])[0]
    return CFG["rng"]


def build(name, args):
    c, sig, types, af, vf = ACTIONS[name]
    if c == "rng":
        _rng()
    if not CFG[c]:
        raise SystemExit(f"missing address for {c}: set SEEDS_{c.upper()} or --deployment")
    data = "0x" + (selector(sig) + encode(types, af(args))).hex()
    value = vf(args)
    r = {"action": name, "to": CFG[c], "inputData": data, "value": str(value), "valueBNB": value / 1e18,
         "baw_preview": f"baw contract-call preview --binanceChainId {CFG['chain']} --from {CFG['from']} --to {CFG[c]} --value {value} --inputData {data} --json",
         "baw_execute": "baw contract-call execute --requestId <requestId from preview> --json"}
    if name in ("water", "care-water", "tend"):
        unit = water_fee_unit()
        n = value // unit if unit else 0
        r["waterFeeWei"] = str(value)
        if name in ("care-water", "tend") and unit and not n:
            try:
                due = call("care", "waterFeeOf(uint256)", ["uint256"], [int(args[0])], ["uint256"])[0]
            except SystemExit:
                due = 0
            if due:
                r["note"] = "%s (water fee %s BNB from the owner's care budget: half to the pot, half to the creator)" % (
                    DROP_NOTE, f"{due / 1e18:.5f}")
        if n:
            r["note"] = "%s%s (water fee %s BNB: half to the pot, half to the creator)" % (
                DROP_NOTE, "" if n == 1 else " x%d" % n, f"{value / 1e18:.5f}")
        elif "note" not in r:
            r["note"] = "free water (first-light ritual / first 3 waters)" if unit else "free water"
    return r


# ───────────────────────── reveal liveness: settle your own pending randomness ─────────────────────────
FALLBACK_BLOCKS = 200  # SeedsRandom.FALLBACK_BLOCKS
LOG_CHUNK = 5000       # blocks per eth_getLogs call (public BSC RPCs cap the range)


def _t_uint(n):
    return "0x" + int(n).to_bytes(32, "big").hex()


def _t_addr(a):
    return "0x" + "00" * 12 + a.lower().replace("0x", "").rjust(40, "0")


def _topic0(sig):
    return "0x" + keccak(sig.encode()).hex()


# bsc-dataseed (the default SEEDS_RPC) refuses eth_getLogs ("limit exceeded"); log searches then use this endpoint
LOGS_RPC = os.environ.get("SEEDS_LOGS_RPC") or {56: "https://bsc-rpc.publicnode.com",
                                                 97: "https://bsc-testnet-rpc.publicnode.com"}.get(CFG["chain"], "")


def _get_logs(f):
    try:
        return rpc("eth_getLogs", [f])
    except SystemExit:
        if not LOGS_RPC or LOGS_RPC == CFG["rpc"]:
            raise
        return rpc("eth_getLogs", [f], LOGS_RPC)


def _logs_newest(address, topics, bn, blocks, first_only=False):
    """eth_getLogs over the last `blocks` blocks, newest chunk first (stops at the first hit if first_only)."""
    out, hi = [], bn
    while hi > max(0, bn - blocks):
        lo = max(0, bn - blocks, hi - LOG_CHUNK + 1)
        r = _get_logs({"address": address, "topics": topics, "fromBlock": hex(lo), "toBlock": hex(hi)})
        r = sorted(r, key=lambda x: (int(x["blockNumber"], 16), int(x.get("logIndex", "0x0"), 16)), reverse=True)
        out += r
        if first_only and out:
            return out[:1]
        hi = lo - 1
    return out


def _live_vrf(job):
    """(vrfId, status) of a game job; vrfId is the current one (a retry() replaces it)."""
    j = call("game", "jobInfo(uint256)", ["uint256"], [int(job)], ["(uint8,uint8,uint8,address,uint40,uint40,uint64,uint256)"])[0]
    return j[7], j[1]


def find_requests(wallet, token_ids=None, blocks=100000, spins=True):
    """Randomness requests that belong to `wallet`: blooms/packs of its blooming flowers (or `token_ids`) and its
    pending spins. Read-only: eth_getLogs + receipts. Returns [{requestId, job, source}]."""
    bn = int(rpc("eth_blockNumber", []), 16)
    if token_ids is None:
        token_ids = []
        for t in call("nft", "tokensOf(address)", ["address"], [wallet], ["uint256[]"])[0]:
            st = dict(zip(STATE_K, call("nft", "state(uint256)", ["uint256"], [t], [STATE_T])[0]))
            if st["blooming"] and not st["bloomed"]:
                token_ids.append(t)
    found, seen_jobs = [], set()
    req_t0 = _topic0("Requested(uint256,uint8,uint256)")
    for t in token_ids:
        hit = _logs_newest(CFG["nft"], [[_topic0("BloomRequested(uint256,uint16)"), _topic0("PackOpened(uint256,address)")], _t_uint(t)],
                           bn, blocks, first_only=True)
        if not hit:
            continue
        rc = rpc("eth_getTransactionReceipt", [hit[0]["transactionHash"]])
        for lg in rc.get("logs", []):
            if lg["address"].lower() == CFG["game"].lower() and lg["topics"][0] == req_t0:
                job = int(lg["topics"][1], 16)
                if job in seen_jobs:
                    continue
                seen_jobs.add(job)
                vrf, jst = _live_vrf(job)
                if jst == 1:
                    found.append({"requestId": vrf, "job": job, "source": "bloom #%d" % t})
    if wallet and spins:
        for lg in _logs_newest(CFG["game"], [_topic0("SpinPlaced(uint256,address,uint256)"), None, _t_addr(wallet)], bn, blocks):
            job = int(lg["topics"][1], 16)
            if job in seen_jobs:
                continue
            seen_jobs.add(job)
            vrf, jst = _live_vrf(job)
            if jst == 1:
                found.append({"requestId": vrf, "job": job, "source": "spin job %d" % job})
    return found


def classify_request(p, created_block, bn, fb=FALLBACK_BLOCKS):
    """p = SeedsRandom.pending(id) tuple (status, index, targetBlock, fallbackReady). Pure; returns (verdict, blocks_left).
    verdict: done | fallback (send rng-fallback now) | settle (send rng-settle now) | wait | unknown"""
    status, index, target, ready = p
    if status == 3:
        return "done", 0
    if status == 1:
        if ready:
            return "fallback", 0
        no_chain = index == (1 << 64) - 1
        return "wait", max(1, (target if no_chain else created_block + fb) + 1 - bn)
    if status == 2:
        return ("settle", 0) if bn > target else ("wait", target + 1 - bn)
    return "unknown", 0


def settle_pending(wallet=None, token_ids=None, req_ids=None, blocks=100000, spins=True):
    """One-shot liveness check for a player's own randomness. Prints nothing; returns a plan with ready-to-send
    rng-fallback / rng-settle transactions (anyone may send them; they only need gas)."""
    _rng()
    reqs = [{"requestId": int(r), "job": None, "source": "requestId"} for r in (req_ids or [])]
    if not req_ids:
        reqs = find_requests(wallet, token_ids, blocks, spins)
    bn = int(rpc("eth_blockNumber", []), 16)
    out, txs = [], []
    for r in reqs:
        p = call("rng", "pending(uint256)", ["uint256"], [r["requestId"]], ["uint8", "uint64", "uint64", "bool"])
        created = call("rng", "reqs(uint256)", ["uint256"], [r["requestId"]],
                       ["address", "uint32", "uint8", "bool", "uint64", "uint64", "uint64", "bytes32", "bytes32"])[4]
        verdict, left = classify_request(p, created, bn)
        row = dict(r, status=p[0], verdict=verdict, blocksLate=max(0, bn - created) if p[0] == 1 else 0)
        if verdict == "wait":
            row["blocksLeft"] = left
        if verdict in ("fallback", "settle"):
            tx = build("rng-fallback" if verdict == "fallback" else "rng-settle", [str(r["requestId"])])
            row["tx"] = tx; txs.append(tx)
            row["log"] = ("reveal for %s is %d blocks late (operator silent): sending public fallbackFulfil(%d)"
                          % (r["source"], row["blocksLate"], r["requestId"]) if verdict == "fallback" else
                          "request %d has its value fixed but was re-targeted: sending settle(%d)" % (r["requestId"], r["requestId"]))
        out.append(row)
    res = {"block": bn, "requests": out, "transactions": txs}
    if not out:
        res["note"] = "no pending randomness found for this wallet (in the last %d blocks)" % blocks
    elif txs:
        res["note"] = "send each transaction (baw preview -> execute); it reveals now with blockhash + your commitment, no operator needed"
    elif all(x["verdict"] == "done" for x in out):
        res["note"] = "all found requests are already revealed: nothing to send"
    else:
        res["note"] = "operator still inside its %d-block window: check again in ~%d blocks" % (
            FALLBACK_BLOCKS, min(x.get("blocksLeft", 1) for x in out if x["verdict"] == "wait"))
    return res


def read(name, a):
    if name == "rng-status":
        _rng()
        k = ["capacity", "revealed", "nextIndex", "nextRequestId", "operatorGas", "gasSlice", "operator", "fallbackBlocks"]
        sigs = [("capacity()", "uint64"), ("revealed()", "uint64"), ("nextIndex()", "uint64"), ("nextId()", "uint256"),
                ("operatorGas()", "uint256"), ("gasSlice()", "uint256"), ("operator()", "address"), ("FALLBACK_BLOCKS()", "uint256")]
        r = {kk: call("rng", sg, [], [], [t])[0] for kk, (sg, t) in zip(k, sigs)}
        r["backlog"] = r["nextIndex"] - r["revealed"]  # requests waiting for the operator's reveal
        r["chainLeft"] = r["capacity"] - r["nextIndex"]  # requests the committed hash chain can still serve
        return r
    if name == "rng-request":
        _rng()
        r = call("rng", "pending(uint256)", ["uint256"], [int(a[0])], ["uint8", "uint64", "uint64", "bool"])
        st = {0: "none", 1: "pending", 2: "value fixed (settle after target block)", 3: "done"}
        return {"status": r[0], "statusName": st.get(r[0], "?"), "chainIndex": r[1], "targetBlock": r[2], "fallbackReady": r[3]}
    if name == "state":
        r = call("nft", "state(uint256)", ["uint256"], [int(a[0])], [STATE_T])[0]
        return dict(zip(STATE_K, r))
    if name == "tokens":
        return call("nft", "tokensOf(address)", ["address"], [a[0]], ["uint256[]"])[0]
    if name == "pot":
        r = call("vault", "potInfo()", [], [], ["uint256"] * 7 + ["bool"])
        return dict(zip(["playerBucket", "sponsorBucket", "sponsorFree", "bonusReserved", "creatorBalance", "owedToWinners", "maxSingleWin", "paused"], r))
    if name == "floor":  # paginated scan (safe at any listing count)
        n = call("market", "listingsCount()", [], [], ["uint256"])[0]
        best = {"tokenId": 0, "price": 0}
        for off in range(0, n, 200):
            r = call("market", "floor(uint256,uint256)", ["uint256", "uint256"], [off, 200], ["uint256", "uint256"])
            if r[0] and (not best["tokenId"] or r[1] < best["price"]):
                best = {"tokenId": r[0], "price": r[1]}
        return best
    if name == "best-offer":  # paginated scan
        n = call("market", "activeOffersCount()", [], [], ["uint256"])[0]
        best = {"offerId": 0, "bidder": "0x" + "00" * 20, "amount": 0}
        for off in range(0, n, 200):
            r = call("market", "bestOffer(uint256,uint256,uint256)", ["uint256", "uint256", "uint256"], [int(a[0]), off, 200],
                     ["uint256", "address", "uint256"])
            if r[2] > best["amount"]:
                best = {"offerId": r[0], "bidder": r[1], "amount": r[2]}
        return best
    if name == "pow-params":
        r = call("nft", "powParams(address)", ["address"], [a[0]], ["uint256", "address", "bytes32", "bytes32", "uint256", "bool"])
        return dict(zip(["chainId", "nft", "agentId", "salt", "bits", "eligible"], r))
    if name == "next-action":
        r = call("lens", "nextAction(address,uint256)", ["address", "uint256"], [a[0], int(a[1]) if len(a) > 1 else 0],
                 ["uint8", "uint256", "uint256"])
        names = ["NONE", "PLANT", "WATER", "BLOOM", "WAIT_REVEAL", "WAIT_WATER"]
        code, wait, val = r
        if code == 2 and wait:  # older Lens (no WAIT_WATER): WATER with a wait means "not yet"
            code = 5
        out = {"action": names[code] if code < len(names) else code, "code": code, "wait_seconds": wait, "value_wei": val}
        if code == 5:
            out["water_at"] = _now() + wait
            out["note"] = "not yet: the next water opens in %dh%02dm (water() reverts NotReady before then)" % (wait // 3600, wait % 3600 // 60)
        if code == 4 and len(a) > 1 and int(a[1]) and os.environ.get("SEEDS_AUTO_FALLBACK", "1") != "0":
            try:
                plan = settle_pending(a[0], [int(a[1])], spins=False)  # this flower only: 1-8 getLogs
                if plan["transactions"]:
                    out["fallback"] = plan["transactions"]
                    out["note"] = "; ".join(x["log"] for x in plan["requests"] if x.get("log")) + \
                        " -> send the `fallback` tx(s) now (baw preview -> execute), then poll state %s" % a[1]
                    print("[seeds] " + out["note"], file=sys.stderr)
                else:
                    left = [x["blocksLeft"] for x in plan["requests"] if x.get("blocksLeft")]
                    out["note"] = ("reveal pending; operator has %d more blocks before the public fallback opens" % min(left)) if left \
                        else "reveal pending: check again in a minute"
            except (SystemExit, Exception) as e:  # never let the liveness check break next-action
                out["note"] = "reveal pending (fallback check failed: %s); run `seeds.py settle-pending %s %s`" % (str(e)[:80], a[0], a[1])
        if code in (2, 5) and val:
            out["note"] = (out.get("note", "") + "; " if code == 5 else "") + "water fee %s BNB (%s)" % (f"{val / 1e18:.5f}", DROP_NOTE)
        return out
    if name == "nudges":
        return nudges(int(a[0]))
    if name == "germination":
        r = call("nft", "germination(uint256)", ["uint256"], [int(a[0])], ["uint8", "uint40", "uint8", "uint40", "uint40"])
        return dict(zip(["status(0 sprouted,1 germinating,2 dormant)", "startedAt", "waters", "nextWaterAt", "deadline"], r))
    if name == "listings":
        off, lim = (int(a[0]) if a else 0), (int(a[1]) if len(a) > 1 else 50)
        r = call("market", "listings(uint256,uint256)", ["uint256", "uint256"], [off, lim], ["uint256[]", "address[]", "uint256[]", "bool[]"])
        return [{"tokenId": r[0][i], "seller": r[1][i], "price": r[2][i], "valid": r[3][i]} for i in range(len(r[0]))]
    if name == "owed":
        w = call("vault", "winnings(address)", ["address"], [a[0]], ["uint256"])[0]
        p = call("market", "proceeds(address)", ["address"], [a[0]], ["uint256"])[0]
        return {"vaultWinnings": w, "marketProceeds": p}
    if name == "week":
        return call("game", "currentWeek()", [], [], ["uint256"])[0]
    if name == "draw-info":
        r = call("game", "drawInfo(uint256)", ["uint256"], [int(a[0])], ["uint256", "uint256", "uint8", "address", "uint256"])
        return dict(zip(["tickets", "entries", "status", "winner", "estPrize"], r))
    if name == "show-info":
        r = call("game", "showInfo(uint256)", ["uint256"], [int(a[0])], ["uint256", "uint256", "uint8", "uint256"])
        prize = call("vault", "showPrize(uint256)", ["uint256"], [int(a[0])], ["uint256"])[0]
        return dict(zip(["entries", "totalWeight", "status", "winnerToken"], r), prize=prize)
    if name == "needs":
        h, t, m = call("care", "needsOf(uint256)", ["uint256"], [int(a[0])], ["uint8", "uint8", "uint8"])
        return {"hunger": h, "thirst": t, "mood": m, "moodName": mood_name(m)}
    if name == "care-status":
        r = dict(zip(CARE_STATUS_K, call("care", "status(uint256)", ["uint256"], [int(a[0])], [CARE_STATUS_T])[0]))
        r["moodName"] = mood_name(r["mood"])
        return r
    if name == "caretaker":
        r = call("care", "caretakerOf(address)", ["address"], [a[0]], ["address", "uint256", "uint256", "uint256", "uint256", "bool"])
        out = dict(zip(["agent", "allowance", "dailyCap", "spentToday", "expiry", "active"], r))
        out["spendableNow"] = call("care", "spendable(address)", ["address"], [a[0]], ["uint256"])[0]
        return out
    if name == "care-budget":
        return call("care", "careBudget(address)", ["address"], [a[0]], ["uint256"])[0]
    if name == "referrer":
        return call("nft", "referrerOf(address)", ["address"], [a[0]], ["address"])[0]
    if name == "job":
        r = call("game", "jobInfo(uint256)", ["uint256"], [int(a[0])], ["(uint8,uint8,uint8,address,uint40,uint40,uint64,uint256)"])[0]
        return dict(zip(["kind", "status", "n", "player", "requestedAt", "stormAt", "week", "vrfId"], r))
    if name in ("dashboard", "skill"):
        fname = "dashboard.html" if name == "dashboard" else "SKILL.md"
        txt = call("content", "readString(string)", ["string"], [fname], ["string"])[0]
        if name == "dashboard":
            for k, v in {"RPC": CFG["rpc"], "CHAIN_ID": str(CFG["chain"]), "OWNER": CFG["from"], "SEEDS": CFG["nft"],
                         "GAME": CFG["game"], "MARKET": CFG["market"], "VAULT": CFG["vault"], "LENS": CFG["lens"],
                         "CARE": CFG["care"]}.items():
                txt = txt.replace("{{" + k + "}}", v)
            out = a[0] if a else "seeds_dashboard.html"
            open(out, "w").write(txt)
            return {"written": out, "bytes": len(txt)}
        return txt
    raise SystemExit("unknown command " + name)


# ───────────────────────── germination PoW solver (multiprocessing) ─────────────────────────
def _fast_keccak():
    for d in tuple(p for p in [os.environ.get("SEEDS_PYLIB")] if p):  # optional vendored pycryptodome dir
        if os.path.isdir(d) and d not in sys.path:
            sys.path.append(d)
    try:
        from Crypto.Hash import keccak as K
        return lambda b: K.new(digest_bits=256, data=b).digest()
    except ImportError:
        pass
    try:
        from eth_hash.auto import keccak as k
        return k
    except ImportError:
        return keccak


def _pow_worker(pre, bits, start, step, q, stop):
    k = _fast_keccak()
    limit, n = 1 << (256 - bits), start
    while not stop.is_set():
        for _ in range(4096):
            if int.from_bytes(k(pre + n.to_bytes(32, "big")), "big") < limit:
                q.put(n); stop.set(); return
            n += step


def pow_solve(pre, bits, workers=None, start=None):
    """Return a nonce with keccak(pre||nonce) < 2^(256-bits), searched on `workers` processes from `start` (default random)."""
    import multiprocessing as mp
    workers = max(1, int(workers or os.cpu_count() or 1))
    if start is None:
        start = int.from_bytes(os.urandom(8), "big") << 64
    q, stop = mp.Queue(), mp.Event()
    ps = [mp.Process(target=_pow_worker, args=(pre, bits, start + i, workers, q, stop), daemon=True) for i in range(workers)]
    for p in ps:
        p.start()
    n = q.get()
    stop.set()
    for p in ps:
        p.join(2)
        if p.is_alive():
            p.terminate()
    return n


def _opt(args, flag, default):
    if flag in args:
        i = args.index(flag); v = args[i + 1]; del args[i:i + 2]; return int(v)
    return default


def solve_cmd(cmd, args):
    import time
    args = list(args)
    workers = _opt(args, "--workers", os.cpu_count() or 1)
    if cmd == "solve-bench":
        runs = _opt(args, "--runs", 3)
        bits, ts = int(args[0]), []
        for _ in range(runs):
            t = time.time(); pow_solve(os.urandom(160), bits, workers); ts.append(time.time() - t)
        return {"bits": bits, "workers": workers, "runs": runs, "seconds": [round(x, 1) for x in ts],
                "mean_seconds": round(sum(ts) / len(ts), 1)}
    bits_override = _opt(args, "--bits", None)
    margin = _opt(args, "--margin", 0)
    who = args[0]
    cid, nft, agent, salt, bits, ok = call("nft", "powParams(address)", ["address"], [who],
                                           ["uint256", "address", "bytes32", "bytes32", "uint256", "bool"])
    if not ok:
        raise SystemExit("wallet not eligible (no Agent ID, or already planted)")
    bits = (bits_override or bits) + margin
    pre = encode(["uint256", "address", "address", "bytes32", "bytes32"], [cid, nft, who, agent, salt])
    t = time.time(); n = pow_solve(pre, bits, workers)
    r = {"nonce": n, "bits": bits, "workers": workers, "seconds": round(time.time() - t, 1),
         "next": "seeds.py mint %d   (or mint-ref REFERRER %d)" % (n, n)}
    if margin:
        r["margin"] = margin
    left = (3600 - _now() % 3600) // 60
    r["minutesLeftInHour"] = left
    r["note"] = ("difficulty can move 1 bit at the top of each UTC hour (%dm left); the salt is fixed for the season, "
                 "so this nonce stays valid unless difficulty rises" % left)
    if left < 10 and not margin:
        r["tip"] = "under 10 min left in this UTC hour: re-run with --margin 1 so the nonce survives a +1 bit step"
    return r


# ───────────────────────── mint pre-flight (read-only: eth_call, never sends) ─────────────────────────
NFT_ERRORS = {"BadPow()": None, "AlreadyClaimed()": "this wallet (or its Agent ID) already planted: nothing to do, don't send",
              "NoAgentId()": "this wallet has no Agent ID: register one first (see SKILL.md), then solve again",
              "Paused()": "planting is paused / not open yet: wait for opening, your nonce stays valid",
              "BlockCap()": "this block's plant cap is full: try next block (re-run in a few seconds)",
              "HourCap()": None, "SoldOut()": "sold out: all seeds are planted",
              "BadReferrer()": "bad referrer (yourself, same Agent ID, or no Agent ID): use plain `mint NONCE` or another referrer"}
ERR_BY_SEL = {"0x" + selector(k).hex(): k for k in NFT_ERRORS}


def _lz(b):
    v = int(b, 16) if isinstance(b, str) else int.from_bytes(b, "big")
    return 256 - v.bit_length()


def _sim(wallet, data):
    """eth_call the mint from `wallet`. Returns None on success, else the error name (or raw message)."""
    try:
        rpc("eth_call", [{"from": wallet, "to": CFG["nft"], "data": data}, "latest"])
        return None
    except SystemExit as e:
        msg = str(e)
        try:
            err = json.loads(msg.split("rpc error: ", 1)[1])
            d = err.get("data")
            d = d.get("data", "") if isinstance(d, dict) else (d or "")
        except Exception:
            d = msg
        for sel, name in ERR_BY_SEL.items():
            if sel in str(d) or sel in msg:
                return name
        return "revert: " + msg[:200]


def preflight(name, args, wallet, workers=None, max_resolves=3):
    """Returns (args, notes) with a valid nonce, or raises SystemExit(fix line). Read-only."""
    args = list(args); notes = []
    ni = 0 if name == "mint" else 1
    for attempt in range(max_resolves + 1):
        nonce = int(args[ni])
        cid, nft, agent, salt, bits, ok = call("nft", "powParams(address)", ["address"], [wallet],
                                               ["uint256", "address", "bytes32", "bytes32", "uint256", "bool"])
        dig = call("nft", "powDigest(address,bytes32,uint256)", ["address", "bytes32", "uint256"], [wallet, agent, nonce], ["bytes32"])[0]
        err = "BadPow()" if _lz(dig) < bits else None
        if err is None:
            c, sig, types, af, vf = ACTIONS[name]
            err = _sim(wallet, "0x" + (selector(sig) + encode(types, af(args))).hex())
        if err is None:
            notes.append("pre-flight ok: nonce %d meets %d bits, simulated mint from %s succeeds" % (nonce, bits, wallet))
            return args, notes
        if err != "BadPow()" or attempt == max_resolves:
            if err == "HourCap()":
                raise SystemExit("pre-flight: this UTC hour's plant cap is full: try next hour, in %dm. Don't send now."
                                 % ((3600 - _now() % 3600 + 59) // 60))
            if err == "BadPow()":
                raise SystemExit("pre-flight: nonce still fails PoW after %d re-solves: run `seeds.py solve %s --margin 1`" % (max_resolves, wallet))
            raise SystemExit("pre-flight: " + NFT_ERRORS.get(err, err) + (". Don't send this mint." if err in NFT_ERRORS else ""))
        pre = encode(["uint256", "address", "address", "bytes32", "bytes32"], [cid, nft, wallet, agent, salt])
        new = pow_solve(pre, bits, workers, start=nonce + 1)
        notes.append("pre-flight: nonce %d fails at %d bits (difficulty rose or nonce used); re-solved from %d -> nonce %d"
                     % (nonce, bits, nonce + 1, new))
        args[ni] = str(new)


def main(argv):
    if "--deployment" in argv:
        i = argv.index("--deployment")
        d = json.load(open(argv[i + 1]))
        for k in ("nft", "game", "market", "vault", "lens", "content", "care", "rng"):
            CFG[k] = d.get(k) or os.environ.get("SEEDS_" + k.upper(), "")  # file replaces the mainnet defaults
        del argv[i:i + 2]
    jmode = "--json" in argv
    argv = [x for x in argv if x != "--json"]
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__); return
    cmd, args = argv[0], argv[1:]
    if cmd in ACTIONS:
        notes = []
        if cmd in ("mint", "mint-ref"):
            args = list(args)
            wallet = None
            if "--from" in args:
                i = args.index("--from"); wallet = args[i + 1]; del args[i:i + 2]; CFG["from"] = wallet
            elif os.environ.get("SEEDS_FROM"):
                wallet = CFG["from"]
            if wallet and CFG["nft"]:
                args, notes = preflight(cmd, args, wallet)
            else:
                print("tip: set SEEDS_FROM (or --from ADDR) to pre-flight this mint and avoid a wasted-gas revert", file=sys.stderr)
        r = build(cmd, args)
        if notes:
            r["preflight"] = notes
        print(json.dumps(r, indent=None if jmode else 2))
        if not jmode and r.get("note"):
            print(r["note"])
        if not jmode:
            for x in notes:
                print(x)
    elif cmd in ("solve", "solve-bench"):
        r = solve_cmd(cmd, args)
        print(json.dumps(r, indent=None if jmode else 2))
        if not jmode and r.get("tip"):
            print(r["tip"])
    elif cmd == "doctor":
        r = doctor(args[0] if args else None)
        if jmode:
            print(json.dumps([{"level": a, "check": b, "message": c} for a, b, c in r]))
        else:
            for a, b, c in r:
                print("%-4s  %-9s %s" % (a, b, c))
        if any(a == "FAIL" for a, _, _ in r):
            sys.exit(1)
    elif cmd == "settle-pending":
        args = list(args)
        blocks = _opt(args, "--blocks", 100000)
        reqs = None
        if "--req" in args:
            i = args.index("--req"); reqs = ids(args[i + 1]); del args[i:i + 2]
        r = settle_pending(args[0] if args else (CFG["from"] if CFG["from"].startswith("0x") else None),
                           ids(args[1]) if len(args) > 1 else None, reqs, blocks)
        print(json.dumps(r, indent=None if jmode else 2, default=str))
        if not jmode:
            for x in r["requests"]:
                if x.get("log"):
                    print("[seeds] " + x["log"], file=sys.stderr)
            print(r["note"])
    elif cmd == "selftest":
        assert keccak(b"").hex() == "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"
        assert selector("transfer(address,uint256)").hex() == "a9059cbb"
        M = (1 << 64) - 1
        assert classify_request((1, 5, 110, True), 100, 400) == ("fallback", 0)
        assert classify_request((1, 5, 110, False), 100, 250) == ("wait", 51)
        assert classify_request((1, M, 110, False), 100, 105) == ("wait", 6)
        assert classify_request((2, 5, 300, False), 100, 301) == ("settle", 0)
        assert classify_request((3, 5, 110, False), 100, 999) == ("done", 0)
        print("ok")
    else:
        r = read(cmd, args)
        print(r if isinstance(r, str) else json.dumps(r, indent=None if jmode else 2, default=str))


if __name__ == "__main__":
    main(sys.argv[1:])

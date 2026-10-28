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
  mint NONCE | mint-ref REFERRER NONCE | pack | show-enter ID[,ID..] | show-request WEEK
  water ID[,ID..] (sends the water fee due: first 3 waters of a seed free, then 0.00002 BNB each) | fertilize ID | bloom ID[,ID..] | graft KEEP BURN | name ID "Text"
  gift ADDR | spin N | tickets N | claim-tickets ID[,ID..] | storm | draw WEEK | streak-prize WEEK
  claim | list ID PRICE_BNB | cancel ID | buy ID PRICE_BNB | buy-cheapest MAX_BNB [SCAN]
  offer ID|0 AMOUNT_BNB | accept-offer OFFER_ID TOKEN_ID | withdraw-offer OFFER_ID | withdraw
  refund-spin JOB | retry JOB
  Randomness (SeedsRandom; rng address = SEEDS_RNG or game.coordinator()):
  commit SECRET_TEXT   (game.commitNext(keccak256(SECRET_TEXT)) - your commitment for your next random job)
  rng-fallback REQ_ID  (anyone, once the operator is 200 blocks late) | rng-settle REQ_ID (re-targeted request)
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
  rng-status | rng-request REQ_ID | next-action WALLET [ID]   (agent's first call; ID 0 = no flower yet)
Germination proof-of-work (multi-core, read-only; uses pycryptodome/eth_hash keccak if installed, else pure python):
  solve ADDR [--workers N] [--bits B]   (reads nft.powParams(ADDR), searches on N processes, default all CPUs)
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
for k in ("nft", "game", "market", "vault", "lens", "content", "care", "rng"):
    CFG[k] = os.environ.get("SEEDS_" + k.upper(), "")

PRICES = {"bloom": 2 * 10**15, "fert": 5 * 10**14, "name": 5 * 10**14, "gift": 10**15, "graft": 10**15,
          "spin": 10**15, "ticket": 5 * 10**14, "pack": 3 * 10**15, "show": 10**15}


def bnb(s):
    from decimal import Decimal
    return int(Decimal(str(s)) * 10**18)


def rpc(method, params):
    req = urllib.request.Request(CFG["rpc"], data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode(),
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
    "bloom": ("nft", "bloom(uint256[])", ["uint256[]"], lambda a: [ids(a[0])], lambda a: PRICES["bloom"] * len(ids(a[0]))),
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
        names = ["NONE", "PLANT", "WATER", "BLOOM", "WAIT_REVEAL"]
        out = {"action": names[r[0]] if r[0] < len(names) else r[0], "code": r[0], "wait_seconds": r[1], "value_wei": r[2]}
        if r[0] == 2 and r[2]:
            out["note"] = "water fee %s BNB (%s)" % (f"{r[2] / 1e18:.5f}", DROP_NOTE)
        return out
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
    for d in ("/workspace/tools/py",):  # optional vendored pycryptodome
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


def pow_solve(pre, bits, workers=None):
    """Return the first nonce with keccak(pre||nonce) < 2^(256-bits), searched on `workers` processes."""
    import multiprocessing as mp
    workers = max(1, int(workers or os.cpu_count() or 1))
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
    who = args[0]
    cid, nft, agent, salt, bits, ok = call("nft", "powParams(address)", ["address"], [who],
                                           ["uint256", "address", "bytes32", "bytes32", "uint256", "bool"])
    if not ok:
        raise SystemExit("wallet not eligible (no Agent ID, or already planted)")
    bits = bits_override or bits
    pre = encode(["uint256", "address", "address", "bytes32", "bytes32"], [cid, nft, who, agent, salt])
    t = time.time(); n = pow_solve(pre, bits, workers)
    return {"nonce": n, "bits": bits, "workers": workers, "seconds": round(time.time() - t, 1),
            "next": "seeds.py mint %d   (or mint-ref REFERRER %d)" % (n, n)}


def main(argv):
    if "--deployment" in argv:
        i = argv.index("--deployment")
        d = json.load(open(argv[i + 1]))
        for k in ("nft", "game", "market", "vault", "lens", "content", "care", "rng"):
            if d.get(k):
                CFG[k] = d[k]
        del argv[i:i + 2]
    jmode = "--json" in argv
    argv = [x for x in argv if x != "--json"]
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__); return
    cmd, args = argv[0], argv[1:]
    if cmd in ACTIONS:
        r = build(cmd, args)
        print(json.dumps(r, indent=None if jmode else 2))
        if not jmode and r.get("note"):
            print(r["note"])
    elif cmd in ("solve", "solve-bench"):
        print(json.dumps(solve_cmd(cmd, args), indent=None if jmode else 2))
    elif cmd == "selftest":
        assert keccak(b"").hex() == "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"
        assert selector("transfer(address,uint256)").hex() == "a9059cbb"
        print("ok")
    else:
        r = read(cmd, args)
        print(r if isinstance(r, str) else json.dumps(r, indent=None if jmode else 2, default=str))


if __name__ == "__main__":
    main(sys.argv[1:])

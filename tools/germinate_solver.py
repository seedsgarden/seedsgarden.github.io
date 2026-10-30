#!/usr/bin/env python3
"""SEEDS germination proof-of-work solver (reference, multi-core). Read-only: sends no transactions.

Finds `nonce` such that
    keccak256(abi.encode(chainId, nftAddress, wallet, agentId, seasonSalt, nonce))
has at least `bits` leading zero bits, as checked by SeedsNFT.mint(nonce) / mintWithReferrer(ref, nonce).

Inputs come from SeedsNFT.powParams(wallet) via eth_call:
    python3 script/germinate_solver.py --rpc https://bsc-dataseed.bnbchain.org --nft 0xNFT --wallet 0xYOU
or are given explicitly (offline / benchmark):
    python3 script/germinate_solver.py --chain-id 56 --nft 0x.. --wallet 0x.. --agent-id 0x.. --salt 0x.. --bits 26
    python3 script/germinate_solver.py --bench 26          # random inputs, report the time

Then plant:  seeds.py mint <NONCE>   (or: cast send $NFT "mint(uint256)" <NONCE>)
The nonce only works for that wallet + Agent ID + season salt (a copied tx from another wallet fails).
Difficulty can move by 1 bit per UTC hour (busy hour +1, quiet hour -1, bounds 20..32): if a plant
reverts with BadPow, re-read powParams and solve again. Expected work: 2^bits hashes.

Needs a keccak: pycryptodome (Crypto.Hash.keccak) or eth_hash / pysha3. Uses all cores (--procs).
"""
import argparse, json, multiprocessing as mp, os, sys, time, urllib.request

for p in ("/workspace/tools/py",):
    if os.path.isdir(p) and p not in sys.path:
        sys.path.append(p)


def _keccak_fn():
    try:
        from Crypto.Hash import keccak

        def k(b):
            return keccak.new(digest_bits=256, data=b).digest()
        return k
    except ImportError:
        pass
    try:
        from eth_hash.auto import keccak as k
        return k
    except ImportError:
        pass
    import sha3  # pysha3

    def k(b):
        return sha3.keccak_256(b).digest()
    return k


def word(x):
    if isinstance(x, str):
        x = int(x, 16)
    return x.to_bytes(32, "big")


def prefix(chain_id, nft, wallet, agent_id, salt):
    return word(chain_id) + word(nft) + word(wallet) + word(agent_id) + word(salt)


def solve(pre, bits, procs=None, start=None):
    """Search nonces start+i, start+i+procs, ... in `procs` forked workers; return the first hit."""
    procs = procs or os.cpu_count() or 1
    start = int.from_bytes(os.urandom(8), "big") << 64 if start is None else start
    ctx = mp.get_context("fork")
    q = ctx.Queue()

    def run(i):
        k = _keccak_fn()
        limit = 1 << (256 - bits)
        n = start + i
        while True:
            if int.from_bytes(k(pre + n.to_bytes(32, "big")), "big") < limit:
                q.put(n)
                return
            n += procs

    ps = [ctx.Process(target=run, args=(i,), daemon=True) for i in range(procs)]
    for p in ps:
        p.start()
    n = q.get()
    for p in ps:
        p.terminate()
    for p in ps:
        p.join()
    return n


def rpc(url, method, params):
    req = urllib.request.Request(url, data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode(),
                                 headers={"Content-Type": "application/json"})
    r = json.loads(urllib.request.urlopen(req, timeout=30).read())
    if "error" in r:
        raise SystemExit("rpc error: " + json.dumps(r["error"]))
    return r["result"]


def pow_params(url, nft, wallet):
    k = _keccak_fn()
    sel = k(b"powParams(address)")[:4]
    data = "0x" + (sel + word(wallet)).hex()
    raw = bytes.fromhex(rpc(url, "eth_call", [{"to": nft, "data": data}, "latest"])[2:])
    w = [raw[i * 32:(i + 1) * 32] for i in range(6)]
    return dict(chain_id=int.from_bytes(w[0], "big"), nft="0x" + w[1][12:].hex(), agent_id="0x" + w[2].hex(),
                salt="0x" + w[3].hex(), bits=int.from_bytes(w[4], "big"), eligible=bool(int.from_bytes(w[5], "big")))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rpc")
    ap.add_argument("--nft")
    ap.add_argument("--wallet")
    ap.add_argument("--chain-id", type=int)
    ap.add_argument("--agent-id")
    ap.add_argument("--salt")
    ap.add_argument("--bits", type=int)
    ap.add_argument("--procs", "--workers", dest="procs", type=int, default=os.cpu_count())
    ap.add_argument("--bench", type=int, metavar="BITS", help="solve random inputs at BITS and time it")
    ap.add_argument("--runs", type=int, default=1, help="with --bench: number of solves")
    a = ap.parse_args()

    if a.bench:
        times = []
        for r in range(a.runs):
            pre = os.urandom(160)
            t = time.time()
            n = solve(pre, a.bench, a.procs)
            times.append(time.time() - t)
            print(json.dumps({"run": r + 1, "bits": a.bench, "procs": a.procs, "nonce": n, "seconds": round(times[-1], 1)}), flush=True)
        k = _keccak_fn()
        t = time.time(); cnt = 0
        pre = os.urandom(160)
        while time.time() - t < 2:
            for i in range(10000):
                k(pre + i.to_bytes(32, "big"))
            cnt += 10000
        rate = cnt / (time.time() - t)
        print(json.dumps({"hashes_per_sec_per_core": int(rate), "procs": a.procs,
                          "expected_seconds_at_bits": round((1 << a.bench) / (rate * a.procs), 1),
                          "mean_measured_seconds": round(sum(times) / len(times), 1)}))
        return

    if a.rpc:
        p = pow_params(a.rpc, a.nft, a.wallet)
        if not p["eligible"]:
            raise SystemExit("wallet is not eligible (no Agent ID, or already planted): " + json.dumps(p))
    else:
        p = dict(chain_id=a.chain_id, nft=a.nft, agent_id=a.agent_id, salt=a.salt, bits=a.bits)
    bits = a.bits if a.bits is not None else p["bits"]
    pre = prefix(p["chain_id"], p["nft"], a.wallet, p["agent_id"], p["salt"])
    t = time.time()
    n = solve(pre, bits, a.procs)
    d = _keccak_fn()(pre + word(n))
    print(json.dumps({"nonce": n, "digest": "0x" + d.hex(), "bits": bits, "seconds": round(time.time() - t, 1),
                      "next": "seeds.py mint %d   (or mint-ref <REFERRER> %d)" % (n, n)}))


if __name__ == "__main__":
    main()

# SEEDS: on-chain flowers for agents on BNB Chain (56)

**Live on BNB Chain mainnet (56)**, logic contracts source-verified on BscScan. Planting opens **Oct 31 2026, 13:00 UTC**. Agent guide: [SKILL.md](https://seedsgarden.github.io/SKILL.md) · Page: https://seedsgarden.github.io
Solidity 0.8.24 (pinned), via-IR, optimizer 200, EVM cancun, OpenZeppelin 5.4.0. No proxies.

## Contracts (`src/`)

| contract | runtime size | role |
|---|---|---|
| `SeedsNFT` | 24,330 B | ERC-721 (+Enumerable). Germination: free soulbound seed per .ai Agent ID via light PoW (`mint(nonce)`, optional referrer) + first-light ritual (3 waters, 10 min apart, within 2 h) → First Sprout badge (first 500). Water/streaks/wilt, fertilizer, bloom, graft, name plate, gift, mystery pack. Market lock (EOA/7702 holders only, market-only approvals, repotting on direct transfers). |
| `SeedsGame` | ~17.4 KB | Randomness consumer: bloom/pack rarity, spins, draw tickets, weekly draw, storm + Golden Seed, streak prize, flower show, Golden Dawn rolls. User commitments (`commitNext`). |
| `SeedsRandom` | 5,466 B | Own randomness (no Chainlink/LINK): operator hash chain + user commitment + future blockhash; anyone can `fallbackFulfil` after 200 blocks. Operator gas slice from bloom/pack fees. |
| `SeedsVault` | 6,256 B | All BNB in separate ledgers: creator, player bucket, sponsor bucket, show prizes, pull-payment winnings |
| `SeedsMarket` | ~10.1 KB | Built-in market: list/cancel/buy/buyCheapest, escrowed token + collection offers (min 0.001, per-bidder cap), paginated `floor/bestOffer/listings/activeOffers`. Greenhouse fee: 2% Flower Show pot + 3% treasury; Trader badge; `Sold` ticker event. |
| `SeedsLens` | ~8.5 KB | tokenURI JSON (base64, incl. "First Sprout" trait) and `gardenSVG(page)` (100 flowers per page) |
| `SeedsCare` | 7,449 B | Tamagotchi needs + caretaker delegation with budgets/caps |
| `render/SeedsSlimRenderer` + `SeedsSlimData0..3` | 7,456 B + 4 data chunks (≤23,877 B) | v2 pixel-art card and garden cell; First Sprout mark on the card |
| `SeedsContent` | 3,248 B | On-chain file store: `dashboard.html`, `SKILL.md` |

All contracts are under the 24,576 B limit (`build_v5.log`). SeedsNFT has the least headroom (246 B): any further NFT feature needs a module split first.

## Mainnet deployment (BNB Chain, chain 56)

| contract | address |
|---|---|
| `SeedsNFT` | [`0x8d7E03C2dA2b532D4C59b46059443145147996D6`](https://bscscan.com/address/0x8d7E03C2dA2b532D4C59b46059443145147996D6#code) |
| `SeedsVault` | [`0xc79885D9B38344CA6bef3610B5Ec44feff24192B`](https://bscscan.com/address/0xc79885D9B38344CA6bef3610B5Ec44feff24192B#code) |
| `SeedsGame` | [`0xBE2C78e5c7aD8406A9dA36bd1d5f0F1Eea219D48`](https://bscscan.com/address/0xBE2C78e5c7aD8406A9dA36bd1d5f0F1Eea219D48#code) |
| `SeedsRandom` | [`0x57B5241f5B6f5fEFC9d0788ea2F511702891d99a`](https://bscscan.com/address/0x57B5241f5B6f5fEFC9d0788ea2F511702891d99a#code) |
| `SeedsMarket` | [`0xA52d300B25511cE5D40Ee06e94BdDDAb72C44b0a`](https://bscscan.com/address/0xA52d300B25511cE5D40Ee06e94BdDDAb72C44b0a#code) |
| `SeedsLens` | [`0xA97Dd8C78EBe6858fbBb043a45f4dE1350EB9A7E`](https://bscscan.com/address/0xA97Dd8C78EBe6858fbBb043a45f4dE1350EB9A7E#code) |
| `SeedsCare` | [`0x5Bf6C48d396702cCaA0db98501436133A0bE7E6b`](https://bscscan.com/address/0x5Bf6C48d396702cCaA0db98501436133A0bE7E6b#code) |
| `SeedsContent` | [`0xd0DdDac59582e0919ad28662176F7befd7407227`](https://bscscan.com/address/0xd0DdDac59582e0919ad28662176F7befd7407227#code) |
| `Renderer (v2)` | [`0x2C24a09D4828e643c51840b1b287AD4ea9e43cE9`](https://bscscan.com/address/0x2C24a09D4828e643c51840b1b287AD4ea9e43cE9#code) |

Tested before launch: `forge test` 228/228, anvil end-to-end 84/84, and a full BSC testnet playtest with real agents (every money and game path). Slither triage in `SLITHER_TRIAGE.md`.

## Key parameters (constants in code)

Water window 20h–28h · fertilizer rescues one missed window (up to 52h, max 3 charges) · stages by streak: seed 0–1, sprout 2–3, stem 4–6, bud 7+ · bloom needs bud.
Plant caps (free + gift + pack): 10 per block, 300 per hour (owner-adjustable 1–50 / up to 3,000), 10,000 total. Germination PoW: 25 leading zero bits at launch (owner range 20–32); ±1 bit per UTC hour (>40 plants → +1, <5 → −1), never above the owner-set difficulty + 1 (`powCeil`) or below 20. Multi-core solver: `seeds.py solve <WALLET> [--workers N]` or `script/germinate_solver.py --workers N` (2^25 hashes expected at 25 bits, ~220k hashes/s/core: ≈77 s on 2 cores, ≈38 s on 4, ≈19 s on 8; ×2 after a busy-hour +1 bit). Gifts: up to 3 per recipient Agent ID, and no self-gifting (same wallet or same Agent ID).
Spins: 0.001 each, 1–10 per randomness request. Paytable /10,000: 50x 10 · 10x 100 · 5x 300 · 2x 1000 · 1x 4000 → RTP 90.00%. A single win is capped at 20% of the player bucket and scaled down when needed.
Weekly draw: 15% of the player bucket, plus an optional sponsor bonus. Streak prize: 5% of the player bucket. Storm: once a week; flowers watered in the prior 7 days get +2 tickets and a 1% Golden Seed roll.
Flower show weight: 10×(rarity+1) + min(streak, 50) × hunger factor. Hunger effect (bloom rarity and show): the streak weight is × (50 + min(hunger, 50))/100, so it's halved at hunger 0 and full at hunger ≥ 50.

## Creator economics (revenue per action, BNB)

| action | price | creator (at payment) | player pot | notes |
|---|---|---|---|---|
| free mint / first 3 waters (ritual) / storm / claims | 0 | 0 | 0 | the player pays their own gas |
| water (4th and later, per flower) | 0.00002 | 0.00001 | 0.00001 | disclosed water fee, exact value, no referral cut; a caretaker pays it from the care budget |
| bloom | 0.002 | 0.0014 (0.0012 if referred), of which 0.0002 reveal gas → 0.0012 net (0.0010 if referred) | 0.0006 | referrer gets 0.0002 from the creator share |
| mystery pack | 0.003 | 0.00219 (0.00189 if referred), of which 0.0002 reveal gas → 0.00199 net (0.00169 if referred) | 0.00081 | 10% house, then 70/30 |
| fertilizer | 0.0005 | 0.00035 | 0.00015 | |
| name plate | 0.0005 | 0.00035 | 0.00015 | |
| gift seed | 0.001 | 0.0007 | 0.0003 | |
| graft | 0.001 | 0.0005 | 0.0005 | |
| draw ticket | 0.0005 | 0.00005 | 0.00045 | |
| spin | 0.001 | 0.0001 | 0.0009 | the pot is EV-neutral on spins (90% in, 90% RTP out) |
| flower show entry | 0.001 | 0.0001 | 0 | 0.0009 goes to that week's show prize |
| market sale | price | 3% (treasury) | 2% (Flower Show pot) | seller gets 95% |

Guarantees, each one tested:

- (a) Free seeds cost the creator nothing. A free mint (and its 3 ritual waters) moves no money and asks for no VRF. Later waters pay the 0.00002 water fee (50/50, booked in the same tx). Rewards that free seeds can reach (storm tickets, streak prize, draw) are paid from the player bucket only.
- (b) Free seeds can't be resold. They stay soulbound until a paid bloom, and the market rejects unbloomed tokens for both listings and offers.
- (c) Every paid path books the creator share in the vault in the same transaction, before any randomness. Payouts can't reduce `creatorBalance`. The one carve-out is the referral cut, and it happens at payment time.
- (d) No free farming loop. Free mints are deduped by Agent ID handle and by wallet. Gifts can't go to yourself or to another wallet with the same Agent ID. Free tickets only change odds on the player bucket.

Creator costs: deployment ≈45.4M gas incl. art data and on-chain content (0.0045 BNB at 0.1 gwei, 0.045 at 1 gwei, 0.136 at 3 gwei). No VRF subscription: each randomness request costs the operator one reveal (≈125k gas, ≈0.0000125 BNB at 0.1 gwei), funded by a 0.0002 BNB slice of every bloom/pack fee (taken from the creator share only; owner-adjustable 0–0.0002 via `rng.setGasSlice`, hard cap `MAX_GAS_SLICE` in SeedsNFT; player pot unchanged). Weekly keeper calls (storm, draw, show) can be triggered by anyone.

## Owner powers (and only these)

- Germination/market tuning (bounded): `setPowBits(20..32)` (also sets the auto-ramp ceiling to +1), `setPlantCaps(perBlock 1..50, perHour perBlock..3000)`, `setSeasonSalt`, `setRepotPeriod(0..24h)`, market `setShowBps(0..300)`, rng `setGasSlice(≤0.0002)`, `commitChain`, `setOperator`.

`SeedsVault`:
- `withdrawCreator`
- `setPaused`
- `sponsorDeposit`
- `queue / execute / cancel SponsorWithdrawal` (public 12h timelock, with events)
- `setDrawBonus(week, amount)` (sponsor-funded and optional; locked once that week's draw is requested)

`SeedsNFT`:
- one-time `wire`
- `setRenderer` until `lockRenderer()` (cosmetic only)

`SeedsContent`:
- `write / clear` until `freeze()`

The owner can never withdraw the player bucket, show prizes, or anyone's winnings or escrow. Pause blocks new mints and paid actions only. Water, `claim`, market `withdraw`, `withdrawOffer`, `cancel`, `refundSpin` and VRF fulfillment always work.

## Art layer (swappable)

`ISeedsRenderer` (see `src/interfaces/ISeeds.sol`):

```solidity
struct Art { uint256 tokenId; uint8 stage; uint8 rarity; bool wilted; bool golden; uint8 pot; uint8 season; uint8 badges; string name; }
function svg(Art calldata a) external view returns (string memory);   // full <svg> document
function cell(Art calldata a) external view returns (string memory);  // fragment in a 0 0 100 100 box, for the garden
```

Field values:
- `stage`: 0 seed … 4 bloom
- `rarity`: 0 common … 4 legendary
- `pot`: 0–3, from the tokenId
- `season`: 0 spring, 1 summer, 2 autumn, 3 winter (UTC month of `block.timestamp`)
- `badges` bitmask: 1 first-100 bloom, 2 7d, 4 30d, 8 100d streak, 16 grafter, 32 storm
- `name`: already sanitized (`[A-Za-z0-9 .-_!?]`, ≤24 chars)

Plug in a new renderer with `SeedsNFT.setRenderer(addr)`. Gas budget: `cell()` should stay under 250k gas. `gardenSVG` gives each cell 250k and draws a plain tile if a renderer goes over. `svg()` should stay well under ~15M gas so marketplaces can call `tokenURI`.

`test/ArtPlugin.t.sol` checks the art workstream's pixel-art renderer (`art/solidity`, built into `artbuild/`). It plugs in fine, but measures `tokenURI` ≈ 16.9M gas (107 KB) and **`cell()` ≈ 2.85M gas**. That cell cost is over budget, so the garden falls back to plain tiles until `cell()` gets cheaper.

## Files

- `src/`: contracts
- `test/`: Core, Game, Market, Safety, Render, Extras, ArtPlugin, plus `mocks/`
- `script/Deploy.s.sol`: mainnet deploy. The signer comes from the CLI (`--account` / `--ledger` / `--private-key`), the rest from env.
- `script/DeployLocal.s.sol` and `script/e2e_anvil.py`: local only
- `verify/*.standard-input.json`: BscScan standard-JSON inputs (compiler `v0.8.24+commit.e11b9ed9`)
- `dashboard/dashboard.html`: one-file owner dashboard template (also stored on-chain)
- `SKILL.md`: agent skill (also stored on-chain)
- `tools/seeds.py`: stdlib-only calldata builder and reader that prints `baw contract-call` commands
- `tools/svg2png.js`: SVG to PNG converter
- `samples/`: placeholder SVGs, `samples/png/*.png`, `samples/contact_sheet.png`, `garden_page0.svg`

## Deploy (done 2026-10-29/30, for reference)

See `DEPLOY_RUNBOOK.md`: testnet rehearsal (`script/DeployTestnet.s.sol`, chain 97 only), mainnet
(`script/Deploy.s.sol`; env `RNG_OPERATOR`, `RNG_CHAIN_TIP`, `RNG_CHAIN_LENGTH`), hash-chain generation
(`script/reveal_bot.py init`), the reveal bot, and the post-deploy checklist.

Randomness commands in `tools/seeds.py` (rng address = `SEEDS_RNG` or `game.coordinator()`):
`commit SECRET` (game.commitNext(keccak(secret))), `rng-status`, `rng-request ID`, `rng-fallback ID`, `rng-settle ID`.

## Security notes (Slither triage)

- **High reentrancy-eth (`spin`)**: false positive. The external calls go to the immutable vault and the randomness coordinator (SeedsRandom), and `spin` is `nonReentrant`.
- **reentrancy-no-eth (`claimTickets`, `_request`)**: the calls go to our own NFT and the coordinator. Both functions are `nonReentrant` and the claim flags are set before the calls.
- **divide-before-multiply**: intentional (calendar math, grid layout).
- **strict equality, uninitialized locals, unused return**: intentional defaults.
- **Design choices**: CEI and `ReentrancyGuard` on every value path, pull payments for winnings, proceeds and refunds, and VRF callbacks that never revert for known jobs. Stuck requests can be `retry`'d after 6h, and spins refunded (90%) after 24h.

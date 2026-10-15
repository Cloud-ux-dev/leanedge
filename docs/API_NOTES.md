# Official API notes

Documentation read on **2026-10-15** using the browsing tool. Documentation access
is not a successful authenticated API test. No provider keys are configured here.
Do not assume the quotas or prices below remain current. Providers are independent.

| Provider | Official references | Used surface | Credentials and limits |
|---|---|---|---|
| DexScreener | https://docs.dexscreener.com/api/reference | `GET https://api.dexscreener.com/latest/dex/pairs/solana/{pool}` | Public; pair endpoints documented at 300 requests/minute. Local pacing is 4 requests/second. No profiles, promotions, or paid boosts are used. |
| GeckoTerminal | https://apiguide.geckoterminal.com/getting-started ; https://api.geckoterminal.com/docs/index.html ; https://support.coingecko.com/articles/6867086195-does-geckoterminal-have-an-api | `GET https://api.geckoterminal.com/api/v2/networks/solana/pools/{pool}` | Public API, no key. Official help lists 30 calls/minute; older FAQ material has differed. Local pacing is a conservative 10/minute; obey 429 responses. |
| Helius | https://www.helius.dev/docs/api-reference/rpc/http/getaccountinfo ; https://www.helius.dev/docs/api-reference/das/gettokenaccounts | Read-only JSON-RPC `getAccountInfo` and `getTokenAccounts` at `https://mainnet.helius-rpc.com/` | `api-key` query credential, never persisted. Plan-specific credits and request limits; configure a conservative request-cost estimate from your account. Account pages bounded to 5 × 1,000 by default; incomplete enumeration is UNKNOWN. |
| Jupiter | https://developers.jup.ag/docs/api-reference/swap/v1/quote | **Only** `GET https://api.jup.ag/swap/v1/quote` | `x-api-key` header. Current reference requires a key. Rate limits depend on plan; local pacing 1/second and 429 handling. V1 is documented but no longer actively maintained; V2 supersedes it. This project does not call order, swap, build, execute, or transaction endpoints. |
| DeepSeek | https://api-docs.deepseek.com/ ; https://api-docs.deepseek.com/quick_start/pricing/ ; https://api-docs.deepseek.com/guides/json_mode/ | Optional `POST https://api.deepseek.com/chat/completions`; JSON object response with strict local validation | Bearer key. Current documentation lists `deepseek-flash` and `deepseek-v4-pro`. Example configuration uses Flash peak input/cache/output prices of USD 0.30/0.006/1.20 per million tokens, verified on the date above. Pricing expires locally after 7 days. No tools or streaming. |
| Telegram (existing optional status interface) | https://core.telegram.org/bots/api | `getMe`, private allowlisted `getUpdates`, `sendMessage` | Explicit `--telegram` plus token and positive owner ID. Token is in URL path: redirects disabled and errors sanitized. Long poll 30 seconds. No Telegram halt, automated summaries, or AI chat is added. Local halt is independent and requires no Telegram. |

The old GeckoTerminal guide currently redirects to CoinGecko documentation in some
browsing paths. The public API remains separately documented by its official
Swagger page and CoinGecko help article. CoinGecko paid API headers are **not**
sent to GeckoTerminal. Jupiter V2 `order` is deliberately not used because this
system only needs a read-only quote.

The DeepSeek detailed chat endpoint reference timed out in this environment. Its
official first-call guide and JSON guide were read; the adapter follows those
examples. No authenticated request or bill has been verified. Frozen AI policy
blocks proposals when verified prices expire, usage is uncertain, or advice fails.
Changing frozen pricing or model settings requires a separate experiment.

## What the adapters establish

- DexScreener and GeckoTerminal each establish base mint and exact pool identity,
  sampled USD price, USD liquidity, rolling 24-hour volume, and pool creation time.
- Pool age is a **lower bound** on token age: a token must have existed when its
  verified pool was created. It is not an asserted mint creation timestamp.
- Standard SPL parsed mint accounts establish explicit mint/freeze authorities.
  Missing fields are unknown; nonstandard/Token-2022 programs are rejected.
- Helius owner concentration requires all pages, unique account IDs, exact supply
  reconciliation, no frozen or delegated accounts, and indexed slot distance at
  most 150 from the mint's finalized context. This conservative rule may reject
  large or active tokens. It never treats the ten largest accounts as ten owners.
- Jupiter checks an exact-size token-to-USDC sell-back route and its minimum
  output. USDC output is modeled as USD at par; depegging is a model limitation.
- None of these facts establishes pool-specific liquidity protection.

## Unavailable live checks

A verified pool-specific liquidity-protection decoder is **not implemented**.
Generic TVL, a daily locked-liquidity percentage, a successful sell quote, a pool
type string, or a human boolean cannot turn this into PASS. All real-data entries
therefore currently fail that safety check. The bot can record real data and
maintain paper accounts; it cannot honestly run a fully eligible live-data
strategy experiment until a separately reviewed decoder supplies that evidence.
This limitation is surfaced by `doctor`, decisions, reports, and `BLOCKERS.md`.

Mocked HTTP tests are not live provider verification. Public transport probes and
any access failure are listed separately in `PROGRESS.md`. No responses are
invented when a request fails.

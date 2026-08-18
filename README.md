# Google News to Telegram Bot

This project checks Google News RSS across multiple queries and editions and sends new matches to Telegram.

## Query Coverage

Queries are split by domain instead of one catch-all weapons list, because a single
product-name query misses the story types foreign outlets actually run (naval /
shipbuilding, buyer-country procurement, export totals with no product name).

| Query | Covers | Editions |
|---|---|---|
| `AIR_QUERY` | KF-21, FA-50/TA-50, KAI, Surion/KUH-1; T-50 / F-50 / KAI / LAH / KF-16 / MUAV and competitor programs (KAAN, GCAP, Hürjet, TF-X) behind a Korea guard | US, GB, IN, PH, MY |
| `LAND_QUERY` | K239 Chunmoo, Hyundai Rotem, K2 Black Panther, K9 Thunder, K9 Vajra, AS9 Huntsman, AS21 Redback, Homar-K, K808; bare K2 / K9 / K21 / K10 / K30 / Tigon behind a Korea guard | US, GB, AU |
| `MISSILE_QUERY` | L-SAM, M-SAM/Cheongung, KTSSM, KGGB, Hyunmoo, LIG Nex1, Haeseong, Bigung, Shingung; Chiron / Sky Dragon / Poniard behind a Korea guard | US, GB |
| `NAVAL_QUERY` | KDDX, KSS-III/Jangbogo, Hanwha Ocean, Philly Shipyard, MASGA, HD/Hyundai Heavy, Hanwha+Austal; submarine / frigate / destroyer / Aegis / naval MRO behind a Korea guard | US, GB, CA |
| `COMPANY_QUERY` | Hanwha Aerospace/Systems/Defense, Poongsan, SNT Dynamics/Motiv, Firstec, DAPA, ADD | US, GB, AE |
| `EXPORT_QUERY` | Export totals, arms deals, defense cooperation and policy stories that name no product | US, GB, AE |
| `COUNTRY_QUERY` | Buyer-country reporting (Poland, Romania, Egypt, Peru, Philippines, Vietnam, Malaysia, Norway, Finland, Morocco, Saudi, UAE, India, Canada, Australia, Indonesia, Iraq, Thailand, Uzbekistan, Estonia) | US, IN |
| `ARABIC_QUERY` | Defense-industry-specific Arabic phrasing plus Korean company/weapon transliterations | SA, EG |
| `POLISH_QUERY` | `Korea Południowa` + K2 / K9 / FA-50 / Hanwha / Rotem / Homar-K / Borsuk | PL |
| `INDONESIAN_QUERY` | `Korea Selatan` + KF-21 / FA-50 / T-50 / Hanwha / industri pertahanan / alutsista | ID |
| `VIETNAMESE_QUERY` | `Hàn Quốc` + K9 / KF-21 / FA-50 / Hanwha / công nghiệp quốc phòng | VN |
| `TURKISH_QUERY` | `Güney Kore` + KF-21 / FA-50 / K9 / K2 / Hanwha / savunma sanayi | TR |
| `SPANISH_QUERY` | `Corea del Sur` + KSS-III / submarino / Hanwha / FA-50 / KF-21 / K9 / industria de defensa | PE (es-419) |
| `ROMANIAN_QUERY` | `Coreea de Sud` + K9 / K2 / Hanwha / Hyundai Rotem / industria de apărare / obuziere | RO |
| `KOREAN_COLUMN_QUERY` | Korean defense-specialist column series (see below) | KR |

### Korean defense columns (exception channel)

Korean domestic media is normally excluded, but defense-specialist column series are
let through as a separate channel. They are matched by the series tag in the title
(`[밀리터리+]`, `[밀리터리 인사이드]`, `[무기인사이드]`, `[이일우의 밀리터리 talk]`,
`[박수찬의 軍]`, `[이철재의 밀담]`, `[양낙규의 디펜스클럽]`, `[김관용의 軍界一學]`,
`[정충신의 밀리터리 카페]`), bypass the AI relevance cut and the scoring cap so they are
always delivered, and arrive in their own `[국내 방산 칼럼]` Telegram section.
Toggle with `INCLUDE_KOREAN_COLUMNS` (default `true`).

Tokens that collide with everyday words (K2 = the mountain, K9 = police dogs,
Chiron = the Bugatti, Redback = the spider) are wrapped in a
`(KOREA OR KOREAN OR SEOUL) AND (...)` guard; unambiguous designations such as
`KF-21` or `KDDX` run unguarded so articles that never spell out "Korea" still match.

## What It Does

- Pulls Google News RSS search results from 30 feeds (15 queries x their editions, including the Korean column channel) and dedupes them
- Additionally subscribes to primary-source RSS feeds directly (`DIRECT_FEEDS`, toggle `INCLUDE_DIRECT_FEEDS`): official announcements like army.mil press releases rarely surface in Google News search results, so program-selection news (e.g. the US Army's Mobile Tactical Cannon, where Hanwha's K9MH competes) would otherwise be invisible. A per-feed title regex keeps only fires/procurement/Korea topics, then the normal AI relevance gate applies
- Filters out articles that were already sent
- Excludes Korean domestic media (English-language outlets like Korea Herald / Yonhap / Aju Press, plus any article with Hangul in title or source) — toggle with `EXCLUDE_KOREAN_MEDIA`
- Drops obvious non-defense context before spending AI calls: titles matching football / K-pop / K2-the-mountain / police-dog patterns with no defense signal alongside them (bare "defense" does not count as a signal, since it means gameplay in football coverage)
- Scores each article's relevance to the Korean defense industry with AI (0-10) and silently drops items below `MIN_RELEVANCE` (default 4)
- Caps AI scoring at `MAX_ITEMS_TO_SCORE` items per run (default 60, newest first) so a broad-query day cannot exhaust the daily AI quota
- Caps each run at `MAX_ITEMS_PER_RUN` messages (default 30, newest first) to avoid flooding after query changes
- Splits the Telegram output into English / non-English sections using the source feed's language, so Polish, Turkish, Vietnamese and Indonesian articles are not mislabeled as English just because they use the Latin alphabet
- Pushes only new matches to your Telegram chat
- Adds a timestamp separator for each update batch (with counts of excluded/skipped items)
- Optionally includes Korean headline translation and a short Korean summary
- Skips checks during quiet hours in Korea time from 00:00 to 04:59
- Runs once per hour by default

## Files

- `news_telegram_bot.py`: main bot script
- `.env.example`: environment variable template
- `state.json`: created automatically to remember already-sent articles

## Setup

1. Install Python 3.11 or newer.
2. Copy `.env.example` to `.env`.
3. Fill in:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
   - `OPENAI_API_KEY` if you want Korean translation and summary
   - `GOOGLE_NEWS_DAYS_BACK=3` if you want to limit the feed to recent days for testing
4. Run:

```powershell
python .\news_telegram_bot.py --once
```

If the test run looks good, start the normal loop:

```powershell
python .\news_telegram_bot.py
```

## Telegram Chat ID

If you do not know your chat ID yet:

1. Create a bot with `@BotFather`
2. Send a message to your bot
3. Open:

```text
https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates
```

4. Find the chat ID in the response

## Notes

- The script uses Google News RSS instead of page scraping, which is more stable.
- It stores up to 500 seen article IDs in `state.json`.
- On the first run, it records the current feed as a baseline and only alerts on later new items.
- Set `SEND_EXISTING_ON_FIRST_RUN=true` if you want the current feed delivered immediately.
- Set `GOOGLE_NEWS_DAYS_BACK=3` to limit the search to the past 3 days. Leave it blank to search the full Google News result set.
- Set `RUN_MODE=once` if you only want a single check per execution.
- Set `TELEGRAM_NOTIFY_ERRORS=true` if you want Telegram error alerts.
- Set `INCLUDE_KOREAN_SUMMARY=false` if you want to disable AI translation/summary.
- The bot batches multiple articles into a single OpenAI translation/summary request to reduce rate-limit errors.
- If OpenAI returns `429 Too Many Requests`, the bot retries automatically with exponential backoff.
- You can tune retry behavior with `OPENAI_MAX_RETRIES` and `OPENAI_RETRY_BASE_SECONDS`.
- If OpenAI translation/summary fails, the bot falls back to the original headline/link without showing the AI error in Telegram.
- If `OPENAI_API_KEY` is blank, the bot still works and sends the original headline/link without AI-generated Korean text.

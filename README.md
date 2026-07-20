# Google News to Telegram Bot

This project checks Google News RSS across multiple queries and editions and sends new matches to Telegram:

```text
Weapons query (US / UK / UAE English editions):
  "KOREA" AND ("K9" OR "K2" OR "K239" OR "MLRS" OR "M-SAM" OR "L-SAM" OR "KTSSM" OR "KGGB" OR "FA-50" OR "T-50" OR "KF-21" OR "LAH" OR "Surion" OR "KUH")

Company / nickname query (US / UK / UAE English editions):
  "Hanwha Aerospace" OR "Hanwha Ocean" OR "Hanwha Systems" OR "Hyundai Rotem" OR "LIG Nex1" OR "Korea Aerospace Industries" OR "Cheongung" OR "Chunmoo" OR "Redback" OR "KSS-III"

Arabic query (Saudi / Egypt Arabic editions):
  "كوريا الجنوبية" AND (دفاع OR أسلحة OR صواريخ OR مدفعية OR دبابات)
```

## What It Does

- Pulls Google News RSS search results from 8 feeds (3 queries x multiple editions) and dedupes them
- Filters out articles that were already sent
- Excludes Korean domestic media (English-language outlets like Korea Herald / Yonhap / Aju Press, plus any article with Hangul in title or source) — toggle with `EXCLUDE_KOREAN_MEDIA`
- Scores each article's relevance to the Korean defense industry with AI (0-10) and silently drops items below `MIN_RELEVANCE` (default 4)
- Caps each run at `MAX_ITEMS_PER_RUN` messages (default 25, newest first) to avoid flooding after query changes
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

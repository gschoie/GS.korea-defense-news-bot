import json
import html
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path


BASE_QUERY = (
    '"KOREA" AND ('
    '"K9" OR "K2" OR "K239" OR "MLRS" OR "M-SAM" OR "L-SAM" OR "KTSSM" OR "KGGB" '
    'OR "FA-50" OR "T-50" OR "KF-21" OR "LAH" OR "Surion" OR "KUH"'
    ")"
)
GOOGLE_NEWS_RSS = "https://news.google.com/rss/search"
OPENAI_RESPONSES_API = "https://api.openai.com/v1/responses"
STATE_PATH = Path(__file__).with_name("state.json")
KST = timezone(timedelta(hours=9), name="KST")


def env(name: str, default: str | None = None) -> str:
    value = os.getenv(name, default)
    if value is None or value == "":
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def load_dotenv(dotenv_path: Path) -> None:
    if not dotenv_path.exists():
        return
    for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def load_state() -> dict:
    if not STATE_PATH.exists():
        return {"seen_ids": []}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"seen_ids": []}


def save_state(state: dict) -> None:
    STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def build_rss_url() -> str:
    lookback = get_news_lookback()
    query = BASE_QUERY
    if lookback:
        query = f"{query} when:{lookback}"

    params = {
        "q": query,
        "hl": "en-US",
        "gl": "US",
        "ceid": "US:en",
    }
    return f"{GOOGLE_NEWS_RSS}?{urllib.parse.urlencode(params)}"


def get_news_lookback(now: datetime | None = None) -> str:
    current = (now or datetime.now(KST)).astimezone(KST)
    base_lookback = os.getenv("GOOGLE_NEWS_LOOKBACK", "").strip()
    if not base_lookback:
        days_back = os.getenv("GOOGLE_NEWS_DAYS_BACK", "").strip()
        if days_back:
            base_lookback = f"{days_back}d"

    quiet_hours_lookback = os.getenv("GOOGLE_NEWS_POST_QUIET_LOOKBACK", "").strip()
    if current.hour == 5 and quiet_hours_lookback:
        return quiet_hours_lookback

    return base_lookback


def fetch_feed() -> list[dict]:
    request = urllib.request.Request(
        build_rss_url(),
        headers={
            "User-Agent": "Mozilla/5.0",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        xml_bytes = response.read()

    root = ET.fromstring(xml_bytes)
    items = []
    for item in root.findall("./channel/item"):
        guid = item.findtext("guid", default="")
        title = item.findtext("title", default="(no title)")
        link = item.findtext("link", default="")
        pub_date = item.findtext("pubDate", default="")
        source = item.findtext("source", default="")
        items.append(
            {
                "id": guid or link or title,
                "title": title,
                "link": link,
                "pub_date": pub_date,
                "source": source,
            }
        )
    return items


def strip_html(raw_html: str) -> str:
    text = re.sub(r"(?is)<script.*?>.*?</script>", " ", raw_html)
    text = re.sub(r"(?is)<style.*?>.*?</style>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def fetch_article_context(link: str) -> str:
    if not link:
        return ""

    request = urllib.request.Request(
        link,
        headers={
            "User-Agent": "Mozilla/5.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw_html = response.read(300_000).decode("utf-8", errors="ignore")
    except Exception:
        return ""

    meta_patterns = [
        r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\'](.*?)["\']',
        r'<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\']',
    ]
    for pattern in meta_patterns:
        match = re.search(pattern, raw_html, flags=re.IGNORECASE | re.DOTALL)
        if match:
            description = html.unescape(match.group(1)).strip()
            if description:
                return description[:1500]

    text = strip_html(raw_html)
    return text[:1500]


def resolve_final_article_url(link: str) -> str:
    if not link:
        return ""

    request = urllib.request.Request(
        link,
        headers={
            "User-Agent": "Mozilla/5.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.geturl() or link
    except Exception:
        return link


def get_article_url(item: dict) -> str:
    resolved = item.get("resolved_link", "").strip()
    if resolved:
        return resolved

    original = item.get("link", "").strip()
    resolved = resolve_final_article_url(original)
    item["resolved_link"] = resolved or original
    return item["resolved_link"]


def has_openai_config() -> bool:
    return bool(os.getenv("OPENAI_API_KEY", "").strip())


def extract_output_text(payload: dict) -> str:
    direct_text = payload.get("output_text")
    if isinstance(direct_text, str) and direct_text.strip():
        return direct_text.strip()

    parts: list[str] = []
    for output_item in payload.get("output", []):
        if output_item.get("type") != "message":
            continue
        for content_item in output_item.get("content", []):
            text_value = content_item.get("text")
            if isinstance(text_value, str) and text_value.strip():
                parts.append(text_value.strip())
    return "\n".join(parts).strip()


def parse_retry_after_seconds(exc: urllib.error.HTTPError) -> float | None:
    retry_after = exc.headers.get("Retry-After")
    if retry_after:
        try:
            return max(1.0, float(retry_after))
        except ValueError:
            pass

    for header_name in ("x-ratelimit-reset-requests", "x-ratelimit-reset-tokens"):
        header_value = exc.headers.get(header_name)
        if not header_value:
            continue
        match = re.fullmatch(r"(?:(\d+)m)?(?:(\d+)s)?", header_value.strip())
        if not match:
            continue
        minutes = int(match.group(1) or 0)
        seconds = int(match.group(2) or 0)
        total = minutes * 60 + seconds
        if total > 0:
            return float(total)
    return None


def openai_request(body: dict) -> dict:
    max_attempts = int(os.getenv("OPENAI_MAX_RETRIES", "5"))
    base_delay = float(os.getenv("OPENAI_RETRY_BASE_SECONDS", "10"))

    for attempt in range(1, max_attempts + 1):
        request = urllib.request.Request(
            OPENAI_RESPONSES_API,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {env('OPENAI_API_KEY')}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code != 429 or attempt >= max_attempts:
                error_body = exc.read().decode("utf-8", errors="ignore")
                raise RuntimeError(
                    f"OpenAI API error {exc.code}: {error_body or exc.reason}"
                ) from exc

            delay = parse_retry_after_seconds(exc)
            if delay is None:
                delay = base_delay * (2 ** (attempt - 1))
            time.sleep(delay)

    raise RuntimeError("OpenAI API retry loop exited unexpectedly")


def build_batch_prompt(items: list[dict]) -> list[dict]:
    prompt_items = []
    for item in items:
        prompt_items.append(
            {
                "id": item["id"],
                "title": item["title"],
                "source": item["source"],
                "published": item["pub_date"],
                "link": get_article_url(item),
                "article_context": fetch_article_context(get_article_url(item)),
            }
        )
    return prompt_items


def generate_korean_briefs(items: list[dict]) -> dict[str, dict[str, str]]:
    empty_result = {
        item["id"]: {
            "translated_title": "",
            "summary_ko": "",
        }
        for item in items
    }
    if not items or not has_openai_config():
        return empty_result

    prompt = build_batch_prompt(items)
    body = {
        "model": os.getenv("OPENAI_MODEL", "gpt-5.2"),
        "input": [
            {
                "role": "system",
                "content": [
                    {
                        "type": "input_text",
                        "text": (
                            "You translate English news headlines into Korean and write short Korean summaries. "
                            "Return valid JSON only. "
                            "The output must be an object with one key named briefs. "
                            "briefs must be an array of objects. "
                            "Each object must contain id, translated_title, and summary_ko. "
                            "translated_title must be a natural Korean translation of the original title. "
                            "summary_ko must be 2 short Korean sentences grounded only in the provided information. "
                            "If context is thin, say that the available article snippet is limited. "
                            "Preserve every input id exactly once."
                        ),
                    }
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": json.dumps(prompt, ensure_ascii=False),
                    }
                ],
            },
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "news_briefs",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        "briefs": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "id": {"type": "string"},
                                    "translated_title": {"type": "string"},
                                    "summary_ko": {"type": "string"},
                                },
                                "required": ["id", "translated_title", "summary_ko"],
                                "additionalProperties": False,
                            },
                        }
                    },
                    "required": ["briefs"],
                    "additionalProperties": False,
                },
            }
        },
    }
    payload = openai_request(body)

    output_text = extract_output_text(payload)
    if not output_text:
        raise RuntimeError("OpenAI response did not include usable text output")

    parsed = json.loads(output_text)
    briefs = parsed.get("briefs", [])
    results = dict(empty_result)
    for brief in briefs:
        brief_id = str(brief.get("id", "")).strip()
        if brief_id and brief_id in results:
            results[brief_id] = {
                "translated_title": str(brief.get("translated_title", "")).strip(),
                "summary_ko": str(brief.get("summary_ko", "")).strip(),
            }
    return results


def format_separator(now: datetime | None = None) -> str:
    stamp = (now or datetime.now(KST)).astimezone(KST).strftime("%Y-%m-%d %H:%M")
    return (
        "<b>한국 방산 뉴스 알림</b>\n"
        f"<b>기준시각:</b> {stamp} (KST)"
    )


def format_no_updates_message(now: datetime | None = None) -> str:
    current = (now or datetime.now(KST)).astimezone(KST)
    stamp = current.strftime("%Y-%m-%d %H:%M")
    lookback = get_news_lookback(current) or "1h"
    return (
        "<b>뉴스 체크 결과</b>\n"
        f"<b>기준시각:</b> {stamp} (KST)\n"
        f"{lookback} 동안 신규 기사가 없습니다."
    )


def should_skip_for_quiet_hours(now: datetime | None = None) -> bool:
    current = (now or datetime.now(KST)).astimezone(KST)
    return 0 <= current.hour < 5


def format_item(
    item: dict,
    brief: dict[str, str] | None = None,
    index: int | None = None,
    total: int | None = None,
) -> str:
    published = item["pub_date"]
    if published:
        try:
            dt = parsedate_to_datetime(published).astimezone(timezone.utc)
            published = dt.strftime("%Y-%m-%d %H:%M UTC")
        except (TypeError, ValueError, OverflowError):
            pass

    translated_title = (brief or {}).get("translated_title", "")
    summary_ko = (brief or {}).get("summary_ko", "")

    prefix = ""
    if index is not None and total is not None:
        prefix = f"[{index}/{total}] "

    escaped_title = html.escape(item["title"])
    lines = [f"{prefix}<b>{escaped_title}</b>"]
    if translated_title:
        lines.append(f"Title (Korean): {translated_title}")
    if item["source"]:
        lines.append(f"Source: {item['source']}")
    if published:
        lines.append(f"Published: {published}")
    if summary_ko:
        lines.append(f"Summary (KO): {summary_ko}")
    lines.append(f"Link: {get_article_url(item)}")
    return "\n".join(lines)


def telegram_api(method: str) -> str:
    token = env("TELEGRAM_BOT_TOKEN")
    return f"https://api.telegram.org/bot{token}/{method}"


def send_telegram_message(text: str) -> None:
    chat_id = env("TELEGRAM_CHAT_ID")
    payload = urllib.parse.urlencode(
        {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": "true",
            "parse_mode": "HTML",
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        telegram_api("sendMessage"),
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        response.read()


def run_once() -> int:
    if should_skip_for_quiet_hours():
        print("Skipped check during quiet hours (Asia/Seoul 00:00-04:59).", flush=True)
        return 0

    state = load_state()
    seen_ids_list = state.get("seen_ids", [])
    seen_ids = set(seen_ids_list)
    items = fetch_feed()
    first_run = not seen_ids_list

    new_items = [item for item in items if item["id"] not in seen_ids]
    new_items.sort(
        key=lambda item: parse_date_for_sort(item["pub_date"]),
    )

    if first_run and os.getenv("SEND_EXISTING_ON_FIRST_RUN", "false").lower() != "true":
        for item in items:
            seen_ids.add(item["id"])
        new_count = 0
        send_telegram_message(format_no_updates_message())
    else:
        briefs_by_id: dict[str, dict[str, str]] = {}
        if new_items and os.getenv("INCLUDE_KOREAN_SUMMARY", "true").lower() == "true":
            try:
                briefs_by_id = generate_korean_briefs(new_items)
            except Exception as exc:
                print(f"AI summary skipped: {exc}", file=sys.stderr, flush=True)

        if new_items:
            send_telegram_message(format_separator())
            total_items = len(new_items)
            for index, item in enumerate(new_items, start=1):
                send_telegram_message(
                    format_item(
                        item,
                        briefs_by_id.get(item["id"]),
                        index=index,
                        total=total_items,
                    )
                )
                seen_ids_list.append(item["id"])
                seen_ids.add(item["id"])
        else:
            send_telegram_message(format_no_updates_message())
        new_count = len(new_items)

    if first_run and not seen_ids_list:
        seen_ids_list = [item["id"] for item in items]

    state["seen_ids"] = seen_ids_list[-500:]
    state["last_checked_at"] = datetime.now(timezone.utc).isoformat()
    state["last_result_count"] = len(items)
    state["last_new_count"] = new_count
    save_state(state)

    print(
        f"Checked {len(items)} item(s), sent {new_count} new message(s).",
        flush=True,
    )
    return new_count


def parse_date_for_sort(value: str) -> float:
    if not value:
        return 0.0
    try:
        return parsedate_to_datetime(value).timestamp()
    except (TypeError, ValueError, OverflowError):
        return 0.0


def sleep_until_next_hour() -> None:
    now = time.time()
    seconds = 3600 - (int(now) % 3600)
    time.sleep(seconds)


def main() -> int:
    load_dotenv(Path(__file__).with_name(".env"))
    mode = os.getenv("RUN_MODE", "loop").strip().lower()

    if mode == "once" or "--once" in sys.argv:
        run_once()
        return 0

    while True:
        try:
            run_once()
        except Exception as exc:
            error_message = f"News bot error: {exc}"
            print(error_message, file=sys.stderr, flush=True)
            try:
                if os.getenv("TELEGRAM_NOTIFY_ERRORS", "false").lower() == "true":
                    send_telegram_message(error_message)
            except Exception:
                pass
        sleep_until_next_hour()


if __name__ == "__main__":
    raise SystemExit(main())

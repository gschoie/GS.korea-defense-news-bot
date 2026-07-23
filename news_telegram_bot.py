# TEST 2026-03-30

import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path


WEAPONS_QUERY = (
    '"KOREA" AND ('
    '"K9" OR "K2" OR "K239" OR "MLRS" OR "M-SAM" OR "L-SAM" OR "KTSSM" OR "KGGB" '
    'OR "FA-50" OR "T-50" OR "KF-21" OR "LAH" OR "Surion" OR "KUH"'
    ")"
)
COMPANY_QUERY = (
    '"Hanwha Aerospace" OR "Hanwha Ocean" OR "Hanwha Systems" OR "Hyundai Rotem" '
    'OR "LIG Nex1" OR "Korea Aerospace Industries" '
    # Redback은 호주에서 독거미·경마 이름으로 흔해 방산 문맥을 함께 요구한다
    'OR "Cheongung" OR "Chunmoo" OR (Redback AND (Hanwha OR IFV OR army)) OR "KSS-III"'
)
# 일반 단어(دفاع=방어/수비, صواريخ=미사일)는 축구·중동정치 기사까지 잡으므로
# 방산업 특정 표현·회사/무기 아랍어 표기·라틴 제식명만 사용한다
ARABIC_QUERY = (
    '"كوريا الجنوبية" AND ('
    '"الصناعات الدفاعية" OR "صفقة أسلحة" OR "صفقة دفاعية" OR "صادرات الأسلحة" '
    'OR "التعاون الدفاعي" OR هانوا OR هانفا OR "هيونداي روتيم" OR تشونمو OR تشيونغونغ '
    'OR "K9" OR "KF-21" OR "FA-50"'
    ")"
)

# (query, hl, gl, ceid) — 에디션별로 색인/랭킹이 달라 미국판 하나로는 중동 매체 기사를 놓친다
FEEDS = [
    (WEAPONS_QUERY, "en-US", "US", "US:en"),
    (WEAPONS_QUERY, "en-GB", "GB", "GB:en"),
    (WEAPONS_QUERY, "en-AE", "AE", "AE:en"),
    (COMPANY_QUERY, "en-US", "US", "US:en"),
    (COMPANY_QUERY, "en-GB", "GB", "GB:en"),
    (COMPANY_QUERY, "en-AE", "AE", "AE:en"),
    (ARABIC_QUERY, "ar", "SA", "SA:ar"),
    (ARABIC_QUERY, "ar", "EG", "EG:ar"),
]

# 한국 언론사 영어보도 제외 (source 이름 소문자 부분일치)
EXCLUDED_SOURCE_KEYWORDS = [
    "korea herald",
    "korea times",
    "korea joongang",
    "joongang",
    "yonhap",
    "يونهاب",  # 연합뉴스 아랍어 서비스
    "chosun",
    "dong-a",
    "donga",
    "hankyoreh",
    "hankyung",
    "korea economic daily",
    "ked global",
    "maeil",
    "pulse by",
    "businesskorea",
    "business korea",
    "aju business",
    "aju press",
    "arirang",
    "kbs world",
    "korea bizwire",
    "korea.net",
    "korea pro",
    "korea daily",
    "newsis",
    "koreatechtoday",
    "seoul economic",
    "sedaily",
    "thelec",
    "the elec",
    "etnews",
    "asia economic",
    "asiae",
    "ajunews",
    "money today",
    "newspim",
    "edaily",
    "heraldcorp",
    "hankook ilbo",
    "kyunghyang",
    "segye",
    "chosunbiz",
]
# 원 매체가 아닌 재발행·기계번역·집계 사이트 (도메인 정확일치 또는 서브도메인)
EXCLUDED_DOMAINS = [
    "vietnam.vn",
    "khlaasa.net",
]
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
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


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


def build_rss_url(query: str, hl: str, gl: str, ceid: str) -> str:
    lookback = get_news_lookback()
    if lookback:
        query = f"{query} when:{lookback}"

    params = {
        "q": query,
        "hl": hl,
        "gl": gl,
        "ceid": ceid,
    }
    return f"{GOOGLE_NEWS_RSS}?{urllib.parse.urlencode(params)}"


def is_excluded_source(item: dict) -> bool:
    if os.getenv("EXCLUDE_KOREAN_MEDIA", "true").lower() != "true":
        return False
    # 한글 매체명/제목 = 국내 보도 → 해외 보도만 남긴다
    if re.search(r"[가-힣]", f"{item.get('source', '')} {item.get('title', '')}"):
        return True
    source = item.get("source", "").lower()
    if not source:
        return False
    return any(keyword in source for keyword in EXCLUDED_SOURCE_KEYWORDS)


# 아랍어·키릴·CJK 문자가 제목에 있으면 비영어 기사로 분류 (한글은 앞단에서 이미 제외됨)
NON_LATIN_PATTERN = re.compile(
    r"[Ѐ-ӿ؀-ۿݐ-ݿ一-鿿぀-ヿ]"
)


def is_english_item(item: dict) -> bool:
    return not NON_LATIN_PATTERN.search(item.get("title", ""))


def is_blocked_domain(item: dict) -> bool:
    host = urllib.parse.urlparse(item.get("source_url", "")).netloc.lower()
    if not host:
        return False
    extra = [
        domain.strip().lower()
        for domain in os.getenv("EXTRA_EXCLUDED_DOMAINS", "").split(",")
        if domain.strip()
    ]
    return any(
        host == domain or host.endswith("." + domain)
        for domain in EXCLUDED_DOMAINS + extra
    )


def fetch_single_feed(query: str, hl: str, gl: str, ceid: str) -> list[dict]:
    request = urllib.request.Request(
        build_rss_url(query, hl, gl, ceid),
        headers={"User-Agent": "Mozilla/5.0"},
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
        source_el = item.find("source")
        source = (source_el.text or "") if source_el is not None else ""
        source_url = source_el.get("url", "") if source_el is not None else ""
        items.append(
            {
                "id": guid or link or title,
                "title": title,
                "link": link,
                "pub_date": pub_date,
                "source": source,
                "source_url": source_url,
            }
        )
    return items


def fetch_feed() -> list[dict]:
    merged: list[dict] = []
    seen_keys: set[str] = set()
    for query, hl, gl, ceid in FEEDS:
        try:
            feed_items = fetch_single_feed(query, hl, gl, ceid)
        except Exception as exc:
            print(f"Feed fetch failed ({ceid}): {exc}", file=sys.stderr, flush=True)
            continue
        for item in feed_items:
            # 같은 기사가 에디션마다 다른 guid로 잡힐 수 있어 제목+매체로도 dedupe
            dedupe_keys = [
                item["id"],
                f"{item['title'].strip().lower()}|{item['source'].strip().lower()}",
            ]
            if any(key in seen_keys for key in dedupe_keys):
                continue
            seen_keys.update(dedupe_keys)
            merged.append(item)
    return merged


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
        headers={"User-Agent": "Mozilla/5.0"},
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

    return strip_html(raw_html)[:1500]


def resolve_final_article_url(link: str) -> str:
    if not link:
        return ""

    request = urllib.request.Request(
        link,
        headers={"User-Agent": "Mozilla/5.0"},
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


class QuotaExhaustedError(RuntimeError):
    """OpenAI 크레딧/쿼터 소진 — 재시도로 회복 불가, 결제 필요."""


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
            error_body = exc.read().decode("utf-8", errors="ignore")
            # 크레딧 소진은 429여도 기다린다고 회복되지 않는다 — 즉시 중단
            if exc.code == 429 and "insufficient_quota" in error_body:
                raise QuotaExhaustedError(
                    "OpenAI quota exhausted (check billing at platform.openai.com)"
                ) from exc
            if exc.code != 429 or attempt >= max_attempts:
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
        article_url = get_article_url(item)
        prompt_items.append(
            {
                "id": item["id"],
                "title": item["title"],
                "source": item["source"],
                "published": item["pub_date"],
                "link": article_url,
                "article_context": fetch_article_context(article_url),
            }
        )
    return prompt_items


def generate_korean_briefs(items: list[dict]) -> dict[str, dict[str, str]]:
    # 한 번에 전부 처리하다 실패하면 전량 무점수 발송(폴백)이 되므로
    # 청크로 나눠 실패를 청크 단위로 격리한다
    empty_result = {
        item["id"]: {
            "translated_title": "",
            "summary_ko": "",
            "relevance": None,
        }
        for item in items
    }
    if not items or not has_openai_config():
        return empty_result

    results = dict(empty_result)
    batch_size = max(1, int(os.getenv("OPENAI_BATCH_SIZE", "10")))
    for start in range(0, len(items), batch_size):
        chunk = items[start : start + batch_size]
        try:
            results.update(generate_korean_briefs_chunk(chunk))
        except QuotaExhaustedError:
            raise  # 남은 청크·재시도 전부 무의미 — 바로 올려보낸다
        except Exception as exc:
            print(
                f"AI brief chunk failed (items {start + 1}-{start + len(chunk)}): {exc}",
                file=sys.stderr,
                flush=True,
            )

    # 실패 청크·누락 id 재시도 한 번
    unscored = [
        item for item in items if results[item["id"]]["relevance"] is None
    ]
    for start in range(0, len(unscored), batch_size):
        chunk = unscored[start : start + batch_size]
        try:
            results.update(generate_korean_briefs_chunk(chunk))
        except QuotaExhaustedError:
            raise
        except Exception as exc:
            print(
                f"AI brief retry failed ({len(chunk)} item(s)): {exc}",
                file=sys.stderr,
                flush=True,
            )
    return results


def generate_korean_briefs_chunk(items: list[dict]) -> dict[str, dict[str, str]]:
    empty_result = {
        item["id"]: {
            "translated_title": "",
            "summary_ko": "",
            "relevance": None,
        }
        for item in items
    }
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
                            "You translate news headlines (any language) into Korean and write short Korean summaries. "
                            "Return valid JSON only. "
                            "The output must be an object with one key named briefs. "
                            "briefs must be an array of objects. "
                            "Each object must contain id, translated_title, summary_ko, and relevance. "
                            "translated_title must be a natural Korean translation of the original title. "
                            "summary_ko must be 2 short Korean sentences grounded only in the provided information. "
                            "If context is thin, say that the available article snippet is limited. "
                            "relevance must be an integer from 0 to 10 scoring how relevant the article is to "
                            "South Korea's defense industry: Korean weapons systems, arms exports and deals, "
                            "defense companies (Hanwha, KAI, Hyundai Rotem, LIG Nex1, etc.), or military "
                            "cooperation involving South Korea. Articles merely mentioning Korea in passing, "
                            "about North Korea only, or about unrelated industries must score 3 or lower. "
                            "Score 0-2 for: sports coverage (in football, 'defense'/دفاع refers to gameplay, "
                            "not the military), culture, UNESCO heritage, tourism, entertainment; and articles "
                            "about other countries' politics or conflicts (Iran, Israel, the US, etc.) where "
                            "South Korea appears only in passing (e.g., frozen funds, trade statistics). "
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
                                    "relevance": {"type": "integer"},
                                },
                                "required": [
                                    "id",
                                    "translated_title",
                                    "summary_ko",
                                    "relevance",
                                ],
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
    results = dict(empty_result)
    for brief in parsed.get("briefs", []):
        brief_id = str(brief.get("id", "")).strip()
        if brief_id and brief_id in results:
            try:
                relevance = int(brief.get("relevance"))
            except (TypeError, ValueError):
                relevance = None
            results[brief_id] = {
                "translated_title": str(brief.get("translated_title", "")).strip(),
                "summary_ko": str(brief.get("summary_ko", "")).strip(),
                "relevance": relevance,
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

    prefix = f"[{index}/{total}] " if index is not None and total is not None else ""
    escaped_title = html.escape(item["title"])

    lines = [f"{prefix}<b>{escaped_title}</b>"]
    if translated_title:
        lines.append(f"Title (Korean): {html.escape(translated_title)}")
    if item["source"]:
        lines.append(f"Source: {html.escape(item['source'])}")
    if published:
        lines.append(f"Published: {published}")
    if summary_ko:
        lines.append(f"Summary (KO): {html.escape(summary_ko)}")
    lines.append(f"Link: {html.escape(get_article_url(item))}")
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


def parse_date_for_sort(value: str) -> float:
    if not value:
        return 0.0
    try:
        return parsedate_to_datetime(value).timestamp()
    except (TypeError, ValueError, OverflowError):
        return 0.0


def run_once() -> int:
    if should_skip_for_quiet_hours():
        print("Skipped check during quiet hours (Asia/Seoul 00:00-04:59).", flush=True)
        return 0

    state = load_state()
    seen_ids_list = state.get("seen_ids", [])
    seen_ids = set(seen_ids_list)
    items = fetch_feed()
    first_run = not seen_ids_list

    excluded_count = 0
    blocked_domain_count = 0
    new_items = []
    for item in items:
        if item["id"] in seen_ids:
            continue
        if is_excluded_source(item):
            excluded_count += 1
            seen_ids_list.append(item["id"])
            seen_ids.add(item["id"])
            continue
        if is_blocked_domain(item):
            blocked_domain_count += 1
            print(
                f"Blocked domain: {item['source_url']} — {item['title'][:80]}",
                flush=True,
            )
            seen_ids_list.append(item["id"])
            seen_ids.add(item["id"])
            continue
        new_items.append(item)
    new_items.sort(key=lambda item: parse_date_for_sort(item["pub_date"]))

    deferred_count = 0
    if first_run and os.getenv("SEND_EXISTING_ON_FIRST_RUN", "false").lower() != "true":
        for item in items:
            seen_ids.add(item["id"])
        new_count = 0
        send_telegram_message(format_no_updates_message())
    else:
        briefs_by_id: dict[str, dict[str, str]] = {}
        quota_exhausted = False
        if new_items and os.getenv("INCLUDE_KOREAN_SUMMARY", "true").lower() == "true":
            try:
                briefs_by_id = generate_korean_briefs(new_items)
            except QuotaExhaustedError as exc:
                quota_exhausted = True
                print(f"AI scoring unavailable: {exc}", file=sys.stderr, flush=True)
            except Exception as exc:
                print(f"AI summary skipped: {exc}", file=sys.stderr, flush=True)

        # AI 관련성 점수가 기준 미달이면 발송 없이 seen 처리.
        # AI 실패로 무점수인 기사는 발송하지 않고 seen 처리도 하지 않는다
        # → 다음 회차(1시간 뒤)에 다시 새 기사로 잡혀 재채점됨
        min_relevance = int(os.getenv("MIN_RELEVANCE", "4"))
        ai_filter_active = (
            os.getenv("INCLUDE_KOREAN_SUMMARY", "true").lower() == "true"
            and has_openai_config()
        )
        low_relevance_count = 0
        send_items = []
        for item in new_items:
            relevance = (briefs_by_id.get(item["id"]) or {}).get("relevance")
            if isinstance(relevance, int) and relevance < min_relevance:
                low_relevance_count += 1
                print(
                    f"Dropped (relevance {relevance}): "
                    f"{item['source']} — {item['title'][:80]}",
                    flush=True,
                )
                seen_ids_list.append(item["id"])
                seen_ids.add(item["id"])
                continue
            if relevance is None and ai_filter_active:
                deferred_count += 1
                print(
                    f"Deferred (no AI score): "
                    f"{item['source']} — {item['title'][:80]}",
                    flush=True,
                )
                continue
            send_items.append(item)

        # 쿼리 확대 직후 폭주 방지: 최신 N건만 발송, 나머지는 seen 처리
        max_per_run = int(os.getenv("MAX_ITEMS_PER_RUN", "25"))
        overflow_count = 0
        if len(send_items) > max_per_run:
            overflow_count = len(send_items) - max_per_run
            for item in send_items[:-max_per_run]:
                seen_ids_list.append(item["id"])
                seen_ids.add(item["id"])
            send_items = send_items[-max_per_run:]

        if send_items:
            notes = []
            if deferred_count:
                notes.append(f"AI 채점 보류: {deferred_count}건")
            if low_relevance_count:
                notes.append(f"관련성 낮음 제외: {low_relevance_count}건")
            if excluded_count:
                notes.append(f"한국언론 제외: {excluded_count}건")
            if blocked_domain_count:
                notes.append(f"집계사이트 제외: {blocked_domain_count}건")
            if overflow_count:
                notes.append(f"건수 초과 생략: {overflow_count}건")
            separator = format_separator()
            if notes:
                separator += "\n" + " · ".join(notes)
            send_telegram_message(separator)
            total_items = len(send_items)
            # 영어/비영어를 섹션으로 나눠 발송하고 번호도 섹션별로 매긴다
            sections = [
                ("영어 뉴스", [i for i in send_items if is_english_item(i)]),
                ("비영어 뉴스", [i for i in send_items if not is_english_item(i)]),
            ]
            for section_title, section_items in sections:
                if not section_items:
                    continue
                send_telegram_message(
                    f"<b>[{section_title}]</b> {len(section_items)}건"
                )
                section_total = len(section_items)
                for index, item in enumerate(section_items, start=1):
                    send_telegram_message(
                        format_item(
                            item,
                            briefs_by_id.get(item["id"]),
                            index=index,
                            total=section_total,
                        )
                    )
                    seen_ids_list.append(item["id"])
                    seen_ids.add(item["id"])
                    if total_items > 3:
                        time.sleep(1.1)
        else:
            if deferred_count:
                # '신규 기사 없음'이 아니라 채점을 못 한 것 — 정확히 알린다
                stamp = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
                status = (
                    "<b>뉴스 체크 결과</b>\n"
                    f"<b>기준시각:</b> {stamp} (KST)\n"
                    f"AI 채점 실패로 {deferred_count}건 발송 보류 — 다음 회차에 재시도합니다."
                )
                if quota_exhausted:
                    status += (
                        "\n⚠️ OpenAI 크레딧 소진 — platform.openai.com 결제 확인 필요."
                    )
                send_telegram_message(status)
            else:
                send_telegram_message(format_no_updates_message())

        new_count = len(send_items)

    if first_run and not seen_ids_list:
        seen_ids_list = [item["id"] for item in items]

    state["seen_ids"] = seen_ids_list[-2000:]
    state["last_checked_at"] = datetime.now(timezone.utc).isoformat()
    state["last_result_count"] = len(items)
    state["last_new_count"] = new_count
    save_state(state)

    print(
        f"Checked {len(items)} item(s), sent {new_count} new message(s), "
        f"excluded {excluded_count} Korean-media item(s), "
        f"deferred {deferred_count} unscored item(s).",
        flush=True,
    )
    return new_count


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

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


# 제식명 하나로는 잡히지 않는 기사가 많다 (KKMD가 다루는 외신 상당수가
# 함정·정책·수출 총액 기사라 제품명이 본문에만 있거나 아예 없다).
# → 영역별 쿼리로 쪼개고, 일반 단어와 겹치는 토큰만 한국 문맥을 함께 요구한다.
KOREA_GUARD = "KOREA OR KOREAN OR SEOUL"

# 항공: KF-21/FA-50 계열. T-50·LAH·KF-16은 타국 기사와 겹쳐 한국 문맥을 요구.
# KAAN·GCAP·Hurjet은 경쟁 기종 — KF-21 언급 없는 비교·수주전 기사를 잡기 위한 것으로,
# 단독으로는 튀르키예·영국 국내 기사 홍수라 한국 문맥 가드 필수.
# F-50은 단좌형 명칭(차량·카메라 모델명과 겹침), KAI는 인명·약어 중복이 많아 역시 가드.
AIR_QUERY = (
    '"KF-21" OR "Boramae" OR "FA-50" OR "TA-50" OR "Golden Eagle jet" '
    'OR "Korea Aerospace Industries" OR "Surion" OR "KUH-1" OR "KF-21EX" '
    f'OR (({KOREA_GUARD}) AND ("T-50" OR "F-50" OR "KAI" OR "LAH" OR "KF-16" '
    'OR "light armed helicopter" OR "MUAV" '
    'OR "KAAN" OR "GCAP" OR "Hurjet" OR "Hürjet" OR "TF-X"))'
)
# 지상: K2/K9/K21은 산(K2)·군견(K9)과 겹쳐 한국 문맥 또는 완전한 이름을 요구.
# K9 Vajra는 인도 현지 표기(제목에 Korea가 없는 경우 다수).
# Tigon은 사자·호랑이 교잡종 동물명과 겹쳐 가드 구간으로
LAND_QUERY = (
    '"K239" OR "Chunmoo" OR "Cheonmoo" OR "Hyundai Rotem" OR "K2 Black Panther" '
    'OR "K9 Thunder" OR "K9 Vajra" OR "AS9 Huntsman" OR "AS21 Redback" OR "Homar-K" OR "K808" '
    # 미 육군 자주포 사업 — K9MH가 응찰. 사업명 기사엔 Korea/Hanwha가 제목에 없어
    # 사업명 자체를 무가드로 잡는다 (KAAN·GCAP과 같은 '경쟁 프로그램' 패턴)
    'OR "Mobile Tactical Cannon" OR "SPH-M" '
    f'OR (({KOREA_GUARD}) AND ("K2 tank" OR "K9 howitzer" OR "K21" OR "K10" OR "K30" '
    'OR Redback OR Tigon))'
)
# 유도무기·방공. Chiron(부가티)·Poniard는 동음이의어라 한국 문맥을 요구
MISSILE_QUERY = (
    '"L-SAM" OR "M-SAM" OR "Cheongung" OR "KM-SAM" OR "KTSSM" OR "KGGB" OR "Hyunmoo" '
    'OR "LIG Nex1" OR "Haeseong" OR "SSM-700K" OR "Bigung" OR "Shingung" '
    f'OR (({KOREA_GUARD}) AND ("Chiron" OR "Sky Dragon" OR "Poniard" OR "hypersonic missile"))'
)
# 함정·조선: KDDX, 잠수함, MASGA·필리조선소·미 해군 MRO — 현행 로직이 통째로 놓치던 영역
NAVAL_QUERY = (
    '"KDDX" OR "KSS-III" OR "Jangbogo" OR "Hanwha Ocean" OR "Philly Shipyard" OR "MASGA" '
    'OR "HD Hyundai Heavy" OR "Hanwha Philly" OR "Hyundai Heavy Industries" '
    # Austal 인수전 기사는 Hanwha Ocean 풀네임 없이 Hanwha만 쓰는 경우가 많다
    "OR (Hanwha AND Austal) "
    f'OR (({KOREA_GUARD}) AND (submarine OR frigate OR destroyer OR corvette OR shipbuilder '
    'OR "naval MRO" OR "Aegis" OR "shipbuilding deal"))'
)
COMPANY_QUERY = (
    '"Hanwha Aerospace" OR "Hanwha Systems" OR "Hanwha Defense" OR "Hanwha Defence" '
    'OR "Poongsan" OR "SNT Dynamics" OR "SNT Motiv" OR "Firstec" '
    'OR "Defense Acquisition Program Administration" OR "Agency for Defense Development" '
    f'OR (({KOREA_GUARD}) AND ("DAPA" OR "defense industry association" OR "ADEX"))'
)
# 제품명이 안 나오는 총론·정책 기사 (수출 총액, 방산 협력 MOU 등)
EXPORT_QUERY = (
    '("South Korea" OR "Korean" OR "Seoul") AND ('
    '"arms export" OR "arms exports" OR "defense export" OR "defence export" '
    'OR "arms deal" OR "defense deal" OR "defence deal" OR "arms sale" OR "arms sales" '
    'OR "defense industry" OR "defence industry" OR "defense cooperation" '
    'OR "defence cooperation" OR "defense contract" OR "defence contract" '
    'OR "weapons exports" OR "K-defense" OR "military aid package"'
    ")"
)
# 상대국 관점 보도 — 한국 매체보다 현지 매체가 먼저 쓰는 경우가 많다.
# 단독 arms(arms race/up in arms)·tanks(think tanks)는 오검색이 커서 구문으로 좁힌다
COUNTRY_QUERY = (
    '("South Korea" OR "Korean") AND ('
    'Poland OR Romania OR Egypt OR Peru OR Philippines OR Vietnam OR Malaysia '
    'OR Norway OR Finland OR Morocco OR "Saudi Arabia" OR "United Arab Emirates" '
    'OR India OR Canada OR Australia OR Indonesia '
    'OR Iraq OR Thailand OR Uzbekistan OR Estonia'
    ") AND ("
    '"defense contract" OR "defence contract" OR "arms deal" OR "arms sale" '
    'OR "arms export" OR howitzer OR "fighter jet" '
    'OR "battle tank" OR missile OR frigate OR submarine OR artillery'
    ")"
)
# 국내 방산 전문기자 연재 — 한국언론 제외 필터의 예외 채널.
# 연재물은 제목에 [밀리터리+] 같은 태그를 다는 관행이 있어
# 쿼리는 넓게 잡고 제목 태그로 정밀 필터한다
KOREAN_COLUMN_QUERY = (
    '"밀리터리+" OR "밀리터리 인사이드" OR "무기인사이드" OR "이일우의 밀리터리" '
    'OR "박수찬의 軍" OR "이철재의 밀담" OR "양낙규의 Defence" OR "양낙규의 디펜스" '
    'OR "김관용의 軍界一學" OR "정충신의 밀리터리"'
)
# 제목 안의 연재 태그로 확정 — 쿼리가 물어온 무관 기사를 걸러낸다
KOREAN_COLUMN_TITLE_PATTERN = re.compile(
    r"\[(밀리터리\+|밀리터리 ?인사이드|(최현호의 ?)?무기 ?인사이드"
    r"|이일우의 ?밀리터리 ?(talk|톡)?|박수찬의 ?軍|이철재의 ?밀담"
    r"|양낙규의 ?(Defence|디펜스) ?(클럽|Club)?|김관용의 ?軍界一學"
    r"|정충신의 ?밀리터리 ?(카페)?)\]"
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
# 현지어 보도 — 폴란드·인니·베트남·튀르키예는 계약 당사국이라 자국어 기사가 먼저 뜬다.
# 국가명 전체 표기를 필수로 걸어 오검색을 막는다
POLISH_QUERY = (
    '"Korea Południowa" AND ('
    'K2 OR K9 OR "FA-50" OR Hanwha OR "Hyundai Rotem" OR "Homar-K" OR Chunmoo OR Borsuk'
    ")"
)
INDONESIAN_QUERY = (
    '"Korea Selatan" AND ('
    '"KF-21" OR "FA-50" OR "T-50" OR Hanwha OR "industri pertahanan" OR "alutsista"'
    ")"
)
VIETNAMESE_QUERY = (
    '"Hàn Quốc" AND ('
    '"K9" OR "KF-21" OR "FA-50" OR Hanwha OR "công nghiệp quốc phòng" OR "xuất khẩu vũ khí"'
    ")"
)
TURKISH_QUERY = (
    '"Güney Kore" AND ('
    '"KF-21" OR "FA-50" OR "K9" OR "K2" OR Hanwha OR "savunma sanayi"'
    ")"
)
# 페루 잠수함·자주포, 중남미 FA-50 검토국 — 스페인어 현지 보도가 영어보다 빠르다
SPANISH_QUERY = (
    '"Corea del Sur" AND ('
    '"KSS-III" OR submarino OR Hanwha OR "FA-50" OR "KF-21" OR "K9" '
    'OR "industria de defensa" OR "exportación de armas"'
    ")"
)
# 루마니아: K9·레드백·천궁 대량 구매국
ROMANIAN_QUERY = (
    '"Coreea de Sud" AND ('
    '"K9" OR "K2" OR Hanwha OR "Hyundai Rotem" OR "industria de apărare" OR obuziere'
    ")"
)
# 공보 사이트 전용 구글뉴스 site: 피드 — army.mil 보도자료는 일반 주제 쿼리
# 랭킹에 거의 안 올라오므로 도메인을 지정해 강제로 끌어온다. (army.mil RSS를
# 직접 치는 방식은 Akamai가 GitHub Actions IP를 403으로 차단해 불가)
SITE_ARMY_QUERY = (
    'site:army.mil (howitzer OR artillery OR cannon OR "Mobile Tactical Cannon" '
    "OR Korea OR Hanwha OR K9 OR Chunmoo OR HIMARS)"
)

# (query, hl, gl, ceid) — 에디션별로 색인/랭킹이 달라 미국판 하나로는
# 중동·동남아·유럽 현지 매체 기사를 놓친다. 영어판을 앞에 두어
# 중복 기사는 영어 기사가 대표로 남게 한다.
FEEDS = [
    (AIR_QUERY, "en-US", "US", "US:en"),
    (AIR_QUERY, "en-GB", "GB", "GB:en"),
    (AIR_QUERY, "en-IN", "IN", "IN:en"),
    (AIR_QUERY, "en-PH", "PH", "PH:en"),
    (AIR_QUERY, "en-MY", "MY", "MY:en"),
    (LAND_QUERY, "en-US", "US", "US:en"),
    (LAND_QUERY, "en-GB", "GB", "GB:en"),
    (LAND_QUERY, "en-AU", "AU", "AU:en"),
    (MISSILE_QUERY, "en-US", "US", "US:en"),
    (MISSILE_QUERY, "en-GB", "GB", "GB:en"),
    (NAVAL_QUERY, "en-US", "US", "US:en"),
    (NAVAL_QUERY, "en-GB", "GB", "GB:en"),
    (NAVAL_QUERY, "en-CA", "CA", "CA:en"),
    (COMPANY_QUERY, "en-US", "US", "US:en"),
    (COMPANY_QUERY, "en-GB", "GB", "GB:en"),
    (COMPANY_QUERY, "en-AE", "AE", "AE:en"),
    (EXPORT_QUERY, "en-US", "US", "US:en"),
    (EXPORT_QUERY, "en-GB", "GB", "GB:en"),
    (EXPORT_QUERY, "en-AE", "AE", "AE:en"),
    (COUNTRY_QUERY, "en-US", "US", "US:en"),
    (COUNTRY_QUERY, "en-IN", "IN", "IN:en"),
    (ARABIC_QUERY, "ar", "SA", "SA:ar"),
    (ARABIC_QUERY, "ar", "EG", "EG:ar"),
    (POLISH_QUERY, "pl", "PL", "PL:pl"),
    (INDONESIAN_QUERY, "id", "ID", "ID:id"),
    (VIETNAMESE_QUERY, "vi", "VN", "VN:vi"),
    (TURKISH_QUERY, "tr", "TR", "TR:tr"),
    (SPANISH_QUERY, "es-419", "PE", "PE:es-419"),
    (ROMANIAN_QUERY, "ro", "RO", "RO:ro"),
    (SITE_ARMY_QUERY, "en-US", "US", "US:en"),
]
# 국내 방산 칼럼 피드 — 한국언론 제외·AI 관련성 컷을 우회하는 별도 채널
COLUMN_FEEDS = [
    (KOREAN_COLUMN_QUERY, "ko", "KR", "KR:ko"),
]

# 공보·1차 소스 RSS 직접 구독 (name, url, 제목 프리필터 정규식).
# 공보 피드는 진급·부대 소식이 대부분이라 프리필터로 화력·조달 주제만 남기고
# AI 채점이 최종 관련성을 가른다. 현재 비어 있음 — army.mil은 데이터센터 IP를
# 차단해 위의 site: 피드로 대체했고, 봇 접근을 허용하는 1차 소스가 생기면 추가.
DIRECT_FEEDS: list[tuple[str, str, str]] = []

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
GEMINI_API_TEMPLATE = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)

BRIEF_SYSTEM_PROMPT = (
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
    "defense companies (Hanwha, KAI, Hyundai Rotem, LIG Nex1, Poongsan, HD Hyundai, etc.), "
    "or military cooperation involving South Korea. "
    "Score 6 or higher for: naval and shipbuilding stories tied to Korean yards "
    "(KDDX, KSS-III submarines, Hanwha Ocean, Philly Shipyard, MASGA, US Navy MRO work); "
    "procurement decisions, tenders, negotiations and evaluations by buyer countries "
    "(the United States, Poland, Romania, Egypt, Peru, Philippines, Vietnam, Malaysia, "
    "India, Saudi Arabia, UAE, Norway, Australia, Canada, Indonesia) involving Korean "
    "bids — including US Army artillery programs such as the Mobile Tactical Cannon "
    "and SPH-M where Hanwha's K9 competes, even when the article does not name the "
    "Korean bidder; policy and "
    "financing news affecting Korean arms exports (export credit, offsets, tech transfer, "
    "local production); and analysis, comparison or ranking pieces that assess Korean "
    "weapons against foreign competitors. A foreign-media analysis or ranking of Korean "
    "systems is relevant even when it reports no new contract. "
    "Articles merely mentioning Korea in passing, "
    "about North Korea only, or about unrelated industries must score 3 or lower. "
    "Score 0-2 for: sports coverage (in football, 'defense'/دفاع refers to gameplay, "
    "not the military), culture, UNESCO heritage, tourism, entertainment; and articles "
    "about other countries' politics or conflicts (Iran, Israel, the US, etc.) where "
    "South Korea appears only in passing (e.g., frozen funds, trade statistics). "
    "Preserve every input id exactly once."
)
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
    # site: 공보 피드는 구글의 .mil 색인 시차가 커서 일반 12h 창이면 놓친다
    # → 넓은 창을 쓴다. seen dedupe가 재발송을 막으므로 비용은 미미하다
    if query.startswith("site:"):
        lookback = os.getenv("SITE_FEED_LOOKBACK", "48h")
    else:
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
    # 방산 전문기자 연재는 국내 매체라도 통과시킨다
    if item.get("is_column"):
        return False
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
    # 폴란드어·튀르키예어·베트남어·인니어는 라틴 문자라 글자만으로는 구분되지 않는다
    # → 기사를 잡아온 피드의 언어를 우선 근거로 삼는다
    if item.get("feed_lang", "en") != "en":
        return False
    return not NON_LATIN_PATTERN.search(item.get("title", ""))


# 오검색이 잦은 문맥: 축구(defense/defence), K2=산, K9=군견, K-컬처.
# 방산 신호가 함께 없으면 AI 채점 전에 버려 쿼터를 아낀다
NOISE_TITLE_PATTERN = re.compile(
    r"(?i)\b(football|soccer|la\s?liga|premier league|world cup|striker|midfielder"
    r"|k-?pop|bts|blackpink|netflix|box office|drama series|idol"
    r"|mount k2|k2 mountain|mountaineer|climber|summit push|everest"
    r"|police dog|dog handler|k-?9 unit|golf|olympic|marathon)\b"
)
# 축구 기사의 'defense'(수비)에 구조되지 않도록 단독 defense/arms는 신호로 치지 않는다
STRONG_DEFENSE_PATTERN = re.compile(
    r"(?i)(hanwha|hyundai rotem|lig nex1|korea aerospace|poongsan|kf-21|fa-50|k239"
    r"|chunmoo|cheongung|kddx|kss-iii|ktssm|l-sam|m-sam|hyunmoo|redback|masga"
    r"|defen[cs]e (?:industry|export|deal|contract|ministry|budget|sector|cooperation"
    r"|procurement|firm|company|giant)"
    r"|military|missile|artillery|howitzer|frigate|destroyer|submarine"
    r"|fighter jet|warship|shipyard|main battle tank|armoured vehicle|armored vehicle"
    r"|arms (?:deal|export|sale|contract)|weapons? (?:deal|export|system|sale))"
)


def is_noise_item(item: dict) -> bool:
    title = item.get("title", "")
    return bool(NOISE_TITLE_PATTERN.search(title)) and not STRONG_DEFENSE_PATTERN.search(
        title
    )


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


def fetch_single_feed(
    query: str, hl: str, gl: str, ceid: str, is_column: bool = False
) -> list[dict]:
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
                "feed_lang": hl.split("-")[0].lower(),
                "is_column": is_column,
            }
        )
    return items


def fetch_direct_feed(name: str, url: str, title_filter: str) -> list[dict]:
    """일반 RSS(공보 사이트 등)를 직접 읽는다. 구글뉴스 형식과 달리 source 태그가 없다."""
    # army.mil(Akamai)은 축약 UA 'Mozilla/5.0'을 403으로 차단한다 — 완전한 브라우저 UA 필요
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            ),
            "Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        xml_bytes = response.read()

    pattern = re.compile(title_filter)
    host = urllib.parse.urlparse(url).netloc
    items = []
    for item in ET.fromstring(xml_bytes).findall("./channel/item"):
        title = (item.findtext("title") or "(no title)").strip()
        if not pattern.search(title):
            continue
        link = (item.findtext("link") or "").strip()
        guid = (item.findtext("guid") or "").strip()
        items.append(
            {
                "id": guid or link or title,
                "title": title,
                "link": link,
                "pub_date": item.findtext("pubDate", default=""),
                "source": name,
                "source_url": f"https://{host}",
                "feed_lang": "en",
                "is_column": False,
            }
        )
    return items


def fetch_feed() -> list[dict]:
    merged: list[dict] = []
    seen_keys: set[str] = set()
    feed_specs = [(spec, False) for spec in FEEDS]
    if os.getenv("INCLUDE_KOREAN_COLUMNS", "true").lower() == "true":
        feed_specs += [(spec, True) for spec in COLUMN_FEEDS]
    for (query, hl, gl, ceid), is_column in feed_specs:
        try:
            feed_items = fetch_single_feed(query, hl, gl, ceid, is_column=is_column)
        except Exception as exc:
            print(f"Feed fetch failed ({ceid}): {exc}", file=sys.stderr, flush=True)
            continue
        if is_column:
            # 쿼리는 넓게 잡았으므로 제목의 연재 태그로 확정한다
            feed_items = [
                item
                for item in feed_items
                if KOREAN_COLUMN_TITLE_PATTERN.search(item["title"])
            ]
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

    if os.getenv("INCLUDE_DIRECT_FEEDS", "true").lower() == "true":
        for name, url, title_filter in DIRECT_FEEDS:
            try:
                direct_items = fetch_direct_feed(name, url, title_filter)
            except Exception as exc:
                print(
                    f"Direct feed fetch failed ({name}): {exc}",
                    file=sys.stderr,
                    flush=True,
                )
                continue
            for item in direct_items:
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


def ai_provider() -> str | None:
    # Gemini 키가 있으면 무료 티어인 Gemini 우선, 없으면 OpenAI
    if os.getenv("GEMINI_API_KEY", "").strip():
        return "gemini"
    if has_openai_config():
        return "openai"
    return None


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


def gemini_request(system_text: str, user_text: str) -> str:
    """Gemini generateContent 호출 → 응답 텍스트(JSON 문자열) 반환."""
    model = os.getenv("GEMINI_MODEL", "gemini-flash-lite-latest")
    body = {
        "systemInstruction": {"parts": [{"text": system_text}]},
        "contents": [{"role": "user", "parts": [{"text": user_text}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": {
                "type": "OBJECT",
                "properties": {
                    "briefs": {
                        "type": "ARRAY",
                        "items": {
                            "type": "OBJECT",
                            "properties": {
                                "id": {"type": "STRING"},
                                "translated_title": {"type": "STRING"},
                                "summary_ko": {"type": "STRING"},
                                "relevance": {"type": "INTEGER"},
                            },
                            "required": [
                                "id",
                                "translated_title",
                                "summary_ko",
                                "relevance",
                            ],
                        },
                    }
                },
                "required": ["briefs"],
            },
        },
    }

    max_attempts = int(os.getenv("GEMINI_MAX_RETRIES", "4"))
    base_delay = float(os.getenv("GEMINI_RETRY_BASE_SECONDS", "15"))
    for attempt in range(1, max_attempts + 1):
        request = urllib.request.Request(
            GEMINI_API_TEMPLATE.format(model=model),
            data=json.dumps(body).encode("utf-8"),
            headers={
                "x-goog-api-key": env("GEMINI_API_KEY"),
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                payload = json.loads(response.read().decode("utf-8"))
            parts = []
            for candidate in payload.get("candidates", []):
                for part in candidate.get("content", {}).get("parts", []):
                    text_value = part.get("text")
                    if isinstance(text_value, str) and text_value.strip():
                        parts.append(text_value.strip())
            output_text = "\n".join(parts).strip()
            if not output_text:
                raise RuntimeError("Gemini response did not include usable text output")
            return output_text
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="ignore")
            if exc.code == 429:
                # 일일 무료 쿼터 소진은 오늘 안에 회복 안 됨 — 즉시 중단.
                # 분당 rate limit이면 잠시 쉬고 재시도
                if "PerDay" in error_body or "per day" in error_body.lower():
                    raise QuotaExhaustedError(
                        "Gemini daily free-tier quota exhausted"
                    ) from exc
                if attempt < max_attempts:
                    delay_match = re.search(r'"retryDelay":\s*"(\d+)', error_body)
                    delay = (
                        float(delay_match.group(1)) + 1.0
                        if delay_match
                        else base_delay * (2 ** (attempt - 1))
                    )
                    time.sleep(delay)
                    continue
            raise RuntimeError(
                f"Gemini API error {exc.code}: {error_body or exc.reason}"
            ) from exc

    raise RuntimeError("Gemini API retry loop exited unexpectedly")


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
    if not items or ai_provider() is None:
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
    user_text = json.dumps(prompt, ensure_ascii=False)

    if ai_provider() == "gemini":
        output_text = gemini_request(BRIEF_SYSTEM_PROMPT, user_text)
    else:
        body = {
            "model": os.getenv("OPENAI_MODEL", "gpt-5.2"),
            "input": [
                {
                    "role": "system",
                    "content": [{"type": "input_text", "text": BRIEF_SYSTEM_PROMPT}],
                },
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": user_text}],
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
        f"trailing {lookback} 동안의 신규 기사가 없습니다.\n"
        "Refresh 주기 1시간. Github-GLOBAL_DEFENCE_NEWs."
    )


def should_skip_for_quiet_hours(now: datetime | None = None) -> bool:
    # 수동 테스트 실행이 조용시간에 막히지 않도록 하는 우회 스위치
    if os.getenv("IGNORE_QUIET_HOURS", "false").lower() == "true":
        return False
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
    # 원제가 이미 한국어인 국내 칼럼에는 번역 제목이 무의미하다
    if translated_title and item.get("feed_lang") != "ko":
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

    # 구글뉴스가 when: 필터를 무시하고 수개월 전 기사를 섞어줄 때가 있다
    # → 발행일이 컷오프보다 오래된 기사는 발송 없이 seen 처리
    max_age_hours = float(os.getenv("MAX_ITEM_AGE_HOURS", "72"))
    age_cutoff_ts = time.time() - max_age_hours * 3600

    excluded_count = 0
    blocked_domain_count = 0
    stale_count = 0
    noise_count = 0
    new_items = []
    for item in items:
        if item["id"] in seen_ids:
            continue
        pub_ts = parse_date_for_sort(item["pub_date"])
        if pub_ts and pub_ts < age_cutoff_ts:
            stale_count += 1
            print(
                f"Stale (older than {max_age_hours:.0f}h): "
                f"{item['pub_date']} — {item['title'][:80]}",
                flush=True,
            )
            seen_ids_list.append(item["id"])
            seen_ids.add(item["id"])
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
        if is_noise_item(item):
            noise_count += 1
            print(
                f"Noise (no defense signal): {item['title'][:80]}",
                flush=True,
            )
            seen_ids_list.append(item["id"])
            seen_ids.add(item["id"])
            continue
        new_items.append(item)
    new_items.sort(key=lambda item: parse_date_for_sort(item["pub_date"]))

    # 쿼리 확대로 후보가 폭증하면 AI 채점 호출만으로 일일 쿼터가 마른다
    # → 채점 대상 자체를 최신 N건으로 제한하고 나머지는 seen 처리
    max_to_score = int(os.getenv("MAX_ITEMS_TO_SCORE", "60"))
    unscored_overflow_count = 0
    # 칼럼은 반드시 발송해야 하므로 채점 한도 컷 대상에서 제외
    scorable = [item for item in new_items if not item.get("is_column")]
    if len(scorable) > max_to_score:
        column_items = [item for item in new_items if item.get("is_column")]
        unscored_overflow_count = len(scorable) - max_to_score
        for item in scorable[:-max_to_score]:
            seen_ids_list.append(item["id"])
            seen_ids.add(item["id"])
        new_items = column_items + scorable[-max_to_score:]
        new_items.sort(key=lambda item: parse_date_for_sort(item["pub_date"]))
        print(
            f"Skipped AI scoring for {unscored_overflow_count} older candidate(s) "
            f"(MAX_ITEMS_TO_SCORE={max_to_score}).",
            flush=True,
        )

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
            and ai_provider() is not None
        )
        low_relevance_count = 0
        send_items = []
        for item in new_items:
            # 방산 전문 연재는 관련성 점수·채점 실패와 무관하게 반드시 발송
            if item.get("is_column"):
                send_items.append(item)
                continue
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
            if stale_count:
                notes.append(f"오래된 기사 제외: {stale_count}건")
            if noise_count:
                notes.append(f"비방산 문맥 제외: {noise_count}건")
            if unscored_overflow_count:
                notes.append(f"채점 한도 초과 생략: {unscored_overflow_count}건")
            if overflow_count:
                notes.append(f"건수 초과 생략: {overflow_count}건")
            separator = format_separator()
            if notes:
                separator += "\n" + " · ".join(notes)
            send_telegram_message(separator)
            total_items = len(send_items)
            # 국내 칼럼/영어/비영어를 섹션으로 나눠 발송하고 번호도 섹션별로 매긴다
            column_items = [i for i in send_items if i.get("is_column")]
            foreign_items = [i for i in send_items if not i.get("is_column")]
            sections = [
                ("국내 방산 칼럼", column_items),
                ("영어 뉴스", [i for i in foreign_items if is_english_item(i)]),
                ("비영어 뉴스", [i for i in foreign_items if not is_english_item(i)]),
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
                    if ai_provider() == "gemini":
                        status += (
                            "\n⚠️ Gemini 일일 무료 한도 초과 — 내일 자동 회복됩니다."
                        )
                    else:
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
        f"dropped {noise_count} noise item(s), "
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

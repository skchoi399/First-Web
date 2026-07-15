from datetime import datetime
from zoneinfo import ZoneInfo
import html
import io
import json
import re
import urllib.parse
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

import pandas as pd
import streamlit as st


st.set_page_config(
    page_title="인테리어·건설 Daily Briefing",
    page_icon="🏗️",
    layout="wide",
)

st.markdown(
    """
    <style>
    .block-container {padding-top: 1.8rem; padding-bottom: 3rem; max-width: 1450px;}
    [data-testid="stMetric"] {background:#fff; border:1px solid #e7eaf0; padding:16px; border-radius:14px;}
    .brief {background:#effcf7; border:1px solid #77ddb1; border-radius:14px; padding:18px 20px; color:#174f3b;}
    .news-card {background:#fff; border:1px solid #e6e9ef; border-left:5px solid #1c8c68;
                border-radius:12px; padding:15px 17px; margin-bottom:12px; min-height:138px;}
    .news-meta {color:#7a8290; font-size:0.82rem; margin-bottom:7px;}
    .news-title {font-size:1.03rem; font-weight:700; line-height:1.45; margin-bottom:7px;}
    .tag {display:inline-block; background:#edf7f3; color:#176b51; border-radius:999px;
          padding:3px 9px; font-size:0.75rem; margin-right:5px;}
    .muted {color:#7a8290; font-size:0.86rem;}
    </style>
    """,
    unsafe_allow_html=True,
)


FEEDS = {
    "국토부·정책": "https://news.google.com/rss/search?q=%EA%B5%AD%ED%86%A0%EA%B5%90%ED%86%B5%EB%B6%80%20%EA%B1%B4%EC%84%A4&hl=ko&gl=KR&ceid=KR:ko",
    "건축문화신문": "https://www.ancnews.kr/rss/allArticle.xml",
    "대한전문건설신문": "https://www.koscaj.com/rss/allArticle.xml",
}

KEYWORDS = {
    "정책·법규": ["법", "제도", "정책", "기준", "규제", "국토부", "공고"],
    "공사비·원가": ["공사비", "원가", "가격", "단가", "물가", "노임", "환율"],
    "건축자재": ["자재", "철강", "구리", "유리", "타일", "목재", "시멘트", "가구"],
    "실내건축": ["실내건축", "인테리어", "리모델링", "마감", "공간"],
    "안전·하도급": ["안전", "하도급", "산재", "중대재해", "사고"],
}


def clean_text(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value or "")
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def classify(text: str) -> list[str]:
    found = [name for name, words in KEYWORDS.items() if any(w in text for w in words)]
    return found[:2] or ["건설·건축"]


def fetch_text(url: str, timeout: int = 15) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; InteriorDaily/1.0)",
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.7",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read()
    for encoding in ("utf-8", "euc-kr", "cp949"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


@st.cache_data(ttl=3600, show_spinner=False)
def load_market_snapshot() -> tuple[pd.DataFrame, list[str]]:
    rows, failed = [], []
    try:
        exchange_html = fetch_text("https://www.smbs.biz/ExRate/TodayExRate.jsp")
        exchange_text = clean_text(exchange_html)
        patterns = {
            "달러/원": r"(?:미국\s*달러|USD).*?([0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]+)?)",
            "유로/원": r"(?:유로|EUR).*?([0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]+)?)",
        }
        for name, pattern in patterns.items():
            match = re.search(pattern, exchange_text, re.I)
            if match:
                rows.append(
                    {
                        "지표": name,
                        "현재값": float(match.group(1).replace(",", "")),
                        "단위": "원",
                        "출처": "서울외국환중개",
                        "공식 조회": "https://www.smbs.biz/ExRate/TodayExRate.jsp",
                    }
                )
            else:
                failed.append(name)
    except Exception:
        failed.extend(["달러/원", "유로/원"])

    try:
        eia_html = fetch_text("https://www.eia.gov/todayinenergy/prices.php")
        eia_text = clean_text(eia_html)
        for name in ("WTI", "Brent"):
            match = re.search(rf"\b{name}\b\s+([0-9]+(?:\.[0-9]+)?)", eia_text, re.I)
            if match:
                rows.append(
                    {
                        "지표": name + " 유가",
                        "현재값": float(match.group(1)),
                        "단위": "USD/배럴",
                        "출처": "미국 에너지정보청(EIA)",
                        "공식 조회": "https://www.eia.gov/todayinenergy/prices.php",
                    }
                )
            else:
                failed.append(name + " 유가")
    except Exception:
        failed.extend(["WTI 유가", "Brent 유가"])
    return pd.DataFrame(rows), failed


@st.cache_data(ttl=3600, show_spinner=False)
def load_news() -> tuple[pd.DataFrame, list[str]]:
    rows, failed = [], []
    for source, url in FEEDS.items():
        try:
            request = urllib.request.Request(
                url, headers={"User-Agent": "Mozilla/5.0 (compatible; DailyBriefing/1.0)"}
            )
            with urllib.request.urlopen(request, timeout=8) as response:
                root = ET.fromstring(response.read())
            items = root.findall(".//item")
            if not items:
                raise ValueError("empty feed")
            for item in items[:20]:
                def node_text(tag: str) -> str:
                    node = item.find(tag)
                    return node.text if node is not None and node.text else ""

                title = clean_text(node_text("title"))
                summary = clean_text(node_text("description"))
                published = node_text("pubDate")
                rows.append(
                    {
                        "source": source,
                        "title": title,
                        "summary": summary[:220],
                        "link": node_text("link"),
                        "published": published,
                        "tags": classify(f"{title} {summary}"),
                    }
                )
        except Exception:
            failed.append(source)
    return pd.DataFrame(rows), failed


@st.cache_data(ttl=21600, show_spinner=False)
def load_commodity_prices() -> tuple[pd.DataFrame, str, list[str], dict]:
    """Load daily market prices first, with monthly FRED data as a fallback."""
    market_series = {
        "HG=F": "구리",
        "ALI=F": "알루미늄",
        "NI=F": "니켈",
        "TIO=F": "철광석",
        "LBS=F": "원목",
    }
    units = {
        "구리": "USD/파운드",
        "알루미늄": "USD/톤",
        "니켈": "USD/톤",
        "철광석": "USD/톤",
        "원목": "USD/1,000 board feet",
    }
    frames, failed, details, loaded_names = [], [], {}, set()
    for symbol, korean_name in market_series.items():
        try:
            encoded_symbol = urllib.parse.quote(symbol, safe="")
            url = (
                "https://query1.finance.yahoo.com/v8/finance/chart/"
                f"{encoded_symbol}?range=3mo&interval=1d&events=history"
            )
            request = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (compatible; InteriorDaily/1.0)",
                    "Accept": "application/json,*/*",
                },
            )
            with urllib.request.urlopen(request, timeout=20) as response:
                payload = json.loads(response.read().decode("utf-8"))
            result = payload["chart"]["result"][0]
            timestamps = result.get("timestamp", [])
            closes = result["indicators"]["quote"][0].get("close", [])
            frame = pd.DataFrame(
                {
                    "date": pd.to_datetime(timestamps, unit="s", utc=True).tz_localize(None),
                    korean_name: closes,
                }
            )
            frame[korean_name] = pd.to_numeric(frame[korean_name], errors="coerce")
            frame = frame.dropna(subset=["date", korean_name])
            if frame.empty:
                raise ValueError("공표값 없음")
            frames.append(frame)
            loaded_names.add(korean_name)
            details[korean_name] = {
                "unit": units[korean_name],
                "source": "Yahoo Finance 선물 일별 종가",
            }
        except Exception as exc:
            failed.append(f"{korean_name} 일별: {type(exc).__name__}")

    # 일별 심볼이 없는 품목만 FRED·IMF 월별 공표값으로 각각 보완합니다.
    fred_series = {
        "PCOPPUSDM": ("구리", "USD/톤"),
        "PALUMUSDM": ("알루미늄", "USD/톤"),
        "PNICKUSDM": ("니켈", "USD/톤"),
        "PIORECRUSDM": ("철광석", "USD/톤"),
        "PLOGOREUSDM": ("원목", "USD/㎥"),
    }
    for code, (korean_name, fred_unit) in fred_series.items():
        if korean_name in loaded_names:
            continue
        try:
            url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={code}"
            request = urllib.request.Request(
                url,
                headers={"User-Agent": "Mozilla/5.0", "Accept": "text/csv,*/*"},
            )
            with urllib.request.urlopen(request, timeout=20) as response:
                frame = pd.read_csv(io.BytesIO(response.read()))
            frame.columns = ["date", korean_name]
            frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
            frame[korean_name] = pd.to_numeric(frame[korean_name], errors="coerce")
            frame = frame.dropna(subset=["date", korean_name])
            if frame.empty:
                raise ValueError("공표값 없음")
            frames.append(frame)
            loaded_names.add(korean_name)
            details[korean_name] = {
                "unit": fred_unit,
                "source": "FRED · IMF 월별 공표가격",
            }
        except Exception as exc:
            failed.append(f"{korean_name} 월별: {type(exc).__name__}")
    if not frames:
        return pd.DataFrame(), "원자재 자료 연결 대기", failed, details
    sources = sorted({item["source"] for item in details.values()})
    source = " + ".join(sources)
    out = frames[0]
    for frame in frames[1:]:
        out = out.merge(frame, on="date", how="outer")
    return out.sort_values("date").tail(90), source, failed, details


@st.cache_data(ttl=3600, show_spinner=False)
def load_franchise_interior_costs(
    service_key: str, base_year: str, brand_manage_no: str
) -> tuple[pd.DataFrame, int]:
    params = {
        "serviceKey": service_key,
        "pageNo": 1,
        "numOfRows": 100,
        "year": base_year.strip(),
        "brandManageNo": brand_manage_no.strip(),
    }

    url = (
        "https://apis.data.go.kr/1130000/FftcBrandFrcsIntInfo2_Service/"
        "getbrandFrcsBzmnIntrrctinfo?" + urllib.parse.urlencode(params)
    )
    request = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0 (compatible; InteriorDaily/1.0)"}
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise ValueError(f"HTTP {exc.code}: {clean_text(detail)}") from exc
    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception:
        root = ET.fromstring(raw)
        payload = {
            "response": {
                "header": {
                    "resultCode": root.findtext(".//resultCode", ""),
                    "resultMsg": root.findtext(".//resultMsg", ""),
                },
                "body": {
                    "totalCount": root.findtext(".//totalCount", "0"),
                    "items": [
                        {child.tag: child.text or "" for child in item}
                        for item in root.findall(".//item")
                    ],
                },
            }
        }

    api_response = payload.get("response", payload)
    header = api_response.get("header", {})
    code = str(header.get("resultCode", ""))
    if code not in {"00", "0"}:
        raise ValueError(f"{header.get('resultMsg', 'API 요청 실패')} (코드 {code})")
    body = api_response.get("body", {})
    items = body.get("items", [])
    if isinstance(items, dict):
        items = items.get("item", items)
    if isinstance(items, dict):
        items = [items]
    if not isinstance(items, list):
        items = []

    def pick(item: dict, *names: str):
        for name in names:
            if item.get(name) not in {None, ""}:
                return item.get(name)
        return ""

    rows = []
    for item in items:
        rows.append(
            {
                "기준년도": pick(item, "year", "yr", "baseYr", "bsnsYr"),
                "브랜드명": pick(item, "brandNm", "brndNm"),
                "브랜드관리번호": pick(item, "brandManageNo", "brandMnno", "brndMngNo"),
                "단위면적 인테리어금액": pd.to_numeric(
                    pick(item, "unitArIntrrAmt", "unitAreaIntrrAmt"), errors="coerce"
                ),
                "인테리어금액": pd.to_numeric(
                    pick(item, "intrrAmt", "interiorAmt"), errors="coerce"
                ),
                "점포기준면적": pd.to_numeric(
                    pick(item, "storCrtraAr", "storeStdArea", "storBasAr"), errors="coerce"
                ),
            }
        )
    return pd.DataFrame(rows), int(body.get("totalCount", len(rows)) or 0)


@st.cache_data(ttl=21600, show_spinner=False)
def load_franchise_brand_list(service_key: str, base_year: str) -> pd.DataFrame:
    """Load franchise outlets and reduce them to unique brand-name suggestions."""
    params = {
        "serviceKey": service_key,
        "pageNo": 1,
        "numOfRows": 1000,
        "year": base_year.strip(),
    }
    url = (
        "https://apis.data.go.kr/1130000/FftcbrandfrcslistinfoService/"
        "getbrandFrcsListinfo?" + urllib.parse.urlencode(params)
    )
    request = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0 (compatible; InteriorDaily/1.0)"}
    )
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise ValueError(f"HTTP {exc.code}: {clean_text(detail)}") from exc
    try:
        payload = json.loads(raw.decode("utf-8"))
        api_response = payload.get("response", payload)
        header = api_response.get("header", {})
        code = str(header.get("resultCode", ""))
        if code not in {"00", "0"}:
            raise ValueError(f"{header.get('resultMsg', 'API 요청 실패')} (코드 {code})")
        body = api_response.get("body", {})
        items = body.get("items", [])
        if isinstance(items, dict):
            items = items.get("item", items)
        if isinstance(items, dict):
            items = [items]
    except (json.JSONDecodeError, UnicodeDecodeError):
        root = ET.fromstring(raw)
        code = root.findtext(".//resultCode", "")
        if code not in {"00", "0"}:
            raise ValueError(root.findtext(".//resultMsg", "API 요청 실패"))
        items = [
            {child.tag: child.text or "" for child in item}
            for item in root.findall(".//item")
        ]
    if not isinstance(items, list):
        items = []

    def pick(item: dict, *names: str):
        for name in names:
            if item.get(name) not in {None, ""}:
                return str(item.get(name))
        return ""

    rows = []
    for item in items:
        rows.append(
            {
                "브랜드명": pick(item, "brandNm", "brndNm"),
                "브랜드관리번호": pick(
                    item, "brandManageNo", "brandMnno", "brndMngNo", "jnghdqrtrsBrandManageNo"
                ),
            }
        )
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    frame = frame[(frame["브랜드명"] != "") & (frame["브랜드관리번호"] != "")]
    return frame.drop_duplicates().sort_values("브랜드명").reset_index(drop=True)


G2B_ENDPOINTS = {
    "건축": "getPriceInfoListFcltyCmmnMtrilBildng",
    "기계설비": "getPriceInfoListFcltyCmmnMtrilMchnEqp",
    "전기·정보통신": "getPriceInfoListFcltyCmmnMtrilElctyIrmc",
}


def get_data_go_key() -> str:
    try:
        return str(st.secrets.get("DATA_GO_KR_KEY", "")).strip()
    except Exception:
        return ""


@st.cache_data(ttl=3600, show_spinner=False)
def load_g2b_prices(
    service_key: str, category: str, item_keyword: str, spec_keyword: str
) -> tuple[pd.DataFrame, int]:
    endpoint = G2B_ENDPOINTS[category]
    params = {
        "serviceKey": service_key,
        "pageNo": 1,
        "numOfRows": 999,
        "type": "json",
    }
    if item_keyword.strip():
        params["prdctClsfcNoNm"] = item_keyword.strip()
    if spec_keyword.strip():
        params["krnPrdctNm"] = spec_keyword.strip()

    url = (
        f"https://apis.data.go.kr/1230000/ao/PriceInfoService/{endpoint}?"
        + urllib.parse.urlencode(params)
    )
    request = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0 (compatible; InteriorDaily/1.0)"}
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        payload = json.loads(response.read().decode("utf-8"))

    api_response = payload.get("response", payload)
    header = api_response.get("header", {})
    result_code = str(header.get("resultCode", ""))
    if result_code not in {"00", "0"}:
        message = header.get("resultMsg", "공공데이터 API 요청 실패")
        raise ValueError(f"{message} (코드 {result_code})")

    body = api_response.get("body", {})
    raw_items = body.get("items", {})
    if isinstance(raw_items, dict):
        raw_items = raw_items.get("item", [])
    if isinstance(raw_items, dict):
        raw_items = [raw_items]
    if not isinstance(raw_items, list):
        raw_items = []

    rows = []
    for item in raw_items:
        rows.append(
            {
                "품명": item.get("prdctClsfcNoNm", ""),
                "규격": item.get("krnPrdctNm", ""),
                "단위": item.get("unit", ""),
                "가격(원)": pd.to_numeric(item.get("prce"), errors="coerce"),
                "게시일": str(item.get("nticeDt", ""))[:10],
                "공급지역": item.get("splyJrsdctRgnNm", ""),
                "VAT": item.get("vatYnNm", ""),
                "인도조건": item.get("dlvryCndtnNm", ""),
                "가격구분": item.get("prceDiv", ""),
                "물품분류번호": item.get("prdctClsfcNo", ""),
            }
        )
    return pd.DataFrame(rows), int(body.get("totalCount", len(rows)) or 0)


def news_card(row: pd.Series) -> str:
    tags = "".join(f'<span class="tag">{html.escape(tag)}</span>' for tag in row["tags"])
    summary = html.escape(row["summary"] or "원문에서 세부 내용을 확인하세요.")
    title = html.escape(row["title"])
    link = html.escape(row["link"], quote=True)
    return f"""
    <div class="news-card">
      <div class="news-meta">{html.escape(row['source'])} · {html.escape(row['published'][:22])}</div>
      <div class="news-title"><a href="{link}" target="_blank">{title}</a></div>
      <div class="muted">{summary}</div>
      <div style="margin-top:9px">{tags}</div>
    </div>
    """


today = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y.%m.%d %H:%M KST")
st.caption("PUBLIC DATA DASHBOARD")
left, right = st.columns([4, 1])
with left:
    st.title("🏗️ 인테리어·건설 Daily Briefing")
    st.caption("공개된 시황·정책·건설뉴스를 한 화면에서 확인합니다.")
with right:
    st.markdown(f"<div style='text-align:right;color:#7a8290;padding-top:18px'>{today}<br>접속 시 자동 갱신</div>", unsafe_allow_html=True)

news, failed_feeds = load_news()
market_snapshot, failed_market = load_market_snapshot()
prices, price_source, failed_prices, price_details = load_commodity_prices()

news_count = len(news)
policy_count = int(news["tags"].apply(lambda x: "정책·법규" in x).sum()) if news_count else 0
material_count = int(news["tags"].apply(lambda x: "건축자재" in x).sum()) if news_count else 0

m1, m2, m3, m4 = st.columns(4)
m1.metric("수집 뉴스", f"{news_count}건")
m2.metric("정책·법규", f"{policy_count}건")
m3.metric("자재 관련", f"{material_count}건")
m4.metric("원자재 기준", prices["date"].max().strftime("%Y.%m") if not prices.empty else "연결 대기")

st.markdown(
    """
    <div class="brief"><b>오늘의 활용법</b><br>
    ① 정책·법규 변경 확인 → ② 자재·공사비 기사 확인 → ③ 원자재 3개월 추세 확인<br>
    <span class="muted">공개 시장지표는 견적 검토의 참고자료이며 실제 계약단가를 의미하지 않습니다.</span></div>
    """,
    unsafe_allow_html=True,
)

tab1, tab_market, tab2, tab3, tab4, tab5 = st.tabs(
    ["📰 주요 뉴스", "🌍 시장동향", "📈 원자재 동향", "🧱 이번주 원자재 영향", "💰 조달 자재가격", "🏪 프랜차이즈 가맹점 투자비 현황"]
)

with tab1:
    st.subheader("건설·건축·실내건축 주요 뉴스")
    categories = ["전체", *KEYWORDS.keys()]
    category = st.selectbox("분류", categories, label_visibility="collapsed")
    filtered = news
    if category != "전체" and not news.empty:
        filtered = news[news["tags"].apply(lambda tags: category in tags)]
    if filtered.empty:
        st.info("현재 불러온 뉴스가 없습니다. 잠시 후 새로고침하거나 원문 링크를 확인해 주세요.")
    else:
        cols = st.columns(2)
        for i, (_, row) in enumerate(filtered.head(12).iterrows()):
            with cols[i % 2]:
                st.markdown(news_card(row), unsafe_allow_html=True)
    if failed_feeds:
        st.caption("일시적으로 연결되지 않은 출처: " + ", ".join(failed_feeds))

with tab_market:
    st.subheader("환율·에너지·주요 자재 시장동향")
    st.caption("공식 공개페이지에서 자동 확인 가능한 값과, 별도 구독·조회 대상 자료를 구분했습니다.")
    if market_snapshot.empty:
        st.warning("환율·유가 공개페이지가 현재 응답하지 않습니다. 아래 공식 조회 링크를 이용해 주세요.")
    else:
        st.dataframe(
            market_snapshot,
            use_container_width=True,
            hide_index=True,
            column_config={
                "현재값": st.column_config.NumberColumn(format="%,.2f"),
                "공식 조회": st.column_config.LinkColumn(display_text="열기"),
            },
        )
    if failed_market:
        st.caption("자동 갱신 대기: " + " · ".join(dict.fromkeys(failed_market)))

    st.markdown("#### 추가 자재가격 공식 조회")
    source_guide = pd.DataFrame(
        [
            ["철광석", "USD/톤", "한국자원정보서비스(KOMIS)", "공식 사이트 조회", "https://www.komis.or.kr/"],
            ["니켈·알루미늄", "USD/톤", "런던금속거래소(LME)", "라이선스 데이터", "https://www.lme.com/market-data/accessing-market-data"],
            ["STS", "USD/톤", "LME 원재료 지표 참고", "LME 직접 계약 품목 아님", "https://www.lme.com/market-data/accessing-market-data"],
            ["유리", "원/장", "한국물가정보", "회원·가격자료 조회", "https://www.kpi.or.kr/www/"],
            ["MDF·합판·석고보드", "원/장", "한국목재신문", "기사·시황 조회", "https://www.woodkorea.co.kr/"],
        ],
        columns=["품목", "요청 단위", "출처", "자동 연동 상태", "공식 조회"],
    )
    st.dataframe(
        source_guide,
        use_container_width=True,
        hide_index=True,
        column_config={"공식 조회": st.column_config.LinkColumn(display_text="열기")},
    )
    st.info("LME·한국물가정보의 가격을 웹에 자동 재게시하려면 해당 기관의 데이터 이용권한 또는 API 계약이 필요합니다.")

with tab2:
    st.subheader("글로벌 원자재 가격 추세")
    st.caption(f"출처: {price_source} · 최신 종가/공표값 기준이며 실시간 체결가격이 아닙니다.")
    if prices.empty:
        st.error("원자재 제공처가 현재 응답하지 않습니다.")
        if failed_prices:
            st.caption("연결 진단: " + " · ".join(failed_prices))
        if st.button("원자재 다시 불러오기"):
            load_commodity_prices.clear()
            st.rerun()
    else:
        options = [c for c in prices.columns if c != "date"]
        selected = st.multiselect("표시 품목", options, default=options[:3])
        if selected:
            unit_text = " · ".join(
                f"{name}: {price_details.get(name, {}).get('unit', '단위 확인 중')}"
                for name in selected
            )
            st.info("금액 단위 — " + unit_text)
            chart_data = prices.set_index("date")[selected]
            st.line_chart(chart_data, height=440)
            cards = st.columns(len(selected))
            for card, name in zip(cards, selected):
                item_values = prices[name].dropna()
                if len(item_values) >= 2:
                    prev, curr = item_values.iloc[-2], item_values.iloc[-1]
                    change = ((curr / prev) - 1) * 100 if pd.notna(prev) and prev else 0
                    item_source = price_details.get(name, {}).get("source", "")
                    period_label = "전일비" if "일별" in item_source else "전월비"
                    item_unit = price_details.get(name, {}).get("unit", "")
                    card.metric(f"{name} ({item_unit})", f"{curr:,.1f}", f"{change:+.1f}% {period_label}")
        if failed_prices:
            st.caption("일부 품목 연결 대기: " + " · ".join(failed_prices))

with tab3:
    st.subheader("이번주 원자재 영향")
    st.caption("일별 자료는 약 1주 전과, 월별 대체자료는 직전 공표월과 비교합니다.")
    impact_map = [
        ("금속공사", "니켈", "스테인리스·금속가공 단가와 납기 확인"),
        ("전기공사", "구리", "전선·케이블 자재비 변동 확인"),
        ("제작가구", "원목", "합판·목재와 수입 하드웨어 견적 확인"),
        ("철골·금속", "철광석", "형강·철판 견적과 가공비 분리 확인"),
        ("창호·금속", "알루미늄", "알루미늄 프레임·부속 단가 확인"),
    ]
    impact_rows = []
    for process, material, point in impact_map:
        change_text, signal, comparison = "연결 대기", "자료 확인 필요", "-"
        item_source = price_details.get(material, {}).get("source", "연결 대기")
        item_unit = price_details.get(material, {}).get("unit", "-")
        if material in prices.columns:
            values = prices[["date", material]].dropna().sort_values("date")
            if len(values) >= 2:
                is_daily = "일별" in item_source
                lookback = min(6 if is_daily else 2, len(values))
                old, new = values[material].iloc[-lookback], values[material].iloc[-1]
                change = ((new / old) - 1) * 100 if old else 0
                change_text = f"{change:+.1f}%"
                comparison = "최근 1주" if is_daily else "전월 대비"
                signal = "상승 압력" if change > 1 else "하락 가능" if change < -1 else "보합권"
        impact_rows.append([process, material, item_unit, comparison, change_text, signal, point, item_source])
    impact = pd.DataFrame(
        impact_rows,
        columns=["관련 공정", "원자재", "단위", "비교 기준", "가격 변동", "비용 신호", "구매 검토 포인트", "자료 출처"],
    )
    st.dataframe(impact, use_container_width=True, hide_index=True)
    st.caption("원자재 변동이 실제 자재 견적에 반영되는 시점과 폭은 환율·재고·가공비·운송비에 따라 달라집니다.")
    st.warning("이 화면에는 회사 내부 견적, 협력사 정보, 점포명 등 비공개 데이터를 입력하지 마세요.")

with tab4:
    st.subheader("나라장터 시설공통자재 가격 조회")
    st.caption(
        "조달청 가격정보현황서비스의 공개 참고가격입니다. 실제 시장가·견적가·계약단가와 다를 수 있습니다."
    )
    service_key = get_data_go_key()
    if not service_key:
        st.error("API 인증키가 없습니다. Streamlit 앱 설정의 Secrets에 DATA_GO_KR_KEY를 저장해 주세요.")
    else:
        st.success("공공데이터포털 인증키가 연결되어 있습니다.")
        c1, c2, c3 = st.columns([1, 1.4, 1.4])
        with c1:
            g2b_category = st.selectbox("공사 분야", list(G2B_ENDPOINTS))
        with c2:
            if st.button("이 분야 품명 목록 불러오기", use_container_width=True):
                try:
                    catalog, _ = load_g2b_prices(service_key, g2b_category, "", "")
                    st.session_state["g2b_names"] = sorted(
                        catalog["품명"].dropna().astype(str).unique().tolist()
                    )
                    st.session_state["g2b_names_category"] = g2b_category
                    st.success(
                        f"{g2b_category} 분야 품명 {len(st.session_state['g2b_names']):,}개를 불러왔습니다."
                    )
                except Exception as exc:
                    st.error(f"품명 목록을 불러오지 못했습니다: {exc}")
            available_names = (
                st.session_state.get("g2b_names", [])
                if st.session_state.get("g2b_names_category") == g2b_category
                else []
            )
            g2b_item = st.selectbox(
                "품명",
                ["직접 입력", *available_names],
                help="먼저 위의 품명 목록 버튼을 누르세요.",
            )
        with c3:
            manual_item = st.text_input(
                "품명 직접 입력", placeholder="목록에 없으면 예: 타일, 유리, 전선"
            )
            g2b_spec = st.text_input(
                "규격명(선택)", placeholder="예: 자기질, 강화, 난연"
            )

        selected_item = manual_item.strip() or ("" if g2b_item == "직접 입력" else g2b_item)

        if st.button("가격 조회", type="primary", use_container_width=True):
            if not selected_item and not g2b_spec.strip():
                st.warning("품명이나 규격명 중 하나를 입력해 주세요. 처음에는 ‘타일’을 추천합니다.")
            else:
                try:
                    with st.spinner("조달청 공개가격을 조회하고 있습니다..."):
                        g2b_prices, total_count = load_g2b_prices(
                            service_key, g2b_category, selected_item, g2b_spec
                        )
                    if g2b_prices.empty:
                        st.info("검색 결과가 없습니다. 품명을 짧게 입력해 보세요. 예: 타일, 유리, 전선")
                    else:
                        st.metric("검색 결과", f"전체 {total_count:,}건 · 최대 999건 표시")
                        st.dataframe(
                            g2b_prices,
                            use_container_width=True,
                            hide_index=True,
                            column_config={
                                "가격(원)": st.column_config.NumberColumn(format="%,.0f원")
                            },
                        )
                        csv_data = g2b_prices.to_csv(index=False).encode("utf-8-sig")
                        st.download_button(
                            "조회 결과 CSV 다운로드",
                            data=csv_data,
                            file_name=f"나라장터_{g2b_category}_{selected_item or g2b_spec}.csv",
                            mime="text/csv",
                        )
                except Exception as exc:
                    st.error(f"조달청 API 연결에 실패했습니다: {exc}")
                    st.caption(
                        "신규 승인 직후라면 인증키 적용에 시간이 걸릴 수 있습니다. "
                        "공공데이터포털의 Decoding 인증키인지도 확인해 주세요."
                    )

with tab5:
    st.subheader("프랜차이즈 가맹점 투자비 현황")
    st.caption("정보공개서 기반 공개자료입니다. 실제 공사 견적이나 현재 계약금액과 다를 수 있습니다.")
    service_key = get_data_go_key()
    if not service_key:
        st.error("Streamlit Secrets에 DATA_GO_KR_KEY를 저장해 주세요.")
    else:
        current_year = datetime.now(ZoneInfo("Asia/Seoul")).year
        year_options = [str(year) for year in range(current_year - 1, 2017, -1)]
        franchise_year = st.selectbox(
            "정보공개서 기준년도(필수)",
            year_options,
            index=min(1, len(year_options) - 1),
            help="공표 시차 때문에 최근 연도에 자료가 없으면 한 해 이전을 선택해 보세요.",
        )
        brand_query = st.text_input(
            "프랜차이즈 브랜드 검색", placeholder="예: 메가커피, 교촌, 올리브영"
        )
        selected_brand_no = ""
        try:
            with st.spinner("브랜드 목록을 준비하고 있습니다..."):
                brand_catalog = load_franchise_brand_list(service_key, franchise_year)
            if brand_query.strip():
                matched = brand_catalog[
                    brand_catalog["브랜드명"].str.contains(
                        brand_query.strip(), case=False, na=False, regex=False
                    )
                ].head(30)
                if matched.empty:
                    st.info("추천 브랜드가 없습니다. 검색어를 짧게 입력해 보세요.")
                else:
                    labels = [
                        f"{row['브랜드명']}  ·  {row['브랜드관리번호']}"
                        for _, row in matched.iterrows()
                    ]
                    selected_label = st.selectbox("추천 브랜드", labels)
                    selected_brand_no = selected_label.rsplit("  ·  ", 1)[-1]
                    st.success(f"브랜드관리번호 {selected_brand_no}가 자동 선택되었습니다.")
            else:
                st.caption("브랜드명을 입력하면 아래에 최대 30개의 추천 브랜드가 표시됩니다.")
        except Exception as exc:
            st.warning(f"브랜드 추천 목록을 불러오지 못했습니다: {exc}")
            st.caption("승인 직후라면 잠시 뒤 다시 시도할 수 있습니다.")

        manual_brand_no = st.text_input(
            "브랜드관리번호 직접 입력(선택)",
            placeholder="추천이 없을 때만 입력",
        )
        franchise_brand_no = selected_brand_no or manual_brand_no.strip()
        if st.button("인테리어 투자비 조회", type="primary", use_container_width=True):
            if not franchise_brand_no.strip():
                st.warning("브랜드를 검색해서 선택하거나 브랜드관리번호를 입력해 주세요.")
            else:
                try:
                    with st.spinner("가맹정보를 조회하고 있습니다..."):
                        franchise_costs, franchise_total = load_franchise_interior_costs(
                            service_key, franchise_year, franchise_brand_no
                        )
                    if franchise_costs.empty:
                        st.info("검색 결과가 없습니다. 기준년도나 브랜드관리번호를 확인해 주세요.")
                    else:
                        st.metric("검색 결과", f"전체 {franchise_total:,}건 · 최대 100건 표시")
                        st.dataframe(
                            franchise_costs,
                            use_container_width=True,
                            hide_index=True,
                            column_config={
                                "단위면적 인테리어금액": st.column_config.NumberColumn(format="%,.0f원"),
                                "인테리어금액": st.column_config.NumberColumn(format="%,.0f원"),
                                "점포기준면적": st.column_config.NumberColumn(format="%,.2f㎡"),
                            },
                        )
                except Exception as exc:
                    st.error(f"가맹정보 API 연결에 실패했습니다: {exc}")
                    st.caption("신규 승인 직후라면 인증키 반영까지 시간이 걸릴 수 있습니다.")

st.divider()
st.caption("자료 출처: Google News의 국토부·정책 검색 RSS, 각 전문매체 RSS, Yahoo Finance 원자재 선물·FRED·IMF 공표가격, 조달청 가격정보현황서비스, 공정거래위원회 가맹정보. 원문 저작권은 각 제공처에 있습니다.")

from datetime import datetime, timezone
import html
import json
import re
import urllib.parse
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


@st.cache_data(ttl=86400, show_spinner=False)
def load_commodity_prices() -> tuple[pd.DataFrame, str]:
    """Load individual IMF commodity series through FRED's stable CSV endpoint."""
    series = {
        "PCOPPUSDM": "구리",
        "PALUMUSDM": "알루미늄",
        "PNICKUSDM": "니켈",
        "PIORECRUSDM": "철광석",
        "PLOGOREUSDM": "원목",
    }
    frames = []
    for code, korean_name in series.items():
        try:
            url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={code}"
            frame = pd.read_csv(url)
            frame.columns = ["date", korean_name]
            frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
            frame[korean_name] = pd.to_numeric(frame[korean_name], errors="coerce")
            frames.append(frame.dropna(subset=["date"]))
        except Exception:
            continue
    if not frames:
        return pd.DataFrame(), "FRED 원자재 자료 연결 대기"
    out = frames[0]
    for frame in frames[1:]:
        out = out.merge(frame, on="date", how="outer")
    return out.sort_values("date").tail(36), "FRED · IMF Primary Commodity Prices"


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
        "numOfRows": 100,
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


today = datetime.now(timezone.utc).astimezone().strftime("%Y.%m.%d %H:%M")
st.caption("PUBLIC DATA DASHBOARD")
left, right = st.columns([4, 1])
with left:
    st.title("🏗️ 인테리어·건설 Daily Briefing")
    st.caption("공개된 시황·정책·건설뉴스를 한 화면에서 확인합니다.")
with right:
    st.markdown(f"<div style='text-align:right;color:#7a8290;padding-top:18px'>{today}<br>접속 시 자동 갱신</div>", unsafe_allow_html=True)

news, failed_feeds = load_news()
prices, price_source = load_commodity_prices()

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

tab1, tab2, tab3, tab4 = st.tabs(
    ["📰 주요 뉴스", "📈 원자재 동향", "🧱 품목별 영향", "💰 조달 자재가격"]
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

with tab2:
    st.subheader("글로벌 원자재 월별 추세")
    st.caption(f"출처: {price_source} · 최신 공표값 기준이며 실시간 거래가격이 아닙니다.")
    if prices.empty:
        st.info("원자재 자료가 일시적으로 연결되지 않았습니다. 뉴스 탭은 계속 사용할 수 있습니다.")
    else:
        options = [c for c in prices.columns if c != "date"]
        selected = st.multiselect("표시 품목", options, default=options[:3])
        if selected:
            chart_data = prices.set_index("date")[selected]
            st.line_chart(chart_data, height=440)
            latest = prices.dropna(subset=selected, how="all").tail(2)
            if len(latest) == 2:
                cards = st.columns(len(selected))
                for card, name in zip(cards, selected):
                    prev, curr = latest[name].iloc[-2], latest[name].iloc[-1]
                    change = ((curr / prev) - 1) * 100 if pd.notna(prev) and prev else 0
                    card.metric(name, f"{curr:,.1f}", f"{change:+.1f}% 전월비")

with tab3:
    st.subheader("시장지표가 인테리어 품목에 미치는 일반적 영향")
    impact = pd.DataFrame(
        [
            ["금속공사", "철강·니켈·환율", "스테인리스 및 금속가공 단가 변동 확인"],
            ["전기공사", "구리·환율", "전선·케이블 자재비 인상 근거 확인"],
            ["제작가구", "목재·환율", "합판과 수입 하드웨어 비중 확인"],
            ["타일·석재", "환율·수입물량", "원산지별 재고와 납기 확인"],
            ["유리·창호", "판유리 물가·에너지", "가공비와 운반·양중비 분리 확인"],
            ["도장·마감", "유가·화학제품 물가", "도료·접착제 가격 변동 확인"],
        ],
        columns=["품목", "관련 지표", "구매 검토 포인트"],
    )
    st.dataframe(impact, use_container_width=True, hide_index=True)
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
            g2b_item = st.text_input(
                "품명", placeholder="예: 타일, 유리, 전선, 합판"
            )
        with c3:
            g2b_spec = st.text_input(
                "규격명(선택)", placeholder="예: 자기질, 강화, 난연"
            )

        if st.button("가격 조회", type="primary", use_container_width=True):
            if not g2b_item.strip() and not g2b_spec.strip():
                st.warning("품명이나 규격명 중 하나를 입력해 주세요. 처음에는 ‘타일’을 추천합니다.")
            else:
                try:
                    with st.spinner("조달청 공개가격을 조회하고 있습니다..."):
                        g2b_prices, total_count = load_g2b_prices(
                            service_key, g2b_category, g2b_item, g2b_spec
                        )
                    if g2b_prices.empty:
                        st.info("검색 결과가 없습니다. 품명을 짧게 입력해 보세요. 예: 타일, 유리, 전선")
                    else:
                        st.metric("검색 결과", f"전체 {total_count:,}건 · 최대 100건 표시")
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
                            file_name=f"나라장터_{g2b_category}_{g2b_item or g2b_spec}.csv",
                            mime="text/csv",
                        )
                except Exception as exc:
                    st.error(f"조달청 API 연결에 실패했습니다: {exc}")
                    st.caption(
                        "신규 승인 직후라면 인증키 적용에 시간이 걸릴 수 있습니다. "
                        "공공데이터포털의 Decoding 인증키인지도 확인해 주세요."
                    )

st.divider()
st.caption("자료 출처: Google News의 국토부·정책 검색 RSS, 각 전문매체 RSS, FRED·IMF 원자재 가격, 조달청 가격정보현황서비스. 원문 저작권은 각 제공처에 있습니다.")

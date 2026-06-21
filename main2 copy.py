import json
from openai_codex_auth import authenticate, chat
from skills import search_news
import yfinance as yf

# ============================================================
# 1단계: 인증
# ============================================================
print("=" * 60)
print("  NVIDIA 주가 예측 뉴스 + 재무 지표 종합 분석")
print("=" * 60)

authenticate()

# ============================================================
# 2단계: AI에게 검색 키워드 리스트 요청 (영어, 엄격 포맷)
# ============================================================
print("\n[1/7] NVIDIA 관련 뉴스 검색 키워드 생성 중...")

keyword_prompt = (
    "I need English news search keywords to predict NVIDIA (NVDA) stock price movement. "
    "Give me 5 diverse keywords covering: earnings, AI/GPU demand, competitors, supply chain, and market sentiment. "
    "Rules:\n"
    "- One keyword per line\n"
    "- English only\n"
    "- Do NOT say anything else, no numbering, no explanation, no greeting\n"
    "- Just the raw search keywords, nothing more"
)

keyword_response = chat(keyword_prompt)
keywords = [k.strip() for k in keyword_response.strip().splitlines() if k.strip()]

print(f"  생성된 키워드 ({len(keywords)}개):")
for kw in keywords:
    print(f"    - {kw}")

# ============================================================
# 3단계: 각 키워드로 뉴스 검색 + 중복 제거
# ============================================================
print("\n[2/7] 키워드별 뉴스 검색 중...")

all_articles = []
seen_links = set()

for kw in keywords:
    print(f"  검색 중: \"{kw}\"")
    result = search_news(kw, country="us")
    articles = result.get("results") or []
    count = 0
    for article in articles:
        link = article.get("link", "")
        if link and link not in seen_links:
            seen_links.add(link)
            all_articles.append(article)
            count += 1
    print(f"    -> {count}개 뉴스 추가 (중복 제외)")

print(f"\n  총 수집된 뉴스: {len(all_articles)}개")

if not all_articles:
    print("뉴스를 찾지 못했습니다. 프로그램을 종료합니다.")
    exit()

# ============================================================
# 4단계: NVDA 재무 지표 수집 (yfinance)
# ============================================================
print("\n[3/7] NVIDIA(NVDA) 재무 지표 수집 중...")

ticker = yf.Ticker("NVDA")
info = ticker.info

current_price = info.get("currentPrice")
previous_close = info.get("previousClose")
pe_ratio = info.get("trailingPE")
forward_pe = info.get("forwardPE")
eps = info.get("trailingEps")
market_cap = info.get("marketCap")
week52_high = info.get("fiftyTwoWeekHigh")
week52_low = info.get("fiftyTwoWeekLow")
volume = info.get("volume")
avg_volume = info.get("averageVolume")
roe = info.get("returnOnEquity")
revenue_growth = info.get("revenueGrowth")
profit_margin = info.get("profitMargins")
beta = info.get("beta")
dividend_yield = info.get("dividendYield")

# 등락률 계산
if current_price and previous_close:
    change_pct = ((current_price - previous_close) / previous_close) * 100
else:
    change_pct = None

# 시가총액 포맷팅
def format_market_cap(cap):
    if cap is None:
        return "N/A"
    if cap >= 1e12:
        return f"${cap / 1e12:.2f}T"
    if cap >= 1e9:
        return f"${cap / 1e9:.2f}B"
    return f"${cap / 1e6:.2f}M"

# 최근 5일 종가 (단기 추세 파악용)
print("  최근 5일 주가 데이터 가져오는 중...")
hist = ticker.history(period="5d")
recent_prices = []
for date, row in hist.iterrows():
    date_str = date.strftime("%Y-%m-%d")
    close = row["Close"]
    vol = row["Volume"]
    recent_prices.append(f"    {date_str}: ${close:.2f} (거래량: {vol:,.0f})")

# 콘솔 출력
print(f"\n  --- NVDA 주요 재무 지표 ---")
print(f"  현재가:          ${current_price}" if current_price else "  현재가:          N/A")
print(f"  전일 종가:       ${previous_close}" if previous_close else "  전일 종가:       N/A")
print(f"  등락률:          {change_pct:+.2f}%" if change_pct is not None else "  등락률:          N/A")
print(f"  P/E (Trailing):  {pe_ratio:.2f}" if pe_ratio else "  P/E (Trailing):  N/A")
print(f"  P/E (Forward):   {forward_pe:.2f}" if forward_pe else "  P/E (Forward):   N/A")
print(f"  EPS:             ${eps:.2f}" if eps else "  EPS:             N/A")
print(f"  시가총액:        {format_market_cap(market_cap)}")
print(f"  52주 최고:       ${week52_high:.2f}" if week52_high else "  52주 최고:       N/A")
print(f"  52주 최저:       ${week52_low:.2f}" if week52_low else "  52주 최저:       N/A")
print(f"  거래량:          {volume:,}" if volume else "  거래량:          N/A")
print(f"  평균 거래량:     {avg_volume:,}" if avg_volume else "  평균 거래량:     N/A")
print(f"  ROE:             {roe * 100:.2f}%" if roe else "  ROE:             N/A")
print(f"  매출 성장률:     {revenue_growth * 100:.2f}%" if revenue_growth else "  매출 성장률:     N/A")
print(f"  순이익률:        {profit_margin * 100:.2f}%" if profit_margin else "  순이익률:        N/A")
print(f"  베타:            {beta:.2f}" if beta else "  베타:            N/A")
print(f"  배당 수익률:     {dividend_yield * 100:.2f}%" if dividend_yield else "  배당 수익률:     N/A")

print(f"\n  --- 최근 5일 주가 추세 ---")
for line in recent_prices:
    print(line)

# LLM에 전달할 재무 지표 텍스트 조립
financial_text = (
    f"=== NVIDIA (NVDA) Financial Data ===\n"
    f"Current Price: ${current_price}\n"
    f"Previous Close: ${previous_close}\n"
    f"Change: {change_pct:+.2f}%\n" if change_pct is not None else ""
)
financial_text += (
    f"Trailing P/E: {pe_ratio}\n" if pe_ratio else ""
)
financial_text += (
    f"Forward P/E: {forward_pe}\n" if forward_pe else ""
)
financial_text += (
    f"EPS: ${eps}\n" if eps else ""
)
financial_text += (
    f"Market Cap: {format_market_cap(market_cap)}\n"
    f"52-Week High: ${week52_high}\n" if week52_high else ""
)
financial_text += (
    f"52-Week Low: ${week52_low}\n" if week52_low else ""
)
financial_text += (
    f"Volume: {volume:,}\n" if volume else ""
)
financial_text += (
    f"Average Volume: {avg_volume:,}\n" if avg_volume else ""
)
financial_text += (
    f"ROE: {roe * 100:.2f}%\n" if roe else ""
)
financial_text += (
    f"Revenue Growth: {revenue_growth * 100:.2f}%\n" if revenue_growth else ""
)
financial_text += (
    f"Profit Margin: {profit_margin * 100:.2f}%\n" if profit_margin else ""
)
financial_text += (
    f"Beta: {beta}\n" if beta else ""
)
financial_text += (
    f"Dividend Yield: {dividend_yield * 100:.2f}%\n" if dividend_yield else ""
)

# 최근 5일 추세 추가
financial_text += "\n=== Recent 5-Day Price Trend ===\n"
for date, row in hist.iterrows():
    financial_text += f"{date.strftime('%Y-%m-%d')}: Close=${row['Close']:.2f}, Volume={row['Volume']:,.0f}\n"

print("\n  재무 지표 수집 완료!")

# ============================================================
# 5단계: NVIDIA 주가 관련 뉴스 필터링
# ============================================================
print("\n[4/7] NVIDIA 주가 관련 뉴스 필터링 중...")

news_list_text = ""
for i, article in enumerate(all_articles):
    title = article.get("title", "No title")
    description = (article.get("description") or "")[:200]
    news_list_text += f"[{i}] {title} | {description}\n"

filter_prompt = (
    "Below is a list of news articles. "
    "Decide whether each article is relevant to predicting NVIDIA (NVDA) stock price. "
    "An article is relevant if it discusses NVIDIA's business, financials, AI/GPU industry, "
    "semiconductor market, competitors (AMD, Intel), supply chain, or broader market factors "
    "that could impact NVIDIA stock.\n\n"
    "Rules:\n"
    "- Respond ONLY with a JSON array of the index numbers that are RELEVANT.\n"
    "- Example: [0, 2, 5]\n"
    "- Do NOT say anything else. No explanation, no greeting.\n\n"
    f"Articles:\n{news_list_text}"
)

filter_response = chat(filter_prompt)

try:
    cleaned = filter_response.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    relevant_indices = set(json.loads(cleaned))
except (json.JSONDecodeError, ValueError):
    relevant_indices = set(range(len(all_articles)))
    print("  (필터링 파싱 실패 - 전체 뉴스를 유지합니다)")

kept_articles = []
removed_articles = []
for i, article in enumerate(all_articles):
    if i in relevant_indices:
        kept_articles.append(article)
    else:
        removed_articles.append(article)

print(f"\n  --- 제거된 뉴스 ({len(removed_articles)}개) ---")
for article in removed_articles:
    print(f"    ✕ {article.get('title', 'No title')}")

print(f"\n  --- 유지된 뉴스 ({len(kept_articles)}개) ---")
for article in kept_articles:
    print(f"    ✓ {article.get('title', 'No title')}")

if not kept_articles:
    print("\n관련 뉴스가 없습니다. 프로그램을 종료합니다.")
    exit()

# ============================================================
# 6단계: 뉴스 + 재무 지표 통합 분석
# ============================================================
print("\n[5/7] 뉴스 + 재무 지표 통합 분석 중...")
print("  LLM에게 뉴스와 재무 데이터를 함께 전달합니다.")

kept_news_text = ""
for i, article in enumerate(kept_articles, 1):
    title = article.get("title", "No title")
    description = (article.get("description") or "")[:300]
    pub_date = article.get("pubDate", "Unknown date")
    kept_news_text += f"{i}. [{pub_date}] {title}\n   {description}\n\n"

predict_prompt = (
    "You are a professional stock market analyst. "
    "You have TWO sources of data to analyze NVIDIA (NVDA) stock:\n\n"
    "=== SOURCE 1: Recent News Articles ===\n"
    f"{kept_news_text}\n"
    "=== SOURCE 2: Current Financial Data ===\n"
    f"{financial_text}\n\n"
    "Based on BOTH the news AND the financial data, provide a comprehensive analysis:\n\n"
    "1. 뉴스 분석: 각 뉴스가 NVIDIA 주가에 미치는 영향 (긍정/부정/중립)\n"
    "2. 재무 지표 분석:\n"
    "   - 현재 주가 위치 (52주 범위 대비)\n"
    "   - 밸류에이션 판단 (P/E, Forward P/E 기반 고평가/저평가)\n"
    "   - 수익성 평가 (ROE, 순이익률, 매출 성장률)\n"
    "   - 거래량 분석 (현재 vs 평균 - 시장 관심도)\n"
    "   - 최근 5일 주가 추세 해석\n"
    "3. 종합 판단: 뉴스 감성 + 재무 지표를 결합한 주가 방향 예측 (UP/DOWN)\n"
    "4. 신뢰도: high / medium / low\n\n"
    "Respond in Korean."
)

prediction = chat(predict_prompt)
print("\n" + prediction)

# ============================================================
# 7단계: 누락 요인 / 변수 분석
# ============================================================
print("\n[6/7] 뉴스와 지표로 파악하기 어려운 변수 분석 중...")
print("  이미 반영된 재무 지표를 제외한 숨겨진 요인을 분석합니다.")

factors_prompt = (
    "You are a stock market analyst who just analyzed NVIDIA stock using news articles AND financial metrics "
    "(P/E, EPS, ROE, revenue growth, profit margin, 52-week range, volume, recent price trend).\n\n"
    "Now identify factors that could NOT be captured from news or standard financial metrics. "
    "Since you already have financial data, focus on truly hidden factors:\n\n"
    "- Geopolitical factors (US-China tensions, export controls, tariffs)\n"
    "- Macroeconomic shifts (interest rate changes, inflation trajectory, Fed policy)\n"
    "- Technical analysis signals (support/resistance levels, RSI, MACD, moving averages)\n"
    "- Institutional investor movements (hedge fund positions, 13F filings)\n"
    "- Supply chain risks not yet in the news\n"
    "- Upcoming catalysts (earnings dates, product launches, conferences)\n"
    "- Options market sentiment (put/call ratio, unusual options activity)\n"
    "- Market-wide sentiment indicators (VIX, fear/greed index)\n"
    "- Regulatory risks (antitrust, AI regulation)\n\n"
    "For each factor:\n"
    "1. 현재 상황 추정\n"
    "2. 주가에 미칠 수 있는 영향\n"
    "3. 기존 분석을 뒤집을 가능성\n\n"
    "Respond in Korean."
)

factors = chat(factors_prompt)
print("\n" + factors)

# ============================================================
# 8단계: 종합 평가
# ============================================================
print("\n[7/7] 종합 평가 작성 중...")
print("  뉴스 + 재무 지표 + 누락 요인을 모두 종합합니다.")

final_prompt = (
    "You are a professional stock market analyst. You have completed a thorough 3-layer analysis of NVIDIA (NVDA):\n\n"
    "=== Layer 1: News + Financial Data Analysis ===\n"
    f"{prediction}\n\n"
    "=== Layer 2: Hidden Factors Analysis ===\n"
    f"{factors}\n\n"
    "=== Raw Financial Data (for reference) ===\n"
    f"{financial_text}\n\n"
    "Now provide your FINAL comprehensive evaluation considering ALL three layers.\n\n"
    "Include:\n"
    "1. 최종 판정: UP 또는 DOWN (신뢰도: high/medium/low)\n"
    "2. 핵심 근거 (뉴스, 재무 지표, 숨겨진 요인에서 각각 핵심 포인트)\n"
    "3. 최대 리스크: 예측을 뒤집을 수 있는 가장 큰 변수\n"
    "4. 투자 시나리오:\n"
    "   - Best case: 어떤 조건에서 최대 상승?\n"
    "   - Worst case: 어떤 조건에서 최대 하락?\n"
    "   - Base case: 가장 가능성 높은 시나리오\n"
    "5. 분석 한계 및 면책 사항\n\n"
    "Respond in Korean."
)

final_evaluation = chat(final_prompt)

print("\n" + "=" * 60)
print("  최종 종합 평가")
print("=" * 60)
print("\n" + final_evaluation)
print("\n" + "=" * 60)
print("  분석 완료")
print("=" * 60)

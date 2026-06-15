#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
매크로 지표 대시보드 — 매일 1회 실행하면:
  1) 지표를 수집하고
  2) data/날짜.json 으로 그날 기록을 저장하고
  3) dashboard.html 을 다시 그립니다 (서버 없이 더블클릭으로 열림).

실행:  python3 record.py
필요한 것:  requests (이미 설치됨), FRED 무료 키(미국 경제지표용 — 없어도 나머지는 동작)
"""

import json
import os
import sys
import time
import fcntl
from datetime import datetime, timezone, timedelta

import requests

# 야후는 짧은 시간에 여러 요청이 몰리면 429(요청 과다)로 막습니다.
# → 하나의 세션을 재사용하고, 요청 사이에 잠깐 쉬고, 막히면 재시도합니다.
SESSION = requests.Session()

# ──────────────────────────────────────────────────────────────────────────
#  경로 설정
# ──────────────────────────────────────────────────────────────────────────
ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(ROOT, "data")
HTML_PATH = os.path.join(ROOT, "dashboard.html")
ENV_PATH = os.path.join(ROOT, ".env")

# ──────────────────────────────────────────────────────────────────────────
#  지표 정의  ← 여기만 고치면 지표를 추가/삭제할 수 있어요.
#
#  group 1 = 시장 심리 (변동성·심리·유동성 등 분위기 지표)
#  group 2 = 주요 자산 (증시·환율·원자재·암호화폐 대표 종목)
#  group 3 = 미국 매크로 경제지표 (금리·물가·고용 등 펀더멘털)
#
#  source "stooq"    → ticker 는 Stooq 심볼 (지수·환율·원자재)
#  source "fred"     → ticker 는 FRED 시리즈 ID (미국 경제지표 + VIX·국채금리)
#  source "naver"    → ticker 는 "KOSPI"·"KOSDAQ" (네이버 금융 국내 지수)
#  source "coinbase" → ticker 는 "ETH-USD" 같은 코인 쌍 (암호화폐)
#  source "cnn"      → CNN 공포·탐욕 지수 (ticker 불필요)
#  invert=True 면 "값이 오르면 빨강(위험)"으로 표시 (예: VIX, 실업률)
#
#  ※ 주가·환율·코인은 소스가 '현재값'만 주므로, 전일 대비 변화율은
#    프로그램이 어제 저장해 둔 기록과 비교해서 계산합니다(첫날은 '—').
#    FRED 지표는 발표 시계열이 있어 직전 발표 대비로 자동 계산됩니다.
# ──────────────────────────────────────────────────────────────────────────
#  group 값(1/2/3)은 순위가 아니라 '성격별 묶음'입니다.
INDICATORS = [
    # ── 1) 시장 심리 ───────────────────────────────────────────────────
    {"key": "vix",     "name": "VIX 공포지수",    "category": "변동성",   "source": "cboe",     "ticker": "VIX",      "group": 1, "invert": True},
    {"key": "fng",     "name": "공포·탐욕 지수",    "category": "심리지표", "source": "cnn",      "ticker": "",         "group": 1},
    {"key": "dxy",     "name": "달러 인덱스",      "category": "환율",     "source": "dxy",      "ticker": "",         "group": 1},
    {"key": "spread",  "name": "장단기 금리차(10Y-2Y)", "category": "경기신호", "source": "treasury", "ticker": "SPREAD", "group": 1, "unit": "%p"},
    {"key": "m2",      "name": "M2 통화량",        "category": "유동성",   "source": "fred",     "ticker": "M2SL",     "group": 1, "unit": "B"},

    # ── 2) 주요 자산 ───────────────────────────────────────────────────
    {"key": "sp500",   "name": "S&P 500",        "category": "미국 증시", "source": "fred",       "ticker": "SP500",     "group": 2},
    {"key": "nasdaq",  "name": "나스닥",          "category": "미국 증시", "source": "fred",       "ticker": "NASDAQCOM", "group": 2},
    {"key": "kospi",   "name": "코스피",          "category": "한국 증시", "source": "naver",      "ticker": "KOSPI",     "group": 2},
    {"key": "gold",    "name": "금",              "category": "원자재",   "source": "twelvedata", "ticker": "XAU/USD",   "group": 2},
    {"key": "wti",     "name": "WTI 유가",        "category": "원자재",   "source": "fred",       "ticker": "DCOILWTICO","group": 2},
    {"key": "usdkrw",  "name": "달러/원 환율",     "category": "환율",     "source": "twelvedata", "ticker": "USD/KRW",   "group": 2},
    {"key": "btc",     "name": "비트코인",        "category": "암호화폐", "source": "coinbase", "ticker": "BTC-USD",  "group": 2},
    {"key": "eth",     "name": "이더리움",        "category": "암호화폐", "source": "coinbase", "ticker": "ETH-USD",  "group": 2},

    # ── 3) 미국 매크로 경제지표 ────────────────────────────────────────
    {"key": "us10y",   "name": "미국 10년물 금리", "category": "금리",     "source": "treasury", "ticker": "10 Yr", "group": 3, "unit": "%"},
    {"key": "us2y",    "name": "미국 2년물 금리",  "category": "금리",     "source": "treasury", "ticker": "2 Yr",  "group": 3, "unit": "%"},
    {"key": "cpi",     "name": "소비자물가(CPI)",  "category": "인플레이션", "source": "fred", "ticker": "CPIAUCSL", "group": 3},
    {"key": "unrate",  "name": "실업률",          "category": "고용",     "source": "fred", "ticker": "UNRATE",   "group": 3, "unit": "%", "invert": True},
    {"key": "fedfunds","name": "기준금리(Fed)",    "category": "금리",     "source": "fred", "ticker": "FEDFUNDS", "group": 3, "unit": "%"},
    {"key": "sofr",    "name": "SOFR",            "category": "금리",     "source": "fred", "ticker": "SOFR",     "group": 3, "unit": "%"},
    {"key": "fedbs",   "name": "Fed Balance",     "category": "유동성",   "source": "fred", "ticker": "WALCL",    "group": 3, "unit": "B", "scale": 0.001},
    {"key": "rrp",     "name": "역레포(RRP)",      "category": "유동성",   "source": "fred", "ticker": "RRPONTSYD","group": 3, "unit": "B"},

    # ── 4) 미국 섹터 (S&P500 SPDR ETF 11종, 그날 상승률 순 히트맵) ──────
    {"key": "sec_xlk", "name": "기술",        "category": "XLK", "source": "twelvedata", "ticker": "XLK",  "group": 4},
    {"key": "sec_xlf", "name": "금융",        "category": "XLF", "source": "twelvedata", "ticker": "XLF",  "group": 4},
    {"key": "sec_xlv", "name": "헬스케어",     "category": "XLV", "source": "twelvedata", "ticker": "XLV",  "group": 4},
    {"key": "sec_xly", "name": "임의소비재",   "category": "XLY", "source": "twelvedata", "ticker": "XLY",  "group": 4},
    {"key": "sec_xlp", "name": "필수소비재",   "category": "XLP", "source": "twelvedata", "ticker": "XLP",  "group": 4},
    {"key": "sec_xle", "name": "에너지",       "category": "XLE", "source": "twelvedata", "ticker": "XLE",  "group": 4},
    {"key": "sec_xli", "name": "산업재",       "category": "XLI", "source": "twelvedata", "ticker": "XLI",  "group": 4},
    {"key": "sec_xlb", "name": "소재",        "category": "XLB", "source": "twelvedata", "ticker": "XLB",  "group": 4},
    {"key": "sec_xlu", "name": "유틸리티",     "category": "XLU", "source": "twelvedata", "ticker": "XLU",  "group": 4},
    {"key": "sec_xlre","name": "리츠",        "category": "XLRE","source": "twelvedata", "ticker": "XLRE", "group": 4},
    {"key": "sec_xlc", "name": "커뮤니케이션",  "category": "XLC", "source": "twelvedata", "ticker": "XLC",  "group": 4},
]

STOOQ_URL = "https://stooq.com/q/l/?s={sym}&f=sd2t2c&h&e=csv"
NAVER_INDEX_URL = "https://m.stock.naver.com/api/index/{code}/basic"
COINBASE_URL = "https://api.coinbase.com/v2/prices/{pair}/spot"
CNN_FNG_URL = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
FRED_URL = ("https://api.stlouisfed.org/fred/series/observations"
            "?series_id={sid}&api_key={key}&file_type=json&sort_order=desc&limit=2")
# CNN은 봇을 막으므로 브라우저처럼 보이는 헤더(특히 Referer)가 필요합니다.
CNN_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.cnn.com/markets/fear-and-greed",
}
# 네이버 금융 모바일 API도 브라우저형 헤더(특히 Referer)가 있어야 안정적입니다.
NAVER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Referer": "https://m.stock.naver.com/",
}


def load_fred_key():
    """`.env` 파일이나 환경변수에서 FRED_API_KEY 를 읽습니다."""
    key = os.environ.get("FRED_API_KEY", "").strip()
    if key:
        return key
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("FRED_API_KEY"):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def fetch_stooq(ind):
    """Stooq에서 현재 종가를 받아옵니다 (CSV 한 줄). 전일 대비는 나중에 계산."""
    url = STOOQ_URL.format(sym=requests.utils.quote(ind["ticker"]))
    r = SESSION.get(url, timeout=15)
    r.raise_for_status()
    lines = [ln for ln in r.text.strip().splitlines() if ln]
    if len(lines) < 2:
        raise ValueError("응답 비어있음")
    cells = lines[-1].split(",")          # Symbol,Date,Time,Close
    close = cells[-1].strip()
    if close in ("N/D", "", "0"):
        raise ValueError(f"값 없음({close})")
    return {"value": round(float(close), 4), "change_pct": None}


def fetch_naver(ind):
    """네이버 금융에서 국내 지수 종가를 받아옵니다 (ticker = "KOSPI"·"KOSDAQ").
    무료·레이트리밋 없음. 전일 대비는 다른 시장지표와 같게 나중에 계산."""
    url = NAVER_INDEX_URL.format(code=ind["ticker"])
    r = SESSION.get(url, headers=NAVER_HEADERS, timeout=15)
    r.raise_for_status()
    close = r.json().get("closePrice", "").replace(",", "").strip()
    if not close:
        raise ValueError("종가 없음")
    return {"value": round(float(close), 4), "change_pct": None}


def fetch_coinbase(ind):
    """Coinbase 현물가를 받아옵니다. 전일 대비는 나중에 계산."""
    url = COINBASE_URL.format(pair=ind["ticker"])
    r = SESSION.get(url, timeout=15)
    r.raise_for_status()
    amount = r.json()["data"]["amount"]
    return {"value": round(float(amount), 4), "change_pct": None}


def fetch_cnn_fng(ind):
    """CNN 공포·탐욕 지수(0~100)를 받아옵니다. 전일 대비는 나중에 계산."""
    r = SESSION.get(CNN_FNG_URL, headers=CNN_HEADERS, timeout=15)
    r.raise_for_status()
    f = r.json()["fear_and_greed"]
    return {"value": round(float(f["score"]), 1), "change_pct": None,
            "rating": f.get("rating")}


def period_label(gap_days):
    """두 데이터 시점 사이의 간격(일)을 보고 알맞은 비교 기간 라벨을 고름."""
    if gap_days is None:
        return ""
    if gap_days <= 3:
        return "전일"
    if gap_days <= 10:
        return "전주"
    if gap_days <= 45:
        return "전월"
    return "직전발표"


def fetch_fred(ind, key):
    """FRED에서 최근 2개 관측치를 받아 최신값과 직전 발표 대비 변화를 계산.
    월간·주간·일간 시리즈가 섞여 있으므로 두 관측 시점의 간격으로 비교 기간을 정함."""
    if not key:
        return {"value": None, "change_pct": None, "note": "FRED 키 필요"}
    url = FRED_URL.format(sid=ind["ticker"], key=key)
    for attempt in range(4):                  # 429면 잠깐 쉬고 재시도
        r = SESSION.get(url, timeout=15)
        if r.status_code == 429:
            time.sleep(3 + attempt * 3)
            continue
        break
    r.raise_for_status()
    obs = [o for o in r.json().get("observations", []) if o.get("value") not in (".", None, "")]
    if not obs:
        return {"value": None, "change_pct": None, "note": "데이터 없음"}
    value = float(obs[0]["value"])
    if len(obs) >= 2:
        prev = float(obs[1]["value"])
        gap = (datetime.strptime(obs[0]["date"], "%Y-%m-%d")
               - datetime.strptime(obs[1]["date"], "%Y-%m-%d")).days
    else:
        prev, gap = value, None
    change_pct = ((value - prev) / prev * 100) if prev else 0.0
    scale = ind.get("scale", 1)   # 단위 환산 (예: 백만→십억은 0.001)
    return {"value": round(value * scale, 4), "change_pct": round(change_pct, 2),
            "obs_date": obs[0]["date"], "period": period_label(gap)}


# 미국 국채금리(10Y·2Y·금리차)는 FRED 대신 '미국 재무부' 공식 일별 수익률곡선에서 받는다.
# (FRED의 원천이며 발표가 1~2영업일 빠르고, API 키가 필요 없음)
TREASURY_CSV = ("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
                "daily-treasury-rates.csv/{year}/all?type=daily_treasury_yield_curve"
                "&field_tdr_date_value={year}&page&_format=csv")
_TREASURY_CACHE = {}


def treasury_series(year):
    """재무부 일별 국채금리 → 정렬된 [(YYYY-MM-DD, {'2 Yr':v, '10 Yr':v}), ...] (키 불필요)."""
    import csv as _csv
    from io import StringIO as _SIO
    if year in _TREASURY_CACHE:
        return _TREASURY_CACHE[year]
    r = SESSION.get(TREASURY_CSV.format(year=year),
                    headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                             "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"},
                    timeout=25)
    r.raise_for_status()
    rows = list(_csv.reader(_SIO(r.text)))
    hdr = rows[0]
    i2, i10 = hdr.index("2 Yr"), hdr.index("10 Yr")
    out = []
    for row in rows[1:]:
        if not row or not row[0].strip():
            continue
        try:
            mm, dd, yy = row[0].split("/")
            out.append((f"{yy}-{mm}-{dd}", {"2 Yr": float(row[i2]), "10 Yr": float(row[i10])}))
        except (ValueError, IndexError):
            continue
    out.sort()
    _TREASURY_CACHE[year] = out
    return out


def treasury_value(row, ticker):
    """재무부 행(dict)에서 지표 티커에 맞는 값. 'SPREAD' = 10Y - 2Y."""
    if ticker == "10 Yr":
        return row["10 Yr"]
    if ticker == "2 Yr":
        return row["2 Yr"]
    if ticker == "SPREAD":
        return round(row["10 Yr"] - row["2 Yr"], 2)
    return None


def fetch_treasury(ind):
    """treasury 소스 지표(us10y/us2y/spread)의 최신값. 전일 대비는 collect()에서 계산."""
    series = treasury_series(datetime.now().strftime("%Y"))
    if not series:
        return {"value": None, "change_pct": None, "note": "재무부 데이터 없음"}
    d, row = series[-1]
    return {"value": treasury_value(row, ind["ticker"]), "obs_date": d}


# ── Twelve Data (무료): gold(XAU/USD)·usdkrw(USD/KRW)·섹터ETF 11 + DXY 합성용 FX ──
# 무료 플랜은 지수·원자재 미지원 → 지수·WTI는 FRED, 달러인덱스(ICE DXY)는 FX로 합성.
TD_QUOTE_URL = "https://api.twelvedata.com/quote"
DXY_FX = ["EUR/USD", "USD/JPY", "GBP/USD", "USD/CAD", "USD/SEK", "USD/CHF"]
_TD_CACHE = {}


def load_twelvedata_key():
    k = os.environ.get("TWELVEDATA_API_KEY", "").strip()
    if k:
        return k
    if os.path.exists(ENV_PATH):
        for line in open(ENV_PATH, encoding="utf-8"):
            line = line.strip()
            if line.startswith("TWELVEDATA_API_KEY"):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def _td_get(chunk, key, tries=4):
    """TD /quote 한 배치를 받아 _TD_CACHE를 채운다. 일시적 실패 시 대기 후 재시도.
    - 429(요청 과다): 무료 8 req/분 → 분 창이 리셋되도록 61초 대기 후 재시도
    - connection-reset 등 네트워크 오류: 20초 대기 후 재시도
    - 정상 응답(HTTP 200)인데 일부 심볼이 비면 미지원이므로 재시도하지 않음"""
    for attempt in range(tries):
        last = attempt == tries - 1
        try:
            r = SESSION.get(TD_QUOTE_URL, params={"symbol": ",".join(chunk), "apikey": key}, timeout=30)
            d = r.json() if r.content else {}
            rate_limited = (r.status_code == 429) or (isinstance(d, dict) and d.get("code") == 429)
            if rate_limited and not last:
                print(f"  [TD 429] {chunk} → 61s 대기 후 재시도({attempt + 1}/{tries})", file=sys.stderr)
                time.sleep(61)
                continue
            r.raise_for_status()
            for s in chunk:
                v = d.get(s) if len(chunk) > 1 else d
                if isinstance(v, dict) and v.get("close") not in (None, ""):
                    _TD_CACHE[s] = float(v["close"])
            return                      # 정상 응답 — 미지원 심볼은 재시도해도 동일
        except Exception as e:
            if not last:
                print(f"  [TD 재시도] {chunk}: {str(e)[:50]} → 20s 후({attempt + 1}/{tries})", file=sys.stderr)
                time.sleep(20)
                continue
            print(f"  [WARN] TwelveData {chunk}: {str(e)[:60]}", file=sys.stderr)


def _td_populate():
    """필요한 Twelve Data 심볼을 8개/분 배치로 받아 _TD_CACHE에 채운다(무료 8 req/분)."""
    key = load_twelvedata_key()
    if not key:
        print("  [WARN] TWELVEDATA_API_KEY 없음", file=sys.stderr)
        return
    syms = [ind["ticker"] for ind in INDICATORS
            if ind["source"] == "twelvedata" and ind["ticker"]] + DXY_FX
    for i in range(0, len(syms), 8):
        chunk = syms[i:i + 8]
        if i:
            time.sleep(61)
        _td_get(chunk, key)


def fetch_twelvedata(ind):
    """gold·usdkrw·섹터ETF 의 최신값(전일 대비는 collect에서 계산)."""
    if not _TD_CACHE:
        _td_populate()
    v = _TD_CACHE.get(ind["ticker"])
    return {"value": round(v, 4)} if v is not None else {"value": None, "note": "TD 미지원/실패"}


def fetch_dxy(ind):
    """달러인덱스(ICE DXY)를 6개 FX로 합성. (TD 무료는 DXY 미제공)"""
    if not _TD_CACHE:
        _td_populate()
    try:
        fx = {s: _TD_CACHE[s] for s in DXY_FX}
    except KeyError:
        return {"value": None, "note": "DXY 합성 FX 부족"}
    dxy = (50.14348112 * fx["EUR/USD"] ** -0.576 * fx["USD/JPY"] ** 0.136
           * fx["GBP/USD"] ** -0.119 * fx["USD/CAD"] ** 0.091
           * fx["USD/SEK"] ** 0.042 * fx["USD/CHF"] ** 0.036)
    return {"value": round(dxy, 3)}


# ── CBOE VIX (무료, 키 불필요) — FRED VIXCLS와 동일하나 발표 지연이 없음 ──
CBOE_VIX_URL = "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"
_CBOE_VIX = {}


def cboe_vix_series():
    """CBOE VIX 일별 종가 {YYYY-MM-DD: close}. (FRED VIXCLS의 원천, 무지연)"""
    if _CBOE_VIX:
        return _CBOE_VIX
    import csv as _csv
    from io import StringIO as _SIO
    try:
        r = SESSION.get(CBOE_VIX_URL, timeout=20)
        r.raise_for_status()
        for row in _csv.DictReader(_SIO(r.text)):
            ds, cl = (row.get("DATE") or "").strip(), (row.get("CLOSE") or "").strip()
            if not ds or not cl:
                continue
            try:
                mm, dd, yy = ds.split("/")
                _CBOE_VIX[f"{yy}-{int(mm):02d}-{int(dd):02d}"] = float(cl)
            except ValueError:
                continue
    except Exception as e:
        print(f"  [WARN] CBOE VIX: {str(e)[:50]}", file=sys.stderr)
    return _CBOE_VIX


def fetch_cboe_vix(ind):
    """VIX 최신 종가(CBOE). 전일 대비는 collect()에서 계산."""
    s = cboe_vix_series()
    if not s:
        return {"value": None, "note": "CBOE VIX 실패"}
    d = max(s)
    return {"value": round(s[d], 4), "obs_date": d}


def us_session_date(fallback):
    """파일 날짜 = '가장 최근에 마감된 미국 영업일'.
    미국 재무부 일별 발표(영업일마다 갱신)의 최신 일자를 사용한다.
    (Stooq가 봇 차단되어 더는 못 씀.) 실패 시 fallback(달력 날짜)."""
    try:
        s = treasury_series(datetime.now().strftime("%Y"))
        if s:
            return s[-1][0]
    except Exception:
        pass
    return fallback


# 일간 FRED 지표(매일 종가가 존재) — 각 파일의 '세션 날짜' 관측값으로 맞춘다.
# (CPI·M2처럼 가끔 발표되는 건 제외. 그쪽은 발표 시차 반영 방식을 유지.)
# us10y·us2y·spread 는 재무부(treasury), vix 는 CBOE 소스로 이전 → FRED 일간 보정 대상에서 제외.
# sp500·nasdaq·wti 는 FRED 일간 시리즈로 이전(Stooq 봇차단) → 발표 지연 self-heal 위해 포함.
DAILY_FRED = {"rrp": "RRPONTSYD", "sofr": "SOFR",
              "sp500": "SP500", "nasdaq": "NASDAQCOM", "wti": "DCOILWTICO"}


def fred_obs_series(sid, key):
    """FRED 시리즈의 (관측일, 값) 리스트(오름차순). realtime 없이 실제 관측값."""
    url = ("https://api.stlouisfed.org/fred/series/observations"
           f"?series_id={sid}&api_key={key}&file_type=json"
           "&observation_start=2026-01-01&sort_order=asc")
    for attempt in range(4):
        r = SESSION.get(url, timeout=15)
        if r.status_code == 429:
            time.sleep(3 + attempt * 3)
            continue
        break
    r.raise_for_status()
    return [(o["date"], float(o["value"])) for o in r.json().get("observations", [])
            if o["value"] not in (".", "", None)]


def reconcile_daily_fred(recent=6):
    """일간 FRED(VIX·금리·RRP)를 각 파일의 세션 날짜 종가로 보정.
    FRED는 ~1영업일 늦게 발표하므로 기록 당일엔 직전값이 들어가는데,
    이후 실행에서 발표된 값으로 자동으로 맞춰준다(self-heal)."""
    key = load_fred_key()
    if not key:
        return
    series = {}
    for k, sid in DAILY_FRED.items():
        try:
            series[k] = fred_obs_series(sid, key)
        except Exception:
            series[k] = []
        time.sleep(0.6)
    files = sorted(f for f in os.listdir(DATA_DIR) if f.endswith(".json"))
    if recent:
        files = files[-recent:]
    for fn in files:
        path = os.path.join(DATA_DIR, fn)
        with open(path, encoding="utf-8") as f:
            snap = json.load(f)
        day = snap["date"]
        for k in DAILY_FRED:
            obs = series.get(k) or []
            ci = -1
            for i, (d, _) in enumerate(obs):
                if d <= day:
                    ci = i
                else:
                    break
            if ci < 0 or k not in snap["indicators"]:
                continue
            cur = obs[ci]
            prev = obs[ci - 1] if ci >= 1 else None
            chg = round((cur[1] - prev[1]) / prev[1] * 100, 2) if prev and prev[1] else None
            gap = None
            if prev:
                gap = (datetime.strptime(cur[0], "%Y-%m-%d")
                       - datetime.strptime(prev[0], "%Y-%m-%d")).days
            snap["indicators"][k].update({"value": round(cur[1], 4), "change_pct": chg,
                                          "obs_date": cur[0], "period": period_label(gap),
                                          "status": "ok"})
        with open(path, "w", encoding="utf-8") as f:
            json.dump(snap, f, ensure_ascii=False, indent=2)


def reconcile_cboe_vix(recent=6):
    """VIX(CBOE)를 각 파일의 세션 날짜 종가로 보정(발표 지연 self-heal).
    기록 당일(미 장 마감 직후)엔 CBOE가 그날 종가를 아직 안 올려 직전값(stale)이
    들어가는데, 이후 실행에서 발표된 값으로 자동으로 맞춰준다.
    (reconcile_daily_fred의 CBOE판 — VIX는 FRED가 아니라 CBOE 소스이므로 별도 보정)"""
    s = cboe_vix_series()
    if not s:
        return
    obs = sorted(s.items())   # 오름차순 [(YYYY-MM-DD, close), ...]
    files = sorted(f for f in os.listdir(DATA_DIR) if f.endswith(".json"))
    if recent:
        files = files[-recent:]
    for fn in files:
        path = os.path.join(DATA_DIR, fn)
        with open(path, encoding="utf-8") as f:
            snap = json.load(f)
        day = snap["date"]
        if "vix" not in snap["indicators"]:
            continue
        ci = -1
        for i, (d, _) in enumerate(obs):
            if d <= day:
                ci = i
            else:
                break
        if ci < 0:
            continue
        cur = obs[ci]
        prev = obs[ci - 1] if ci >= 1 else None
        chg = round((cur[1] - prev[1]) / prev[1] * 100, 2) if prev and prev[1] else None
        gap = None
        if prev:
            gap = (datetime.strptime(cur[0], "%Y-%m-%d")
                   - datetime.strptime(prev[0], "%Y-%m-%d")).days
        snap["indicators"]["vix"].update({"value": round(cur[1], 4), "change_pct": chg,
                                          "obs_date": cur[0], "period": period_label(gap),
                                          "status": "ok"})
        with open(path, "w", encoding="utf-8") as f:
            json.dump(snap, f, ensure_ascii=False, indent=2)


def collect():
    """모든 지표를 수집해서 한 건의 스냅샷(dict)으로 반환."""
    fred_key = load_fred_key()
    # 날짜 라벨은 KST가 아니라 '미국 세션 날짜' 기준으로 통일(백필과 일치).
    today = us_session_date(datetime.now().strftime("%Y-%m-%d"))
    snapshot = {
        "date": today,
        "recorded_at": datetime.now().isoformat(timespec="seconds"),
        "indicators": {},
    }
    fetchers = {"stooq": fetch_stooq, "naver": fetch_naver, "coinbase": fetch_coinbase,
                "cnn": fetch_cnn_fng, "treasury": fetch_treasury, "twelvedata": fetch_twelvedata,
                "dxy": fetch_dxy, "cboe": fetch_cboe_vix}
    prev_snap = load_previous_snapshot(today)   # 어제(또는 가장 최근) 기록
    # 시장 데이터(주가·코인·심리)는 내가 저장한 직전 기록과 비교하므로,
    # 그 기록이 며칠 전인지로 비교 기간 라벨을 정함(보통 '전일').
    market_gap = None
    if prev_snap:
        market_gap = (datetime.strptime(today, "%Y-%m-%d")
                      - datetime.strptime(prev_snap["date"], "%Y-%m-%d")).days
    market_period = period_label(market_gap)
    for ind in INDICATORS:
        meta = {k: ind.get(k) for k in ("name", "category", "group", "unit", "invert")}
        try:
            if ind["source"] == "fred":
                data = fetch_fred(ind, fred_key)
            elif ind["source"] == "none":
                data = {"value": None, "change_pct": None, "note": "무료 소스 없음"}
            else:
                data = fetchers[ind["source"]](ind)
                # 주가·코인은 현재값만 오므로 직전 기록 대비를 직접 계산
                data["change_pct"] = change_vs_prev(prev_snap, ind["key"], data["value"])
                data["period"] = market_period
            status = "ok" if data.get("value") is not None else "pending"
            print(f"  ✓ {ind['name']:<18} {data.get('value')}")
        except Exception as e:
            data = {"value": None, "change_pct": None, "note": str(e)[:60]}
            status = "error"
            print(f"  ✗ {ind['name']:<18} 실패: {str(e)[:50]}")
        snapshot["indicators"][ind["key"]] = {**meta, **data, "status": status}
        time.sleep(0.4)   # 요청이 한꺼번에 몰리지 않게 가벼운 간격
    return snapshot


def load_previous_snapshot(today):
    """오늘 이전의 가장 최근 날짜별 기록을 반환 (없으면 None)."""
    if not os.path.isdir(DATA_DIR):
        return None
    files = sorted(fn for fn in os.listdir(DATA_DIR)
                   if fn.endswith(".json") and fn[:-5] < today)
    if not files:
        return None
    with open(os.path.join(DATA_DIR, files[-1]), encoding="utf-8") as f:
        return json.load(f)


def change_vs_prev(prev_snap, key, value):
    """이전 기록 대비 변화율(%). 비교할 값이 없으면 None(첫날 등)."""
    if not prev_snap or value is None:
        return None
    prev = prev_snap.get("indicators", {}).get(key, {}).get("value")
    if not prev:
        return None
    return round((value - prev) / prev * 100, 2)


def save_snapshot(snapshot):
    """data/날짜.json 으로 저장 (같은 날 다시 실행하면 덮어씀 = 그날 최신값)."""
    os.makedirs(DATA_DIR, exist_ok=True)
    path = os.path.join(DATA_DIR, f"{snapshot['date']}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)
    return path


def load_history():
    """data/ 의 모든 날짜별 파일을 날짜순으로 읽어옵니다 (추세용)."""
    if not os.path.isdir(DATA_DIR):
        return []
    files = sorted(fn for fn in os.listdir(DATA_DIR) if fn.endswith(".json"))
    history = []
    for fn in files:
        try:
            with open(os.path.join(DATA_DIR, fn), encoding="utf-8") as f:
                history.append(json.load(f))
        except Exception:
            pass
    return history


# ──────────────────────────────────────────────────────────────────────────
#  dashboard.html 생성
# ──────────────────────────────────────────────────────────────────────────
def build_dashboard(history):
    groups = {1: [], 2: [], 3: [], 4: []}
    for ind in INDICATORS:
        groups[ind["group"]].append(ind["key"])
    group_titles = {
        1: ("시장 심리", "시장의 위험 선호·분위기를 읽는 지표"),
        2: ("주요 자산", "증시·환율·원자재·암호화폐 대표 종목"),
        3: ("미국 매크로 경제지표", "금리·물가·고용·유동성 등 경제 펀더멘털"),
        4: ("미국 섹터", "S&P500 11개 섹터 — 상승(왼쪽)→하락(오른쪽), 등락폭이 클수록 폭이 넓고 색이 진함"),
    }

    def fmt(v, unit):
        if v is None:
            return "—"
        unit = unit or ""
        if abs(v) >= 1000:
            return f"{v:,.0f}{unit}"
        if abs(v) >= 1:
            return f"{v:,.2f}{unit}"
        return f"{v:,.4f}{unit}"

    def render_sections(hist):
        """주어진 기록 슬라이스 기준으로(마지막 날짜를 '그 날'로) 전체 섹션 HTML 생성.
        스파크라인·추세는 그 날짜까지의 데이터만 사용 → 과거 날짜를 골라도 그 시점 화면."""
        latest = hist[-1]
        series = {}
        for snap in hist:
            for key, v in snap["indicators"].items():
                if v.get("value") is not None:
                    series.setdefault(key, []).append((snap["date"], v["value"]))

        def change_over(key, days):
            pts = series.get(key, [])
            if len(pts) < 2:
                return None
            target = datetime.strptime(latest["date"], "%Y-%m-%d") - timedelta(days=days)
            past = None
            for d, val in pts:
                if datetime.strptime(d, "%Y-%m-%d") <= target:
                    past = val
            if past is None or past == 0:
                return None
            return (pts[-1][1] - past) / past * 100

        def spark(key):
            pts = [v for _, v in series.get(key, [])][-30:]
            if len(pts) < 2:
                return ''
            lo, hi = min(pts), max(pts)
            rng = (hi - lo) or 1
            w, h = 54, 18
            step = w / (len(pts) - 1)
            coords = " ".join(f"{i*step:.1f},{h - (p-lo)/rng*h:.1f}" for i, p in enumerate(pts))
            up = pts[-1] >= pts[0]
            color = "#16a34a" if up else "#dc2626"
            return (f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}">'
                    f'<polyline fill="none" stroke="{color}" stroke-width="1.6" points="{coords}"/></svg>')

        def card(key):
            v = latest["indicators"][key]
            invert = v.get("invert")
            ch = v.get("change_pct")
            def cls(x):
                if x is None:
                    return "flat"
                good = (x >= 0) if not invert else (x <= 0)
                if abs(x) < 0.001:
                    return "flat"
                return "good" if good else "bad"
            arrow = "" if ch is None else ("▲" if ch > 0 else ("▼" if ch < 0 else "■"))
            day_ch = "" if ch is None else f'<span class="chg {cls(ch)}">{arrow} {abs(ch):.2f}%</span>'
            if v.get("status") != "ok":
                day_ch = f'<span class="chg pending">{v.get("note", "대기")}</span>'
            d7, d30, d60, d90 = (change_over(key, n) for n in (7, 30, 60, 90))
            def mini(label, x):
                if x is None:
                    return f'<div class="mini"><span>{label}</span><b class="flat">—</b></div>'
                return f'<div class="mini"><span>{label}</span><b class="{cls(x)}">{x:+.1f}%</b></div>'
            return f'''<div class="card">
        <div class="card-top">
          <div><div class="cat">{v["category"]}</div><div class="name">{v["name"]}</div></div>
          <div class="spark">{spark(key)}</div>
        </div>
        <div class="value">{fmt(v.get("value"), v.get("unit"))}</div>
        <div class="row">{day_ch}<span class="lbl">{v.get("period") or "전일"}</span></div>
        <div class="trends">{mini("1주", d7)}{mini("1개월", d30)}{mini("60일", d60)}{mini("90일", d90)}</div>
      </div>'''

        def sector_heat(keys):
            vals = [latest["indicators"].get(k, {}) for k in keys]
            vals.sort(key=lambda v: (v.get("change_pct") is None, -(v.get("change_pct") or 0)))
            tiles = ""
            for v in vals:
                ch = v.get("change_pct")
                if ch is None:
                    weight, bg, txt = 0.3, "rgba(125,134,156,0.10)", "—"
                else:
                    mag = abs(ch)
                    weight = max(mag, 0.2)
                    alpha = 0.15 + min(mag / 2.5, 1.0) * 0.6
                    rgb = "34,200,120" if ch > 0.001 else ("255,80,80" if ch < -0.001 else "125,134,156")
                    bg, txt = f"rgba({rgb},{alpha:.2f})", f"{ch:+.2f}%"
                tiles += f'''<div class="sec" style="flex-grow:{weight:.2f};background:{bg}" title="{v.get("name","")} ({v.get("category","")})">
          <div class="sec-tk">{v.get("category","")}</div>
          <div class="sec-chg">{txt}</div>
          <div class="sec-nm">{v.get("name","")}</div>
        </div>'''
            return f'<div class="sec-row">{tiles}</div>'

        secs = ""
        for g in (1, 2, 3):
            cards = "".join(card(k) for k in groups[g])
            title, sub = group_titles[g]
            secs += f'''<section class="grp grp-{g}">
        <div class="grp-head"><h2>{title}</h2><p>{sub}</p></div>
        <div class="grid">{cards}</div>
      </section>'''
        title, sub = group_titles[4]
        secs += f'''<section class="grp grp-4">
        <div class="grp-head"><h2>{title}</h2><p>{sub}</p></div>
        {sector_heat(groups[4])}
      </section>'''
        return secs

    # 날짜별 화면(snap)과 날짜 칩 만들기 — 최신이 기본 표시
    last = len(history) - 1
    snaps_html, chips = "", ""
    for i, snap in enumerate(history):
        disp = "block" if i == last else "none"
        snaps_html += (f'<div class="snap" id="snap{i}" style="display:{disp}">'
                       f'<div class="snapmeta">📅 {snap["date"]} <span class="snapsub">(미국 장 마감 종가 기준)</span></div>'
                       f'{render_sections(history[:i+1])}</div>')
    for i in range(last, -1, -1):   # 칩은 최신 → 과거 순
        cls = " active" if i == last else ""
        if datetime.strptime(history[i]["date"], "%Y-%m-%d").weekday() == 0:
            cls += " mon"           # 월요일은 미색 배경으로 구분
        chips += f'<button class="chip{cls}" id="chip{i}" onclick="showSnap({i})">{history[i]["date"][5:]}</button>'

    html = f'''<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>매크로 대시보드 · {history[-1]["date"]}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, "Apple SD Gothic Neo", sans-serif;
    background: #0b0e14; color: #e6e9ef; padding: 18px 16px 16px 32px; }}
  header {{ max-width: 1480px; margin: 0 auto 12px; display: flex;
    align-items: baseline; justify-content: space-between; flex-wrap: wrap; gap: 6px; }}
  header h1 {{ font-size: 16px; letter-spacing: -0.3px; }}
  header .meta {{ color: #7d869c; font-size: 11.5px; }}
  .grp {{ max-width: 1480px; margin: 0 auto 24px; }}
  .grp-head {{ display: flex; align-items: baseline; gap: 8px; margin-bottom: 9px; }}
  .grp-head h2 {{ font-size: 13px; }}
  .grp-head p {{ color: #7d869c; font-size: 11px; }}
  .grid {{ display: grid; gap: 6px 7px; grid-template-columns: repeat(auto-fill, minmax(164px, 1fr)); }}
  .card {{ background: #141925; border: 1px solid #232a3a; border-radius: 9px; padding: 9px 10px; }}
  .card-top {{ display: flex; justify-content: space-between; align-items: flex-start; gap: 4px; }}
  .cat {{ color: #6b7488; font-size: 10px; }}
  .name {{ font-weight: 600; font-size: 12px; margin-top: 1px; line-height: 1.2;
    min-height: 2.4em; }}  /* 항상 2줄 높이 확보 → 모든 카드의 아래 줄 정렬 일치 */
  .value {{ font-size: 18px; font-weight: 700; letter-spacing: -0.5px; margin: 5px 0 3px; }}
  .row {{ display: flex; align-items: baseline; gap: 5px; }}
  .lbl {{ color: #6b7488; font-size: 10px; }}
  .chg {{ font-size: 11.5px; font-weight: 600; }}
  .good {{ color: #34d27b; }}
  .bad {{ color: #ff6b6b; }}
  .flat {{ color: #8a93a6; }}
  .pending {{ color: #c79a4b; font-size: 10.5px; font-weight: 500; }}
  .trends {{ display: flex; justify-content: space-between; gap: 3px;
    margin-top: 7px; padding-top: 6px; border-top: 1px solid #232a3a; }}
  .mini {{ display: flex; flex-direction: column; gap: 1px; }}
  .mini span {{ color: #6b7488; font-size: 9.5px; }}
  .mini b {{ font-size: 10px; font-weight: 600; white-space: nowrap; }}
  .spark svg {{ display: block; }}
  /* 섹터 — 한 줄, 등락폭 비례 너비 */
  .sec-row {{ display: flex; gap: 5px; align-items: stretch; }}
  .sec {{ flex-basis: 0; min-width: 46px; border: 1px solid #2a3242; border-radius: 8px;
    padding: 8px 5px; overflow: hidden; text-align: center; }}
  .sec-tk {{ font-size: 10px; font-weight: 700; color: #cdd4e2; white-space: nowrap; }}
  .sec-chg {{ font-size: 14px; font-weight: 700; color: #f2f5fa; margin: 3px 0 2px;
    letter-spacing: -0.3px; white-space: nowrap; }}
  .sec-nm {{ font-size: 9.5px; color: #c2c9d6; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
  /* 날짜 네비게이션 */
  .datenav {{ max-width: 1480px; margin: 0 auto 16px; display: flex; flex-wrap: nowrap;
    gap: 4px; overflow-x: auto; padding-bottom: 6px; scrollbar-width: thin; }}
  .datenav::-webkit-scrollbar {{ height: 7px; }}
  .datenav::-webkit-scrollbar-thumb {{ background: #2a3242; border-radius: 4px; }}
  .chip {{ background: #141925; border: 1px solid #232a3a; color: #9aa3b8; font-family: inherit;
    font-size: 10.5px; padding: 3px 8px; border-radius: 6px; cursor: pointer;
    flex-shrink: 0; white-space: nowrap; }}
  .chip:hover {{ border-color: #3a4660; color: #cdd4e2; }}
  .chip.mon {{ background: rgba(239,231,211,0.10); border-color: rgba(216,205,176,0.35); color: #d6c8a3; }}  /* 월요일 미색(투명도 90%) */
  .chip.active {{ background: #1d3a6b; border-color: #3b6db5; color: #dbe7ff; font-weight: 700; }}
  .snapmeta {{ max-width: 1480px; margin: 0 auto 16px; color: #8a93a6; font-size: 12px; font-weight: 600; }}
  .snapsub {{ color: #565e72; font-size: 10.5px; font-weight: 400; }}
  footer {{ max-width: 1480px; margin: 6px auto 0; color: #565e72; font-size: 10.5px; line-height: 1.5; }}
</style></head><body>
  <header>
    <h1>📊 매크로 지표 대시보드</h1>
    <div class="meta">날짜를 클릭하면 그날의 지표를 볼 수 있어요</div>
  </header>
  <div class="datenav">{chips}</div>
  {snaps_html}
  <footer>
    데이터: Stooq · FRED · Coinbase · CNN &nbsp;|&nbsp; 매일 KST 08:00 자동 기록.<br>
    <b>날짜 기준</b>: 각 날짜 = 해당 <b>미국 장 마감(종가)</b> 세션. 미국 주식·섹터·VIX·금리는 그 세션 종가,
    코인은 기록 시점 실시간, 환율·원자재는 그 시각 시세.<br>
    색상은 일반 자산은 상승=초록, VIX·실업률처럼 ▲가 나쁜 지표는 자동 반전 처리됩니다.
  </footer>
  <script>
    function showSnap(i) {{
      document.querySelectorAll('.snap').forEach(function(e) {{ e.style.display = 'none'; }});
      document.querySelectorAll('.chip').forEach(function(e) {{ e.classList.remove('active'); }});
      document.getElementById('snap' + i).style.display = 'block';
      document.getElementById('chip' + i).classList.add('active');
      window.scrollTo(0, 0);
    }}
  </script>
</body></html>'''
    with open(HTML_PATH, "w", encoding="utf-8") as f:
        f.write(html)
    return HTML_PATH


def main():
    # 중복 실행 방지(cron·launchd 동시 트리거 등): 락을 못 잡으면 즉시 종료.
    _lock = open(os.path.join(ROOT, ".record.lock"), "w")
    try:
        fcntl.flock(_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("이미 다른 인스턴스가 실행 중 — 중복 실행 방지로 종료")
        return
    print(f"\n📊 매크로 지표 수집 — {datetime.now():%Y-%m-%d %H:%M}\n")
    snapshot = collect()
    path = save_snapshot(snapshot)
    print(f"\n💾 저장: {os.path.relpath(path, ROOT)}")
    reconcile_daily_fred()   # 일간 FRED(금리·RRP·지수·WTI)를 세션 날짜 종가로 보정(발표 지연 self-heal)
    print("🔧 일간 FRED 보정 완료")
    reconcile_cboe_vix()     # VIX(CBOE)를 세션 날짜 종가로 보정(발표 지연 self-heal)
    print("🔧 VIX(CBOE) 보정 완료")
    history = load_history()
    html = build_dashboard(history)
    print(f"🖥️  대시보드 갱신: {os.path.relpath(html, ROOT)}  (누적 {len(history)}일치)\n")


if __name__ == "__main__":
    main()

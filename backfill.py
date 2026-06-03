#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
과거 데이터 백필 — 지난 영업일(토·일 제외)의 지표 값을 받아
record.py 와 똑같은 형식의 data/날짜.json 파일로 저장합니다.

과거 데이터 출처(현재값 소스와 다름에 주의):
  · 지수/환율/원자재(원래 Stooq) → Yahoo Finance 과거 시계열
  · 암호화폐 → Coinbase 과거 spot(?date=)
  · 공포·탐욕 → CNN graphdata 의 historical 배열
  · FRED 지표 → FRED 시계열(발표일 기준 as-of)

실행:  python3 backfill.py
"""

import json
import time
from datetime import datetime, date, timedelta

import requests
import record   # INDICATORS, period_label, save_snapshot, build_dashboard 등 재사용
from fix_fred_realtime import fetch_vintages, value_asof   # FRED 발표시차 반영(realtime)

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"}
CNN_HEADERS = {**UA, "Accept": "application/json, text/plain, */*",
               "Referer": "https://www.cnn.com/markets/fear-and-greed"}

# 원래 Stooq 심볼 → Yahoo 심볼 (과거 시계열용)
STOOQ_TO_YAHOO = {
    "sp500": "^GSPC", "nasdaq": "^IXIC", "kospi": "^KS11",
    "gold": "GC=F", "wti": "CL=F", "usdkrw": "KRW=X", "dxy": "DX-Y.NYB",
    # 미국 섹터 ETF (그날 종가)
    "sec_xlk": "XLK", "sec_xlf": "XLF", "sec_xlv": "XLV", "sec_xly": "XLY",
    "sec_xlp": "XLP", "sec_xle": "XLE", "sec_xli": "XLI", "sec_xlb": "XLB",
    "sec_xlu": "XLU", "sec_xlre": "XLRE", "sec_xlc": "XLC",
}


# ── 과거 데이터 수집기 ──────────────────────────────────────────────────
def yahoo_history(ysym):
    """야후에서 최근 한 달 일별 종가를 받아 {날짜문자열: 종가} 로 반환."""
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/"
           f"{requests.utils.quote(ysym)}?range=1mo&interval=1d")
    for attempt in range(4):
        r = requests.get(url, headers=UA, timeout=15)
        if r.status_code == 429:
            time.sleep(3 + attempt * 4)
            continue
        break
    r.raise_for_status()
    res = r.json()["chart"]["result"][0]
    ts = res["timestamp"]
    closes = res["indicators"]["quote"][0]["close"]
    out = {}
    for t, c in zip(ts, closes):
        if c is not None:
            d = datetime.fromtimestamp(t).strftime("%Y-%m-%d")
            out[d] = c
    return out


def coinbase_on(pair, day):
    """특정 날짜의 Coinbase spot 가격."""
    url = f"https://api.coinbase.com/v2/prices/{pair}/spot?date={day}"
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    return float(r.json()["data"]["amount"])


def cnn_history():
    """CNN 공포·탐욕 지수의 과거값을 {날짜: 점수} 로 반환."""
    r = requests.get(record.CNN_FNG_URL, headers=CNN_HEADERS, timeout=15)
    r.raise_for_status()
    data = r.json()["fear_and_greed_historical"]["data"]
    out = {}
    for p in data:
        d = datetime.fromtimestamp(p["x"] / 1000).strftime("%Y-%m-%d")
        out[d] = round(float(p["y"]), 1)
    return out


def fred_series(sid, key, start, end):
    """FRED 시계열을 오름차순 [(날짜, 값)] 리스트로 반환."""
    url = ("https://api.stlouisfed.org/fred/series/observations"
           f"?series_id={sid}&api_key={key}&file_type=json"
           f"&observation_start={start}&observation_end={end}")
    for attempt in range(4):
        r = requests.get(url, timeout=15)
        if r.status_code == 429:
            time.sleep(3 + attempt * 3)
            continue
        break
    r.raise_for_status()
    obs = [(o["date"], float(o["value"]))
           for o in r.json().get("observations", []) if o["value"] not in (".", "", None)]
    return obs


# ── 보조 함수 ───────────────────────────────────────────────────────────
def value_on_or_before(hist_map_sorted, day):
    """{날짜:값} 에서 day 이하 중 가장 가까운 (날짜, 값). 없으면 (None, None)."""
    best = (None, None)
    for d, v in hist_map_sorted:
        if d <= day:
            best = (d, v)
        else:
            break
    return best


def business_days(start, end):
    """start~end(포함) 사이의 영업일(월~금) 날짜 문자열 리스트."""
    days, cur = [], start
    while cur <= end:
        if cur.weekday() < 5:        # 0=월 … 4=금
            days.append(cur.strftime("%Y-%m-%d"))
        cur += timedelta(days=1)
    return days


# ── 메인 ────────────────────────────────────────────────────────────────
def main():
    key = record.load_fred_key()
    today = date.today()
    # 지난 일주일: 오늘 기준 7일 전 ~ 어제, 영업일만
    targets = business_days(today - timedelta(days=7), today - timedelta(days=1))
    print(f"백필 대상 영업일: {', '.join(targets)}\n")

    # 1) 시장 데이터(야후/코인/CNN) 과거값 맵 미리 수집 ----------------------
    yahoo_maps = {}
    for key_ind, ysym in STOOQ_TO_YAHOO.items():
        try:
            yahoo_maps[key_ind] = sorted(yahoo_history(ysym).items())
            print(f"  ✓ Yahoo {ysym:<8} {len(yahoo_maps[key_ind])}일치")
        except Exception as e:
            yahoo_maps[key_ind] = []
            print(f"  ✗ Yahoo {ysym:<8} 실패: {str(e)[:50]}")
        time.sleep(1.5)

    try:
        fng_map = sorted(cnn_history().items())
        print(f"  ✓ CNN 공포·탐욕   {len(fng_map)}일치")
    except Exception as e:
        fng_map = []
        print(f"  ✗ CNN 실패: {str(e)[:50]}")

    coin_cache = {}   # (pair, day) → 값

    # 2) FRED 시계열 미리 수집 (대상 기간 + 여유분) -------------------------
    fred_cache = {}
    for ind in record.INDICATORS:
        if ind["source"] == "fred":
            try:
                fred_cache[ind["key"]] = fetch_vintages(ind["ticker"], key)
            except Exception as e:
                fred_cache[ind["key"]] = {}
                print(f"  ✗ FRED {ind['ticker']:<10} 실패: {str(e)[:50]}")
            time.sleep(1.5)   # vintage 응답이 커서 간격 넉넉히(429 방지)
    print(f"  ✓ FRED {sum(1 for v in fred_cache.values() if v)}개 시리즈(realtime)\n")

    # 3) 날짜별 스냅샷 조립 & 저장 ------------------------------------------
    for day in targets:
        snap = {"date": day, "recorded_at": f"{day}T00:00:00", "indicators": {}}
        for ind in record.INDICATORS:
            meta = {k: ind.get(k) for k in ("name", "category", "group", "unit", "invert")}
            data = build_indicator(ind, day, yahoo_maps, fng_map, coin_cache, fred_cache)
            status = "ok" if data.get("value") is not None else "pending"
            snap["indicators"][ind["key"]] = {**meta, **data, "status": status}
        path = record.save_snapshot(snap)
        ok = sum(1 for v in snap["indicators"].values() if v["status"] == "ok")
        print(f"  💾 {day}  ({ok}/{len(record.INDICATORS)} 성공)")

    # 4) 대시보드 재생성 ---------------------------------------------------
    record.build_dashboard(record.load_history())
    print("\n🖥️  대시보드 재생성 완료")


def build_indicator(ind, day, yahoo_maps, fng_map, coin_cache, fred_cache):
    """한 지표의 그 날짜 값/변화율/기간 라벨을 만든다."""
    src = ind["source"]
    try:
        if src == "stooq":   # → Yahoo 과거
            hist = yahoo_maps.get(ind["key"], [])
            d, val = value_on_or_before(hist, day)
            prevd, prev = _prev_point(hist, d)
            return _market(val, prev, d, prevd)

        if src == "coinbase":
            val = _coin(ind["ticker"], day, coin_cache)
            prevday = _prev_trading(day)
            prev = _coin(ind["ticker"], prevday, coin_cache)
            return _market(val, prev, day, prevday)

        if src == "cnn":
            d, val = value_on_or_before(fng_map, day)
            prevd, prev = _prev_point(fng_map, d)
            return _market(val, prev, d, prevd)

        if src == "fred":   # 발표시차 반영(그 날짜에 실제 알려진 값)
            cur, prev = value_asof(fred_cache.get(ind["key"], {}), day)
            if cur is None:
                return {"value": None, "change_pct": None}
            scale = ind.get("scale", 1)
            chg = round((cur[1] - prev[1]) / prev[1] * 100, 2) if prev and prev[1] else None
            gap = _gap(cur[0], prev[0]) if prev else None
            return {"value": round(cur[1] * scale, 4), "change_pct": chg,
                    "obs_date": cur[0], "period": record.period_label(gap)}
    except Exception as e:
        return {"value": None, "change_pct": None, "note": str(e)[:60]}
    return {"value": None, "change_pct": None}


def _market(val, prev, d, prevd):
    if val is None:
        return {"value": None, "change_pct": None}
    chg = round((val - prev) / prev * 100, 2) if prev else None
    return {"value": round(float(val), 4), "change_pct": chg,
            "period": record.period_label(_gap(d, prevd))}


def _prev_point(sorted_pairs, d):
    """정렬된 [(날짜,값)] 에서 날짜 d 바로 이전 점 (날짜, 값)."""
    prev = (None, None)
    for dd, vv in sorted_pairs:
        if dd < d:
            prev = (dd, vv)
        else:
            break
    return prev


def _coin(pair, day, cache):
    k = (pair, day)
    if k not in cache:
        try:
            cache[k] = coinbase_on(pair, day)
        except Exception:
            cache[k] = None
        time.sleep(0.2)
    return cache[k]


def _prev_trading(day):
    d = datetime.strptime(day, "%Y-%m-%d").date() - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.strftime("%Y-%m-%d")


def _gap(d, prevd):
    if not d or not prevd:
        return None
    return (datetime.strptime(d, "%Y-%m-%d") - datetime.strptime(prevd, "%Y-%m-%d")).days


if __name__ == "__main__":
    main()

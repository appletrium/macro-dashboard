#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""코스피가 비어 있는 과거 data 파일을 네이버 일별 시세로 채우는 일회성 보조 스크립트.

배경: 코스피는 오랫동안 무료 소스가 없어 `source:"none"`(value=None)으로 비워 뒀다가
record.py에서 네이버 금융(naver) 소스로 전환됨. 이 스크립트는 그 전환 이전에 쌓인
과거 파일들의 빈 코스피 칸을 네이버 일별 OHLC 시계열로 소급해서 채운다.

날짜 매핑은 backfill.py 관례를 그대로 따른다:
  · 파일의 세션 날짜(day) 이하 가장 가까운 거래일의 종가를 그 칸에 넣고
  · 직전 거래점 대비 변화율(change_pct)과 기간 라벨(period)을 붙인다.
빈 칸(value=None)만 건드리고, 이미 값이 있는 파일·다른 지표는 그대로 둔다.

실행: python3 fill_kospi.py
"""
import json
import os
import re
import sys
import glob
from datetime import datetime, timedelta

import record   # SESSION·NAVER_HEADERS·period_label·DATA_DIR·build_dashboard 재사용

# 네이버 일별 OHLC 시계열 (JS 배열 형태로 응답): 날짜,시가,고가,저가,종가,거래량,외국인소진율
SISE_URL = ("https://api.finance.naver.com/siseJson.naver"
            "?symbol={sym}&requestType=1&startTime={start}&endTime={end}&timeframe=day")


def kospi_daily(start, end, sym="KOSPI"):
    """네이버에서 [start,end] 구간 일별 종가 {YYYY-MM-DD: close} 를 받아온다."""
    url = SISE_URL.format(sym=sym, start=start, end=end)
    r = record.SESSION.get(url, headers=record.NAVER_HEADERS, timeout=20)
    r.raise_for_status()
    out = {}
    # 각 데이터 행: ["20260601", 8485.67, 8874.16, 8485.67, 8788.38, 636175, 0.0]
    #   → 날짜, 시가, 고가, 저가, [종가] 순. 4번째 숫자(종가)만 캡처.
    for m in re.finditer(r'\["(\d{8})",\s*[\d.]+,\s*[\d.]+,\s*[\d.]+,\s*([\d.]+)', r.text):
        ymd, close = m.group(1), float(m.group(2))
        out[f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:]}"] = close
    return out


def on_or_before(sorted_pairs, day):
    """정렬된 [(날짜,종가)] 에서 day 이하 가장 가까운 (날짜, 종가). 없으면 (None, None)."""
    best = (None, None)
    for d, v in sorted_pairs:
        if d <= day:
            best = (d, v)
        else:
            break
    return best


def prev_point(sorted_pairs, d):
    """day d 바로 이전 거래점 (날짜, 종가)."""
    prev = (None, None)
    for dd, vv in sorted_pairs:
        if dd < d:
            prev = (dd, vv)
        else:
            break
    return prev


def main():
    files = sorted(glob.glob(os.path.join(record.DATA_DIR, "*.json")))
    # 코스피가 비어 있는(value=None) 파일만 대상
    empties = []
    for path in files:
        with open(path, encoding="utf-8") as f:
            snap = json.load(f)
        k = snap.get("indicators", {}).get("kospi", {})
        if k.get("value") is None:
            empties.append((path, snap))
    if not empties:
        print("빈 코스피 파일 없음 — 할 일 없음")
        return

    days = [os.path.basename(p)[:-5] for p, _ in empties]
    print(f"빈 코스피 {len(empties)}개: {', '.join(days)}\n")

    # 시계열은 가장 이른 대상보다 더 과거부터 받아야 '직전 거래점'이 잡힌다(20일 여유).
    start = (datetime.strptime(min(days), "%Y-%m-%d") - timedelta(days=20)).strftime("%Y%m%d")
    end = max(os.path.basename(p)[:-5] for p in files).replace("-", "")
    series = sorted(kospi_daily(start, end).items())
    if not series:
        print("✗ 네이버 시계열 수신 실패")
        sys.exit(1)
    print(f"네이버 일별 종가 {len(series)}일치 ({series[0][0]} ~ {series[-1][0]})\n")

    for path, snap in empties:
        day = os.path.basename(path)[:-5]
        d, val = on_or_before(series, day)
        if val is None:
            print(f"  - {day}  매칭 종가 없음(시계열 범위 밖) → 건너뜀")
            continue
        prevd, prev = prev_point(series, d)
        chg = round((val - prev) / prev * 100, 2) if prev else None
        gap = (datetime.strptime(d, "%Y-%m-%d")
               - datetime.strptime(prevd, "%Y-%m-%d")).days if prevd else None
        entry = snap["indicators"].get("kospi", {})
        entry.pop("note", None)
        entry.update({"value": round(float(val), 4), "change_pct": chg,
                      "period": record.period_label(gap), "status": "ok"})
        snap["indicators"]["kospi"] = entry
        with open(path, "w", encoding="utf-8") as f:
            json.dump(snap, f, ensure_ascii=False, indent=2)
        print(f"  ✓ {day}  ← 코스피 {val:,.2f} (종가일 {d}, {chg if chg is not None else '—'}%)")

    record.build_dashboard(record.load_history())
    print("\n🖥️  대시보드 재생성 완료")


if __name__ == "__main__":
    main()

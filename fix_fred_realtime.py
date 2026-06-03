#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
백필된 FRED 지표를 '그 날짜 시점에 실제로 발표돼 있던 값'으로 다시 계산합니다.
(기존 백필은 기준월 1일 날짜를 그대로 써서, 아직 발표 전인 월간 값을 미리 넣는
 lookahead 오류가 있었음 → 월간 지표 스파크라인이 평평했던 원인)

FRED realtime(vintage) 데이터를 써서 발표 시차를 반영합니다.
실행:  python3 fix_fred_realtime.py
"""

import json
import os
import time
from datetime import datetime

import requests
import record

def fetch_vintages(sid, key):
    """{기준월: [(발표일, 값), ...]} 형태의 vintage 맵.
    realtime_end 는 생략 → FRED가 자기 기준 '오늘'까지로 자동 설정(미래 날짜 오류 방지).
    realtime_start 를 과거로 주면 그 사이의 모든 발표(개정) 이력을 받습니다."""
    url = ("https://api.stlouisfed.org/fred/series/observations"
           f"?series_id={sid}&api_key={key}&file_type=json"
           f"&observation_start=2025-10-01&realtime_start=2026-03-01")
    for attempt in range(5):
        r = requests.get(url, timeout=20)
        if r.status_code == 429:
            time.sleep(4 + attempt * 4)
            continue
        break
    r.raise_for_status()
    out = {}
    for o in r.json().get("observations", []):
        if o["value"] in (".", "", None):
            continue
        out.setdefault(o["date"], []).append((o["realtime_start"], float(o["value"])))
    return out


def value_asof(vintages, day):
    """day 시점에 알려진 (기준월, 값)들 중 최신 2개를 (현재, 직전)으로 반환."""
    known = []
    for ref, vlist in vintages.items():
        valid = [v for v in vlist if v[0] <= day]   # 발표일 <= 해당 날짜
        if valid:
            known.append((ref, max(valid, key=lambda x: x[0])[1]))
    known.sort()                                     # 기준월 오름차순
    if not known:
        return None, None
    cur = known[-1]
    prev = known[-2] if len(known) >= 2 else None
    return cur, prev


def main():
    key = record.load_fred_key()
    fred_inds = [ind for ind in record.INDICATORS if ind["source"] == "fred"]

    print("FRED vintage 수집:")
    vintages = {}
    for ind in fred_inds:
        vintages[ind["key"]] = fetch_vintages(ind["ticker"], key)
        print(f"  {ind['ticker']:<10} {len(vintages[ind['key']])} 기준월")
        time.sleep(1.5)   # FRED 연속 호출 간격(429 방지)

    files = sorted(f for f in os.listdir(record.DATA_DIR) if f.endswith(".json"))
    print(f"\n{len(files)}개 파일 보정:")
    for fn in files:
        p = os.path.join(record.DATA_DIR, fn)
        snap = json.load(open(p, encoding="utf-8"))
        day = snap["date"]
        for ind in fred_inds:
            cur, prev = value_asof(vintages[ind["key"]], day)
            cell = snap["indicators"][ind["key"]]
            if cur is None:
                continue
            scale = ind.get("scale", 1)
            chg = round((cur[1] - prev[1]) / prev[1] * 100, 2) if prev and prev[1] else None
            gap = None
            if prev:
                gap = (datetime.strptime(cur[0], "%Y-%m-%d")
                       - datetime.strptime(prev[0], "%Y-%m-%d")).days
            cell.update({"value": round(cur[1] * scale, 4), "change_pct": chg,
                         "obs_date": cur[0], "period": record.period_label(gap),
                         "status": "ok"})
        json.dump(snap, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    # 보정 결과 미리보기: CPI가 날짜별로 어떻게 변하는지
    print("\nCPI 보정 결과(날짜 시점 발표값):")
    for fn in files:
        d = json.load(open(os.path.join(record.DATA_DIR, fn), encoding="utf-8"))
        c = d["indicators"]["cpi"]
        print(f"  {d['date']}  CPI={c['value']}  (기준월 {c.get('obs_date')}, {c.get('change_pct')}% {c.get('period')})")

    record.build_dashboard(record.load_history())
    print("\n🖥️  대시보드 재생성 완료")


if __name__ == "__main__":
    main()

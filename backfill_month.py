#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
2026-04-27 ~ 2026-05-26 영업일(토·일 제외)을 백필합니다.
지수·환율·원자재는 미리 받아둔 야후 3개월 시계열(/tmp/yh3_*.json)을 쓰고,
암호화폐는 Coinbase 과거 spot, 공포·탐욕은 CNN historical, 나머지는 FRED.

실행:  python3 backfill_month.py   (먼저 yh3_*.json 들이 준비돼 있어야 함)
"""

import json
import datetime
import sys
from datetime import date

import record
import backfill as bf

# 기간은 명령행 인자로 받음:  python3 backfill_month.py 2026-03-27 2026-04-24
START = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else date(2026, 4, 27)
END = date.fromisoformat(sys.argv[2]) if len(sys.argv) > 2 else date(2026, 5, 26)


def load_yahoo_temp():
    maps = {}
    for key in bf.STOOQ_TO_YAHOO:
        res = json.load(open(f"/tmp/yh3_{key}.json"))["chart"]["result"][0]
        ts = res["timestamp"]
        cl = res["indicators"]["quote"][0]["close"]
        m = {}
        for t, c in zip(ts, cl):
            if c is not None:
                m[datetime.datetime.fromtimestamp(t).strftime("%Y-%m-%d")] = c
        maps[key] = sorted(m.items())
        print(f"  {key:<8} {len(maps[key])}일치 ({maps[key][0][0]} ~ {maps[key][-1][0]})")
    return maps


def main():
    key = record.load_fred_key()
    print("야후 임시 시계열 로드:")
    yahoo_maps = load_yahoo_temp()

    fng_map = sorted(bf.cnn_history().items())
    print(f"  CNN 공포·탐욕 {len(fng_map)}일치")

    import time as _t
    from fix_fred_realtime import fetch_vintages
    fred_cache = {}
    for ind in record.INDICATORS:
        if ind["source"] == "fred":
            fred_cache[ind["key"]] = fetch_vintages(ind["ticker"], key)   # 발표시차 반영
            _t.sleep(1.5)
    print(f"  FRED {sum(1 for v in fred_cache.values() if v)}개 시리즈(realtime)\n")

    coin_cache = {}
    targets = bf.business_days(START, END)
    print(f"백필 대상 {len(targets)}일: {targets[0]} ~ {targets[-1]}\n")
    for day in targets:
        snap = {"date": day, "recorded_at": f"{day}T00:00:00", "indicators": {}}
        for ind in record.INDICATORS:
            meta = {k: ind.get(k) for k in ("name", "category", "group", "unit", "invert")}
            data = bf.build_indicator(ind, day, yahoo_maps, fng_map, coin_cache, fred_cache)
            status = "ok" if data.get("value") is not None else "pending"
            snap["indicators"][ind["key"]] = {**meta, **data, "status": status}
        record.save_snapshot(snap)
        ok = sum(1 for v in snap["indicators"].values() if v["status"] == "ok")
        print(f"  💾 {day}  ({ok}/{len(record.INDICATORS)})")

    record.build_dashboard(record.load_history())
    print(f"\n🖥️  대시보드 재생성 완료 (누적 {len(record.load_history())}일치)")


if __name__ == "__main__":
    main()

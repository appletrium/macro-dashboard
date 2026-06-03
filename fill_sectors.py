#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
미리 받아둔 섹터 ETF 야후 시계열(/tmp/yh3_sec_*.json)을 이용해
기존 data/*.json 모든 파일에 섹터(group 4) 항목을 추가/갱신합니다.
(섹터는 새로 추가돼 기존 파일엔 키가 없으므로 메타까지 새로 만들어 넣음)

실행:  python3 fill_sectors.py
"""

import json
import os
import datetime

import record
import backfill as bf

SECTORS = [ind for ind in record.INDICATORS if ind["group"] == 4]


def load_maps():
    maps = {}
    for ind in SECTORS:
        res = json.load(open(f"/tmp/yh3_{ind['key']}.json"))["chart"]["result"][0]
        ts = res["timestamp"]
        cl = res["indicators"]["quote"][0]["close"]
        m = {datetime.datetime.fromtimestamp(t).strftime("%Y-%m-%d"): c
             for t, c in zip(ts, cl) if c is not None}
        maps[ind["key"]] = sorted(m.items())
        print(f"  {ind['name']:<8}({ind['category']}) {len(maps[ind['key']])}일치")
    return maps


def main():
    print("섹터 야후 시계열 로드:")
    maps = load_maps()

    files = sorted(f for f in os.listdir(record.DATA_DIR) if f.endswith(".json"))
    print(f"\n{len(files)}개 파일에 섹터 추가:")
    for fn in files:
        p = os.path.join(record.DATA_DIR, fn)
        snap = json.load(open(p, encoding="utf-8"))
        day = snap["date"]
        n = 0
        for ind in SECTORS:
            hist = maps.get(ind["key"], [])
            d, val = bf.value_on_or_before(hist, day)
            meta = {k: ind.get(k) for k in ("name", "category", "group", "unit", "invert")}
            if val is None:
                snap["indicators"][ind["key"]] = {**meta, "value": None,
                                                  "change_pct": None, "status": "pending"}
                continue
            prevd, prev = bf._prev_point(hist, d)
            data = bf._market(val, prev, d, prevd)
            snap["indicators"][ind["key"]] = {**meta, **data, "status": "ok"}
            n += 1
        json.dump(snap, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"  💾 {day}  (+{n} 섹터)")

    record.build_dashboard(record.load_history())
    print("\n🖥️  대시보드 재생성 완료")

    # 최신일 섹터 순위 미리보기
    latest = json.load(open(os.path.join(record.DATA_DIR, files[-1]), encoding="utf-8"))
    rows = [(latest["indicators"][i["key"]]) for i in SECTORS]
    rows = [r for r in rows if r.get("change_pct") is not None]
    rows.sort(key=lambda r: -r["change_pct"])
    print(f"\n{latest['date']} 섹터 순위:")
    for r in rows:
        print(f"  {r['name']:<8} {r['change_pct']:+.2f}%")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
백필 후 비어 있는 Yahoo 지표(지수·환율·원자재)를 채우는 보충 스크립트.
야후는 짧은 시간 여러 요청이 몰리면 429로 막으므로, 종목을 하나씩
충분히 간격을 두고(기본 10초) 받아 기존 날짜별 json 파일에 채워 넣습니다.

실행:  python3 fill_yahoo.py
"""

import json
import os
import time

import record
import backfill as bf

GAP_SEC = 10   # 종목 사이 간격(초) — 429 방지


def main():
    # 채워야 할 날짜 파일들(백필로 만들어진 것 = 오늘 이전)
    today = record.datetime.now().strftime("%Y-%m-%d")
    files = sorted(fn for fn in os.listdir(record.DATA_DIR)
                   if fn.endswith(".json") and fn[:-5] < today)
    print(f"대상 파일: {', '.join(f[:-5] for f in files)}\n")

    # 1) 야후 시계열을 종목별로 천천히 수집
    yahoo_maps = {}
    for key_ind, ysym in bf.STOOQ_TO_YAHOO.items():
        for attempt in range(5):
            try:
                yahoo_maps[key_ind] = sorted(bf.yahoo_history(ysym).items())
                print(f"  ✓ {ysym:<8} {len(yahoo_maps[key_ind])}일치")
                break
            except Exception as e:
                if attempt < 4:
                    time.sleep(15)
                    continue
                yahoo_maps[key_ind] = []
                print(f"  ✗ {ysym:<8} 실패: {str(e)[:50]}")
        time.sleep(GAP_SEC)

    # 2) 각 날짜 파일의 stooq(=야후 백필) 지표 채우기
    print()
    stooq_keys = [ind["key"] for ind in record.INDICATORS if ind["source"] == "stooq"]
    for fn in files:
        path = os.path.join(record.DATA_DIR, fn)
        snap = json.load(open(path, encoding="utf-8"))
        day = snap["date"]
        filled = 0
        for key_ind in stooq_keys:
            hist = yahoo_maps.get(key_ind, [])
            d, val = bf.value_on_or_before(hist, day)
            if val is None:
                continue
            prevd, prev = bf._prev_point(hist, d)
            data = bf._market(val, prev, d, prevd)
            snap["indicators"][key_ind].update(data)
            snap["indicators"][key_ind]["status"] = "ok"
            filled += 1
        json.dump(snap, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        ok = sum(1 for v in snap["indicators"].values() if v["status"] == "ok")
        print(f"  💾 {day}  (+{filled} 채움 → {ok}/{len(record.INDICATORS)})")

    # 3) 대시보드 재생성
    record.build_dashboard(record.load_history())
    print("\n🖥️  대시보드 재생성 완료")


if __name__ == "__main__":
    main()

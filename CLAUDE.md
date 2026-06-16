# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 프로젝트 개요

매일 1회 매크로 지표를 수집해 `data/날짜.json`으로 저장하고 `dashboard.html`을 다시 생성하는 단일 스크립트 도구. 서버·빌드·테스트 없음. 코드 주석과 UI 텍스트는 모두 한국어.

## 명령어

```bash
python3 record.py        # 일일 수집 + data/ 저장 + dashboard.html 재생성 (메인 진입점)
python3 backfill.py      # 과거 영업일 백필 (record.py 함수 재사용)
python3 backfill_month.py 2026-03-27 2026-04-24   # 특정 기간 백필 (/tmp/yh3_*.json 필요)
```

- `dashboard.html`은 브라우저에서 직접 열어 확인 (서버 불필요).
- 자동 실행: launchd `com.macro-dashboard.daily` (매일 07:00, `~/Library/LaunchAgents/com.macro-dashboard.daily.plist`), 로그는 `logs/launchd.log`.
- API 키: `.env`에 `FRED_API_KEY`(FRED 지표), `TWELVEDATA_API_KEY`(금·환율·섹터ETF·DXY합성). 없으면 해당 지표만 실패하고 나머지는 동작.

## 아키텍처

`record.py` 하나가 핵심. `main()` 흐름:

```
collect() → save_snapshot() → reconcile_daily_fred() → reconcile_cboe_vix()
→ load_history() → build_dashboard()
```

### INDICATORS 목록 (record.py 상단) — 유일한 설정 지점

지표 추가/삭제/수정은 이 리스트만 고치면 됨. 각 항목: `key`(데이터 파일의 키), `source`, `ticker`, `group`(1=시장심리, 2=주요자산, 3=미국매크로, 4=섹터 히트맵), `invert`(True면 상승=빨강, 예: VIX·실업률), `unit`, `scale`.

소스별 fetcher: `fred`, `treasury`(미 재무부 일별 금리), `cboe`(VIX), `twelvedata`, `dxy`(TwelveData FX 6종으로 합성), `coinbase`, `cnn`(공포·탐욕), `naver`(코스피 — 네이버 금융 모바일 API), `none`(무료 소스 없는 지표용 placeholder).

**주의: README의 소스 설명은 일부 구버전.** Stooq는 봇 차단으로 못 쓰게 되어 sp500·nasdaq·wti는 FRED 일간 시리즈, us10y·us2y·spread는 treasury, vix는 CBOE로 이전됨. 실제 소스는 INDICATORS 목록이 진실.

### 날짜 규칙과 self-heal 보정

- 파일 날짜는 KST 달력 날짜가 아니라 **미국 세션 날짜**(`us_session_date()` — 재무부 일별 발표의 최신 영업일). 백필 데이터와 날짜 기준을 맞추기 위함.
- FRED·CBOE는 ~1영업일 늦게 발표하므로 기록 당일엔 직전값(stale)이 들어감. `reconcile_daily_fred()`/`reconcile_cboe_vix()`가 매 실행마다 **최근 6개 data 파일을 다시 써서** 발표된 종가로 보정(self-heal). 즉 data 파일은 불변이 아님.
- 시장 데이터(주가·코인·심리)의 `change_pct`는 직전 저장 스냅샷과 비교해 계산. FRED 지표는 시계열에서 직전 발표 대비로 계산.
- `.record.lock` + `fcntl.flock`으로 중복 실행 방지.

### 데이터 형식

`data/YYYY-MM-DD.json` (같은 날 재실행 시 덮어씀):

```json
{"date": "...", "recorded_at": "...",
 "indicators": {"vix": {"name", "category", "group", "unit", "invert",
                        "value", "change_pct", "obs_date", "period", "status"}}}
```

`status`: ok / pending / error.

### dashboard.html

`build_dashboard()`가 전체 히스토리를 임베드해 생성하는 ~1.8MB 단일 파일. **직접 편집 금지** — 화면 수정은 `build_dashboard()` 안의 HTML/CSS/JS 템플릿을 고치고 `python3 record.py`로 재생성. 과거 날짜 선택 시 그 시점까지의 데이터만으로 화면을 그림(`render_sections`).

### 보조 스크립트 (일회성 백필·수정 도구)

`backfill.py`(과거 백필, Yahoo/CNN historical/FRED vintage 사용), `fill_yahoo.py`(429 회피하며 빈 값 보충), `fill_sectors.py`(기존 파일에 섹터 추가), `fix_fred_realtime.py`(FRED 발표 시차 lookahead 오류 수정). 모두 `record.py`를 import해 재사용.

## 외부 API 주의사항

- Yahoo·FRED·Twelve Data 모두 429 차단 있음 — 요청 간 `time.sleep` 간격과 재시도가 코드에 들어있으니 새 fetcher 추가 시 같은 패턴 유지.
- CNN은 봇 차단 — 브라우저형 헤더(특히 `Referer`) 필수 (`CNN_HEADERS`).
- Twelve Data 무료 플랜은 분당 호출 제한이 있어 `_td_populate()`가 심볼을 묶어서 한 번에 받아 캐시함.

# Phase R1 Risk Context Collection

This phase is observe-only.

It does not block recommendations and does not change the trading policy. It stores live context around the current candidates so later validation can check whether market flow, orderbook, trade strength, sector/index flow, news, and disclosures helped reduce stop-loss and time-exit cases.

## One-click launchers

- `일일수집검증_실행.bat`: now also collects R1 risk context automatically after candidate generation.
- `리스크컨텍스트_수집.bat`: collect risk context for the latest top candidates.
- `리스크컨텍스트_결과_열기.bat`: open the latest HTML report.
- `리스크컨텍스트_검증.bat`: join accumulated context to actual outcomes and analyze candidate gates.
- `리스크컨텍스트_검증결과_열기.bat`: open the latest validation HTML report.

## Outputs

- Full per-ticker JSON snapshots: `data/live_context/YYYYMMDD/`
- Summary CSV/JSON/HTML: `output/risk_context/`
- Joined validation CSV/HTML: `output/risk_context_validation/`

## CLI fallback

```powershell
python -m kr_precision_backtest.collect_risk_context --max-tickers 5
```

Daily pipeline collection is enabled by default:

```powershell
python -m kr_precision_backtest.daily_pipeline --max-tickers 2
```

Disable it only when debugging:

```powershell
python -m kr_precision_backtest.daily_pipeline --skip-risk-context
```

Useful options:

```powershell
python -m kr_precision_backtest.collect_risk_context --tickers 005930,000660 --skip-news --skip-dart
```

Validate accumulated context against actual outcomes:

```powershell
python -m kr_precision_backtest.analyze_risk_context
```

## Data sources

- KIS current price
- KIS orderbook and expected execution
- KIS current conclusion
- KIS time-by-time conclusion
- KIS KOSPI/KOSDAQ index price and minute context
- OpenDART disclosure search, if `OPENDART_API_KEY` exists
- Naver news search, if `NAVER_CLIENT_ID` and `NAVER_CLIENT_SECRET` exist

## Promotion rule

Do not convert these fields into hard blocking rules until enough observe-only records exist. At minimum, compare blocked-vs-kept outcomes after 30 actual entries; prefer 50+ entries before promoting a risk gate into automatic decision logic.

Validation only counts risk context collected on the same date and before the recorded entry time. Context collected after entry is treated as post-event data and excluded from gate evaluation.

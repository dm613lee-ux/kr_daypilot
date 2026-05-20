# KR DayPilot Score Coverage Audit

- Generated at: 2026-05-20T08:26:48+09:00
- Signal day: 20260519
- Universe rows: 204
- Fundamental coverage: 98.04%
- Flow as-of coverage: 8.33%
- Active event coverage: 3.43%
- Tactical top15 missing quality: 1
- Core top15 missing quality: 0
- High risk score columns: event_flow_confirmation_score, event_score, flow_score

## Score Columns

|engine|score_column|coverage|raw_full|raw_partial|raw_none|neutral/fallback|midband_45_55|median|
|---|---:|---:|---:|---:|---:|---:|---:|---:|
|shared|value_score|100.0%|80.88%|19.12%|0.0%|0.0%|8.82%|45.613|
|shared|quality_score|98.04%|98.04%|0.0%|1.96%|0.0%|13.73%|51.438|
|shared|momentum_score|100.0%|99.02%|0.98%|0.0%|0.0%|13.24%|47.937|
|shared|defensive_score|100.0%|100.0%|0.0%|0.0%|0.0%|19.61%|50.98|
|shared|flow_score|100.0%|8.33%|0.0%|91.67%|91.67%|92.16%|49.755|
|shared|liquidity_score|100.0%|100.0%|0.0%|0.0%|0.0%|10.78%|50.858|
|shared|recovery_score|100.0%|100.0%|0.0%|0.0%|0.0%|13.24%|65.032|
|shared|event_score|100.0%|3.43%|0.0%|96.57%|96.57%|0.0%|100.0|
|tactical|raw_final_score|100.0%|100.0%|0.0%|0.0%|0.0%|24.51%|60.521|
|tactical|final_score|100.0%|100.0%|0.0%|0.0%|0.0%|26.96%|59.132|
|core|fundamental_score|100.0%|80.88%|19.12%|0.0%|0.0%|19.61%|53.007|
|core|value_momentum_score|100.0%|79.9%|20.1%|0.0%|0.0%|18.14%|51.792|
|core|risk_adjustment_score|100.0%|100.0%|0.0%|0.0%|0.0%|25.98%|50.735|
|core|event_flow_confirmation_score|100.0%|3.43%|4.9%|91.67%|91.67%|93.14%|49.142|
|core|raw_final_score|100.0%|100.0%|0.0%|0.0%|0.0%|29.9%|51.376|
|core|final_score|100.0%|100.0%|0.0%|0.0%|0.0%|29.41%|49.841|

## Raw Inputs

|group|input_flag|present|
|---|---:|---:|
|valuation|per_valid|80.88%|
|valuation|pbr_valid|98.04%|
|valuation|dividend_yield_present|100.0%|
|quality|roe_present|98.04%|
|quality|roa_present|98.04%|
|quality|operating_margin_present|98.04%|
|quality|net_margin_present|98.04%|
|momentum|momentum_120_present|100.0%|
|momentum|momentum_240_present|99.02%|
|momentum|price_vs_ma120_present|100.0%|
|momentum|ret_60d_present|100.0%|
|defensive|volatility_60d_present|100.0%|
|defensive|drawdown_60d_present|100.0%|
|defensive|price_vs_ma120_present|100.0%|
|flow|smart_flow_asof_present|8.33%|
|flow|smart_flow_nonzero|8.33%|
|liquidity|market_cap_present|100.0%|
|liquidity|avg_value_20_present|100.0%|
|event|event_context_present|3.43%|

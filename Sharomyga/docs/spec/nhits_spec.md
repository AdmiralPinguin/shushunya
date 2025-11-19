# N-HiTS spec for Sharomyga

## Цель
Predict:
- move_magnitude (normalized) = |P[t+H] - P[t]|
- move_sign ∈ {-1,0,1}
- stability ∈ [0,1] = fraction of bars until t+H with same sign

## Входы (window L)
- Primary TF window: L bars (e.g. 256) of features:
  - close, high, low, volume, returns, atr, ema_diff, rsi, spread
- Multi-TF context (aggregated):
  - 1h: ema_1h, atr_1h, rsi_1h
  - 4h/day: atr_4h, trend_4h
- Preprocessing: per-feature StandardScaler or robust scaling

## Выход (H)
- H = 5 (1..5 bars)
- move_magnitude normalized (0..1), move_sign, stability (0..1)

## Use in bot
- if sign>0 and stability>th and magnitude>th -> OPEN LONG (size per bucket)
- if sign<0 and stability>th and magnitude>th -> OPEN SHORT
- otherwise HOLD

## Logs
Write per-decision: ts,symbol,tf,inputs_hash,move_mag_pred,move_sign_pred,stability_pred,confidence,latency_ms

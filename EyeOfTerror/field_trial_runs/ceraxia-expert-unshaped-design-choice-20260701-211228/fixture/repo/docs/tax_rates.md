# Tax Rates

Design decision: use a `RATES` table plus a small compatible caller wrapper. Rejected options: hardcoding fixture values would not generalize; broad rewrite would add unnecessary churn.

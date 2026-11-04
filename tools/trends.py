from pytrends.request import TrendReq
kw = ["japanese company history", "japan economy", "keyence", "toyota production system"]
try:
    p = TrendReq(hl="en-US", tz=0, timeout=(5, 20))
    p.build_payload(kw, timeframe="today 5-y", geo="")
    df = p.interest_over_time().drop(columns="isPartial")
    print(df.resample("YE").mean().round(1))
except Exception as e:
    print("FAILED:", type(e).__name__, e)

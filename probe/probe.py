import json, urllib.request, http.cookiejar, urllib.parse
UA={"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"}
op=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
g=lambda u:op.open(urllib.request.Request(u,headers=UA),timeout=20).read().decode()
syms=urllib.parse.quote("@C.1|@S.1|@W.1|@CL.1|@RB.1|@HO.1")
q=json.loads(g(f"https://quote.cnbc.com/quote-html-webservice/restQuote/symbolType/symbol?symbols={syms}&requestMethod=itv&noform=1&partnerId=2&fund=1&exthrs=1&output=json"))["FormattedQuoteResult"]["FormattedQuote"]
for x in q: print("::warning::CNBC "+json.dumps({k:x.get(k) for k in ("symbol","name","last","change","change_pct","previous_day_closing","last_time","last_timedate","curmktstatus","settlePrice","settleDate","settle_time")}))
print("::warning::CNBCKEYS "+",".join(sorted(q[0].keys())))
c=g("https://query1.finance.yahoo.com/v1/test/getcrumb")
y=json.loads(g(f"https://query1.finance.yahoo.com/v7/finance/quote?symbols=ZC%3DF,ZS%3DF,ZW%3DF,CL%3DF,RB%3DF,HO%3DF&crumb={urllib.parse.quote(c)}"))["quoteResponse"]["result"]
for x in y: print("::warning::YAHOO "+json.dumps({k:x.get(k) for k in ("symbol","shortName","regularMarketPrice","regularMarketPreviousClose","regularMarketChangePercent","regularMarketTime","marketState")}))

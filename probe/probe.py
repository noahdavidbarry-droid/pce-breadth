import json, urllib.request, http.cookiejar, urllib.parse
UA={"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36","Accept":"*/*"}
cj=http.cookiejar.CookieJar(); op=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
def t(name,url,extra=None):
    try:
        r=op.open(urllib.request.Request(url,headers={**UA,**(extra or {})}),timeout=20); b=r.read().decode('utf-8','replace')
        print(f"::warning::OK {name} {r.status} {b[:220]!r}"); return b
    except Exception as e:
        body=''
        try: body=e.read().decode()[:120]
        except Exception: pass
        print(f"::warning::FAIL {name} {type(e).__name__} {e} {body!r}")
syms=urllib.parse.quote("@C.1|@S.1|@W.1|@CL.1|@RB.1|@HO.1")
t("cnbc",f"https://quote.cnbc.com/quote-html-webservice/restQuote/symbolType/symbol?symbols={syms}&requestMethod=itv&noform=1&partnerId=2&fund=1&exthrs=1&output=json")
t("yahoo_fc","https://fc.yahoo.com")
c=t("yahoo_crumb","https://query1.finance.yahoo.com/v1/test/getcrumb")
if c and len(c)<40: t("yahoo_v7",f"https://query1.finance.yahoo.com/v7/finance/quote?symbols=ZC=F,CL=F&crumb={urllib.parse.quote(c)}")
t("stooq_cl","https://stooq.com/q/l/?s=cl.f&f=sd2t2ohlc&h&e=csv")
t("stooq_pl_zc","https://stooq.pl/q/l/?s=zc.f&f=sd2t2ohlc&h&e=csv")
t("cme_corn","https://www.cmegroup.com/CmeWS/mvc/Quotes/Future/300/G",{"Referer":"https://www.cmegroup.com/"})
t("eia_noKey","https://api.eia.gov/v2/petroleum/pri/spt/data/?frequency=daily&data[0]=value&length=1")

import json, urllib.request, time
def t(name,url,h):
    try:
        r=urllib.request.urlopen(urllib.request.Request(url,headers=h),timeout=30); b=r.read()
        print(f"OK {name} {r.status} len={len(b)} {b[:120]!r}"); return b
    except Exception as e:
        body=b''
        try: body=e.read()[:400]
        except Exception: pass
        print(f"FAIL {name} {e} {body!r}")
u="https://data.sec.gov/api/xbrl/companyfacts/CIK0000789019.json"
for n,h in [("ua_noreply_name",{"User-Agent":"Noah Barry noahdavidbarry-droid@users.noreply.github.com"}),
            ("ua_plain_email_gzip",{"User-Agent":"pce-breadth admin@example.com","Accept-Encoding":"gzip, deflate","Host":"data.sec.gov"}),
            ("ua_sample",{"User-Agent":"Sample Company Name AdminContact@samplecompanydomain.com"})]:
    t(n,u,h); time.sleep(1)
t("sec_www_tickers","https://www.sec.gov/files/company_tickers.json",{"User-Agent":"Noah Barry noahdavidbarry-droid@users.noreply.github.com"})
B={"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"}
import time as T
p2=int(T.time()); p1=p2-6*365*86400
b=t("yahoo_fund",f"https://query2.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/MSFT?type=quarterlyCapitalExpenditure,annualCapitalExpenditure&period1={p1}&period2={p2}",B)
if b:
    j=json.loads(b)
    for r in j["timeseries"]["result"]:
        k=r["meta"]["type"][0]; v=r.get(k) or []
        print("  ",k,len(v),[ (x["asOfDate"],x["reportedValue"]["raw"]) for x in v if x][:8])

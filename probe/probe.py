import json, urllib.request, http.cookiejar, urllib.parse, re
B={"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"}
SEC={"User-Agent":"pce-breadth research bot github.com/noahdavidbarry-droid/pce-breadth"}
op=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
def t(name,url,h=B,show=200):
    try:
        r=op.open(urllib.request.Request(url,headers=h),timeout=30); b=r.read().decode('utf-8','replace')
        print(f"::warning::OK {name} {r.status} len={len(b)} {b[:show]!r}"); return b
    except Exception as e:
        print(f"::warning::FAIL {name} {type(e).__name__} {e}")
b=t("edgar_msft","https://data.sec.gov/api/xbrl/companyfacts/CIK0000789019.json",SEC,80)
if b:
    g=json.loads(b)["facts"]["us-gaap"]; print("::warning::MSFT concepts "+",".join(k for k in g if "PropertyPlant" in k or "ProductiveAssets" in k))
for tk,cik in [("AMZN","0001018724"),("META","0001326801"),("GOOGL","0001652044"),("ORCL","0001341439")]:
    b=t("edgar_"+tk,f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json",SEC,0)
    if b:
        g=json.loads(b)["facts"]["us-gaap"]
        for k in ("PaymentsToAcquirePropertyPlantAndEquipment","PaymentsToAcquireProductiveAssets"):
            u=g.get(k,{}).get("units",{}).get("USD",[])
            print(f"::warning::{tk} {k} n={len(u)} last_end={max([x['end'] for x in u]) if u else None}")
for n,u in [("speeches","https://www.federalreserve.gov/feeds/speeches.xml"),("testimony","https://www.federalreserve.gov/feeds/testimony.xml"),("press_all","https://www.federalreserve.gov/feeds/press_all.xml"),("fomccal","https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"),("calendar_htm","https://www.federalreserve.gov/newsevents/calendar.htm")]:
    b=t(n,u,B,150)
    if b and n=="calendar_htm":
        print("::warning::calendar json refs "+" ".join(sorted(set(re.findall(r'["\']([^"\']+\.json[^"\']*)',b)))[:10]))
    if b and n=="speeches":
        print("::warning::speech titles "+" | ".join(re.findall(r"<title>(.*?)</title>",b)[1:6]))
t("fed_cal_json","https://www.federalreserve.gov/json/calendar.json",B,300)
t("cnbc_bars","https://ts-api.cnbc.com/harmony/app/bars/@C.1/1D/20250101000000/20261020000000/adjusted/EST5EDT.json",B,300)
c=t("y_crumb","https://query1.finance.yahoo.com/v1/test/getcrumb")
t("y_chart","https://query1.finance.yahoo.com/v8/finance/chart/ZC=F?range=2y&interval=1d"+(f"&crumb={c}" if c else ""),B,150)

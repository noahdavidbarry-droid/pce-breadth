import json, urllib.request, re
B={"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"}
g=lambda u: urllib.request.urlopen(urllib.request.Request(u,headers=B),timeout=30).read().decode('utf-8-sig','replace')
j=json.loads(g("https://www.federalreserve.gov/json/calendar.json"))
ev=j["events"]; print("keys",list(j.keys()),"n",len(ev)); print("event keys",sorted(set(k for e in ev for k in e)))
w=[e for e in ev if "warsh" in json.dumps(e).lower()]
print("warsh events",len(w))
for e in sorted(w,key=lambda e:str(e.get("days") or e.get("month") or ""))[-6:]: print(json.dumps(e)[:400])
print("types",sorted(set(str(e.get("type")) for e in ev))[:30])
h=g("https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm")
i=h.find("2026 FOMC Meetings"); print("FOMC2026 idx",i); seg=h[i:i+6000]
print(re.sub(r"\s+"," ",re.sub(r"<[^>]+>"," ",seg))[:1500])
i=h.find("2027 FOMC Meetings"); print("FOMC2027 idx",i)
s=g("https://www.federalreserve.gov/feeds/speeches.xml"); items=re.findall(r"<item>(.*?)</item>",s,re.S); print("speech items",len(items)); print(items[0][:600])

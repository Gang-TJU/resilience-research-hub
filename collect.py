import datetime as dt
import hashlib
import html
from html.parser import HTMLParser
import json
import re
import time
from pathlib import Path
from urllib.parse import urlencode, urljoin, urlparse
from urllib.request import Request, urlopen

ROOT=Path(__file__).resolve().parent
CFG=json.loads((ROOT/'sources.json').read_text())
ARCH=ROOT/'data'/'archive.json'
NOW=dt.datetime.now(dt.timezone.utc)
DAY=NOW.astimezone(dt.timezone(dt.timedelta(hours=8))).date().isoformat()
STAMP=NOW.isoformat(timespec='seconds')
WINDOW=(NOW-dt.timedelta(days=30)).date().isoformat()
def get(url):
    for attempt in range(2):
        try:
            req=Request(url,headers={'User-Agent':'ResilienceResearchHub/1.0 (+https://github.com/Gang-TJU/resilience-research-hub)','Accept':'*/*'})
            with urlopen(req,timeout=20) as f:
                raw=f.read(8_000_001)
                if len(raw)>8_000_000: raise ValueError('response too large')
                return raw.decode(f.headers.get_content_charset() or 'utf-8',errors='replace')
        except Exception:
            if attempt: raise
            time.sleep(2)
def clean(s):
    return re.sub(r'\s+',' ',html.unescape(re.sub(r'<[^>]+>',' ',str(s or '')))).strip()
def classify(title):
    s=title.lower()
    return [t for t,words in CFG['topics'].items() if any(w.lower() in s for w in words)]
def item(kind,source,title,url,date,key,extra=None):
    title=clean(title)
    if not title or urlparse(url).scheme!='https': return None
    return dict(id=hashlib.sha256(key.encode()).hexdigest()[:24],type=kind,source=source,title=title,url=url,date=date,topics=classify(title),first_seen=DAY,last_seen=DAY,metadata=extra or {})
def papers(name,issn):
    url='https://api.crossref.org/journals/'+issn+'/works?'+urlencode({'filter':'from-pub-date:'+WINDOW,'sort':'published','order':'desc','rows':100,'select':'DOI,title,published,container-title,type'})
    data=json.loads(get(url))['message']
    out=[]
    for a in data['items']:
        if a.get('type')!='journal-article' or not a.get('title') or not a.get('DOI'): continue
        title=a['title'][0]
        if not classify(clean(title)): continue
        p=a.get('published',{}).get('date-parts',[[]])[0]
        date='-'.join(str(x).zfill(2) for x in p) if p else None
        doi=a['DOI'].lower()
        out.append(item('文献',name,title,'https://doi.org/'+doi,date,'doi:'+doi,{'doi':doi,'source_api':'Crossref','date_type':'published'}))
    return out, ('结果超出100条，当前仅取最新100条' if data.get('total-results',0)>100 else '')
class Links(HTMLParser):
    def __init__(self): super().__init__(convert_charrefs=True);self.href=None;self.parts=[];self.links=[]
    def handle_starttag(self,tag,attrs):
        if tag=='a': self.href=dict(attrs).get('href');self.parts=[]
    def handle_data(self,data):
        if self.href: self.parts.append(data)
    def handle_endtag(self,tag):
        if tag=='a' and self.href:
            self.links.append((self.href,clean(' '.join(self.parts))));self.href=None
def page(src):
    p=Links();p.feed(get(src['url']))
    out=[]
    for href,title in p.links:
        url=urljoin(src['url'],href).split('#')[0]
        path=urlparse(url).path
        if urlparse(url).hostname!=src['host'] or len(title)<10 or len(title)>200: continue
        match=re.search(r'/(?:t)?(20\d{2})(\d{2})(\d{2})(?:_|/)',path)
        if not match or not path.endswith(('.html','.shtml','.htm')): continue
        if not classify(title): continue
        out.append(item(src['type'],src['name'],title,url,None,'url:'+url,{'url_date_hint':'-'.join(match.groups()),'date_type':'URL日期线索，非核实发布日期'}))
    if not out: return [],'未匹配到相关条目；可能是页面结构变化或无相关内容'
    return out,''
def dataset(short):
    url='https://cmr.earthdata.nasa.gov/search/collections.json?'+urlencode({'short_name':short,'page_size':50})
    out=[]
    for a in json.loads(get(url))['feed']['entry']:
        cid=a.get('id')
        if not cid: continue
        x=item('数据','NASA CMR',a.get('dataset_id') or a.get('title') or short,'https://cmr.earthdata.nasa.gov/search/concepts/'+cid+'.html',a.get('updated'),'cmr:'+cid,{'short_name':short,'version':a.get('version_id'),'date_type':'CMR metadata updated'})
        if x:
            if not x['topics']: x['topics']=['生态系统服务与价值实现','城市生态与气候健康'] if short.startswith(('MOD','MYD','MCD')) else ['自然灾害风险治理']
            out.append(x)
    return out, '' if out else '目录无返回；待核查'
def save(path,obj):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding='utf-8');tmp.replace(path)
def main():
    old=json.loads(ARCH.read_text()) if ARCH.exists() else {'items':[],'runs':[]}
    records={x['id']:x for x in old['items']}
    statuses=[]
    def run(name,fn):
        try:
            rows,note=fn();n=0
            for x in rows:
                if not x: continue
                prev=records.get(x['id'])
                if prev: x['first_seen']=prev['first_seen']
                else: n+=1
                records[x['id']]=x
            statuses.append({'source':name,'status':'warning' if note else 'ok','matched':len(rows),'new':n,'note':note})
        except Exception as e:
            statuses.append({'source':name,'status':'failed','matched':0,'new':0,'note':str(e)[:200]})
        print(json.dumps(statuses[-1],ensure_ascii=False),flush=True)
    for name,issn in CFG['journals']:
        run(name,lambda:papers(name,issn));time.sleep(.5)
    for src in CFG['pages']: run(src['name'],lambda:page(src))
    for short in CFG['datasets']: run('NASA '+short,lambda:dataset(short))
    rows=sorted(records.values(),key=lambda x:(x['first_seen'],x.get('date') or '',x['id']),reverse=True)
    report={'date':DAY,'checked_at':STAMP,'sources':statuses,'new':sum(s['new'] for s in statuses)}
    runs=(old.get('runs',[])+[report])[-90:]
    payload={'items':rows,'runs':runs,'latest':report,'method':'题名关键词筛选；规则统计，非AI导读；来源覆盖不完整。'}
    save(ARCH,payload);save(ROOT/'site'/'data.json',payload)
if __name__=='__main__': main()

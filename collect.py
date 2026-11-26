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
from urllib.error import HTTPError
from urllib.robotparser import RobotFileParser

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
def rank(title):
    s=title.lower()
    phrases=[w for w in CFG['priority_phrases'] if w.lower() in s]
    return min(100, 20+15*len(phrases)+5*len(classify(title))) if classify(title) else 0, phrases
def datepart(a,k):
    v=a.get(k,{}).get('date-parts',[[]])[0]
    return '-'.join(str(x).zfill(2) for x in v) if v else None
def item(kind,source,title,url,date,key,extra=None):
    title=clean(title)
    if not title or urlparse(url).scheme!='https': return None
    return dict(id=hashlib.sha256(key.encode()).hexdigest()[:24],type=kind,source=source,title=title,url=url,date=date,topics=classify(title),first_seen=DAY,last_seen=DAY,metadata=extra or {})
def papers(name,issn):
    base='https://api.crossref.org/journals/'+issn+'/works?'
    out=[];cursor='*';note='';scanned=0
    for page_no in range(6):
        params={'filter':'from-pub-date:'+WINDOW,'rows':200,'cursor':cursor,'select':'DOI,title,published,published-online,published-print,container-title,type,author,license'}
        try:
            data=json.loads(get(base+urlencode(params)))['message']
        except Exception as e:
            if not scanned: raise
            note='分页中断，已保留成功页：'+str(e)[:100];break
        batch=data['items'];scanned+=len(batch)
        for a in batch:
            if a.get('type')!='journal-article' or not a.get('title') or not a.get('DOI'): continue
            title=clean(a['title'][0])
            if not classify(title): continue
            online=datepart(a,'published-online');printed=datepart(a,'published-print');published=datepart(a,'published')
            date=online or published or printed
            dtype='published-online' if online else 'published' if published else 'published-print'
            doi=a['DOI'].lower()
            authors=[clean(' '.join(filter(None,[au.get('given'),au.get('family')])) or au.get('name')) for au in a.get('author',[])[:8]]
            out.append(item('文献',name,title,'https://doi.org/'+doi,date,'doi:'+doi,{'doi':doi,'source_api':'Crossref','date_type':dtype,'online_date':online,'issue_date':printed,'reported_date':published,'future_date':bool(date and date[:7]>DAY[:7] or date and len(date)==10 and date>DAY),'authors':[au for au in authors if au],'license_urls':[v.get('URL') for v in a.get('license',[]) if v.get('URL')]}))
        next_cursor=data.get('next-cursor')
        if len(batch)<200 or not next_cursor: break
        cursor=next_cursor
        time.sleep(.4)
    if scanned<data.get('total-results',scanned) and not note: note='已扫描'+str(scanned)+'条，达到本轮分页上限或来源结果不完整'
    return out,note
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
    origin='https://'+src['host']
    rp=RobotFileParser()
    try: rp.parse(get(origin+'/robots.txt').splitlines())
    except HTTPError as e:
        if e.code!=404: raise
        rp.parse([])
    if not rp.can_fetch('ResilienceResearchHub',src['url']): return [],'robots.txt 禁止采集'
    p=Links();p.feed(get(src['url']))
    out=[]
    for href,title in p.links:
        url=urljoin(src['url'],href).split('#')[0]
        path=urlparse(url).path
        if urlparse(url).hostname!=src['host'] or len(title)<10 or len(title)>200: continue
        match=re.search(r'/(?:20\d{4}/)?(?:t)?(20\d{2})(\d{2})(\d{2})(?:_|/|\.)',path)
        if not match or not rp.can_fetch('ResilienceResearchHub',url) or not path.endswith(('.html','.shtml','.htm')): continue
        if not classify(title): continue
        out.append(item(src['type'],src['name'],title,url,None,'url:'+url,{'url_date_hint':'-'.join(match.groups()),'date_type':'URL日期线索，非核实发布日期'}))
    if not out: return [],'未匹配到相关条目；可能是页面结构变化或无相关内容'
    return out,''
def dataset_links(x):
    m=x['metadata'];short=m.get('short_name','');version=str(m.get('version',''))
    original=m.get('cmr_url') or x['url']
    cid=m.get('concept_id') or original.rsplit('/',1)[-1].split('.')[0]
    m['cmr_url']=original;m['concept_id']=cid
    links=[]
    def add(label,url):
        if url and not any(v['url']==url for v in links): links.append({'label':label,'url':url})
    exact_search='https://search.earthdata.nasa.gov/search?'+urlencode({'q':short})
    if short=='GPM_3IMERGDF' and version in ('07','7'):
        add('NASA GES DISC 产品说明','https://disc.gsfc.nasa.gov/datasets/GPM_3IMERGDF_07/summary')
        add('NASA GPM 官方数据目录','https://gpm.nasa.gov/data/directory')
    elif short=='MOD10A1' and version in ('61','061','7','007'):
        v='7' if version in ('7','007') else '61'
        add('NSIDC 对应版本产品说明','https://nsidc.org/data/mod10a1/versions/'+v)
        if v=='61': m['version_note']='已有 Collection 7；旧版后续可能退役，按研究口径选择版本。'
    elif short=='SPL3SMP' and version in ('009','9'):
        add('NSIDC 对应版本产品说明','https://nsidc.org/data/spl3smp/versions/9')
    elif short in ('MOD13Q1','MYD13Q1','MCD12Q1','MOD11A2','MOD16A2','MOD17A2H','MOD09A1') and version=='061':
        add('Earthdata Search 产品检索（核对版本）',exact_search)
        add('Earth Engine 对应版本数据说明','https://developers.google.com/earth-engine/datasets/catalog/MODIS_061_'+short)
    add('Earthdata Search 产品检索（核对版本）',exact_search)
    add('CMR 原始元数据',original)
    m['access_links']=links
    m['access_note']='产品说明与原始数据下载不同；NASA 下载可能需要免费 Earthdata 登录。链接可达性受网络环境影响。'
    x['url']=links[0]['url']

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
    for x in records.values():
        if x['type']=='数据' and x['metadata'].get('short_name'): dataset_links(x)
        score,phrases=rank(x['title'])
        x['topics']=classify(x['title']) or x['topics']
        x['relevance']=score
        x['matched_phrases']=phrases
        if x['type']=='文献':
            d=x.get('date')
            x['metadata']['future_date']=bool(d and (d[:7]>DAY[:7] or len(d)==10 and d>DAY))
    rows=sorted(records.values(),key=lambda x:(x['first_seen'],x['relevance'],x['id']),reverse=True)
    report={'date':DAY,'checked_at':STAMP,'sources':statuses,'new':sum(s['new'] for s in statuses)}
    runs=(old.get('runs',[])+[report])[-90:]
    payload={'items':rows,'runs':runs,'latest':report,'method':'题名关键词筛选和规则相关性评分（不是学术质量评分）；非AI导读；日期与来源范围见元数据。'}
    save(ARCH,payload);save(ROOT/'site'/'data.json',payload)
if __name__=='__main__': main()

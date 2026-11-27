"""Independent, bounded, read-only public policy collection; no paid APIs."""
import json,re,hashlib,datetime as dt,difflib,concurrent.futures,html,time
from pathlib import Path
from html.parser import HTMLParser
from urllib.parse import urlparse,urljoin
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from urllib.robotparser import RobotFileParser
from threading import Lock
ROOT=Path(__file__).resolve().parent
CFG=json.loads((ROOT/'policy_sources.json').read_text())
ARCH=ROOT/'data/policy_archive.json'
TODAY=dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).date().isoformat()
STAMP=dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')
AGENT='ResilienceResearchHub'
ROBOTS={}; LOCK=Lock()
def official(url):
    p=urlparse(url);h=p.hostname or ''
    return p.scheme=='https' and (h.endswith('.gov.cn') or h.endswith('.npc.gov.cn')) and not p.username and not p.password
def raw(url):
    if not official(url): raise ValueError('not an approved government HTTPS domain')
    with urlopen(Request(url,headers={'User-Agent':AGENT+'/1.2 (+https://github.com/Gang-TJU/resilience-research-hub)'}),timeout=9) as r:
        if not official(r.geturl()): raise ValueError('redirect outside official domain')
        b=r.read(2500001)
        if len(b)>2500000: raise ValueError('response exceeds limit')
        enc=r.headers.get_content_charset()
        if not enc:
            m=re.search(br'charset\s*=\s*["\x27]?\s*([a-zA-Z0-9_-]+)',b[:5000],re.I)
            enc=m.group(1).decode() if m else 'utf-8'
        return b.decode(enc,errors='replace')
def get(url):
    p=urlparse(url);origin=p.scheme+'://'+p.netloc
    with LOCK: rp=ROBOTS.get(origin)
    if rp is None:
        rp=RobotFileParser()
        try: rp.parse(raw(origin+'/robots.txt').splitlines())
        except HTTPError as e:
            if e.code!=404: raise
            rp.parse([])
        with LOCK: ROBOTS[origin]=rp
    if not rp.can_fetch(AGENT,url): raise PermissionError('robots.txt restricted')
    return raw(url)
def norm(s): return re.sub(r'\s+','',html.unescape(s or '')).strip()
class Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True);self.links=[];self.anchor=None;self.at=[];self.blocks=[];self.part=[];self.depth=0;self.skip=0;self.meta={}
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if tag in ('script','style','noscript'): self.skip+=1
        if self.skip:return
        if tag=='a':
            self.anchor=a.get('href');self.at=[]
            if a.get('title'):self.at.append(a['title'])
        if tag=='meta' and a.get('name'):self.meta[a['name'].lower()]=a.get('content','')
        if tag in ('p','h1','h2','h3','td','li','div','br'): self.flush()
    def flush(self):
        x=norm(''.join(self.part));self.part=[]
        if x:self.blocks.append(x)
    def handle_data(self,data):
        if self.skip:return
        self.part.append(data)
        if self.anchor:self.at.append(data)
    def handle_endtag(self,tag):
        if tag in ('script','style','noscript'):self.skip=max(0,self.skip-1);return
        if self.skip:return
        if tag=='a' and self.anchor:
            t=norm(''.join(self.at));self.links.append((self.anchor,t));self.anchor=None
        if tag in ('p','h1','h2','h3','td','li','div'): self.flush()
def parse(url):
    p=Page();p.feed(get(url));p.flush();return p
def relevant(t):return any(k in t for k in CFG['keywords']) and any(k in t for k in CFG['policy_terms']) and not any(k in t for k in CFG['exclude'])
def identifier(url):return hashlib.sha256(url.encode()).hexdigest()[:24]
def link(base,href):
    if href.startswith('http://'):href='https://'+href[7:]
    return urljoin(base,href).split('#')[0]
def department(t):
    for d,patterns in [('发展改革',('发展改革','发改委')),('水利',('水利厅','水务局','水利局','水务厅','水利部')),('生态环境',('生态环境厅','生态环境局','生态环境部')),('自然资源',('自然资源厅','自然资源局','规划和自然资源','自然资源部')),('应急管理',('应急管理厅','应急管理局','应急管理部')),('农业农村',('农业农村厅','农业农村局','农牧厅','农业农村部'))]:
        if any(k in t for k in patterns):return d
    return None
def listing(src):
    try:
        p=parse(src['url']);rows=[];nexts=[];seen=set();host=urlparse(src['url']).hostname
        for href,t in p.links:
            u=link(src['url'],href)
            if not official(u) or u in seen:continue
            seen.add(u);same=urlparse(u).hostname==host
            if 8<=len(t)<=220 and relevant(t) and not t.endswith('...') and not t.endswith('…'):
                if re.search(r'\.(?:html?|shtml|jhtml)$|/20\d{6,}/|/art/',urlparse(u).path,re.I):
                    rows.append({'id':identifier(u),'title':t,'url':u,'region':src['region'],'source':src['name'],'department':src['department'],'issuer':None,'published':None,'kind':'待核实政策线索','first_seen':TODAY,'verification':'目录标题；发文机关、日期、正文待核实'})
            elif len(t)<=24 and (any(k==t or k in t for k in CFG['directory_terms']) and same or src['region']!='全国' and department(t) and ('厅' in t or '局' in t or '委' in t)):
                d=department(t) or src['department']
                if u!=src['url']:nexts.append({'name':src['region']+d+' '+t,'url':u,'region':src['region'],'department':d})
        return rows[:80],nexts[:12],{'source':src['name'],'url':src['url'],'region':src['region'],'department':src['department'],'status':'ok' if rows else 'warning','matched':len(rows),'note':'' if rows else '未匹配政策条目；可能需要专用解析或目录发现'}
    except Exception as e:return [],[],{'source':src['name'],'url':src['url'],'region':src['region'],'department':src['department'],'status':'failed','matched':0,'note':str(e)[:180]}
def dateval(s):
    m=re.search(r'(20\d{2}|19\d{2})[年./-](\d{1,2})[月./-](\d{1,2})',s or '')
    if not m:return None
    try:return dt.date(*map(int,m.groups())).isoformat()
    except ValueError:return None
def clauses(p):
    # A legal text is eligible only if numbered articles are consecutive from 1 and an ending enactment marker is present.
    blocks=p.blocks
    marks=[i for i,b in enumerate(blocks) if re.match(r'^第一条(?:\s|[\u4e00-\u9fff])',b)]
    if not marks:return None
    i=marks[0];chunks=[];current=None
    for b in blocks[i:]:
        if re.match(r'^第[一二三四五六七八九十百零〇两]+章',b):continue
        m=re.match(r'^(第[一二三四五六七八九十百零〇两]+条)(.*)$',b)
        if m:
            if current:chunks.append(current)
            current={'article':m[1],'text':m[2]}
        elif current:
            if re.search(r'版权所有|网站地图|主办单位|ICP备|责任编辑|返回顶部|打印本页|关闭窗口|网站标识码',b):break
            current['text']+=b
    if current:chunks.append(current)
    if len(chunks)<5 or not re.search(r'本(?:法|条例|办法|规定)自.*施行',chunks[-1]['text']):return None
    def cn(s):
        digits='零一二三四五六七八九';n=0;v=0
        for c in s:
            if c in digits:v=digits.index(c)
            elif c=='两':v=2
            elif c in '十百':n+=(v or 1)*{'十':10,'百':100}[c];v=0
            else:return -1
        return n+v
    if [cn(c['article'][1:-1]) for c in chunks]!=list(range(1,len(chunks)+1)):return None
    return chunks
def enrich(x):
    y=dict(x)
    try:
        p=parse(x['url']);m=p.meta;joined='\n'.join(p.blocks)
        for k in ('pubdate','publishdate','publishedtime','date'):
            if not y.get('published') and dateval(m.get(k)):y['published']=dateval(m[k]);y['date_note']='网页发布元数据，不等于成文日期'
        for b in p.blocks:
            if not y.get('docno'):
                q=re.search(r'([\u4e00-\u9fff]{1,16}[〔\[]20\d{2}[〕\]]\d+号|主席令第[一二三四五六七八九十百零〇\d]+号)',b)
                if q:y['docno']=q[1]
            if not y.get('issuer'):
                q=re.match(r'^(?:发布机构|发文机关|发布机关|制定机关)[:：](.{2,65})$',b)
                if q:y['issuer']=q[1]
            if not y.get('published'):
                q=re.match(r'^(?:发布日期|发文日期|公布日期)[:：](.*)',b)
                if q:y['published']=dateval(q[1])
        if not y.get('issuer'):
            v=m.get('publishorg') or m.get('author')
            if v and ('人民政府' in v or '委员会' in v or '部' in v or '厅' in v or '局' in v):y['issuer']=norm(v);y['issuer_note']='网页元数据，待核验是否为原发文机关'
        a=clauses(p)
        if a:
            y['articles']=a;y['text_hash']=hashlib.sha256(json.dumps(a,ensure_ascii=False).encode()).hexdigest();y['text_status']='编号连续且检测到末条施行标记；仍需人工复核全文'
        else:y['text_status']='未获得满足完整性规则的条款正文；不可自动判定增删'
        if not y.get('verification','').startswith('人工'):y['verification']='官方网页已读取；元数据及效力仍需核实'
        y['checked_at']=STAMP;y.pop('error',None)
    except Exception as e:y['checked_at']=STAMP;y['error']=str(e)[:160]
    return y
def family(t):
    v=re.findall(r'《([^》]+)》',t)
    if len(v)==1:t=v[0]
    return re.sub(r'[（(](?:20\d{2}年)?(?:修订|修正|试行|征求意见稿|草案|20\d{2}年版)[）)]','',norm(t))
def diff(a,b):
    if not a.get('articles') or not b.get('articles'):return []
    old=a['articles'];new=b['articles']
    sm=difflib.SequenceMatcher(None,[z['text'] for z in old],[z['text'] for z in new],autojunk=False)
    result=[]
    for tag,i,j,k,l in sm.get_opcodes():
        if tag=='equal':continue
        result.append({'operation':{'insert':'新增候选','delete':'删除候选','replace':'修改或重排候选'}[tag],'old':old[i:j],'new':new[k:l]})
    return result
def comparisons(records):
    out=[];byid={r['id']:r for r in records}
    for b in records:
        if b.get('previous') in byid:
            a=byid[b['previous']];out.append({'old_id':a['id'],'new_id':b['id'],'relation':'已核实修订关系','basis':'官方版本沿革及明确的公布、施行日期','changes':diff(a,b),'chapter_old':a.get('chapters',[]),'chapter_new':b.get('chapters',[])})
    # Same named instrument and same verified issuing body are candidates, never confirmed replacements.
    groups={}
    for x in records:
        if x.get('issuer') and x.get('published') and not x.get('issuer_note') and not x.get('family'):
            groups.setdefault((x['region'],x['issuer'],family(x['title'])),[]).append(x)
    for rows in groups.values():
        rows=sorted(rows,key=lambda x:x['published'])
        for a,b in zip(rows,rows[1:]):
            if a['published']==b['published'] or a.get('docno') and a.get('docno')==b.get('docno'):continue
            out.append({'old_id':a['id'],'new_id':b['id'],'relation':'同机关同名政策候选','basis':'标题与发文机关相同、日期不同；未证实替代关系','changes':diff(a,b)})
    return out
def save(path,d):
    path.parent.mkdir(exist_ok=True,parents=True);tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(d,ensure_ascii=False,indent=2),encoding='utf-8');tmp.replace(path)
def parallel(fn,rows):
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as ex:return list(ex.map(fn,rows))
def main():
    old=json.loads(ARCH.read_text()) if ARCH.exists() else {'records':[],'runs':[],'discovered':[]}
    records={x['id']:x for x in old['records']}
    for s in CFG['seeds']:
        if s['id'] not in records:records[s['id']]={**s,'first_seen':TODAY}
    sources=CFG['central']+CFG['provinces']+CFG['extra']
    found={s['url']:s for s in old.get('discovered',[])}
    stats=[];newids=[]
    def ingest(results):
        for rows,nexts,st in results:
            stats.append(st)
            for s in nexts:
                if s['url'] not in {v['url'] for v in sources}:found[s['url']]=s
            for x in rows:
                if x['url'] in {z['url'] for z in CFG['seeds']}:continue
                if x['id'] not in records:records[x['id']]=x;newids.append(x['id'])
    ingest(parallel(listing,sources))
    # Bound the additional source budget; rotate by least recently checked so all discovered sources can be revisited.
    last={s['url']:s.get('last_checked','') for s in old.get('discovered',[])}
    selected=sorted(found.values(),key=lambda s:(last.get(s['url'],''),s['url']))[:42]
    ingest(parallel(listing,selected))
    for s in selected:found[s['url']]['last_checked']=STAMP
    todo=[records[s['id']] for s in CFG['seeds']]
    todo+=sorted([x for x in records.values() if x['id'] not in {s['id'] for s in CFG['seeds']} and (not x.get('checked_at') or x.get('error'))],key=lambda x:(x.get('checked_at',''),x['first_seen']))[:32]
    for x in parallel(enrich,todo):
        prior=records[x['id']]
        if prior.get('text_hash') and x.get('text_hash') and prior['text_hash']!=x['text_hash']:
            snap={**prior,'id':prior['id']+'-'+prior['text_hash'][:12],'verification':'同URL历史文本快照'}
            records[snap['id']]=snap;x['previous_snapshot']=snap['id']
        records[x['id']]=x
    rows=sorted(records.values(),key=lambda x:(x.get('published') or '',x['first_seen'],x['id']),reverse=True)
    for x in rows:
        if x.get('effective') and x['effective']>TODAY:x['status_label']='已公布，尚未施行'
        elif x.get('superseded_on') and x['superseded_on']<=TODAY:x['status_label']='历史版本（按已核实修订安排）'
        elif x.get('family') and x.get('effective'):x['status_label']='按已核实施行日期已施行；请以最新官方效力记录为准'
        else:x['status_label']='效力状态待核实'
    pairs=comparisons(rows)
    for x in rows:
        if x.get('previous_snapshot') in records:
            a=records[x['previous_snapshot']];pairs.append({'old_id':a['id'],'new_id':x['id'],'relation':'同URL文本变化（非法律效力结论）','basis':'同一官方URL两次正文哈希不同','changes':diff(a,x)})
    run={'checked_at':STAMP,'date':TODAY,'new':len(newids),'sources':stats}
    d={'records':rows,'comparisons':pairs,'latest':run,'runs':(old.get('runs',[])+[run])[-60:],'discovered':list(found.values()),'regions':[s['region'] for s in CFG['provinces']],'departments':CFG['departments'],'coverage_note':'省级门户及中央目录逐轮检查；额外部门目录按预算轮换，未检出不代表无政策。列表线索不等同正式文件；条款差异为文本候选而非法定效力判断。'}
    save(ARCH,d);save(ROOT/'site/policies.json',d)
if __name__=='__main__':main()

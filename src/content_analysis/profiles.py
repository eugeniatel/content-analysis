"""Composable profile tools over the existing reference/revision store."""
import copy,csv,io,json,math,re,subprocess,uuid
from datetime import datetime,timezone
from pathlib import Path
from .store import canonical,channel,digest,now,file_hash,CONFIG
from . import statool

KINDS={'inventory','metrics','profile-run'}
PRIVATE_METRICS={'reach','saves','avg_watch_time','completion_rate','retention_rate','profile_visits','link_clicks'}

def timestamp(value):
    if not isinstance(value,str):raise ValueError('An ISO capture timestamp with timezone is required')
    try:d=datetime.fromisoformat(value.replace('Z','+00:00'))
    except ValueError:raise ValueError('Invalid ISO timestamp') from None
    if d.tzinfo is None:raise ValueError('Timestamp must include timezone')
    if d>datetime.now(timezone.utc):raise ValueError('Future observation timestamps are not accepted')
    return d.astimezone(timezone.utc).isoformat()

def entity(store,kind,title):
    r,_=store.create('urn:content-analysis:'+kind+':'+uuid.uuid4().hex,title)
    r.update(kind=kind,retrieval={'status':'text_only','attempts':[],'limitation':'Structured backend result; not a downloaded social post'},semantic_status='pending')
    return r

def get_kind(store,rid,kind):
    r=store.get(rid)
    if r['kind']!=kind:raise ValueError('Expected '+kind+' reference')
    return r

def profile_identity(url):
    _,_,profiles=statool.load();identity=profiles.profile_identity(url)
    if identity['platform'] not in {'instagram','tiktok','x','youtube','linkedin'}:raise ValueError('Unsupported profile platform')
    # Statool accepts post URLs too; this boundary requires a profile URL.
    if re.search(r'/(?:p|reel|reels|status|video|photo|watch|shorts|feed|posts)(?:/|\?|$)',url):raise ValueError('Provide a profile URL, not an individual post')
    return identity

def inventory_import(store,profile_url,items,source,scope='selected_references',captured_at=None,requested_count=None,expected_total=None,exhausted=False,limitations=None):
    identity=profile_identity(profile_url);stamp=timestamp(captured_at or now())
    if not isinstance(source,str) or not source.strip():raise ValueError('Inventory requires source provenance')
    if not isinstance(items,list) or len(items)>1000:raise ValueError('Inventory must be a bounded list of at most 1000 items')
    if expected_total is not None and (type(expected_total) is not int or expected_total<0):raise ValueError('Invalid expected total')
    normalized=[];seen=set()
    for position,item in enumerate(items):
        if not isinstance(item,dict):raise ValueError('Each inventory item must be an object')
        r=store.get(item['reference_id']) if item.get('reference_id') else None
        url=item.get('source_url') or (r['canonical'] if r else None)
        if not isinstance(url,str) or not url.startswith('https://'):raise ValueError('Each member needs a public HTTPS source URL or existing reference ID')
        key=canonical(url)
        if channel(key)!=identity['platform']:raise ValueError('Inventory members must match the profile platform')
        if key in seen:continue
        seen.add(key)
        published=item.get('published_at')
        if published is not None:published=timestamp(published)
        normalized.append((item,r,key,published,position))
    members=[]
    for item,r,key,published,position in normalized:
        if r is None:r,_=store.create(key)
        members.append({'reference_id':r['id'],'reference_revision':r['revision'],'canonical_url':r['canonical'],'published_at':published,'media_type':item.get('media_type') or ('video' if r['kind']=='audiovisual' else 'image' if r['kind']=='visual' else 'unknown'),'inventory_position':position,'attribution':item.get('attribution','importer supplied profile membership'),'snapshot':copy.deepcopy(r)})
    complete=bool(exhausted is True and expected_total is not None and len(members)==expected_total)
    coverage={'collected':len(members),'requested_count':requested_count,'expected_total':expected_total,'collection_complete':complete,'scope':scope,'limitations':list(limitations or []),'media_retrieved':sum(any(a['role']=='media' for a in m['snapshot']['assets']) for m in members)}
    if not complete:coverage['limitations'].append('Bounded or selected inventory; not established as the full profile')
    r=entity(store,'inventory',identity['handle']+' — inventory')
    r.update(profile=identity,members=members,inventory_coverage=coverage,provenance={'source':source,'captured_at':stamp,'scope':scope},evidence=[{'id':'member-'+m['reference_id'],'kind':'profile_member','reference_id':m['reference_id'],'reference_revision':m['reference_revision'],'source':m['canonical_url'],'position':m['inventory_position'],'note':m['attribution']} for m in members])
    return store.save(r)

def public_profile_inventory(identity,limit):
    from .retrieve import public_url,RetrievalError
    public_url(identity['canonical_url'])
    cmd=[CONFIG['python'],'-m','yt_dlp','--flat-playlist','--skip-download','--dump-single-json','--playlist-end',str(limit),'--socket-timeout','15','--retries','0','--no-warnings','--no-progress',identity['canonical_url']]
    try:p=subprocess.run(cmd,capture_output=True,text=True,timeout=90)
    except subprocess.TimeoutExpired:raise RetrievalError('Public profile metadata retrieval timed out') from None
    if p.returncode:raise RetrievalError('Public profile inventory unavailable through yt-dlp without authentication; no cookies used')
    data=json.loads(p.stdout)
    if canonical(data.get('webpage_url',''))!=canonical(identity['canonical_url']):raise RetrievalError('Public profile response identity could not be established')
    items=[]
    for row in (data.get('entries') or [])[:limit]:
        url=row.get('webpage_url') or row.get('url')
        if not isinstance(url,str) or channel(url)!=identity['platform'] or not re.search(r'/(?:p|reel)/[\w-]+',url):continue
        published=datetime.fromtimestamp(row['timestamp'],timezone.utc).isoformat() if isinstance(row.get('timestamp'),(float,int)) else None
        items.append({'source_url':url,'published_at':published,'media_type':'video','attribution':'yt-dlp metadata linked from requested public profile'})
    if not items:raise RetrievalError('Public profile response contained no source-bound individual posts')
    return items

def inventory_collect(store,profile_url,limit=50,allow_keychain_prompt=True):
    """One bounded read via Soapbox's existing authorized Meta discovery route."""
    identity=profile_identity(profile_url)
    if identity['platform']!='instagram':raise ValueError('Native profile collection currently supports Instagram; import a dated inventory for other platforms')
    if type(limit) is not int or not 1<=limit<=50:raise ValueError('Collection limit must be 1–50')
    from .retrieve import meta_discovery,RetrievalError
    try:posts,total,exhausted=meta_discovery(identity['handle'],limit=limit,interactive_auth=allow_keychain_prompt)
    except Exception as error:
        reason=str(error) if isinstance(error,RetrievalError) else type(error).__name__
        try:
            items=public_profile_inventory(identity,limit)
            r=inventory_import(store,profile_url,items,source='yt-dlp public profile metadata',scope='bounded_public_profile',requested_count=limit,limitations=[reason,'Public metadata may omit profile posts and media formats; full collection is not established'])
            r['collection_status']='retrieved';return store.save(r)
        except Exception as fallback:
            fallback_reason=str(fallback) if isinstance(fallback,RetrievalError) else type(fallback).__name__
            r=inventory_import(store,profile_url,[],source='Soapbox Meta discovery and public yt-dlp profile fallback',scope='bounded_api',requested_count=limit,limitations=[reason,fallback_reason])
            r['collection_status']='blocked';return store.save(r)
    items=[]
    for p in posts:
        if p.get('permalink'):items.append({'source_url':p['permalink'],'published_at':p.get('timestamp'),'media_type':p.get('media_type'),'attribution':'Meta business_discovery response for '+identity['handle']})
    r=inventory_import(store,profile_url,items,source='Meta Graph business_discovery via established Soapbox credential',scope='bounded_api',requested_count=limit,expected_total=total,exhausted=exhausted)
    # Captions are retained as caption-only evidence without replacing richer text.
    from .service import capture
    by_source={m['canonical_url']:m for m in r['members']}
    for p in posts:
        if not p.get('permalink'):continue
        m=by_source[canonical(p['permalink'])]
        existing=store.get(m['reference_id'])
        if p.get('caption') and not any(a['role']=='text' for a in existing['assets']):
            saved=capture(store,p['permalink'],{'url':p['permalink'],'scope':'caption','text':p['caption'],'author':identity['handle'],'limitation':'Caption only from bounded profile inventory; attached media not downloaded'},method='authorized Meta Graph profile inventory')
            m['snapshot']=saved;m['reference_revision']=saved['revision']
    revisions={m['reference_id']:m['reference_revision'] for m in r['members']}
    for event in r['evidence']:event['reference_revision']=revisions[event['reference_id']]
    r['collection_status']='retrieved';return store.save(r)

def normalize_metric(row,members,source,access,captured_at):
    if not isinstance(row,dict):raise ValueError('Metric rows must be objects')
    rid=row.get('reference_id')
    if rid is None and row.get('source_url'):rid=members.get(canonical(row['source_url']))
    if rid not in set(members.values()):raise ValueError('Metric does not resolve to an inventoried reference')
    name=str(row.get('metric_name','')).strip().lower();basis=str(row.get('metric_basis','')).strip().lower();unit=str(row.get('metric_unit','')).strip().lower()
    if not name or not basis or not unit:raise ValueError('Metric name, basis and unit are required; they cannot be inferred from a column label')
    if access=='public' and name in PRIVATE_METRICS:raise ValueError('Private metric requires an owned export or established authorized API')
    value=row.get('value',row.get('metric_value'))
    if isinstance(value,bool):raise ValueError('Boolean is not a metric value')
    try:value=float(value)
    except (ValueError,TypeError):raise ValueError('Metric value must be numeric') from None
    if not math.isfinite(value) or value<0:raise ValueError('Metric value must be finite and nonnegative')
    raw_value=value;raw_unit=unit
    if unit=='percent':value/=100;unit='ratio'
    if unit=='minutes':value*=60;unit='seconds'
    if unit not in {'count','ratio','seconds','currency'}:raise ValueError('Unsupported metric unit')
    if unit=='count' and value!=int(value):raise ValueError('Count metrics must be whole numbers')
    denominator=row.get('denominator') or row.get('denominator_name')
    if unit=='ratio' and not denominator:raise ValueError('Rates require an explicit denominator')
    if denominator is not None and not isinstance(denominator,str):raise ValueError('Denominator must be a name, not a numeric count')
    currency=row.get('currency')
    if unit=='currency' and not currency:raise ValueError('Currency metrics require a currency code')
    # Upstream Statool signatures omit denominator; encode it into the comparison basis.
    signature=basis+'|denominator='+str(denominator or 'none').lower()+'|currency='+str(currency or 'none').lower()
    stamp=timestamp(row.get('captured_at') or captured_at)
    result={'reference_id':rid,'piece_id':rid,'metric_name':name,'metric_value':value,'metric_unit':unit,'metric_basis':signature,'original_basis':basis,'denominator_name':denominator,'currency':currency,'raw_value':raw_value,'raw_unit':raw_unit,'source':source,'access':access,'captured_at':stamp}
    result['observation_id']='metric-'+digest(json.dumps(result,sort_keys=True))[:20]
    return result

def metrics_import(store,inventory_id,rows=None,source=None,access='public',captured_at=None,path=None,columns=None,metric_specs=None,supersedes=None,reason=None):
    inventory=get_kind(store,inventory_id,'inventory');file_sha=None
    if access not in {'public','owned_export','authorized_api'}:raise ValueError('Specify public, owned_export or authorized_api metric access')
    if not isinstance(source,str) or not source.strip():raise ValueError('Metrics require explicit source provenance')
    stamp=timestamp(captured_at or now())
    if path:
        p=Path(path)
        if p.suffix.lower() not in {'.csv','.json'} or p.stat().st_size>20_000_000:raise ValueError('Metric import requires CSV or JSON up to 20 MB')
        file_sha=file_hash(p)
        if p.suffix.lower()=='.csv':
            raw=list(csv.DictReader(io.StringIO(p.read_text(encoding='utf-8-sig'))))
            if not columns:raise ValueError('CSV import requires explicit canonical-field to column mapping')
            if metric_specs:
                if not isinstance(metric_specs,list) or len(metric_specs)>100:raise ValueError('metric_specs must be a bounded list')
                rows=[]
                for row in raw:
                    identity={field:row.get(column) for field,column in columns.items()}
                    for spec in metric_specs:
                        if not isinstance(spec,dict) or not spec.get('column') or any(k not in {'column','metric_name','metric_basis','metric_unit','denominator','currency'} for k in spec):raise ValueError('Invalid metric column specification')
                        if spec['column'] not in row:raise ValueError('Specified metric column is absent from the export')
                        value=row[spec['column']]
                        if value is None or not value.strip():continue
                        rows.append({**identity,**{k:v for k,v in spec.items() if k!='column'},'value':value})
            else:rows=[{field:row.get(column) for field,column in columns.items()} for row in raw]
        else:rows=json.loads(p.read_text())
    if not isinstance(rows,list) or len(rows)>100000:raise ValueError('Metric rows must be a bounded list')
    members={m['canonical_url']:m['reference_id'] for m in inventory['members']}
    values=[normalize_metric(row,members,source,access,stamp) for row in rows]
    values=list({v['observation_id']:v for v in values}.values())
    published={m['reference_id']:m.get('published_at') for m in inventory['members']}
    if any(published[v['reference_id']] and v['captured_at']<published[v['reference_id']] for v in values):raise ValueError('Metric capture precedes the publication time')
    if supersedes:
        old=get_kind(store,supersedes,'metrics')
        if old['profile']['id']!=inventory['profile']['id'] or not reason:raise ValueError('Metric correction requires same profile and a reason')
    r=entity(store,'metrics',inventory['profile']['handle']+' — metric snapshot')
    r.update(profile=inventory['profile'],inventory_id=inventory_id,metrics=values,provenance={'source':source,'access':access,'captured_at':stamp,'import_file_sha256':file_sha,'supersedes':supersedes,'reason':reason},evidence=[{'id':v['observation_id'],'kind':'metric','reference_id':v['reference_id'],'measurement':v,'note':'Imported observation, not proof of account ownership or causal effect'} for v in values])
    return store.save(r)

def run_profile(store,inventory_id,objective,metric_snapshot_ids=None,top_k=3,minimum_sample=5,as_of=None,bootstrap_iterations=1000,seed=0,metric_basis=None,metric_unit=None):
    from .features import extract_features,aggregate_captions
    inventory=get_kind(store,inventory_id,'inventory');analytics,_,_=statool.load()
    if not isinstance(objective,str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,63}',objective):raise ValueError('Choose one named objective')
    if type(top_k) is not int or not 1<=top_k<=1000:raise ValueError('top_k must be 1–1000')
    if type(minimum_sample) is not int or minimum_sample<5:raise ValueError('minimum_sample must be at least 5')
    if type(bootstrap_iterations) is not int or not 100<=bootstrap_iterations<=10000:raise ValueError('bootstrap_iterations must be 100–10000')
    cutoff=timestamp(as_of or now());snapshots=[get_kind(store,sid,'metrics') for sid in metric_snapshot_ids or []]
    if any(s['profile']['id']!=inventory['profile']['id'] for s in snapshots):raise ValueError('Metric snapshots must belong to the selected profile')
    superseded={s['provenance'].get('supersedes') for s in snapshots if s['provenance']['captured_at']<=cutoff}
    observations=[]
    for snapshot in snapshots:
        if snapshot['id'] in superseded:continue
        for row in snapshot['metrics']:
            if row['captured_at']<=cutoff:observations.append({**row,'snapshot_id':snapshot['id']})
    pieces=[];flat={};features={};evidence=[]
    for member in inventory['members']:
        # Use current evidence but pin its exact revision into this immutable analysis run.
        r=store.get(member['reference_id']);f=extract_features(store,r['id'],persist=False)
        pieces.append({'id':r['id'],'piece_id':r['id'],'profile_id':inventory['profile']['id'],'platform':inventory['profile']['platform'],'media_type':member['media_type'],'published_at':member['published_at']})
        flat[r['id']]=f['flat'];features[r['id']]=f
        for e in r['evidence']:
            evidence.append({**copy.deepcopy(e),'id':r['id']+':'+e['id'],'reference_id':r['id'],'reference_revision':r['revision'],'source_evidence_id':e['id']})
    allowed={p['id'] for p in pieces};observations=[o for o in observations if o['piece_id'] in allowed]
    result=analytics.analyze_profile_winners(pieces,observations,objective=objective,features=flat,top_k=top_k,minimum_sample=minimum_sample,metric_basis=metric_basis,metric_unit=metric_unit,bootstrap_iterations=bootstrap_iterations,seed=seed)
    for o in observations:evidence.append({'id':o['observation_id'],'kind':'metric','reference_id':o['reference_id'],'snapshot_id':o['snapshot_id'],'measurement':o})
    coverage=inventory['inventory_coverage'];eligible=result['coverage']['eligible_pieces'];fraction=result['coverage']['objective_coverage']
    findings_allowed=eligible>=minimum_sample*2 and fraction>=.8 and coverage['collection_complete'] and len(result['cohorts']['winner_piece_ids'])>=minimum_sample and result['cohorts']['non_winner_count']>=minimum_sample
    eligible_ids={row['piece_id'] for row in result['ranked']}
    feature_coverage={field:sum(flat[rid].get(field) is not None for rid in eligible_ids)/eligible if eligible else 0 for field in {*analytics.DEFAULT_CATEGORICAL_FIELDS,*analytics.DEFAULT_CONTINUOUS_FIELDS} if field!='format'}
    feature_coverage['format']=sum(p['media_type']!='unknown' for p in pieces if p['id'] in eligible_ids)/eligible if eligible else 0
    for finding in result['findings']:
        field=finding['feature'].split('=',1)[0].split(':',1)[0]
        observed=feature_coverage.get(field,0)
        finding['feature_coverage']=observed
        if not findings_allowed or observed<.8 or finding['dimension']=='categorical' and observed<1 or field=='format' and any(p['media_type']=='unknown' for p in pieces):
            finding.update(status='insufficient_evidence',confidence='insufficient',claim='Descriptive association only; collection or comparable metric coverage is insufficient for a profile-wide conclusion.')
        if finding.get('control_match') and any(not p['published_at'] for p in pieces):
            finding['publication_age_limit']='Publication age is not established for all pieces; unknown age strata do not prove age matching'
            finding['statool_control_match']=finding['control_match']
            finding['control_match']=finding['control_match'].replace('publication_age','age_unverified')
            finding['claim']=finding['claim'].replace('publication_age','age_unverified')
            finding['comparison']=finding['comparison'].replace('publication_age','age_unverified')
    insufficient=sum(f['status']=='insufficient_evidence' for f in result['findings'])
    result['finding_summary']={'total':len(result['findings']),'supported':len(result['findings'])-insufficient,'insufficient_evidence':insufficient}
    result['coverage']['feature_coverage']=feature_coverage
    lexical=aggregate_captions(features,result['cohorts']['winner_piece_ids'])
    lexical['profile_pattern_evidence']='descriptive_only' if not findings_allowed or lexical['document_count']<minimum_sample*2 else 'measured_counts_in_declared_corpus'
    r=entity(store,'profile-run',inventory['profile']['handle']+' — '+objective+' analysis')
    r.update(profile=inventory['profile'],inventory_id=inventory_id,metric_snapshot_ids=[s['id'] for s in snapshots],analytics=result,lexical=lexical,features=features,inventory_coverage=coverage,evidence=list({e['id']:e for e in evidence}.values()),provenance={'computed_at':now(),'as_of':cutoff,'dependency':statool.provenance(),'input_revisions':{rid:f['reference_revision'] for rid,f in features.items()},'metric_revisions':{s['id']:s['revision'] for s in snapshots}},analysis_limits={'profile_conclusions_supported':findings_allowed,'association_only':True,'semantic_interpretation':'ChatGPT; not automatically generated','minimum_eligible_for_profile_conclusion':minimum_sample*2,'minimum_objective_coverage':.8})
    return store.save(r)

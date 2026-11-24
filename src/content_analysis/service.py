import json, mimetypes, re, shutil
from pathlib import Path
from .store import Store, atomic_json, now, digest, file_hash, canonical
from .retrieve import retrieve, asset, Document
from .written import analyze as analyze_written

LABELS={'observed','measured','estimated','inferred','adaptation','unknown'}

def verify_media(store,rid):
    from .verification import verify
    with store.lock(rid):
        r=store.get(rid);r['media_validation']=verify(r)
        r['retrieval']['fresh_full_media_verified']=r['media_validation']['fresh_full_media_verified']
        return store.save(r)

def capture(store,source,snapshot,method='browser visible source read'):
    """Persist an explicit browser read without upgrading metadata into media access."""
    if not isinstance(snapshot.get('url'),str) or not snapshot['url'].startswith(('https://','http://')):
        raise ValueError('Browser capture requires its actual source URL')
    if canonical(snapshot.get('url','')) != canonical(source):
        raise ValueError('Browser capture URL does not match this reference')
    scope=snapshot.get('scope')
    if scope not in {'post','article','caption','description_excerpt','player_metadata','transcript'}:
        raise ValueError('Specify the actual browser capture scope')
    text=snapshot.get('text') or ''
    if not isinstance(text,str) or len(text)>2_000_000:
        raise ValueError('Invalid captured text')
    if scope!='player_metadata' and not text.strip():
        raise ValueError('This capture scope requires source text')
    r,_=store.create(source,snapshot.get('title'))
    with store.lock(r['id']):
        r=store.get(r['id']);folder=store.folder(r['id'])
        entry={k:snapshot[k] for k in ('url','scope','title','author','duration_seconds','limitation') if k in snapshot}
        if method not in {'browser visible source read','authorized Meta Graph profile inventory'}:raise ValueError('Unsupported capture provenance method')
        entry.update(recorded_at=now(),method=method,text_sha256=digest(text) if text else None)
        if text:
            write_text(r,folder,text,entry)
        if scope=='transcript':
            captions=[]
            for line in text.splitlines():
                match=re.match(r'^\[(\d+:\d{2}(?::\d{2})?)\]\s*(.+)',line)
                if match:
                    seconds=0
                    for part in match[1].split(':'):seconds=seconds*60+int(part)
                    captions.append({'id':'caption-'+digest(match[1]+'|'+match[2])[:12],'kind':'caption','start':seconds,'end':seconds+5,'text':match[2],'note':'Publisher auto-generated captions; not verified listening or audiovisual inspection.'})
            for current,following in zip(captions,captions[1:]):current['end']=following['start']
            if snapshot.get('duration_seconds') and captions:
                captions[-1]['end']=snapshot['duration_seconds']
                r.setdefault('meta',{}).update(duration_s=snapshot['duration_seconds'],provenance='Browser source-player metadata; media not downloaded')
            r['evidence']=[e for e in r['evidence'] if e['kind']!='caption']+captions
            r['coverage']['caption_spans']=[[captions[0]['start'],captions[-1]['end']]] if captions else []
        if snapshot.get('title'):r['title']=snapshot['title']
        if snapshot.get('author'):r['author']=snapshot['author']
        attempt={'at':entry['recorded_at'],'method':('browser visible '+scope if method=='browser visible source read' else method+' '+scope),'ok':True}
        has_media=any(a['role']=='media' for a in r['assets'])
        limitation=snapshot.get('limitation') or ('Written text retained; attached media not downloaded' if scope in ('post','article') else 'Caption, excerpt or player metadata only; full media not downloaded')
        status='partial' if scope in ('description_excerpt','player_metadata','transcript') else 'text_only'
        if has_media:
            r['last_refresh']={'status':status,'attempts':[attempt],'limitation':limitation}
        else:
            r['retrieval']={'status':status,'attempts':r['retrieval'].get('attempts',[])+[attempt],'limitation':limitation}
        if snapshot.get('duration_seconds') is not None:r['intended_kind']='audiovisual'
        r.setdefault('browser_captures',[]).append(entry)
        return store.save(r)
def write_text(record,folder,text,provenance):
    if not text.strip():return
    previous=next((a['sha256'] for a in record['assets'] if a['role']=='text'),None)
    if previous and previous!=digest(text) and record['observations']:record['semantic_status']='needs_recheck'
    # Preserve exact source bytes referenced by older revisions.
    path=folder/('text-'+digest(text)+'.txt');path.write_text(text)
    record['assets']=[a for a in record['assets'] if a['role']!='text']+[asset(path,'text',provenance)]
    passages,metrics=analyze_written(text)
    record['evidence']=[e for e in record['evidence'] if e['kind']!='passage']+passages;record['written_metrics']=metrics
    record['coverage']['text']=[{'start_char':0,'end_char':len(text),'method':'automated segmentation; semantic inspection separate'}]
    if record['kind']=='unknown':record['kind']='written'

def ingest(store,source,title=None,local_media=None,text=None,handle=None,force=False,cached_at=None):
    record,duplicate=store.create(source,title)
    with store.lock(record['id']):
        record=store.get(record['id']);folder=store.folder(record['id'])
        downloaded_this_attempt=False
        if duplicate and not force and not local_media and text is None:return {**record,'deduplicated':True}
        if local_media:
            p=Path(local_media).expanduser().resolve()
            if not p.is_file():raise ValueError('Supplied local evidence file missing')
            target=folder/('source'+p.suffix.lower());shutil.copy2(p,target)
            record['assets']=[a for a in record['assets'] if a['role']!='media']+[asset(target,'media',{'method':'retained local source supplied for URL','original':str(p),'imported_at':now(),'source_retrieved_at':cached_at,'fresh_retrieval':False})]
            store.add_alias(str(p),record['id']);record['retrieval']={'status':'full_media','attempts':[{'at':now(),'method':'local evidence import','ok':True}],'limitation':'Retained source; current URL access and publication identity not independently reverified'}
        elif text is not None:
            record['retrieval']={'status':'full_media' if any(a['role']=='media' for a in record['assets']) else 'text_only','attempts':record['retrieval'].get('attempts',[])+[{'at':now(),'method':'supplied text','ok':True}],'limitation':'Supplied text supplements only the evidence actually retained; not proof of current media access'}
        else:
            retrieval,assets,body,new_title,author=retrieve(record,folder,handle)
            downloaded_this_attempt=any(a['role']=='media' for a in assets)
            # Keep prior successful evidence on failed refresh; log current failure separately.
            metadata_only=retrieval.get('limitation')=='Public metadata excerpt only; not the full post or media'
            if record['assets'] and (retrieval['status']=='blocked' or (metadata_only and not downloaded_this_attempt)):
                record['last_refresh']=retrieval
            else:
                record.pop('last_refresh',None)
                previous_text=[a for a in record['assets'] if a['role']=='text'] if not body else []
                record['retrieval']=retrieval;record['assets']=assets+previous_text
                if body:write_text(record,folder,body,{'method':'retrieval','retrieved_at':now(),'source':record['canonical']})
            if new_title and record['title'] in (record['source'],record['canonical']):record['title']=new_title
            if author:record['author']=author
        if title:record['title']=title
        if text is not None:write_text(record,folder,text,{'method':'supplied text','imported_at':now(),'source_retrieved_at':cached_at})
        if any(a['role']=='media' and Path(a['path']).suffix.lower() in ('.mp4','.mov','.mkv','.webm','.mp3','.m4a','.wav') for a in record['assets']):record['kind']='audiovisual'
        elif any(a['role']=='media' for a in record['assets']):record['kind']='visual'
        if downloaded_this_attempt:
            from .verification import verify
            record['media_validation']=verify(record)
            record['retrieval']['fresh_full_media_verified']=record['media_validation']['fresh_full_media_verified']
        return store.save(record)

def process(store,rid,full=False,speech=True):
    from .media import analyze
    with store.lock(rid):
        r=store.get(rid);media=next((a for a in r['assets'] if a['role']=='media' and Path(a['path']).suffix.lower() in ('.mp4','.mov','.mkv','.webm')),None)
        if not media:
            images=[a for a in r['assets'] if a['role']=='media' and Path(a['path']).suffix.lower() in ('.jpg','.jpeg','.png','.webp')]
            if images:
                from PIL import Image
                events=[]
                for index,a in enumerate(images):
                    if file_hash(a['path'])!=a['sha256']:raise ValueError('Source hash changed; re-ingest before analysis')
                    with Image.open(a['path']) as image:
                        image.load();width,height=image.size
                    events.append({'id':'image-'+str(index+1)+'-'+a['sha256'][:12],'kind':'image','position':index+1,'frame':a['path'],'measurement':{'width':width,'height':height},'note':'Decoded source image; visual interpretation requires inspection.'})
                previous=[e for e in r['evidence'] if e['kind']=='image']
                if previous==events:return r
                r['evidence']=[e for e in r['evidence'] if e['kind'] not in ('image','shot','speech','audio_signal')]+events
                r['coverage']['visual']=[];r['semantic_status']='needs_recheck' if r['observations'] else 'pending'
                return store.save(r)
            if r['kind']=='written':return r
            raise ValueError('No retrieved video. Caption or thumbnail does not qualify as audiovisual evidence.')
        if file_hash(media['path'])!=media['sha256']:raise ValueError('Source hash changed; re-ingest before analysis')
        identity=digest(media['sha256']+str(full)+str(speech))[:12];folder=store.folder(rid)/'analysis'/identity;folder.mkdir(parents=True,exist_ok=True)
        manifest=folder/'manifest.json'
        if manifest.exists():d=json.loads(manifest.read_text())
        else:d=analyze(media['path'],folder,full,speech);atomic_json(manifest,d)
        previous=[e for e in r['evidence'] if e['kind'] not in ('passage','caption')]
        if r.get('analysis_manifest')==str(manifest) and previous==d['evidence']:
            return r
        r['evidence']=[e for e in r['evidence'] if e['kind']=='passage']+d['evidence'];r['coverage']=d['coverage'];r['meta']=d['meta'];r['signal_reports']=d['reports'];r['analysis_manifest']=str(manifest);r['semantic_status']='pending'
        # Every prior semantic claim remains in history; changed measurements require a new review.
        if r['observations']:r['semantic_status']='needs_recheck'
        return store.save(r)

def annotate(store,rid,patch,expected=None):
    with store.lock(rid):
        r=store.get(rid);ids={e['id'] for e in r['evidence']}
        for ob in patch.get('observations',[]):
            if ob.get('label') not in LABELS or not isinstance(ob.get('text'),str) or not ob['text'].strip():raise ValueError('Each observation needs a label and nonempty text')
            if not ob.get('evidence_ids') or any(x not in ids for x in ob['evidence_ids']):raise ValueError('Observation must cite existing evidence IDs')
            ob.setdefault('id','o-'+digest(json.dumps(ob,sort_keys=True))[:10]);ob['at']=now()
        for t in patch.get('techniques',[]):
            if not t.get('name') or not t.get('steps') or not t.get('evidence_ids') or any(x not in ids for x in t['evidence_ids']):raise ValueError('Technique requires name, concrete steps and valid evidence IDs')
            t.setdefault('id','t-'+digest(json.dumps(t,sort_keys=True))[:10])
        for field in ('observations','techniques'):
            existing={x['id']:x for x in r[field]}
            for x in patch.get(field,[]):existing[x['id']]=x
            r[field]=list(existing.values())
        for field in ('title','author'):
            if field in patch:r[field]=patch[field]
        if 'coverage' in patch:
            for mode,items in patch['coverage'].items():
                if mode not in ('visual','audio','text_review'):raise ValueError('Only inspected modality coverage can be annotated')
                if not isinstance(items,list):raise ValueError('Coverage must be a list of actual inspected spans or frames')
                r['coverage'][mode]=items
            if patch['coverage'].get('text_review') and r['kind']=='written':r['coverage']['method']='Anchored written passages semantically inspected; source claims are not automatically fact-checked'
        if patch.get('semantic_status'):
            if patch['semantic_status'] not in ('pending','reviewed','partial','needs_recheck'):raise ValueError('Invalid semantic status')
            if patch['semantic_status']=='reviewed' and any(any(x not in ids for x in o['evidence_ids']) for o in r['observations']):raise ValueError('Stale observations need re-anchoring before review')
            if patch['semantic_status']=='reviewed' and not r['observations']:raise ValueError('Cannot mark empty analysis reviewed')
            if patch['semantic_status']=='reviewed' and r['kind']=='audiovisual' and not r['coverage'].get('visual'):raise ValueError('Video review requires actual visual inspection coverage')
            r['semantic_status']=patch['semantic_status']
        return store.save(r,expected)

def correct(store,rid,target,text,reason,expected=None):
    if not text.strip() or not reason.strip():raise ValueError('Correction text and reason must be nonempty')
    with store.lock(rid):
        r=store.get(rid);item=next((o for o in r['observations'] if o['id']==target),None)
        if not item:raise ValueError('Correction target must be an observation ID')
        old=dict(item);item['text']=text;item['corrected_at']=now()
        r['corrections'].append({'at':now(),'target':target,'before':old,'after':text,'reason':reason});return store.save(r,expected)

def context(store,rid,at=None,passage=None,query=None,window=3,revision=None):
    r=store.get_revision(rid,revision) if revision is not None else store.get(rid);evidence=r['evidence']
    if at is not None:
        duration=r.get('meta',{}).get('duration_s',0)
        if not 0<=at<=duration:raise ValueError('Timestamp outside source')
        evidence=[e for e in evidence if 'start' in e and e['end']>=at-window and e['start']<=at+window]
    if passage:evidence=[e for e in evidence if e['id']==passage]
    if query:evidence=[e for e in evidence if query.casefold() in json.dumps(e,ensure_ascii=False).casefold()]
    signals=[]
    if at is not None:
        for report in r.get('signal_reports',[]):
            if report['source_start']<=at+window and report['source_end']>=at-window:
                raw=json.loads(Path(report['path']).read_text());audio=raw.get('audio',{});offset=report['source_start']
                signals.append({'source_span':[report['source_start'],report['source_end']],'audio_present':audio.get('present'),'integrated_lufs':audio.get('integrated_lufs'),'beat_reliable':audio.get('beat_reliable'),'rms_dbfs':[dict(t=round(v['t']+offset,3),db=v['db']) for v in audio.get('rms_curve_dbfs',[]) if abs(v['t']+offset-at)<=window],'note':'Mixed-audio amplitude measurements; not source-separated music or listening.'})
    return {'id':rid,'revision':r['revision'],'source':r['canonical'],'title':r['title'],'retrieval':r['retrieval'],'coverage':r['coverage'],'evidence':evidence,'audio_signals':signals,'observations':r['observations'],'techniques':r['techniques'],'corrections':r['corrections'],'signal_reports':r.get('signal_reports',[]),'note':'This returns source evidence for ChatGPT to answer the question. It does not synthesize an answer or prove inspection outside retained coverage.'}

def link(store,a,b,note):
    if a==b:raise ValueError('Cannot link a reference to itself')
    store.get(b)
    for first,second in sorted(((a,b),(b,a))):
        with store.lock(first):
            r=store.get(first);r['related']=[x for x in r['related'] if x['id']!=second]+[{'id':second,'note':note,'at':now()}];store.save(r)

def compare(store,a,b):
    return {'references':[context(store,a),context(store,b)],'note':'Compare anchored evidence and inspected coverage. Do not generalize to author style from one example.'}

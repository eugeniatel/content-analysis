"""Adapt retained source evidence to Statool's deterministic feature functions."""
import copy,json,math
from pathlib import Path
from .store import digest,file_hash,now
from . import statool

SEMANTIC_FIELDS={'topics','content_intent','hook_archetype','narrative_structure','carousel_progression','cta_type','tone_dimensions'}

def _text(record):
    a=next((a for a in record['assets'] if a['role']=='text'),None)
    if not a:return None,None
    if file_hash(a['path'])!=a['sha256']:raise ValueError('Source text hash changed')
    scope=a.get('provenance',{}).get('scope') or record['retrieval'].get('limitation') or 'retained_text'
    if scope=='transcript':return None,None
    return Path(a['path']).read_text(),{'path':a['path'],'sha256':a['sha256'],'scope':scope,'passage_ids':[e['id'] for e in record['evidence'] if e['kind']=='passage']}

def import_ocr(store,reference_id,slides,method,source,expected_revision=None):
    if method not in {'ocr','manual_transcription'} or not source:raise ValueError('Specify OCR/manual transcription method and provenance')
    with store.lock(reference_id):
        r=store.get(reference_id);images={e['id']:e for e in r['evidence'] if e['kind']=='image'}
        if not isinstance(slides,list) or len(slides)>100:raise ValueError('slides must be a bounded list')
        normalized=[];seen=set();events=[]
        for slide in slides:
            image=images.get(slide.get('evidence_id'))
            if image is None or image['id'] in seen:raise ValueError('Each slide must bind to a unique existing source image evidence ID')
            seen.add(image['id']);path=image['frame'];image_asset=next((a for a in r['assets'] if a['role']=='media' and a['path']==path),None)
            if image_asset is None or file_hash(path)!=image_asset['sha256']:raise ValueError('Slide source bytes missing or changed')
            blocks=[];width=image['measurement']['width'];height=image['measurement']['height']
            for index,block in enumerate(slide.get('blocks',[])):
                if not isinstance(block.get('text'),str):raise ValueError('OCR text must be a string')
                b={'text':block['text'],'block_index':index,'confidence':block.get('confidence'),'source_path':path}
                if b['confidence'] is not None and (isinstance(b['confidence'],bool) or not isinstance(b['confidence'],(int,float)) or not 0<=b['confidence']<=1):raise ValueError('Confidence must be between 0 and 1')
                box=block.get('bbox')
                if box is not None:
                    if not isinstance(box,list) or len(box)!=4 or any(isinstance(x,bool) or not isinstance(x,(int,float)) or not math.isfinite(x) for x in box):raise ValueError('bbox requires four finite pixel values')
                    x,y,w,h=box
                    if min(x,y,w,h)<0 or x+w>width or y+h>height:raise ValueError('OCR bounding box is outside source image')
                    b['bbox']=box
                blocks.append(b)
                event={'id':'ocr-'+digest(image['id']+json.dumps(b,sort_keys=True))[:20],'kind':'slide_text','image_evidence_id':image['id'],'slide_index':image['position'],'text':b['text'],'bbox':box,'method':method,'source':source,'source_sha256':image_asset['sha256']}
                events.append(event)
            normalized.append({'slide_index':image['position'],'image_evidence_id':image['id'],'source_sha256':image_asset['sha256'],'image_width':width,'image_height':height,'blocks':blocks,'available':True,'source':source,'method':method})
        old={s['image_evidence_id']:s for s in r.get('ocr_slides',[])};old.update({s['image_evidence_id']:s for s in normalized})
        r['ocr_slides']=sorted(old.values(),key=lambda s:s['slide_index'])
        r['evidence']=[e for e in r['evidence'] if not(e['kind']=='slide_text' and e.get('image_evidence_id') in seen)]+events
        if r['observations']:r['semantic_status']='needs_recheck'
        return store.save(r,expected_revision)

def extract_features(store,reference_id,persist=False):
    _,engine,_=statool.load();r=store.get(reference_id);text,text_source=_text(r)
    spans=r['coverage'].get('signal_spans',[]);manifest=None;valid_manifest=False
    media=next((a for a in r['assets'] if a['role']=='media' and Path(a['path']).suffix.lower() in {'.mp4','.mov','.webm','.mkv'}),None)
    if media and r.get('analysis_manifest'):
        if file_hash(media['path'])!=media['sha256']:raise ValueError('Source media hash changed')
        path=Path(r['analysis_manifest']);identities={digest(media['sha256']+str(full)+str(speech))[:12] for full in (False,True) for speech in (False,True)}
        valid_manifest=path.parent.name in identities and path.is_file()
        if valid_manifest:manifest=json.loads(path.read_text())
    transcripts=manifest.get('transcripts',[]) if manifest else []
    all_segments=[];reels=[];source_refs=[]
    for index,(start,end) in enumerate(spans if valid_manifest else []):
        t=transcripts[index] if index<len(transcripts) else {};segments=t.get('segments') if t.get('complete') else None
        if segments:all_segments.extend(segments)
        words=[{**w,'confidence':w.get('probability')} for seg in segments or [] for w in seg.get('words',[])] or None
        shots=[e for e in r['evidence'] if e['kind']=='shot' and start<=e['start']<end and e['id'].startswith('s'+str(index+1)+'-')]
        ocr=[{'text':e['ocr'],'start_sec':e['frame_time'],'end_sec':e['frame_time'],'evidence_id':e['id']} for e in shots if e.get('ocr')]
        measured=engine.analyze_reel(word_timestamps=words,segments=segments,duration_seconds=end-start,on_screen_blocks=ocr or None,scene_changes=[{'time_sec':e['start'],'evidence_id':e['id']} for e in shots if e['start']>start])
        # The adapter never bridges uninspected gaps to infer pauses or speech pace.
        measured['source_range']=[start,end];measured['measurement_scope']='this processed window only; ASR and detector estimates, not listening'
        measured['evidence_ids']=[e['id'] for e in r['evidence'] if e.get('start') is not None and start<=e['start']<end]
        reels.append(measured);source_refs.extend(measured['evidence_ids'])
    images=sorted([e for e in r['evidence'] if e['kind']=='image'],key=lambda e:e['position'])
    slides=[]
    for image in images:
        current=next((s for s in r.get('ocr_slides',[]) if s['image_evidence_id']==image['id']),None)
        asset=next((a for a in r['assets'] if a['role']=='media' and a['path']==image['frame']),None)
        if current and asset and current.get('source_sha256')==asset['sha256'] and file_hash(asset['path'])==asset['sha256']:
            slides.append(current)
        else:slides.append({'slide_index':image['position'],'available':False,'blocks':None})
    carousel=engine.analyze_carousel(slides,caption=text) if slides else None
    if carousel:
        carousel['measurement_scope']='Only slides with available OCR are measured; missing OCR is not empty text'
        if not carousel['ocr_available_slide_count']:
            carousel['total_word_count']=None;carousel['block_count']=None
    common=engine.analyze_text_sources({'caption':text,'transcript':all_segments or None,'onscreen_text':None,'slides':[{'text':'\n'.join(b['text'] for b in s['blocks']),'slide_index':s['slide_index']} for s in slides if s.get('available')] or None})
    flat={'caption_word_count':common['sources'].get('caption',{}).get('word_count'),'transcript_word_count':common['sources'].get('transcript',{}).get('word_count'),'duration_sec':r.get('meta',{}).get('duration_s'),'slide_count':len(images) if images else None,'words_per_slide_median':(carousel or {}).get('words_per_slide',{}).get('median'),'text_density':(carousel or {}).get('median_density')}
    observed_speech=[x['speech'] for x in reels if x['speech']['words_per_minute'] is not None]
    denominator=sum(x['transcript_span_seconds'] for x in observed_speech)
    flat['words_per_minute']=sum(x['spoken_word_count'] for x in observed_speech)*60/denominator if denominator else None
    for threshold,key in [('0.3','pause_count_300ms'),('0.6','pause_count_600ms'),('1.0','pause_count_1000ms')]:
        counts=[x['pauses'].get('thresholds',{}).get(threshold,{}).get('count') for x in reels]
        flat[key]=sum(c for c in counts if c is not None) if any(c is not None for c in counts) else None
    if r['coverage'].get('sampled') or not transcripts or any(not t.get('complete') for t in transcripts):
        for key in ['transcript_word_count','words_per_minute','pause_count_300ms','pause_count_600ms','pause_count_1000ms']:flat[key]=None
    if not r.get('media_validation',{}).get('source_completeness_verified') or any(not s.get('available') for s in slides):
        for key in ['slide_count','words_per_slide_median','text_density']:flat[key]=None
    # Optional semantic labels must be explicitly supplied by ChatGPT and anchored.
    for dimension in SEMANTIC_FIELDS:
        values=[o.get('value') for o in r['observations'] if r['semantic_status'] in {'partial','reviewed'} and not o.get('corrected_at') and o.get('dimension')==dimension and o['label'] in {'observed','inferred'} and o.get('evidence_ids') and all(any(e['id']==eid for e in r['evidence']) for eid in o['evidence_ids'])]
        flat[dimension]=[v for v in values if isinstance(v,str)] or None
    result={'reference_id':reference_id,'reference_revision':r['revision'],'dependency':statool.provenance(),'source_text':text,'text_provenance':text_source,'common':common,'reel_windows':reels,'carousel':carousel,'flat':flat,'coverage':{'signal_spans':spans if valid_manifest else [],'analysis_matches_source_bytes':valid_manifest,'sampled':r['coverage'].get('sampled'),'source_image_count':len(images),'ocr_slides':sum(s.get('available',False) for s in slides),'source_completeness_verified':r.get('media_validation',{}).get('source_completeness_verified',False),'semantic_labels':'Only anchored ChatGPT observations; no automatic semantic model'},'evidence_refs':source_refs+[e['id'] for e in r['evidence'] if e['kind'] in {'passage','slide_text','image'}]}
    if persist:
        with store.lock(reference_id):
            current=store.get(reference_id)
            if current['revision']!=r['revision']:raise ValueError('Revision changed during extraction; rerun')
            current['measured_features']=result;store.save(current,r['revision'])
    return result

def aggregate_captions(features,winner_ids):
    _,engine,_=statool.load();documents=[f for f in features.values() if f['source_text'] is not None]
    def anchor(rows,docs,field='examples'):
        for row in rows:
            for ex in row.get(field,[]):
                f=docs[ex['document_index']];ex['reference_id']=f['reference_id'];ex['reference_revision']=f['reference_revision'];ex['source_sha256']=f['text_provenance']['sha256'];ex['passage_ids']=f['text_provenance']['passage_ids']
        return rows
    ngrams={str(n):anchor(engine.aggregate_ngrams([f['source_text'] for f in documents],n=n,minimum_count=2,remove_stopwords=n==1),documents) for n in (1,2,3)}
    winners=[f for f in documents if f['reference_id'] in winner_ids];baseline=documents
    comparison=engine.winner_baseline_inputs([f['source_text'] for f in winners],[f['source_text'] for f in baseline])
    anchor(comparison['terms'],winners,'winner_examples');anchor(comparison['terms'],baseline,'baseline_examples')
    return {'document_count':len(documents),'ngrams':ngrams,'winner_vs_baseline':comparison,'scope':'Measured lexical counts in retained text; source scope and extraction coverage apply. Not proof of recurring intent or causal performance.'}

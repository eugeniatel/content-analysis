"""Verify retained bytes separately from a fresh, complete source retrieval."""
import math, subprocess
from datetime import datetime
from pathlib import Path
from .store import canonical, file_hash, now

VIDEO={'.mp4','.mov','.webm','.mkv'}
IMAGE={'.png','.jpg','.jpeg','.webp'}

def verify(record):
    from .media import probe, reference
    media=[a for a in record['assets'] if a['role']=='media']
    retrieval=record['retrieval'];run_id=retrieval.get('run_id')
    latest_run=record.get('last_refresh',{}).get('run_id',run_id)
    expected=retrieval.get('expected_media_count')
    expected_duration=retrieval.get('expected_duration_seconds')
    if expected_duration is None and len(media)==1:
        expected_duration=next((c['duration_seconds'] for c in reversed(record.get('browser_captures',[])) if c.get('duration_seconds')),None)
    expected_assets=retrieval.get('expected_assets',[])
    order_matches=len(media)==1 or (retrieval.get('asset_order_established') is True and [a.get('provenance',{}).get('media_index') for a in media]==list(range(len(media))))
    checks=[]
    for index,a in enumerate(media):
        p=Path(a['path']);provenance=a.get('provenance',{})
        check={'path':str(p),'exists':p.is_file(),'hash_matches':False,'decoded':False,'duration_matches':None,'fresh_download':False,'source_identity_verified':False}
        if not p.is_file():checks.append(check);continue
        check['size_bytes']=p.stat().st_size
        check['hash_matches']=check['size_bytes']>0 and file_hash(p)==a['sha256']
        if not check['hash_matches']:checks.append(check);continue
        try:
            retrieved_at=datetime.fromisoformat(provenance.get('retrieved_at',''))
            valid_time=retrieved_at.tzinfo is not None and retrieved_at<=datetime.fromisoformat(now())
            if retrieval.get('started_at'):valid_time=valid_time and retrieved_at>=datetime.fromisoformat(retrieval['started_at'])
        except (ValueError,TypeError):valid_time=False
        check['fresh_download']=bool(run_id and run_id==latest_run and provenance.get('run_id')==run_id and provenance.get('fresh_retrieval') is True and valid_time)
        source=provenance.get('source')
        try:check['source_identity_verified']=bool(source and canonical(source)==record['canonical'] and retrieval.get('source_identity_verified') is True)
        except (ValueError,OSError):pass
        try:
            if p.suffix.lower() in VIDEO:
                meta=probe(p);check['duration_seconds']=meta['duration_s']
                decode=subprocess.run([reference().ffmpeg_bin(),'-v','error','-xerror','-i',str(p),'-map','0:v?','-map','0:a?','-f','null','-'],capture_output=True,timeout=120)
                check['decoded']=decode.returncode==0
                duration=expected_assets[index].get('duration_seconds') if index<len(expected_assets) else expected_duration
                if duration is not None and not isinstance(duration,bool) and math.isfinite(float(duration)) and float(duration)>0:
                    check['expected_duration_seconds']=float(duration)
                    check['duration_matches']=abs(meta['duration_s']-float(duration))<=max(.5,float(duration)*.005)
            elif p.suffix.lower() in IMAGE:
                from PIL import Image
                with Image.open(p) as image:image.verify()
                with Image.open(p) as image:image.load();check['dimensions']=list(image.size)
                check['decoded']=True
            else:check['error']='Unsupported media type for completeness validation'
        except (OSError,ValueError,RuntimeError,SystemExit,subprocess.TimeoutExpired) as error:
            check['error']=type(error).__name__
        checks.append(check)
    count_matches=type(expected) is int and expected>0 and len(media)==expected
    byte_valid=bool(checks) and all(c['hash_matches'] and c['decoded'] for c in checks)
    completeness=count_matches and order_matches and byte_valid and all(c.get('duration_matches') is True for c in checks if Path(c['path']).suffix.lower() in VIDEO)
    fresh_complete=completeness and all(c['fresh_download'] and c['source_identity_verified'] for c in checks)
    return {'verified_at':now(),'run_id':run_id,'latest_run_id':latest_run,'retained_bytes_valid':byte_valid,'expected_media_count':expected,'retained_media_count':len(media),'asset_count_matches':count_matches,'asset_order_matches':order_matches,'source_completeness_verified':completeness,'fresh_full_media_verified':fresh_complete,'assets':checks,'note':'Cached bytes, browser playback and transcripts do not establish a fresh complete download. Unknown asset counts, order or video durations keep completeness unverified.'}

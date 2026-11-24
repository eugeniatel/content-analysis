import json, os, subprocess, shutil
from pathlib import Path
from .store import CONFIG, atomic_json, file_hash
from .retrieve import module_at

def reference():
    os.environ.setdefault('VRA_NO_REEXEC','1');os.environ.setdefault('NUMBA_CACHE_DIR',str(Path(os.environ.get('TMPDIR','/tmp'))/'content-analysis-numba'))
    return module_at('content_analysis_reference',CONFIG['reference_script'])
def run(cmd,timeout=120):
    p=subprocess.run([str(x) for x in cmd],capture_output=True,timeout=timeout)
    if p.returncode:raise RuntimeError('Media command failed: '+p.stderr.decode(errors='replace')[-350:])
    return p.stdout

def probe(path):return reference().probe(path)
def ranges(duration,full=False):
    if full or duration<=CONFIG['max_full_scan_seconds']:return [(0,duration)]
    w=min(CONFIG['long_sample_seconds'],duration/3)
    return [(0,w),(round(duration/2-w/2,3),round(duration/2+w/2,3)),(round(duration-w,3),duration)]
def transcribe(path,out,offset=0):
    import numpy as np
    from faster_whisper import WhisperModel
    models=sorted(Path(CONFIG['whisper_cache']).glob('**/model.bin'))
    if not models:return {'complete':False,'error':'Cached Whisper small model unavailable; no download attempted','segments':[]}
    modelpath=models[-1].parent
    ff=reference().ffmpeg_bin();pcm=run([ff,'-v','error','-i',path,'-f','f32le','-ac','1','-ar','16000','-'],timeout=180)
    model=WhisperModel(str(modelpath),device='cpu',compute_type='int8',cpu_threads=4,local_files_only=True)
    segments,info=model.transcribe(np.frombuffer(pcm,dtype=np.float32),beam_size=5,word_timestamps=True,condition_on_previous_text=False,vad_filter=True)
    result={'complete':False,'media_sha256':file_hash(path),'model':str(modelpath),'language':info.language,'source_offset':offset,'segments':[],'note':'Automatic speech recognition, not listening; names/numbers/negations require source verification.'}
    for s in segments:
        if s.no_speech_prob>.6 or s.avg_logprob< -1:continue
        result['segments'].append({'start':round(s.start+offset,3),'end':round(s.end+offset,3),'text':s.text.strip(),'avg_logprob':s.avg_logprob,'no_speech_prob':s.no_speech_prob,'words':[{'start':round(w.start+offset,3),'end':round(w.end+offset,3),'text':w.word,'probability':w.probability} for w in s.words or []]})
        atomic_json(out,result)
    result['complete']=True;atomic_json(out,result);return result

def analyze(path,folder,full=False,speech=True):
    meta=probe(path);duration=meta['duration_s'];spans=ranges(duration,full);events=[];reports=[];transcripts=[];ff=reference().ffmpeg_bin()
    for n,(start,end) in enumerate(spans):
        part=folder/('span-'+str(n+1));part.mkdir(parents=True,exist_ok=True)
        selected=Path(path)
        if start!=0 or end<duration-.02:
            selected=part/'sample.mp4'
            run([ff,'-v','error','-y','-ss',start,'-i',path,'-t',end-start,'-c:v','libx264','-preset','ultrafast','-crf','21','-c:a','aac','-movflags','+faststart',selected],timeout=180)
        report=part/'report.json'
        if not report.exists():
            env={**os.environ,'HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1','MPLCONFIGDIR':str(folder/'mpl'),'VRA_WORKDIR':str(folder/'reference-cache'),'NUMBA_CACHE_DIR':str(folder/'numba')}
            env['PYTHONPATH']=str(Path(__file__).resolve().parents[1])+os.pathsep+env.get('PYTHONPATH','')
            p=subprocess.run([CONFIG['python'],'-m','content_analysis.reference_runner',str(selected),'--out',str(part),'--whisper-model','none','--no-ocr'],capture_output=True,text=True,env=env,timeout=900)
            (part/'analyzer.log').write_text(p.stderr)
            if p.returncode:raise RuntimeError('Existing reference analyzer failed; see retained analyzer.log')
        d=json.loads(report.read_text());reports.append({'path':str(report),'source_start':start,'source_end':end})
        for shot in d['shots']:
            e={'id':f's{n+1}-shot{shot["index"]}','kind':'shot','start':round(shot['start_s']+start,3),'end':round(shot['end_s']+start,3),'frame_time':round(shot['keyframe_frame']/meta['fps']+start,3),'frame':shot.get('keyframe'),'label':shot.get('transition_in'),'measurement':{'transition_estimate':shot.get('transition_in'),'duration_seconds':shot['duration_s'],'motion_mean_diff':shot.get('motion_mean_diff'),'mean_luma':shot.get('mean_luma')},'note':'Automated shot boundary/transition estimate. Inspect frames before describing creative decisions.'}
            events.append(e)
        # Optional local OCR; unavailable extraction remains explicit.
        if shutil.which('tesseract'):
            for e in events:
                if not e['id'].startswith(f's{n+1}-') or not e.get('frame'):continue
                try:e['ocr']=run(['tesseract',e['frame'],'stdout'],timeout=30).decode(errors='replace').strip()
                except Exception:e['ocr_status']='unavailable'
        if speech and meta['has_audio']:
            tp=part/'transcript.json'
            try:
                cached=json.loads(tp.read_text()) if tp.exists() else {}
                if cached.get('complete') and cached.get('media_sha256')==file_hash(selected):t=cached
                else:t=transcribe(selected,tp,start)
                transcripts.append(t)
                for i,s in enumerate(t['segments']):events.append({'id':f's{n+1}-speech{i+1}','kind':'speech','start':s['start'],'end':s['end'],'text':s['text'],'quality':{'avg_logprob':s['avg_logprob'],'no_speech_prob':s['no_speech_prob']},'note':'ASR transcript; not verified listening'})
            except Exception as e:transcripts.append({'complete':False,'error':type(e).__name__,'segments':[]})
        audio=d.get('audio',{})
        for i,s in enumerate(audio.get('silent_sections') or []):
            events.append({'id':f's{n+1}-silence{i+1}','kind':'audio_signal','start':round(s['start_s']+start,3),'end':round(s['end_s']+start,3),'measurement':s,'note':'Measured low-amplitude interval from mixed audio; not proof that all sound stops.'})
        # Retain the complete signal report for energy, silence, beat grids and synchronization.
        for i,s in enumerate(audio.get('sfx_candidates') or []):
            if isinstance(s,dict):events.append({'id':f's{n+1}-audio{i+1}','kind':'audio_signal','start':round(float(s.get('t',s.get('time_s',0)))+start,3),'end':round(min(end,float(s.get('t',s.get('time_s',0)))+start+float(s.get('duration_s',.25))),3),'measurement':s,'note':'Signal-derived candidate; not an identified or auditioned sound effect'})
    return {'meta':meta,'evidence':sorted(events,key=lambda x:(x['start'],x['id'])),'reports':reports,'transcripts':transcripts,'coverage':{'signal_spans':[[a,b] for a,b in spans],'visual':[],'audio':[],'text':[],'asr_spans':[[a,b] for (a,b),t in zip(spans,transcripts) if t.get('complete')],'method':'Complete signal processing within listed spans; keyframe sampling per detected shot; ASR where successful. Human/model visual inspection and listening tracked separately.','sampled':sum(b-a for a,b in spans)<duration-.05,'duration':duration,'processed_seconds':round(sum(b-a for a,b in spans),3)}}

def frame(path,t,out):
    m=probe(path)
    if not 0<=t<m['duration_s']:raise ValueError('Timestamp outside source duration')
    run([reference().ffmpeg_bin(),'-v','error','-y','-ss',t,'-i',path,'-frames:v','1','-vf','scale=960:-2',out])
    return str(out)

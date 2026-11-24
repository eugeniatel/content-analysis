"""Structured local tools for invocation by ChatGPT/Work. No server or exports."""
import inspect,json
from . import service,profiles,features


def resolve_evidence(store,reference_id,evidence_id,revision=None):
    r=store.get_revision(reference_id,revision) if revision is not None else store.get(reference_id)
    e=next((e for e in r['evidence'] if e['id']==evidence_id),None)
    if e is None:raise ValueError('Evidence not found in that exact reference revision')
    result={'reference_id':reference_id,'revision':r['revision'],'evidence':e}
    if e.get('reference_id') and e.get('source_evidence_id'):
        result['source']=service.context(store,e['reference_id'],passage=e['source_evidence_id'],revision=e['reference_revision'])
    elif e.get('snapshot_id'):
        snapshot=store.get(e['snapshot_id']);result['source']={'snapshot_id':snapshot['id'],'provenance':snapshot['provenance'],'evidence':[x for x in snapshot['evidence'] if x['id']==evidence_id]}
    return result

def reference_get(store,reference_id,revision=None):return store.get_revision(reference_id,revision) if revision is not None else store.get(reference_id)
def reference_context(store,reference_id,at=None,evidence_id=None,query=None,window=3,revision=None):return service.context(store,reference_id,at,evidence_id,query,window,revision)
def reference_add(store,source,title=None,handle=None,refresh=False):return service.ingest(store,source,title=title,handle=handle,force=refresh)
def reference_annotate(store,reference_id,patch,expected_revision):return service.annotate(store,reference_id,patch,expected_revision)
def reference_correct(store,reference_id,observation_id,text,reason,expected_revision):return service.correct(store,reference_id,observation_id,text,reason,expected_revision)
def reference_process(store,reference_id,full=False,speech=True):return service.process(store,reference_id,full,speech)
def reference_capture(store,source,snapshot):return service.capture(store,source,snapshot)
def reference_list(store,query=''):return store.list(query)
def reference_history(store,reference_id):return store.history(reference_id)
def reference_compare(store,first_id,second_id):return service.compare(store,first_id,second_id)
def analysis_result(store,reference_id,section='ranked',offset=0,limit=20):
    r=profiles.get_kind(store,reference_id,'profile-run')
    if section not in {'ranked','findings','evidence','features','lexical'}:raise ValueError('Unknown result section')
    if type(offset) is not int or offset<0 or type(limit) is not int or not 1<=limit<=100:raise ValueError('Use nonnegative offset and limit 1–100')
    if section in {'ranked','findings'}:values=r['analytics'][section]
    elif section=='features':values=list(r['features'].values())
    elif section=='lexical':values=[{'n':n,**row} for n,rows in r['lexical']['ngrams'].items() for row in rows]
    else:values=r['evidence']
    return {'reference_id':reference_id,'revision':r['revision'],'section':section,'total':len(values),'offset':offset,'items':values[offset:offset+limit],'next_offset':offset+limit if offset+limit<len(values) else None}

TOOLS={
 'reference.add':reference_add,'reference.capture':reference_capture,'reference.process':reference_process,
 'reference.get':reference_get,'reference.list':reference_list,'reference.context':reference_context,
 'reference.annotate':reference_annotate,'reference.correct':reference_correct,'reference.history':reference_history,
 'reference.compare':reference_compare,'evidence.resolve':resolve_evidence,
 'inventory.import':profiles.inventory_import,'inventory.collect':profiles.inventory_collect,
 'metrics.import':profiles.metrics_import,'features.extract':features.extract_features,
 'features.import_ocr':features.import_ocr,'profile.analyze':profiles.run_profile,
 'analysis.result':analysis_result,
}

READ_ONLY_TOOLS={'tools.list','reference.get','reference.list','reference.context','reference.history','reference.compare','evidence.resolve','analysis.result'}
def read_only_tool(name,arguments):return name in READ_ONLY_TOOLS or name=='features.extract' and arguments.get('persist',False) is False

TYPES={**{k:'string' for k in ['source','title','handle','reference_id','evidence_id','query','observation_id','text','reason','first_id','second_id','profile_url','scope','captured_at','access','path','supersedes','inventory_id','objective','as_of','metric_basis','metric_unit','method','section']},**{k:'object' for k in ['snapshot','patch','columns']},**{k:'array' for k in ['items','limitations','rows','slides','metric_snapshot_ids','metric_specs']},**{k:'integer' for k in ['expected_revision','revision','limit','expected_total','requested_count','top_k','minimum_sample','bootstrap_iterations','seed','offset']},**{k:'boolean' for k in ['refresh','full','speech','exhausted','persist','allow_keychain_prompt']},'at':'number','window':'number'}

def catalog():
    rows=[]
    for name,fn in TOOLS.items():
        signature=inspect.signature(fn);params={};required=[]
        for key,p in signature.parameters.items():
            if key=='store':continue
            params[key]={'type':TYPES[key],'description':key.replace('_',' ')}
            if p.default is inspect.Parameter.empty:required.append(key)
            else:
                params[key]['default']=p.default
                if p.default is None:params[key]['type']=[TYPES[key],'null']
        rows.append({'name':name,'input_schema':{'type':'object','properties':params,'required':required,'additionalProperties':False},'output':'schema_version, tool, status, data; stable reference IDs and exact source provenance'})
    return {'schema_version':'1.0','tools':rows,'user_interface':'ChatGPT conversation','application_required':False,'document_export_required':False}

def invoke(store,name,arguments):
    if not isinstance(arguments,dict):raise ValueError('Tool input must be a JSON object')
    if name=='tools.list':result=catalog()
    else:
        if name not in TOOLS:raise ValueError('Unknown tool: '+name)
        fn=TOOLS[name]
        try:inspect.signature(fn).bind(store,**arguments)
        except TypeError as error:raise ValueError('Invalid tool input: '+str(error)) from None
        for key,value in arguments.items():
            default=inspect.signature(fn).parameters[key].default
            if value is None and default is None:continue
            typ=TYPES[key];allowed={'string':str,'object':dict,'array':list,'integer':int,'boolean':bool,'number':(int,float)}[typ]
            if not isinstance(value,allowed) or typ in {'integer','number'} and isinstance(value,bool):raise ValueError('Invalid '+key+': expected '+typ)
        result=fn(store,**arguments)
    status='ok'
    if isinstance(result,dict):
        if result.get('collection_status')=='blocked':status='blocked'
        elif result.get('kind')=='inventory' and not result['inventory_coverage']['collection_complete']:status='partial'
        elif result.get('kind')=='profile-run' and not result['analysis_limits']['profile_conclusions_supported']:status='partial'
        if result.get('kind')=='inventory':result={**result,'members':[{k:v for k,v in m.items() if k!='snapshot'} for m in result['members'][:50]],'member_total':len(result['members'])}
        elif result.get('kind')=='metrics':result={**result,'metrics':result['metrics'][:100],'metric_total':len(result['metrics']),'evidence_count':len(result['evidence']),'evidence':result['evidence'][:20]}
        elif result.get('kind')=='profile-run':
            result={k:result[k] for k in ['id','revision','profile','inventory_id','metric_snapshot_ids','inventory_coverage','analysis_limits','provenance']}|{'analytics':{**result['analytics'],'ranked':result['analytics']['ranked'][:20],'winners':result['analytics']['winners'][:20],'findings':result['analytics']['findings'][:20]},'evidence_count':len(result['evidence']),'lexical':{'document_count':result['lexical']['document_count'],'ngrams':{k:v[:20] for k,v in result['lexical']['ngrams'].items()}},'more':'analysis.result returns paged ranked, findings, evidence, features or lexical results'}
    return {'schema_version':'1.0','tool':name,'status':status,'data':result}

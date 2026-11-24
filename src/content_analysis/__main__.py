import argparse,json,sys,sqlite3
from pathlib import Path
from .store import Store,CONFIG,ROOT,DATA_ROOT
from . import service

def main():
    p=argparse.ArgumentParser(prog='ca',description='Content Analysis — local evidence, ChatGPT interpretation')
    p.add_argument('--data',help='Explicit data root, normally persistent project data/')
    sub=p.add_subparsers(dest='cmd',required=True)
    a=sub.add_parser('add');a.add_argument('source');a.add_argument('--title');a.add_argument('--media');a.add_argument('--text-file');a.add_argument('--handle');a.add_argument('--refresh',action='store_true');a.add_argument('--cached-at')
    a=sub.add_parser('capture');a.add_argument('source');a.add_argument('snapshot',help='JSON containing url, scope and the actual observed text/player metadata')
    a=sub.add_parser('process');a.add_argument('id');a.add_argument('--full',action='store_true');a.add_argument('--no-speech',action='store_true')
    a=sub.add_parser('verify-media');a.add_argument('id')
    a=sub.add_parser('list');a.add_argument('query',nargs='?',default='')
    for cmd in ('show','history'):
        a=sub.add_parser(cmd);a.add_argument('id')
    a=sub.add_parser('context');a.add_argument('id');a.add_argument('--at',type=float);a.add_argument('--passage');a.add_argument('--query');a.add_argument('--window',type=float,default=3)
    a=sub.add_parser('annotate');a.add_argument('id');a.add_argument('patch');a.add_argument('--revision',type=int)
    a=sub.add_parser('correct');a.add_argument('id');a.add_argument('target');a.add_argument('text');a.add_argument('--reason',required=True);a.add_argument('--revision',type=int)
    a=sub.add_parser('link');a.add_argument('a');a.add_argument('b');a.add_argument('--note',required=True)
    a=sub.add_parser('compare');a.add_argument('a');a.add_argument('b')
    a=sub.add_parser('frame');a.add_argument('id');a.add_argument('time',type=float)
    a=sub.add_parser('tool',help='Structured tools invoked inside ChatGPT/Work');a.add_argument('name');a.add_argument('--input',default='{}');a.add_argument('--input-file')
    sub.add_parser('doctor')
    args=p.parse_args();s=None;tool_arguments=None
    try:
        read_only=args.cmd in {'list','show','history','context','compare','doctor'}
        if args.cmd=='tool':
            from .tools import read_only_tool
            raw=(sys.stdin.read() if args.input_file=='-' else Path(args.input_file).read_text()) if args.input_file else args.input
            tool_arguments=json.loads(raw)
            read_only=read_only_tool(args.name,tool_arguments)
        if args.cmd=='tool' and args.name=='tools.list':
            from .tools import invoke
            print(json.dumps(invoke(None,args.name,tool_arguments),ensure_ascii=False,indent=2));return
        s=Store(args.data,read_only=read_only)
        if args.cmd=='add':result=service.ingest(s,args.source,args.title,args.media,Path(args.text_file).read_text() if args.text_file else None,args.handle,args.refresh,args.cached_at)
        elif args.cmd=='capture':result=service.capture(s,args.source,json.loads(Path(args.snapshot).read_text()))
        elif args.cmd=='process':result=service.process(s,args.id,args.full,not args.no_speech)
        elif args.cmd=='verify-media':result=service.verify_media(s,args.id)
        elif args.cmd=='list':result=s.list(args.query)
        elif args.cmd=='show':result=s.get(args.id)
        elif args.cmd=='history':result=s.history(args.id)
        elif args.cmd=='context':result=service.context(s,args.id,args.at,args.passage,args.query,args.window)
        elif args.cmd=='annotate':result=service.annotate(s,args.id,json.loads(Path(args.patch).read_text()),args.revision)
        elif args.cmd=='correct':result=service.correct(s,args.id,args.target,args.text,args.reason,args.revision)
        elif args.cmd=='link':service.link(s,args.a,args.b,args.note);result={'linked':[args.a,args.b]}
        elif args.cmd=='compare':result=service.compare(s,args.a,args.b)
        elif args.cmd=='frame':
            from .media import frame
            r=s.get(args.id);source=next(x['path'] for x in r['assets'] if x['role']=='media');out=s.folder(args.id)/('frame-'+str(args.time)+'.jpg');result={'frame':frame(source,args.time,out)}
        elif args.cmd=='tool':
            from .tools import invoke
            result=invoke(s,args.name,tool_arguments)
        elif args.cmd=='doctor':
            result={'python':sys.executable,'dependencies':{k:Path(CONFIG[k]).exists() for k in ('reference_script','whisper_cache')},'data':str(s.root),'model_api':False,'semantic_analysis':'ChatGPT using WORKFLOW.md','user_interface':'ChatGPT conversation','application_required':False,'document_export_required':False}
        print(json.dumps(result,ensure_ascii=False,indent=2))
    except Exception as e:
        error={'error':str(e),'type':type(e).__name__}
        if args.cmd=='tool':error.update(schema_version='1.0',tool=args.name,status='error')
        if isinstance(e,PermissionError) or isinstance(e,sqlite3.OperationalError) and any(t in str(e).lower() for t in ['unable to open','readonly','read-only']):
            error['required_permission']={'file_system':{'write':[str(Path(args.data).resolve() if args.data else DATA_ROOT)]}}
            error['required_action']='Request normal write permission for this existing evidence directory; do not create another project or copy the database.'
        print(json.dumps(error),file=sys.stderr);sys.exit(1)
    finally:
        if s is not None:s.close()
if __name__=='__main__':main()

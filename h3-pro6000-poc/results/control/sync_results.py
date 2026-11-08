"""Local stdlib transfer controller. Source is fixed to run evidence; never models or .env."""
import concurrent.futures,hashlib,json,os,subprocess,time,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];run=ROOT/'runs/self';state=ROOT/'runs/control';run.mkdir(parents=True,exist_ok=True)
config=str(ROOT/'private/ssh-config');ssh=['ssh','-F',config,'h3-poc'];rsh='ssh -F '+config
source='h3-poc:/data/h3-pro6000-poc/runs/self/'
log=(state/'continuous-sync.jsonl').open('a',buffering=1)
cache={}
once='--once' in sys.argv
frozen='--frozen' in sys.argv
last_ok=False
def event(**kw):log.write(json.dumps({'epoch':time.time(),**kw})+'\n')
def sha(p):
    key=(str(p),p.stat().st_size,p.stat().st_mtime_ns)
    if key not in cache:
        h=hashlib.sha256()
        with p.open('rb') as f:
            for b in iter(lambda:f.read(4*1024*1024),b''):h.update(b)
        cache[key]=h.hexdigest()
    return cache[key]
def rsync(extra,timeout=900):
    p=subprocess.run(['rsync','-az','--timeout=60','--partial-dir=.rsync-partial','-e',rsh,*extra,source,str(run)+'/'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=timeout)
    return {'rc':p.returncode,'stderr_tail':p.stderr.decode(errors='replace')[-400:]}
remote='''import pathlib,json
root=pathlib.Path('/data/h3-pro6000-poc/runs/self')
print(json.dumps([{'path':r['video_path'],'sha256':r['media']['sha256'],'size':r['media']['size_bytes']} for r in (json.loads(p.read_text()) for p in (root/'records').glob('*.json')) if r.get('status')=='validated']))'''
while once or not (state/'STOP_SYNC').exists():
    try:
        if not frozen:subprocess.run(ssh+['python3 /data/h3-pro6000-poc/control/snapshot_evidence.py'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=45,check=True)
        if frozen:
            bootstrap=state/'sync-bootstrap-files.txt';bootstrap.write_text('cost/archive-manifest.json\n')
            initial=rsync(['--files-from='+str(bootstrap)],120)
            if initial['rc']!=0:raise RuntimeError('Cannot fetch frozen manifest')
            frozen_manifest=json.loads((run/'cost/archive-manifest.json').read_text())
            checkpoints=[]
            for item in frozen_manifest['files']:
                rel=Path(item['path'])
                if rel.is_absolute() or '..' in rel.parts:raise RuntimeError('Unsafe frozen source')
                if rel.parts[0]=='checkpoints' and rel.suffix=='.gz':checkpoints.append(rel.as_posix())
            bootstrap.write_text('\n'.join(checkpoints)+'\n')
            compressed=rsync(['--files-from='+str(bootstrap)],300) if checkpoints else {'rc':0}
            if compressed['rc']!=0:raise RuntimeError('Cannot fetch compressed evidence')
            from materialize_checkpoints import materialize
            restored=materialize(run);(state/'checkpoint-materialization-proof.json').write_text(json.dumps(restored,ensure_ascii=False,indent=2)+'\n')
            event(checkpoint_restored_files=len(restored['materialized']),restored_bytes=restored['restored_bytes'])
        exclusions=['--exclude=*.mp4','--exclude=*.part','--exclude=*.lock','--exclude=*.tmp','--exclude=.rsync-partial/']
        if frozen:exclusions=['--checksum',*exclusions]
        if not frozen:exclusions=['--include=checkpoints/***',*exclusions,'--exclude=*.jsonl','--exclude=*.log','--exclude=*console*']
        meta=rsync(exclusions,300)
        response=subprocess.run(ssh+['python3 -c '+__import__('shlex').quote(remote)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30,check=True)
        files=json.loads(response.stdout);pending=[]
        for item in files:
            rel=Path(item['path'])
            if rel.is_absolute() or '..' in rel.parts or rel.parts[0]!='outputs' or rel.suffix!='.mp4':raise RuntimeError('Disallowed source path')
            p=run/rel
            if p.is_file() and p.stat().st_size==item['size'] and sha(p)==item['sha256']:continue
            if not frozen and list(p.parent.glob('.'+p.name+'.*')):continue
            pending.append(item)
        groups=[[] for _ in range(4)];sizes=[0]*4
        for item in sorted(pending,key=lambda x:-x['size']):
            k=min(range(4),key=lambda i:sizes[i]);groups[k].append(item['path']);sizes[k]+=item['size']
        lists=[]
        for k,items in enumerate(groups):
            if not items:continue
            f=state/f'sync-files-{k}.txt';f.write_text('\n'.join(items)+'\n');lists.append(f)
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:transfers=list(pool.map(lambda f:rsync(['--files-from='+str(f)]),lists))
        verified=sum((run/x['path']).is_file() and (run/x['path']).stat().st_size==x['size'] and sha(run/x['path'])==x['sha256'] for x in files)
        last_ok=meta['rc']==0 and all(x['rc']==0 for x in transfers) and verified==len(files)
        event(frozen=frozen,completed_videos_remote=len(files),verified_videos_local=verified,transfer_groups=len(lists),bytes_scheduled=sum(sizes),metadata=meta,transfers=transfers)
    except Exception as exc:last_ok=False;event(error_type=type(exc).__name__)
    if once:break
    for _ in range(30):
        if (state/'STOP_SYNC').exists():break
        time.sleep(1)
event(stopped=True)

if once and not last_ok:raise SystemExit('Final sync incomplete; inspect log and verify archive before release')

"""Server-only optional Turbo session, 45-minute cap including download/restart.
Never auto-starts; the operator runs --execute only after reviewing stability and C2/C4.
"""
import argparse,hashlib,json,os,platform,signal,subprocess,sys,time,urllib.request
from pathlib import Path
BASE=Path(__file__).resolve().parents[1];sys.path.insert(0,str(BASE/'scripts'))
from common import dump,load,sha256,utcnow
from budget import Ledger
from providers import vllm_fields
p=argparse.ArgumentParser();p.add_argument('--execute',action='store_true');a=p.parse_args()
if not a.execute:raise SystemExit('Prepared only; no download, restart or request without --execute')
if platform.system()!='Linux':raise SystemExit('Never run on laptop')
run=BASE/'runs/self';cfg=load(BASE/'config/baseline.json')
if (run/'STOP_SUBMISSIONS').exists() or not (run/'STABILITY_FINISHED.json').exists():raise SystemExit('Stability gate or stop flag')
for phase,n in [('baseline',42),('concurrency_c2',2),('concurrency_c4',4)]:
 rows=[load(x) for x in (run/'records').glob('*.json') if load(x)['phase']==phase]
 if len(rows)!=n or any(x.get('status')!='validated' for x in rows):raise SystemExit('Review incomplete/failed priority phase before optional Turbo: '+phase)
Ledger(BASE/'private/ledger.json').guard(next_gpu_seconds=2700)
marker=run/'TURBO_SESSION_STARTED.json'
if marker.exists():raise SystemExit('Turbo already has a session intent; never auto-repeat')
start=time.monotonic();deadline=start+2700;dump(marker,{'utc':utcnow(),'limit_seconds':2700,'includes':['adapter download and verification','BF16 stop','Turbo service startup','six requests and validation','Turbo shutdown'],'requested_max_samples':6})
service=None;runner=None

def remaining(reserve=0):
 value=deadline-time.monotonic()-reserve
 if value<=0:raise TimeoutError('45-minute total Turbo cap')
 return value

def stop(name):
 path=run/'processes'/(name+'.json')
 if not path.exists():return
 proc=load(path);pid=proc['pid'];cmd=Path('/proc')/str(pid)/'cmdline'
 if not cmd.exists():return
 raw=cmd.read_bytes()
 if b'vllm' not in raw or b'/data/models/MiniMax-H3/FL2VA' not in raw:raise RuntimeError('Refuse unrelated process termination')
 os.killpg(proc['pgid'],signal.SIGTERM)
 until=time.monotonic()+min(35,max(1,deadline-time.monotonic()))
 while cmd.exists() and time.monotonic()<until:
  try:
   if not cmd.read_bytes():break
  except FileNotFoundError:break
  time.sleep(1)
 if cmd.exists() and cmd.read_bytes():os.killpg(proc['pgid'],signal.SIGKILL)
 dump(run/'deploy'/(name+'-stopped.json'),{'utc':utcnow(),'pid':pid,'pgid':proc['pgid'],'action':'planned session transition or phase cap; SIGTERM then bounded SIGKILL fallback'})

def launch(name,log,cmd):
 subprocess.run([sys.executable,'scripts/server_dispatch.py','--name',name,'--log',str(log),'--',*cmd],cwd=BASE,check=True,timeout=min(20,remaining()))
 return load(run/'processes'/(name+'.json'))

outcome='failed';error=None
try:
 with (run/'deploy/turbo-download-console.txt').open('w') as log:
  subprocess.run([sys.executable,'scripts/download_model.py','--turbo','--run-dir','runs/self','--out','runs/self/environment/turbo-file-verified.json'],cwd=BASE,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=min(600,remaining(120)))
 (run/'ARCHIVE_STOP_HEALTH_WATCH').touch();time.sleep(3)
 stop('server-bf16-cpubind')
 service=launch('server-turbo-cpubind',run/'deploy/server-turbo-cpubind.log',[sys.executable,'scripts/launch.py','--execute','--turbo','--numa-mode','cpu-bind','--mapping','private/topology-mapping.json','--out','runs/self/deploy/turbo-cpubind'])
 until=time.monotonic()+min(900,remaining(180))
 while True:
  if time.monotonic()>until:raise TimeoutError('Turbo service readiness cap')
  if (run/'STOP_SUBMISSIONS').exists():raise RuntimeError('Submission stop flag')
  try:
   with urllib.request.urlopen('http://127.0.0.1:8091/health',timeout=3) as resp:ready=resp.status==200
   if ready:break
  except OSError:pass
  time.sleep(3)
 (run/'ARCHIVE_STOP_HEALTH_WATCH').unlink(missing_ok=True)
 launch('fatal-gpu-nccl-watch-turbo',run/'cost/fatal-watch-turbo-console.txt',[sys.executable,'control/fatal_watch.py'])
 cases=[r for r in load(BASE/'cases/matrix.lock.json')['requests'] if r['phase']=='turbo']
 contract=[{'request_id':c['request_id'],'multipart_fields':vllm_fields(c,cfg),'asset_sha256':c.get('asset_sha256')} for c in cases]
 dump(run/'environment/turbo-request-contract.json',{'utc':utcnow(),'harness_provider_source_sha256':sha256(BASE/'scripts/providers.py'),'requests':contract,'purpose':'Exact fields used by the frozen harness; supplement to loader logs, not a live tensor dump'})
 with (run/'deploy/turbo-requests-console.txt').open('w') as log:
  runner=subprocess.Popen([sys.executable,'scripts/run.py','--phase','turbo','--run-dir','runs/self','--execute','--server-log',str(run/'deploy/server-turbo-cpubind.log')],cwd=BASE,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
  rc=runner.wait(timeout=remaining(60))
  if rc:raise RuntimeError('Turbo request runner failed')
 rows=[load(run/'records'/(c['request_id']+'.json')) for c in cases if (run/'records'/(c['request_id']+'.json')).exists()]
 logtext=(run/'deploy/server-turbo-cpubind.log').read_text(errors='replace')
 matching=[x for x in logtext.splitlines() if 'MiniMax-H3 Turbo adapter' in x and cfg['turbo']['filename'] in x and '8 denoiser forwards (9 sigma points)' in x and 'rank=128 alpha=8' in x and 'flow_shift=6/3' in x]
 proof=bool(matching) and len(rows)==6 and all(r.get('status')=='validated' and r.get('actual_denoise_updates')==8 for r in rows) and all(json.loads(c['multipart_fields']['lora'])['scale']==1 for c in contract)
 source=Path('/usr/local/lib/python3.12/dist-packages/vllm_omni/diffusion/models/minimax_h3/lora.py')
 if source.exists():(run/'environment/installed-h3-lora-source.py').write_bytes(source.read_bytes())
 dump(run/'environment/turbo-activation-proof.json',{'utc':utcnow(),'activation_verified':proof,'file_sha256':sha256(cfg['turbo']['path']),'file_revision':cfg['turbo']['revision'],'loader_evidence_lines':matching,'request_contract':'environment/turbo-request-contract.json','request_scale':1,'actual_updates_by_request':{r['request_id']:r.get('actual_denoise_updates') for r in rows},'installed_loader_source_sha256':sha256(source) if source.exists() else None,'scope':'Loader logs plus frozen request LoRA name/path/scale, installed parser and completed 8-update traces; no live tensor dump or scale-zero ablation'})
 outcome='six_validated_and_activation_proved' if proof else 'results_require_activation_review'
except Exception as exc:
 error=type(exc).__name__+': '+str(exc)[:600]
 if runner and runner.poll() is None:
  os.killpg(runner.pid,signal.SIGTERM)
  try:runner.wait(timeout=10)
  except subprocess.TimeoutExpired:os.killpg(runner.pid,signal.SIGKILL)
finally:
 (run/'ARCHIVE_STOP_HEALTH_WATCH').touch()
 if service:
  try:stop('server-turbo-cpubind')
  except Exception as exc:error=(error or '')+'; stop '+type(exc).__name__
 dump(run/'TURBO_SESSION_FINISHED.json',{'utc':utcnow(),'wall_seconds':time.monotonic()-start,'outcome':outcome,'error':error,'phase_cap_seconds':2700,'next_action':'Freeze evidence, validate archive and destroy instance; no paid repeat'})
print(json.dumps({'turbo_session_outcome':outcome,'error':error,'elapsed_seconds':time.monotonic()-start}))

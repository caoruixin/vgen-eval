"""Read-only post-stability gate: classify window cutoff, verify health and GPU drain.
No inference POSTs and no paid retry. Run only after STABILITY_FINISHED exists.
"""
import json,platform,subprocess,sys,time,urllib.error,urllib.request
from pathlib import Path
BASE=Path(__file__).resolve().parents[1];sys.path.insert(0,str(BASE/'scripts'))
from common import dump,load,utcnow
if platform.system()!='Linux':raise SystemExit('Server only')
run=BASE/'runs/self'
if not (run/'STABILITY_FINISHED.json').exists():raise SystemExit('Still executing stability')
if (run/'STOP_SUBMISSIONS').exists() or (run/'FATAL_GPU_OR_COMMUNICATION.json').exists():raise SystemExit('A stop condition is active')
rows=[load(p) for p in (run/'records').glob('stability-*.json')];events=[]
for r in rows:
 if r.get('status')=='validated':continue
 response=r.get('cancellation_response')
 expected='阶段时间到' in r.get('error','') and isinstance(response,dict) and (response.get('deleted') is True or response.get('status') in ('cancelled','canceled'))
 if not expected:raise SystemExit('Unresolved non-window stability outcome: '+r['request_id'])
 task=r['task_id'];url='http://127.0.0.1:8091/v1/videos/'+task
 try:
  with urllib.request.urlopen(url,timeout=8) as resp:data=json.load(resp);status=resp.status
 except urllib.error.HTTPError as exc:
  status=exc.code;data=exc.read(16384).decode(errors='replace');exc.close()
 events.append({'utc':utcnow(),'request_id':r['request_id'],'task_id':task,'post_cancel_http_status':status,'post_cancel_response':data})
 if status!=404 and not (isinstance(data,dict) and data.get('status') in ('cancelled','canceled','completed','failed')):raise SystemExit('Cancelled task still active; no new phase')
dump(run/'analysis/stability-transition-query-evidence.json',{'utc':utcnow(),'window_cutoff_followups':events})
start=time.monotonic();idle_since=None;samples=[]
while time.monotonic()-start<180:
 proc=subprocess.run(['nvidia-smi','--query-gpu=index,utilization.gpu,memory.used','--format=csv,noheader,nounits'],capture_output=True,text=True,timeout=10)
 cards=[[float(x.strip()) for x in line.split(',')] for line in proc.stdout.strip().splitlines()] if proc.returncode==0 else []
 samples.append({'utc':utcnow(),'exit_code':proc.returncode,'cards_index_util_memory_mib':cards})
 if len(cards)==8 and all(c[1]<10 for c in cards):
  if idle_since is None:idle_since=time.monotonic()
  if time.monotonic()-idle_since>=20:break
 else:idle_since=None
 time.sleep(3)
else:
 dump(run/'analysis/stability-transition-gate.json',{'utc':utcnow(),'passed':False,'window_cutoff_followups':events,'gpu_drain_observations':samples,'reason':'No 20-second idle interval within 180 seconds; no new inference submitted'})
 raise SystemExit('No 20-second idle interval within 180 seconds; inspect engine rather than adding concurrency')
with urllib.request.urlopen('http://127.0.0.1:8091/health',timeout=8) as resp:healthy=resp.status==200
with urllib.request.urlopen('http://127.0.0.1:8091/v1/videos',timeout=8) as resp:tasks=json.load(resp)
if not isinstance(tasks.get('data'),list):raise SystemExit('Unexpected task-list schema')
active=[x for x in tasks['data'] if x.get('status') in ('queued','pending','running','in_progress')]
result={'utc':utcnow(),'passed':healthy and not active,'active_listed_tasks':active,'drain_wait_seconds':time.monotonic()-start,'required_idle_seconds':20,'window_cutoff_followups':events,'gpu_drain_observations':samples,'read_only_task_list':tasks,'note':'No inference submission or retry; 20-second sampled idle plus health/list as transition evidence, not production availability guarantee'}
dump(run/'analysis/stability-transition-gate.json',result);print(json.dumps({'transition_passed':result['passed'],'window_cutoffs_followed_up':len(events),'drain_wait_seconds':time.monotonic()-start}))
if not result['passed']:raise SystemExit('Health check or drain task-list failed')

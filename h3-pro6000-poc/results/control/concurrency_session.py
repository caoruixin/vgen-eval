"""One bounded C2 then C4 observation after manual stability review; never retries."""
import argparse,json,os,platform,signal,subprocess,sys,time
from pathlib import Path
BASE=Path(__file__).resolve().parents[1];sys.path.insert(0,str(BASE/'scripts'))
from budget import Ledger
from common import dump,load,utcnow
p=argparse.ArgumentParser();p.add_argument('--execute',action='store_true');a=p.parse_args()
if not a.execute:raise SystemExit('Prepared only; --execute needed after manual review')
if platform.system()!='Linux':raise SystemExit('Server only')
run=BASE/'runs/self';gate=run/'analysis/stability-transition-gate.json'
if not gate.exists() or load(gate).get('passed') is not True:raise SystemExit('Review stability outcome and pass read-only transition gate first')
if (run/'STOP_SUBMISSIONS').exists() or (run/'FATAL_GPU_OR_COMMUNICATION.json').exists():raise SystemExit('Stop condition active')
if (run/'CONCURRENCY_SESSION_STARTED.json').exists():raise SystemExit('Existing concurrency session intent; no automatic repeat')
Ledger(BASE/'private/ledger.json').guard(next_gpu_seconds=1800)
start=time.monotonic();deadline=start+1800;dump(run/'CONCURRENCY_SESSION_STARTED.json',{'utc':utcnow(),'max_wall_seconds':1800,'batches':{'concurrency_c2':2,'concurrency_c4':4},'no_retries':True})
runner=None;outcome='failed';error=None
try:
 for phase,n in [('concurrency_c2',2),('concurrency_c4',4)]:
  if (run/'STOP_SUBMISSIONS').exists() or time.monotonic()>=deadline-30:raise RuntimeError('Stop flag or remaining session cap')
  with (run/'deploy'/(phase+'-console.txt')).open('w') as log:
   runner=subprocess.Popen([sys.executable,'scripts/run.py','--phase',phase,'--run-dir','runs/self','--execute','--server-log',str(run/'deploy/server-bf16-cpubind.log')],cwd=BASE,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
   rc=runner.wait(timeout=min(930,deadline-time.monotonic()-20))
  rows=[load(p) for p in (run/'records').glob(phase+'-*.json')]
  if rc or len(rows)!=n or any(x.get('status')!='validated' for x in rows):raise RuntimeError('Incomplete or failed '+phase+'; do not continue stress or automatically retry')
 outcome='c2_and_c4_all_validated'
except Exception as exc:
 error=type(exc).__name__+': '+str(exc)[:600]
 (run/'STOP_SUBMISSIONS').touch()
 if runner and runner.poll() is None:
  os.killpg(runner.pid,signal.SIGTERM)
  try:runner.wait(timeout=8)
  except subprocess.TimeoutExpired:os.killpg(runner.pid,signal.SIGKILL)
 # No further stress after an unresolved batch; preserve accepted task IDs and logs.
 (run/'ARCHIVE_STOP_HEALTH_WATCH').touch()
 server=load(run/'processes/server-bf16-cpubind.json');cmd=Path('/proc')/str(server['pid'])/'cmdline'
 if cmd.exists() and b'vllm' in cmd.read_bytes() and b'/data/models/MiniMax-H3/FL2VA' in cmd.read_bytes():
  try:os.killpg(server['pgid'],signal.SIGTERM)
  except ProcessLookupError:pass
finally:
 dump(run/'CONCURRENCY_SESSION_FINISHED.json',{'utc':utcnow(),'wall_seconds':time.monotonic()-start,'outcome':outcome,'error':error,'max_wall_seconds':1800})
print(json.dumps({'concurrency_session_outcome':outcome,'error':error,'elapsed_seconds':time.monotonic()-start}))

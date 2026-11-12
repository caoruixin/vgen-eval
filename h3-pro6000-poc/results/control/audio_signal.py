"""Server-only objective audio signal check; never assigns human quality/sync scores."""
import argparse,json,math,platform,re,subprocess,sys,time
from pathlib import Path
BASE=Path(__file__).resolve().parents[1];sys.path.insert(0,str(BASE/'scripts'))
from common import dump,load,utcnow
p=argparse.ArgumentParser();p.add_argument('run_dir',type=Path);a=p.parse_args()
if platform.system()!='Linux':raise SystemExit('Media tools execute only on the server')
results=[]
for path in sorted((a.run_dir/'records').glob('*.json')):
 r=load(path)
 if r.get('status')!='validated':continue
 evidence=a.run_dir/'outputs'/r['request_id'];out=evidence/'audio-signal.json'
 if out.exists():results.append(load(out));continue
 start=time.monotonic();command=['ffmpeg','-nostdin','-hide_banner','-i',str(a.run_dir/r['video_path']),'-vn','-af','astats=metadata=0:reset=0','-f','null','-']
 proc=subprocess.run(command,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,timeout=60)
 text=proc.stderr.decode(errors='replace');(evidence/'audio-astats.stderr.txt').write_text(text)
 def levels(name):return re.findall(re.escape(name)+r':\s*(-?inf|[-+\d.]+)',text,re.I)
 rms=levels('RMS level dB');peak=levels('Peak level dB')
 def number(x):
  try:
   v=float(x);return v if math.isfinite(v) else None
  except (TypeError,ValueError):return None
 result={'request_id':r['request_id'],'observed_utc':utcnow(),'ffmpeg_exit_code':proc.returncode,'rms_level_dbfs':number(rms[-1]) if rms else None,'peak_level_dbfs':number(peak[-1]) if peak else None,'all_digital_silence':bool(peak and peak[-1].lower()=='-inf'),'channel_and_overall_rms_raw':rms,'analysis_seconds':time.monotonic()-start,'interpretation':'Objective signal energy only; no audibility, prompt match, speech intelligibility or AV-sync score inferred'}
 dump(out,result);results.append(result)
dump(a.run_dir/'analysis/audio-signal-summary.json',{'utc':utcnow(),'n':len(results),'analysis_failures':sum(x['ffmpeg_exit_code']!=0 for x in results),'all_digital_silence_count':sum(x['all_digital_silence'] for x in results),'manual_quality':'pending user review'})
print('Audio signal evidence recorded for',len(results),'validated outputs; no subjective score generated.')

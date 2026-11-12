"""Read-only analysis of recorded phases, telemetry and fixed matched samples."""
import collections,datetime,json,statistics
from pathlib import Path
from derive_results import ROOT,lines,load,quantile,save,csvsave

def epoch(x):return datetime.datetime.fromisoformat(x.replace('Z','+00:00')).timestamp()

def main():
 run=ROOT/'runs/self';out=ROOT/'reports/data'
 records=[load(p) for p in (run/'records').glob('*.json')]
 api=[load(p) for p in (ROOT/'runs/api/records').glob('*.json')]
 windows=lines(run,'phase-windows.jsonl');telemetry=lines(run,'telemetry/gpu.jsonl')
 samples=[];invalid=0
 for row in telemetry:
  if row.get('exit_code')!=0 or len(row.get('gpu',[]))!=8:invalid+=1;continue
  try:
   cards=[dict(zip(row['fields'],x)) for x in row['gpu']]
   samples.append({'utc':row['utc'],'epoch':epoch(row['utc']),'cards':[{'index':int(c['index']),'memory_mib':float(c['memory.used']),'utilization_pct':float(c['utilization.gpu']),'temperature_c':float(c['temperature.gpu']),'power_w':float(c['power.draw']),'pcie_gen':float(c['pcie.link.gen.current']),'pcie_width':float(c['pcie.link.width.current'])} for c in cards]})
  except (ValueError,KeyError,TypeError):invalid+=1
 phases=[]
 for phase in dict.fromkeys(w['phase'] for w in windows):
  ws=[w for w in windows if w['phase']==phase];bounds=[(epoch(w['start_utc']),epoch(w['end_utc'])) for w in ws]
  ss=[s for s in samples if any(a<=s['epoch']<=b for a,b in bounds)]
  rs=[r for r in records if r['phase']==phase]
  good=[r for r in rs if r.get('status')=='validated']
  inferred=[float(r['server_inference_time_s']) for r in good if r.get('server_inference_time_s') is not None]
  stage=[]
  for r in good:
   if isinstance(r.get('stage_durations'),dict):stage.append(r['stage_durations'])
  result={'phase':phase,'telemetry_samples':len(ss),'valid_results':len(good),'start_utc':min(w['start_utc'] for w in ws),'end_utc':max(w['end_utc'] for w in ws),'window_seconds':sum(w['wall_seconds'] for w in ws),'server_inference_p50_s':quantile(inferred,.5),'server_inference_p95_s':quantile(inferred,.95),'actual_updates_values':sorted(set(r['actual_denoise_updates'] for r in good if r.get('actual_denoise_updates') is not None)),'n_with_attributed_actual_updates':sum(r.get('actual_denoise_updates') is not None for r in good)}
  if ss:
   cards=[c for s in ss for c in s['cards']];util=[statistics.mean(c['utilization_pct'] for c in s['cards']) for s in ss]
   result.update(max_single_gpu_memory_mib=max(c['memory_mib'] for c in cards),max_sampled_8gpu_total_memory_mib=max(sum(c['memory_mib'] for c in s['cards']) for s in ss),max_temperature_c=max(c['temperature_c'] for c in cards),max_sampled_8gpu_power_w=max(sum(c['power_w'] for c in s['cards']) for s in ss),mean_8gpu_utilization_pct=statistics.mean(util),p50_8gpu_utilization_pct=quantile(util,.5),per_card_peak_memory_mib={str(i):max(c['memory_mib'] for c in cards if c['index']==i) for i in range(8)})
  phases.append(result)
 scene=[]
 for case in load(ROOT/'cases/cases.json')['cases']:
  br=sorted([r for r in records if r['phase']=='baseline' and r['case_id']==case['id'] and r.get('status')=='validated'],key=lambda r:r['seed'])
  ar=next((r for r in api if r['case_id']==case['id'] and r.get('status')=='validated'),None)
  if not br:continue
  lat=[r['e2e_seconds'] for r in br];row={'case_id':case['id'],'title':case['title'],'task':case['task'],'baseline_n':len(br),'baseline_p50_s':quantile(lat,.5),'baseline_p95_s':quantile(lat,.95),'baseline_min_s':min(lat),'baseline_max_s':max(lat),'matched_baseline_seed':br[0]['seed'],'matched_baseline_request_id':br[0]['request_id'],'matched_baseline_e2e_s':br[0]['e2e_seconds'],'official_task_id':ar.get('task_id') if ar else None,'official_e2e_s':ar.get('e2e_seconds') if ar else None}
  scene.append(row)
 proof_path=run/'environment/turbo-activation-proof.json';turbo_verified=proof_path.exists() and load(proof_path).get('activation_verified') is True
 matched=[]
 for tr in records:
  if tr['phase']!='turbo' or tr.get('status')!='validated':continue
  br=next((r for r in records if r['phase']=='baseline' and r['case_id']==tr['case_id'] and r['seed']==tr['seed'] and r.get('status')=='validated'),None)
  matched.append({'case_id':tr['case_id'],'task':tr['task'],'seed':tr['seed'],'bf16_e2e_s':br['e2e_seconds'] if br else None,'eight_step_e2e_s':tr['e2e_seconds'],'turbo_activation_verified':turbo_verified,'observed_e2e_speed_ratio':br['e2e_seconds']/tr['e2e_seconds'] if br and turbo_verified else None,'ratio_status':'withheld because activation is unproved' if not turbo_verified else 'observational only; transport and cold start differ','bf16_transport':br.get('transport') if br else None,'eight_step_transport':tr.get('transport'),'bf16_actual_updates':br.get('actual_denoise_updates') if br else None,'eight_step_actual_updates':tr.get('actual_denoise_updates'),'quality_tradeoff':'unmeasured; user review deferred'})
 gaps=[b['epoch']-a['epoch'] for a,b in zip(samples,samples[1:])]
 save(out/'telemetry-summary.json',{'generated_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'valid_samples':len(samples),'invalid_samples':invalid,'first_utc':samples[0]['utc'] if samples else None,'last_utc':samples[-1]['utc'] if samples else None,'median_interval_seconds':statistics.median(gaps) if gaps else None,'max_interval_seconds':max(gaps) if gaps else None,'phase_metrics':phases,'interpretation':'One-second sampled telemetry, not allocator instantaneous peak or wall-plug energy. Eight separate GPU memories; sum is diagnostic only.'})
 csvsave(out/'scene-results.csv',scene);csvsave(out/'turbo-matched.csv',matched)
 print(json.dumps({'telemetry_samples':len(samples),'invalid_samples':invalid,'phases':len(phases),'scenes':len(scene),'turbo_matched':len(matched)}))
if __name__=='__main__':main()

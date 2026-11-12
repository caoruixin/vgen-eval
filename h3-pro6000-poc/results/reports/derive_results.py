"""Derive tables from measured records only. Works with existing local standard library."""
import argparse,collections,csv,datetime,gzip,json,math,statistics
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def load(p):return json.loads(p.read_text())
def stamp():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def quantile(xs,p):
 if not xs:return None
 xs=sorted(xs);i=(len(xs)-1)*p;a=math.floor(i);b=math.ceil(i);return xs[a]+(xs[b]-xs[a])*(i-a)
def lines(run,name):
 path=run/name;snapshot=run/'checkpoints'/(name.replace('/','__')+'.gz')
 if snapshot.exists() and (not path.exists() or snapshot.stat().st_mtime>path.stat().st_mtime):data=gzip.open(snapshot,'rt').read()
 elif path.exists():data=path.read_text()
 else:return []
 values=[]
 for line in data.splitlines():
  if not line.strip():continue
  try:values.append(json.loads(line))
  except json.JSONDecodeError:raise RuntimeError('Incomplete JSONL snapshot; retry after next checkpoint: '+str(path))
 return values
def save(p,obj):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n')
def csvsave(p,rows):
 p.parent.mkdir(parents=True,exist_ok=True)
 if not rows:return
 fields=list(dict.fromkeys(k for r in rows for k in r))
 with p.open('w',newline='',encoding='utf-8-sig') as f:
  w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)
def main():
 out=ROOT/'reports/data';all_rows=[];all_windows=[];raw_records=[]
 proof_path=ROOT/'runs/self/environment/turbo-activation-proof.json';turbo_verified=proof_path.exists() and load(proof_path).get('activation_verified') is True
 for provider,run in [('self',ROOT/'runs/self'),('official_api',ROOT/'runs/api')]:
  for path in sorted((run/'records').glob('*.json')):
   r=load(path);r['_record']=str(path.relative_to(ROOT));r['_source_kind']='latest_logical_request';raw_records.append(r)
  for path in sorted((run/'attempts').glob('*-attempt-[0-9]*.json')):
   r=load(path);r['_record']=str(path.relative_to(ROOT));r['_source_kind']='preserved_previous_submission_attempt';raw_records.append(r)
  all_windows+=lines(run,'phase-windows.jsonl')
 unique={}
 for r in raw_records:unique[(r['provider'],r['request_id'],r.get('attempt',1))]=r
 records=list(unique.values())
 quality={}
 score_lock=ROOT/'quality/locked/score-lock.json';quality_detail=ROOT/'reports/data/human-quality-detail.csv'
 if score_lock.exists() and quality_detail.exists():
  with quality_detail.open(encoding='utf-8-sig',newline='') as qf:
   quality={(r['provider'],r['request_id']):r for r in csv.DictReader(qf)}
 provider_snapshot=ROOT/'runs/self/cost/provider-live-snapshot.json';fields=load(provider_snapshot).get('fields',{}) if provider_snapshot.exists() else {}
 gpu_rate=fields.get('dph_base',13.867);total_rate=fields.get('dph_total',14.006);disk_rate=total_rate-gpu_rate
 groups=collections.defaultdict(list);phase_groups=collections.defaultdict(list)
 for r in records:
  if not r.get('submitted'):continue
  groups[(r['provider'],r['phase'],r['variant'],r['task'],r.get('transport'))].append(r);phase_groups[(r['provider'],r['phase'])].append(r)
 def outcome(r):
  if r.get('status')=='validated':return 'technical_valid'
  if r.get('cancellation_response') and '阶段时间到' in r.get('error',''):
   response=r['cancellation_response']
   return 'test_window_cancelled' if isinstance(response,dict) and (response.get('deleted') is True or response.get('status') in ('cancelled','canceled')) else 'test_window_stop_requested'
  if r.get('confirmed_input_rejection'):return 'confirmed_input_rejection'
  if r.get('status') in ('submitting','submission_intent','accepted','downloaded','downloaded_pending_server_validation'):return 'pending'
  return r.get('status','unknown')
 summaries=[]
 for key,rs in sorted(groups.items(),key=lambda kv:str(kv[0])):
  valid=[r for r in rs if outcome(r)=='technical_valid'];lat=[r['e2e_seconds'] for r in valid if r.get('e2e_seconds') is not None];counts=dict(collections.Counter(outcome(r) for r in rs))
  summaries.append(dict(zip(('provider','phase','variant','task','transport'),key))|{'n_submitted_attempts':len(rs),'n_technical_valid':len(valid),'outcomes':counts,'e2e_p50_s':quantile(lat,.5),'e2e_p95_s':quantile(lat,.95),'e2e_min_s':min(lat) if lat else None,'e2e_max_s':max(lat) if lat else None,'n_human_reviewed':sum((r['provider'],r['request_id']) in quality for r in valid),'deliverable_rate':(sum(quality[(r['provider'],r['request_id'])]['deliverable_yes_no_unsure']=='yes' for r in valid if (r['provider'],r['request_id']) in quality)/sum((r['provider'],r['request_id']) in quality for r in valid)) if any((r['provider'],r['request_id']) in quality for r in valid) else None,'deliverable_rate_scope':'explicitly reviewed subset only; not all requests'})
 phases=[];allocation={}
 for key,rs in sorted(phase_groups.items()):
  valid=[r for r in rs if outcome(r)=='technical_valid'];windows=[w for w in all_windows if (w['provider'],w['phase'])==key];wall=sum(w['wall_seconds'] for w in windows) if windows else None;cost=total_rate*wall/3600 if wall is not None and key[0]=='self' else None
  complete=bool(windows) and not any(outcome(r)=='pending' for r in rs)
  if key[1]=='baseline':complete=complete and sum(r.get('status')=='validated' for r in rs)==42
  phases.append({'provider':key[0],'phase':key[1],'n_submitted_attempts':len(rs),'n_technical_valid':len(valid),'observed_phase_windows':len(windows),'complete_for_requested_matrix':complete,'window_seconds':wall,'throughput_success_per_hour':len(valid)/wall*3600 if wall else None,'phase_gpu_disk_cost_estimate_usd':cost,'cost_per_technical_success_usd':cost/len(valid) if cost is not None and valid else None,'outcomes':dict(collections.Counter(outcome(r) for r in rs))})
  # Allocate each shared window once. Concurrency uses equal allocation; serial uses normalized observed E2E shares.
  total_e2e=sum(r.get('e2e_seconds',0) or 0 for r in valid)
  for r in valid:
   weight=1/len(valid) if key[1].startswith('concurrency') else (r.get('e2e_seconds',0) or 0)/total_e2e if total_e2e else 1/len(valid)
   allocation[(r['provider'],r['request_id'],r.get('attempt',1))]=cost*weight if cost is not None else None
 for r in sorted(records,key=lambda r:(r.get('submitted_utc') or '',r['provider'],r['request_id'],r.get('attempt',1))):
  m=r.get('media',{});cost=allocation.get((r['provider'],r['request_id'],r.get('attempt',1)));seconds=m.get('video_duration_seconds') or (m.get('frames')/m.get('fps') if m.get('frames') and m.get('fps') else None)
  if r['provider']=='official_api':cost=r.get('api_cost_estimate_usd')
  row={k:r.get(k) for k in ('request_id','case_id','task','phase','provider','variant','seed','attempt','status','submitted','submitted_utc','task_id_saved_utc','terminal_utc','downloaded_utc','finished_utc','resumed_utc','task_id','e2e_seconds','submit_to_terminal_seconds','server_inference_time_s','server_peak_memory_mb','poll_interval_seconds','poll_detection_bound_seconds','requested_num_inference_steps','actual_denoise_updates','actual_denoise_updates_source','config_sha256','matrix_sha256','prompt_sha256','asset_sha256','model_revision','prior_attempt_file','video_path','api_reserved_usd','api_cost_estimate_usd','api_cost_source','ledger_reservation_id')}
  row.update({k:m.get(k) for k in ('technical_valid','width','height','fps','frames','duration_seconds','video_duration_seconds','audio_duration_seconds','video_codec','audio_codec','audio_channels','audio_sample_rate','size_bytes','probe_exit_code','decode_exit_code','validation_seconds','sha256')})
  row.update(outcome=outcome(r),http_status=r.get('http_status') or (r.get('http') or {}).get('http_status'),record_path=r['_record'],source_kind=r['_source_kind'],stage_durations=json.dumps(r.get('stage_durations'),ensure_ascii=False),client_recovery_sessions=len(r.get('recovery_history',[])),estimated_cost_usd=cost,estimated_cost_per_video_second_usd=cost/seconds if cost is not None and seconds else None,cost_source='official usage × published price; invoice pending' if r['provider']=='official_api' else 'one shared phase window, normalized allocation; not per-request billing',human_quality_status='pending_user_review',error=r.get('error'))
  versions=r.get('versions',{});self_provider=r['provider']=='self'
  row.update(image=versions.get('image') if self_provider else 'not_disclosed',source_commit=versions.get('source_commit') if self_provider else 'not_disclosed',harness_sha256=versions.get('harness_sha256'),adapter_sha256=versions.get('adapter_sha256'),versions_scope='self runtime and client' if self_provider else r.get('versions_scope','client reference only; API backend not disclosed'),versions_json=json.dumps(versions,ensure_ascii=False),actual_settled_cost_usd=None,currency='USD',prompt_adherence_1to5=None,visual_motion_1to5=None,first_frame_retention_1to5='NA' if r['task']=='t2va' else None,audio_1to5=None,av_sync_1to5=None,deliverable_yes_no_unsure=None)
  delivery_path=ROOT/'runs/control/local-delivery.jsonl'
  if self_provider and r.get('status')=='validated' and delivery_path.exists():
   for event in (json.loads(x) for x in delivery_path.read_text().splitlines() if x.strip()):
    if event.get('event')=='local_original_video_verified' and event.get('request_id')==r['request_id'] and event.get('sha256')==m.get('sha256'):
     row.update(local_complete_filename_seen_utc=event.get('file_seen_complete_name_utc'),local_sha256_verified_utc=event.get('local_sha256_verified_utc'),local_preexisting_at_observer_start=event.get('preexisting_at_observer_start'),local_delivery_scope='archive trace only; inter-host clocks not independently calibrated');break
  row.update(spec_deviations=json.dumps(m.get('spec_deviations'),ensure_ascii=False),validated_utc=m.get('validated_utc'),usage_json=json.dumps(r.get('usage'),ensure_ascii=False),http_download_transfer_seconds=(r.get('http') or {}).get('download_seconds'))
  qr=quality.get((r['provider'],r['request_id'])) if r.get('status')=='validated' else None
  if qr:
   for field in ('prompt_adherence_1to5','visual_motion_1to5','first_frame_retention_1to5','audio_1to5','av_sync_1to5','deliverable_yes_no_unsure','reviewer','reviewed_utc','defect_timestamps','notes'):
    row[field]=qr.get(field) if field!='first_frame_retention_1to5' or r['task']!='t2va' else 'NA'
   row['human_quality_status']='user_review_locked';row['quality_score_lock']='quality/locked/score-lock.json'
  row['turbo_activation_verified']=turbo_verified if r['phase']=='turbo' else None
  row['variant_evidence_scope']='8-step configuration sample; Turbo activation unproved' if r['phase']=='turbo' and not turbo_verified else 'standard BF16' if self_provider else 'official backend undisclosed'
  all_rows.append(row)
 api=[r for r in records if r['provider']=='official_api'];base=[r for r in records if r['phase']=='baseline' and r['provider']=='self' and outcome(r)=='technical_valid'];bp=next((p for p in phases if p['provider']=='self' and p['phase']=='baseline'),None);util=[]
 if bp and bp['complete_for_requested_matrix'] and base:
  for u in (.25,.5,1):util.append({'utilization':u,'observed_baseline_success_interval_s':bp['window_seconds']/len(base),'gpu_disk_cost_per_success_usd':bp['cost_per_technical_success_usd']/u,'interpretation':'Demand utilization of allocated service time, not nvidia-smi utilization; cold start, traffic and human labor excluded'})
 summary={'derived_utc':stamp(),'kind':'observed_data_only','baseline_technical_valid':len(base),'requested_baseline_n':42,'self_total_valid':sum(r['provider']=='self' and outcome(r)=='technical_valid' for r in records),'official_total_valid':sum(r['provider']=='official_api' and outcome(r)=='technical_valid' for r in records),'official_generation_tasks':len({r['task_id'] for r in api if r.get('task_id')}),'official_download_recovery_sessions':sum(len(r.get('recovery_history',[])) for r in api),'official_published_price_estimate_usd':sum(r.get('api_cost_estimate_usd',0) or 0 for r in api),'gpu_hour_rate_usd':gpu_rate,'disk_hour_rate_usd':disk_rate,'gpu_disk_hour_rate_usd':total_rate,'price_source':'instance provider snapshot' if fields else 'conservative checkout quote','groups':summaries,'phases':phases,'utilization':util,'quality':'Only explicitly locked user scores joined by provider/request_id; missing scores remain blank','quantile_method':'Linear interpolation at (n-1)*p; small n P95 preliminary','data_freshness':'Local snapshots may lag while server execution is active; final report requires archive verification and release evidence'}
 summary['turbo_activation_verified']=turbo_verified
 summary['data_freshness']='Frozen server archive verified locally; independent release confirmed' if (ROOT/'runs/control/release-confirmed.json').exists() else summary['data_freshness']
 for group in summaries+phases:
  group['turbo_activation_verified']=turbo_verified if group['phase']=='turbo' else None
 save(out/'observed-summary.json',summary);csvsave(out/'all-request-attempts.csv',all_rows);csvsave(out/'latency-groups.csv',[{**x,'outcomes':json.dumps(x['outcomes'])} for x in summaries]);csvsave(out/'phase-windows-and-costs.csv',[{**x,'outcomes':json.dumps(x['outcomes'])} for x in phases]);csvsave(out/'utilization-costs.csv',util)
 print(json.dumps({k:summary[k] for k in ('derived_utc','baseline_technical_valid','self_total_valid','official_total_valid','official_generation_tasks','official_download_recovery_sessions')},ensure_ascii=False))
if __name__=='__main__':main()

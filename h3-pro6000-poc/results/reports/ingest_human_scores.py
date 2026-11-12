"""Validate and lock an explicitly user-completed A/B score file, then unblind.
Never run without a matching attestation of the user's completion message.
"""
import argparse,collections,csv,datetime,hashlib,json,shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def load(p):return json.loads(p.read_text())
def main():
 p=argparse.ArgumentParser();p.add_argument('--scores',type=Path,required=True);p.add_argument('--attestation',type=Path,required=True);a=p.parse_args()
 att=load(a.attestation)
 if att.get('user_explicitly_completed_review') is not True or att.get('scores_sha256')!=sha(a.scores) or not att.get('user_message_quote'):raise SystemExit('Missing explicit user completion linked to this file hash')
 locked=ROOT/'quality/locked';locked.mkdir(exist_ok=True)
 if (locked/'score-lock.json').exists():raise SystemExit('Scores already locked; preserve the existing review and investigate revisions separately')
 if a.scores.suffix=='.csv':rows=list(csv.DictReader(a.scores.open(encoding='utf-8-sig',newline='')))
 else:
  data=load(a.scores);rows=data.get('rows') if isinstance(data,dict) else data
 mapping=load(ROOT/'private/blind-review-blind-key.json')['mapping'];keys={(m['case_id'],m['label']) for m in mapping};seen=[]
 dims=['prompt_adherence_1to5','visual_motion_1to5','first_frame_retention_1to5','audio_1to5','av_sync_1to5'];normalized=[]
 for row in rows:
  k=(row.get('case_id'),row.get('label'))
  if k not in keys or k in seen:raise SystemExit('Unknown or duplicate case/label')
  seen.append(k);r=dict(row)
  for d in dims:
   v=str(row.get(d,'')).strip()
   if d=='first_frame_retention_1to5' and k[0].startswith('T'):
    if v not in ('NA','N/A',''):raise SystemExit('T2VA first-frame score must be not applicable')
    r[d]=None;continue
   if v not in ('1','2','3','4','5'):raise SystemExit('Missing/invalid score: '+str(k)+' '+d)
   r[d]=int(v)
  if row.get('deliverable_yes_no_unsure') not in ('yes','no','unsure'):raise SystemExit('Missing deliverability judgement')
  source=next(m for m in mapping if (m['case_id'],m['label'])==k)
  clip=ROOT/'quality/blind-review'/(k[0]+'-'+k[1]+'.mp4')
  if sha(clip)!=source['video_sha256']:raise SystemExit('Original reviewed clip hash changed')
  r.update(source);r['reviewer']=r.get('reviewer') or '项目发起人（按明确完成确认）';normalized.append(r)
 if set(seen)!=keys or len(rows)!=20:raise SystemExit('Review is not complete for all 20 fixed clips; do not fabricate missing ratings')
 stamp=datetime.datetime.now(datetime.timezone.utc).isoformat();copy=locked/('scores-original'+a.scores.suffix);shutil.copyfile(a.scores,copy)
 lock={'locked_utc':stamp,'source_sha256':sha(a.scores),'source_copy':str(copy.relative_to(ROOT)),'attestation_sha256':sha(a.attestation),'n_reviewers':1,'n_clips':20,'scope':'Ten self baseline fixed-minimum-seed representatives and all ten official outputs; no extrapolation to all baseline seeds'}
 (locked/'score-lock.json').write_text(json.dumps(lock,ensure_ascii=False,indent=2)+'\n')
 with (ROOT/'reports/data/human-quality-detail.csv').open('w',encoding='utf-8-sig',newline='') as f:
  fields=list(dict.fromkeys(k for r in normalized for k in r));w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(normalized)
 groups=[]
 for provider in ('self','official_api'):
  rs=[r for r in normalized if r['provider']==provider];counts=dict(collections.Counter(r['deliverable_yes_no_unsure'] for r in rs));means={d:sum(r[d] for r in rs if r[d] is not None)/sum(r[d] is not None for r in rs) for d in dims}
  groups.append({'provider':provider,'n':len(rs),'deliverability_counts':counts,'explicit_yes_fraction':counts.get('yes',0)/len(rs),'dimension_means':means})
 header=['组别','n','遵循','画面运动','首帧保持','音频','同步','可交付是/否/不确定'];table=['| '+' | '.join(header)+' |','| '+' | '.join(['---']*len(header))+' |']
 for g in groups:
  c=g['deliverability_counts'];table.append('| '+' | '.join([{'self':'自建BF16','official_api':'官方API'}[g['provider']],str(g['n']),*[f'{g["dimension_means"][d]:.2f}' for d in dims],'/'.join(str(c.get(k,0)) for k in ('yes','no','unsure'))])+' |')
 text='首位人工评审为项目发起人。评分原件已按SHA256锁定后解盲，共20条原片、10个场景；每组10条。首帧保持仅统计4个FL2VA样本，其他维度每组10条。\n\n'+'\n'.join(table)+'\n\n该结果只描述这10个场景的固定代表样本，不能推断全部42个自建seed的可交付率；不确定判断独立保留，未伪装为成功或失败。'
 out={'generated_utc':stamp,'score_lock':lock,'groups':groups,'report_markdown':text,'all_poc_cost_per_deliverable':'missing: not all self-generated outputs were subjectively reviewed'}
 (ROOT/'reports/data/human-quality-summary.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n');print('Explicit human score file locked and unblinded:',len(normalized),'clips, one reviewer.')
if __name__=='__main__':main()

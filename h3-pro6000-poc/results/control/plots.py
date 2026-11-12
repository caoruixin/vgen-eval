"""Server-only publication figures from measured files. Install plot tools after GPU tests."""
import collections,datetime,hashlib,json,math,platform,sys
from pathlib import Path
BASE=Path(__file__).resolve().parents[1]
if platform.system()!='Linux':raise SystemExit('Plot environment stays on the server')
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties,fontManager
FONT=Path('/data/h3-report-tools/NotoSansCJKsc-Regular.otf')
if FONT.is_file():
 fontManager.addfont(str(FONT));plt.rcParams['font.sans-serif']=[FontProperties(fname=str(FONT)).get_name(),'DejaVu Sans']
plt.rcParams.update({'font.size':9.5,'axes.unicode_minus':False,'axes.spines.top':False,'axes.spines.right':False,'savefig.dpi':300})
SELF=BASE/'runs/self';API=BASE/'runs/api';OUT=SELF/'analysis/figures';OUT.mkdir(parents=True,exist_ok=True)
def read(p):return json.loads(p.read_text())
def epoch(t):return datetime.datetime.fromisoformat(t.replace('Z','+00:00')).timestamp()
def records(folder):return [read(p) for p in (folder/'records').glob('*.json')]
def jsonl(p):return [json.loads(x) for x in p.read_text().splitlines() if x.strip()] if p.exists() else []
def save(fig,name):fig.savefig(OUT/name,bbox_inches='tight',facecolor='white');plt.close(fig)
rs=records(SELF);ar=records(API);base=[r for r in rs if r['phase']=='baseline' and r['status']=='validated'];official=[r for r in ar if r['status']=='validated'];turbo=[r for r in rs if r['phase']=='turbo' and r['status']=='validated'];proof=SELF/'environment/turbo-activation-proof.json';turbo=turbo if proof.is_file() and read(proof).get('activation_verified') is True else [];scenes=[f'T{i:02d}' for i in range(1,7)]+[f'F{i:02d}' for i in range(1,5)];positions={s:i for i,s in enumerate(scenes)}
fig,ax=plt.subplots(figsize=(6.4,3.6));first=True
for scene in scenes:
 rows=sorted([r for r in base if r['case_id']==scene],key=lambda r:r.get('seed') or 0)
 for j,r in enumerate(rows):
  jitter=(j-(len(rows)-1)/2)*.055;ax.scatter(positions[scene]+jitter,r['e2e_seconds'],s=18,color='#2B5686',alpha=.8,label='自建 BF16' if first else None);first=False
if official:ax.scatter([positions[r['case_id']]+.22 for r in official],[r['e2e_seconds'] for r in official],marker='D',s=29,color='#C86B24',label='官方 API')
if turbo:ax.scatter([positions[r['case_id']]-.25 for r in turbo],[r['e2e_seconds'] for r in turbo],marker='^',s=35,color='#2A7F66',label='Turbo 8步')
recovered=next((r for r in official if r.get('recovery_history')),None)
if recovered:ax.annotate('补下载计入交付时延',(positions[recovered['case_id']]+.22,recovered['e2e_seconds']),xytext=(0,12),textcoords='offset points',ha='center',fontsize=8)
ax.axvline(5.5,color='#C4CDD5',lw=.8);ax.set_xticks(range(10),scenes);ax.set_ylabel('各自客户端提交至下载 秒');ax.set_ylim(bottom=0);ax.grid(axis='y',alpha=.22);ax.set_title(f'逐场景真实交付时延  BF16 n={len(base)}  API n={len(official)}');ax.legend(loc='upper left',ncol=3,fontsize=8,frameon=False);fig.tight_layout();save(fig,'latency-by-scene.png')
windows=jsonl(SELF/'phase-windows.jsonl');rental=read(BASE/'private/rental.json');start=epoch(rental['started_utc']);bins={};capacity=97887/1024
for r in jsonl(SELF/'telemetry/gpu.jsonl'):
 if r.get('exit_code') or len(r.get('gpu',[]))!=8:continue
 ds=[dict(zip(r['fields'],row)) for row in r['gpu']]
 try:mem=[float(d['memory.used'])/1024 for d in ds];util=sum(float(d['utilization.gpu']) for d in ds)/8
 except (KeyError,ValueError):continue
 key=int((epoch(r['utc'])-start)//10);b=bins.setdefault(key,{'lo':min(mem),'hi':max(mem),'util':[]});b['lo']=min(b['lo'],min(mem));b['hi']=max(b['hi'],max(mem));b['util'].append(util)
if bins:
 keys=sorted(bins);x=[(k*10+5)/60 for k in keys];fig,(ax,strip)=plt.subplots(2,1,figsize=(6.4,4),sharex=True,gridspec_kw={'height_ratios':[5,.6],'hspace':.08});ax.fill_between(x,[bins[k]['lo'] for k in keys],[bins[k]['hi'] for k in keys],color='#A9C6E1',alpha=.55,label='八卡采样最小至最大');ax.plot(x,[bins[k]['hi'] for k in keys],color='#2B5686',lw=1,label='单卡最大占用');ax.axhline(capacity,color='#777777',ls=':',lw=.8,label='单卡可见容量');ax.set_ylabel('NVML 显存占用 GiB');ax.set_ylim(0,capacity*1.04);ax.grid(axis='y',alpha=.2);right=ax.twinx();right.plot(x,[sum(bins[k]['util'])/len(bins[k]['util']) for k in keys],color='#C86B24',alpha=.55,lw=.7);right.set_ylim(0,105);right.set_ylabel('八卡平均 GPU 忙碌率 %');ax.legend(loc='upper center',fontsize=7.5,ncol=3,frameon=False);ax.set_title('GPU 遥测  1秒采样和10秒包络');strip.set_yticks([]);strip.set_ylim(0,1)
 names={'warmup':'预热','baseline':'BF16','stability':'稳定性','concurrency_c2':'C2','concurrency_c4':'C4','turbo':'8步试验'};colors={'warmup':'#A9C6E1','baseline':'#2B5686','stability':'#6A8E72','concurrency_c2':'#D79B59','concurrency_c4':'#B77C50','turbo':'#2A7F66'}
 for phase in names:
  ws=[w for w in windows if w['phase']==phase]
  if not ws:continue
  lo=min(epoch(w['start_utc']) for w in ws);hi=max(epoch(w['end_utc']) for w in ws);a=(lo-start)/60;b=(hi-start)/60;strip.axvspan(a,b,color=colors[phase],alpha=.55);strip.text((a+b)/2,.5,names[phase],ha='center',va='center',fontsize=7)
 strip.set_xlabel('自创建意图起 分钟');strip.set_xlim(0,max(x));fig.subplots_adjust(left=.12,right=.87,bottom=.13,top=.88);save(fig,'gpu-telemetry.png')
phase=[w for w in windows if w['phase']=='baseline'];wall=sum(w['wall_seconds'] for w in phase)
provider=read(SELF/'cost/provider-live-snapshot.json').get('fields',{});rate=provider.get('dph_total',14.006)
calc={'baseline_n':len(base),'baseline_window_seconds':wall,'gpu_disk_hour_rate_usd':rate}
if base and wall:
 steady=rate*wall/3600/len(base);first=min(epoch(r['submitted_utc']) for r in base);cold=max(0,first-start)*rate/3600;us=[.25,.5,1];values=[steady/u for u in us];amort=[cold/100]*3
 fig,ax=plt.subplots(figsize=(6.4,3.4));ax.bar(range(3),values,width=.55,color='#2B5686',label='GPU和磁盘稳态估算');ax.bar(range(3),amort,bottom=values,width=.55,color='#A9C6E1',label='一次启动分摊100条 示例');api_price=sum(r.get('api_cost_estimate_usd',0) or 0 for r in official)/len(official) if official else .4;ax.axhline(api_price,color='#C86B24',ls='--',label='官方 API 标价估算')
 for i,v in enumerate(values):ax.text(i,v+amort[i]+.035,f'${v+amort[i]:.3f}',ha='center',fontsize=9)
 ax.set_xticks(range(3),['25%','50%','100%']);ax.set_xlabel('需求利用率 不等同于GPU忙碌率');ax.set_ylabel('美元 每个技术有效输出');ax.set_ylim(0,max(v+a for v,a in zip(values,amort))*1.23);ax.grid(axis='y',alpha=.2);ax.set_axisbelow(True);ax.legend(fontsize=7.8,loc='upper right',frameon=False);ax.set_title('按实测基线推导的成本情景');fig.tight_layout();save(fig,'utilization-cost.png');calc.update(steady_gpu_disk_cost_per_success=steady,cold_start_gpu_disk_cost_estimate=cold,cold_start_boundary='creation intent through first baseline submission; includes warmup and setup failures',utilization=us,cost_values=values,cold_amortization_example_results=100,api_list_price_estimate_per_result=api_price)
source={}
for folder in (SELF,API):
 for p in (folder/'records').glob('*.json'):source[str(p.relative_to(BASE))]=hashlib.sha256(p.read_bytes()).hexdigest()
(OUT/'figure-data-and-provenance.json').write_text(json.dumps({'generated_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'record_sha256':source,'latencies':[{'request_id':r['request_id'],'phase':r['phase'],'e2e_seconds':r.get('e2e_seconds')} for r in base+official+turbo],'cost_calculation':calc,'telemetry_note':'Full one-second raw data retained; plotted10-second min/max envelopes preserve sampled extrema. GPU memories are separate.'},ensure_ascii=False,indent=2))
print('Figures from observed files only:',[p.name for p in OUT.glob('*.png')])

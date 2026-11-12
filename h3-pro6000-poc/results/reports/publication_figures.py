"""Publication SVGs from the verified archive, using only existing stdlib.

This adjusts document figure layout after the server is destroyed. It neither
installs a runtime nor imports H3, Torch, media tools or any model weights.
The original server plots remain unchanged in runs/self/analysis/figures.
"""
import datetime,hashlib,html,json,statistics
from pathlib import Path
from derive_results import ROOT,load,lines

OUT=ROOT/'reports/publication-figures';OUT.mkdir(exist_ok=True)
BLUE='#2B5686';ORANGE='#C86B24';LIGHT='#A9C6E1';GRAY='#DCE2E8'

class Plot:
 def __init__(self,w,h,title):
  self.w=w;self.h=h;self.s=[f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">', '<rect width="100%" height="100%" fill="white"/>','<g font-family="Arial,Hiragino Sans GB,sans-serif" fill="#161B22">'];self.text(80,55,title,38)
 def add(self,s):self.s.append(s)
 def text(self,x,y,t,size=26,anchor='start',rotate=None,color='#161B22'):
  tr=f' transform="rotate({rotate} {x} {y})"' if rotate is not None else ''
  self.add(f'<text x="{x:.2f}" y="{y:.2f}" font-size="{size}" text-anchor="{anchor}" fill="{color}"{tr}>{html.escape(str(t))}</text>')
 def line(self,x1,y1,x2,y2,color=GRAY,width=1.8,dash=None):
  self.add(f'<line x1="{x1:.2f}" y1="{y1:.2f}" x2="{x2:.2f}" y2="{y2:.2f}" stroke="{color}" stroke-width="{width}"'+(f' stroke-dasharray="{dash}"' if dash else '')+'/>')
 def rect(self,x,y,w,h,color):self.add(f'<rect x="{x:.2f}" y="{y:.2f}" width="{w:.2f}" height="{h:.2f}" fill="{color}"/>')
 def circle(self,x,y,r,color):self.add(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="{r}" fill="{color}" opacity="0.8"/>')
 def diamond(self,x,y,r,color):self.add(f'<path d="M {x} {y-r} L {x+r} {y} L {x} {y+r} L {x-r} {y} Z" fill="{color}"/>')
 def path(self,points,color,width=2,fill='none',close=False):
  if points:self.add('<path d="M '+' L '.join(f'{x:.2f} {y:.2f}' for x,y in points)+(' Z' if close else '')+f'" fill="{fill}" stroke="{color}" stroke-width="{width}"/>')
 def save(self,name):
  path=OUT/(name+'.svg');path.write_text('\n'.join(self.s+['</g></svg>']));return path

def records(p):return [load(x) for x in (p/'records').glob('*.json')]
def epoch(t):return datetime.datetime.fromisoformat(t.replace('Z','+00:00')).timestamp()
def axes(p,left,top,right,bottom,ymax,ticks, ylabel, xlabels):
 for v in ticks:
  y=bottom-(bottom-top)*v/ymax;p.line(left,y,right,y);p.text(left-22,y+9,f'{v:g}',26,'end')
 p.line(left,top,left,bottom,'#31363B',2.2);p.line(left,bottom,right,bottom,'#31363B',2.2)
 p.text(42,(top+bottom)/2,ylabel,27,'middle',-90)
 for x,label in xlabels:p.line(x,bottom,x,bottom+10,'#31363B',2);p.text(x,bottom+49,label,27,'middle')

def main():
 run=ROOT/'runs/self';rs=records(run);api=records(ROOT/'runs/api');base=[r for r in rs if r['phase']=='baseline' and r['status']=='validated'];official=[r for r in api if r['status']=='validated']
 assert len(base)==42 and len(official)==10 and load(run/'cost/archive-verified.json')['passed']
 scenes=[f'T{i:02}' for i in range(1,7)]+[f'F{i:02}' for i in range(1,5)];idx={s:i for i,s in enumerate(scenes)}
 p=Plot(1600,900,'逐场景交付时延  BF16 n=42  官方 API n=10')
 l,t,r,b=155,165,1520,760;x=lambda v:l+55+(r-l-110)*v/9;y=lambda v:b-(b-t)*v/320
 p.circle(170,110,9,BLUE);p.text(194,120,'自建 BF16',27);p.diamond(450,110,11,ORANGE);p.text(478,120,'官方 API',27)
 axes(p,l,t,r,b,320,range(0,301,50),'客户端提交至完整下载 秒',[(x(i),s) for i,s in enumerate(scenes)])
 p.line((x(5)+x(6))/2,t,(x(5)+x(6))/2,b,'#BAC4CF',2)
 for s in scenes:
  rows=sorted([z for z in base if z['case_id']==s],key=lambda z:z['seed'])
  for j,z in enumerate(rows):p.circle(x(idx[s]+(j-(len(rows)-1)/2)*.055),y(z['e2e_seconds']),7,BLUE)
 for z in official:p.diamond(x(idx[z['case_id']]+.20),y(z['e2e_seconds']),11,ORANGE)
 z=next(z for z in official if z['case_id']=='T04');px,py=x(3.2),y(z['e2e_seconds']);p.line(px+15,py-4,px+75,py-35,ORANGE,1.5);p.text(px+83,py-32,'T04 含原任务补下载',25)
 p.text(800,862,'业务场景  两方下载终点及网络路径不同',26,'middle');p.save('latency-by-scene')

 original=load(run/'analysis/figures/figure-data-and-provenance.json');calc=original['cost_calculation'];rental=load(ROOT/'reports/data/rental-public.json');start=epoch(rental['started_utc']);bins={}
 for z in lines(run,'telemetry/gpu.jsonl'):
  if z.get('exit_code') or len(z.get('gpu',[]))!=8:continue
  cards=[dict(zip(z['fields'],a)) for a in z['gpu']];mem=[float(c['memory.used'])/1024 for c in cards];u=statistics.mean(float(c['utilization.gpu']) for c in cards);k=int((epoch(z['utc'])-start)//10);a=bins.setdefault(k,{'lo':min(mem),'hi':max(mem),'u':[]});a['lo']=min(a['lo'],min(mem));a['hi']=max(a['hi'],max(mem));a['u'].append(u)
 keys=sorted(bins);xmax=max(270,(max(keys)*10+5)/60);p=Plot(1600,980,'GPU 遥测  1秒原始采样与10秒包络')
 l,t,r,b=155,170,1440,710;x=lambda v:l+(r-l)*v/xmax;ym=lambda v:b-(b-t)*v/100;yu=lambda v:b-(b-t)*v/105
 p.rect(155,95,40,16,LIGHT);p.text(211,115,'八卡显存范围',25);p.line(535,103,580,103,BLUE,3);p.text(596,115,'单卡最大显存',25);p.line(925,103,970,103,ORANGE,2);p.text(986,115,'八卡平均忙碌率',25)
 axes(p,l,t,r,b,100,[0,20,40,60,80,100],'NVML 显存占用 GiB',[(x(v),str(v)) for v in range(0,251,50)])
 xx=[(k*10+5)/60 for k in keys];band=[(x(v),ym(bins[k]['lo'])) for v,k in zip(xx,keys)]+[(x(v),ym(bins[k]['hi'])) for v,k in reversed(list(zip(xx,keys)))];p.path(band,'none',0,LIGHT,True)
 p.path([(x(v),ym(bins[k]['hi'])) for v,k in zip(xx,keys)],BLUE,2.4);p.path([(x(v),yu(statistics.mean(bins[k]['u']))) for v,k in zip(xx,keys)],ORANGE,1.6)
 capacity=97887/1024;p.line(l,ym(capacity),r,ym(capacity),'#747D87',1.8,'5 7');p.text(l+20,150,'单卡容量 '+f'{capacity:.2f} GiB',23,color='#55616D')
 for v in [0,20,40,60,80,100]:p.text(r+20,yu(v)+9,str(v),26)
 p.text(1545,(t+b)/2,'八卡平均 GPU 忙碌率 %',27,'middle',-90)
 windows=lines(run,'phase-windows.jsonl');colors=['#AAC4DB','#7F9CB9','#A9BEAD','#E0B483','#C59375','#77AB9A'];names=['warmup','baseline','stability','concurrency_c2','concurrency_c4','turbo'];legend=[]
 for j,phase in enumerate(names):
  ws=[w for w in windows if w['phase']==phase]
  if not ws:continue
  a=(min(epoch(w['start_utc']) for w in ws)-start)/60;c=(max(epoch(w['end_utc']) for w in ws)-start)/60;p.rect(x(a),790,x(c)-x(a),42,colors[j]);p.text((x(a)+x(c))/2,820,chr(65+j),24,'middle')
 p.text(800,882,'自创建意图起 分钟',27,'middle');p.text(155,940,'A 预热    B BF16基线    C 稳定性    D C2    E C4    F 8步配置试验',25);p.save('gpu-telemetry')

 p=Plot(1600,880,'按实测基线推导的利用率成本情景');l,t,r,b=155,175,1520,725;us=calc['utilization'];values=calc['cost_values'];amort=calc['cold_start_gpu_disk_cost_estimate']/100;api_price=calc['api_list_price_estimate_per_result'];ym=lambda v:b-(b-t)*v/3.6;centers=[390,835,1280]
 p.rect(155,92,42,19,BLUE);p.text(215,114,'GPU加磁盘稳态估算',25);p.rect(650,92,42,19,LIGHT);p.text(710,114,'一次启动分摊100条 示例',25);p.line(1160,103,1205,103,ORANGE,2,'9 7');p.text(1220,114,'API标价估算',25)
 axes(p,l,t,r,b,3.6,[0,.5,1,1.5,2,2.5,3,3.5],'美元 每个技术有效输出',[(c,f'{u:.0%}') for c,u in zip(centers,us)])
 for c,v in zip(centers,values):p.rect(c-105,ym(v),210,b-ym(v),BLUE);p.rect(c-105,ym(v+amort),210,ym(v)-ym(v+amort),LIGHT);p.text(c,ym(v+amort)-22,f'${v+amort:.3f}',29,'middle')
 p.line(l,ym(api_price),r,ym(api_price),ORANGE,2.4,'10 8');p.text(800,835,'需求利用率  不等同于 GPU 忙碌率',27,'middle');p.save('utilization-cost')
 source={str(z.relative_to(ROOT)):hashlib.sha256(z.read_bytes()).hexdigest() for folder in [run,ROOT/'runs/api'] for z in (folder/'records').glob('*.json')}
 meta={'generated_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'purpose':'Publication layout from verified archived data; original server figures preserved','no_local_environment_installation':True,'no_model_access':True,'source_record_sha256':source,'original_server_figure_provenance_sha256':hashlib.sha256((run/'analysis/figures/figure-data-and-provenance.json').read_bytes()).hexdigest(),'cost_calculation':calc,'turbo_activation_verified':False,'turbo_not_in_latency_comparison':True,'telemetry':'Original1s samples; 10s min/max envelopes preserve sampled extrema','svg_source_sha256':{z.name:hashlib.sha256(z.read_bytes()).hexdigest() for z in OUT.glob('*.svg')}}
 (OUT/'figure-data-and-provenance.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2)+'\n');print('Publication SVGs created from verified data; no installations or model files accessed.')

if __name__=='__main__':main()

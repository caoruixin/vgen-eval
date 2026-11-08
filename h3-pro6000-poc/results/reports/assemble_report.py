"""Assemble measured Chinese report from frozen evidence; no inferred quality scores."""
import argparse,collections,datetime,json,shutil,statistics
from pathlib import Path
from derive_results import ROOT,load,quantile,lines

def table(headers,rows):
 return '\n'.join(['| '+' | '.join(headers)+' |','| '+' | '.join(['---']*len(headers))+' |']+['| '+' | '.join(str(x) for x in r)+' |' for r in rows])
def f(value,n=2):return '缺失' if value is None else f'{float(value):.{n}f}'
PHASES={'warmup':'预热','baseline':'BF16基线','stability':'独立稳定性','concurrency_c2':'并发C2','concurrency_c4':'并发C4','turbo':'8步配置试验'}
OUTCOMES={'technical_valid':'技术有效','confirmed_input_rejection':'输入拒绝','test_window_cancelled':'窗口主动取消','test_window_stop_requested':'窗口停止待核实','pending':'进行中','failed':'失败','invalid_media':'媒体无效','ambiguous_submission':'提交待核实'}
def rental_info():
 p=ROOT/'private/rental.json';return load(p if p.exists() else ROOT/'reports/data/rental-public.json')
def records(folder):return [load(p) for p in (folder/'records').glob('*.json')]
def epoch(x):return datetime.datetime.fromisoformat(x.replace('Z','+00:00')).timestamp()
def stats(rs):
 xs=[r['e2e_seconds'] for r in rs if r.get('status')=='validated' and r.get('e2e_seconds') is not None]
 return [str(len(xs)),f(quantile(xs,.5)),f(quantile(xs,.95)),f(min(xs) if xs else None)+'–'+f(max(xs) if xs else None)]
def main():
 p=argparse.ArgumentParser();p.add_argument('--draft',action='store_true');a=p.parse_args()
 run=ROOT/'runs/self';summary=load(ROOT/'reports/data/observed-summary.json');tele=load(ROOT/'reports/data/telemetry-summary.json');selfrows=records(run);api=records(ROOT/'runs/api');base=[r for r in selfrows if r['phase']=='baseline' and r['status']=='validated'];costpath=ROOT/'reports/data/cost-reconciliation.json';cost=load(costpath) if costpath.exists() else {};releasepath=ROOT/'runs/control/release-confirmed.json';release=load(releasepath) if releasepath.exists() else {};verified=load(run/'cost/archive-verified.json') if (run/'cost/archive-verified.json').exists() else {}
 if not a.draft and (len(base)!=42 or not verified.get('passed') or release.get('instance_destroyed_confirmed') is not True or release.get('unneeded_storage_remaining') is not False):raise SystemExit('Final report requires complete baseline, verified local archive and independently confirmed instance/storage release')
 out=ROOT/('reports/working/draft' if a.draft else 'reports/final');out.mkdir(parents=True,exist_ok=True)
 sections=[];add=sections.append
 add('# MiniMax H3 八卡 RTX PRO 6000 实测报告')
 add(('工作稿：实验仍在执行，以下仅为已观测数据。' if a.draft else '实测日期：2026年11月7日。')+'\n\n自费 PoC；T2VA 文生有声视频与首帧 FL2VA；官方国际站 API 业务对照。金额均为美元，时间轴统一使用 UTC；当日洛杉矶当地时间为 UTC−8。')
 add('## 决策摘要')
 add('本轮已在单机八张完整 RTX PRO 6000 Blackwell Server Edition 上运行固定 vLLM Omni 0.32.0 与 MiniMax H3。模型和推理环境仅在租用服务器准备，笔记本未安装 H3 环境、未下载或中转模型权重。标准 BF16 基线实际完成 '+str(len(base))+'/42 条技术有效视频；官方 API 完成10/10，首轮完整下载9/10，另一次沿用原任务补下载。')
 add(table(['组别','有效 n','P50 秒','P95 秒','范围 秒'],[['BF16 T2VA',*stats([r for r in base if r['task']=='t2va'])],['BF16 FL2VA',*stats([r for r in base if r['task']=='fl2va'])],['官方API 全场景',*stats(api)]]))
 add('技术有效指 HTTP/文件格式检查、实际规格检查和完整音视频解码通过，不等于业务可交付。人工提示词遵循、运动、首帧保持、音频和同步评分按实际用户评审填写；尚未评分的部分明确保留缺失。自建延迟的下载终点位于服务器，API 的下载终点位于笔记本，两者网络路径不同。')
 bp=next((p for p in summary['phases'] if p['provider']=='self' and p['phase']=='baseline' and p['complete_for_requested_matrix']),None)
 if bp:
  add('按完整基线窗口核算，稳态技术有效输出间隔为 '+f(bp['window_seconds']/len(base))+' 秒，GPU 加已分配磁盘估算为每条 $'+f(bp['cost_per_technical_success_usd'],3)+'。API 按返回的50个计费秒和执行时标价估算为10条 $4.00，约每条 $0.40。BF16当前配方的直接资源成本高于API标价估算，整体交付时延也更长；但两方测量路径不同。当前数据不支持仅为降本扩大自建。人工质量待补，Turbo激活未证实；是否继续还需结合质量和数据部署需求。')
 add(('本次自建Vast余额支出观测 $'+f(cost.get('vast_observed_credit_decrease_usd'))+'，加API标价估算后合计 $'+f(cost.get('whole_poc_observation_plus_api_estimate_usd'))+'，实际API结算待核实。' if cost else '')+'充值 $120 是资金存入，不能当作全部消耗。实例释放状态：'+('已独立确认销毁，且没有本轮不再需要的存储。' if release.get('instance_destroyed_confirmed') else '尚在执行，未释放。'))
 methods=(ROOT/'reports/working/environment-methods.md').read_text();add(methods)
 add('## 执行时间轴与止损检查')
 timeline=[]
 def event(label,t,note):
  if t:timeline.append([label,datetime.datetime.fromisoformat(t.replace('Z','+00:00')).strftime('%H:%M:%S'),note])
 firstapi=next((r for r in api if r['case_id']=='T01'),None)
 if firstapi:event('首条官方API下载',firstapi.get('downloaded_utc'),'生成验证先于GPU租赁，计入10条对照')
 rental=rental_info();event('创建及预算计费起点',rental['started_utc'],'按创建意图计时，含所有部署与等待')
 hw=load(run/'HARDWARE_ACCEPTED.json');event('八卡硬件及通信验收',hw['utc'],'距创建'+f((epoch(hw['utc'])-epoch(rental['started_utc']))/60)+'分钟，20分钟止损内通过')
 dl=load(run/'environment/model-files-verified.json');event('模型下载与校验完成',dl.get('finished_utc'),'仅服务器FL2VA，81文件，耗时'+f(dl.get('wall_seconds'))+'秒')
 fv=load(run/'FIRST_VALID_SELF_VIDEO.json');event('首份自建视频完整验证',fv['utc'],'距创建'+f((epoch(fv['utc'])-epoch(rental['started_utc']))/60)+'分钟，120分钟止损内通过')
 for file,label in [('BASELINE_COMPLETE.json','主基线42条完成'),('STABILITY_FINISHED.json','独立稳定性结束'),('CONCURRENCY_SESSION_FINISHED.json','并发观察结束'),('TURBO_SESSION_FINISHED.json','可选Turbo会话结束')]:
  if (run/file).exists():event(label,load(run/file).get('utc'),'对应阶段原始终态单独保留')
 event('本机归档核验完成',verified.get('verified_utc'),'逐文件大小及SHA256核验，之后才正常销毁')
 event('实例和存储释放确认',release.get('observed_utc'),'独立检查Vast实例与不再需要的存储状态')
 add(table(['事件','UTC 时间','证据与含义'],timeline))
 add('所有时间均为2026年11月7日UTC，源文件保留毫秒及完整时区。硬释放边界预先设为19:55:31 UTC，比累计8小时至少提前5分钟；本次没有通过重启服务或换阶段重置租赁时钟。硬件未通过20分钟、累计付费2小时无有效视频、不可恢复GPU或通信故障均为止损条件；本轮硬件和首视频条件已在限时内通过，后续异常按原始记录报告。')
 add('## 功能与媒体有效性结果')
 valid=[r for r in selfrows if r.get('status')=='validated'];allvalid=valid+[r for r in api if r.get('status')=='validated']
 specs=collections.Counter((r['media'].get('width'),r['media'].get('height'),r['media'].get('fps'),r['media'].get('frames'),r['media'].get('video_codec'),r['media'].get('audio_codec'),r['media'].get('audio_sample_rate'),r['media'].get('audio_channels')) for r in allvalid)
 add(table(['实际规格组合','数量'],[[f'{s[0]}×{s[1]} / {s[2]}fps / {s[3]}帧 / {s[4]} + {s[5]} / {s[6]}Hz / {s[7]}声道',n] for s,n in specs.items()]))
 add('逐条原始规格保留在 ffprobe 与 CSV。T2VA请求比例为16:9，实际1344×768的像素比例为7:4；原生输出按实测像素记录。视频流以帧数除以帧率约为5.166667秒；自建容器与音轨通常5.175秒，API容器约5.167秒。容器或编码尾部长度差异不能直接解释为音画不同步；同步必须实际观看和听音。所有成功样本的 ffmpeg 全文件音视频解码退出码为0，解码时间单列，不混入主 E2E。')
 audios=[]
 for label,path in [('自建',run/'analysis/audio-signal-summary.json'),('官方 API',ROOT/'runs/api/analysis/audio-signal-summary.json')]:
  if path.exists():
   d=load(path);audios.append([label,d['n'],d['analysis_failures'],d['all_digital_silence_count']])
 if audios:add(table(['音频信号检查','n','分析失败','全数字静音'],audios)+'\n\n该检查仅度量解码后信号能量，不代表声音语义、可听质量、台词正确或音画同步。')
 add('## BF16 逐场景性能')
 scene=[]
 for c in load(ROOT/'cases/cases.json')['cases']:
  rs=[r for r in base if r['case_id']==c['id']];scene.append([c['id']+' '+{'T03':'香水瓶环绕','T04':'单人台词口型','F02':'玻璃瓶慢推近'}.get(c['id'],c['title']),*stats(rs)])
 add(table(['场景','有效 n','P50 秒','P95 秒','最小–最大 秒'],scene))
 add('主基线采用同步接口，固定seed和冻结输入。42条记录的镜像摘要、源码commit、模型revision、配置、矩阵和harness哈希均一致，逐项计数见baseline-config-consistency.json。标准请求50步，在可归因的串行日志中实际观察到50次去噪更新；没有将参考文档中的49次当作本次结果。没有测量相同配方多轮冷启动差异，因此当前窄时延分布仅描述这一台实例和这一轮状态。')
 publication=ROOT/'reports/publication-figures';figs=publication if (publication/'latency-by-scene.png').exists() else run/'analysis/figures';(out/'figures').mkdir(exist_ok=True)
 for p0 in figs.glob('*.svg'):shutil.copyfile(p0,out/'figures'/p0.name)
 if (figs/'figure-data-and-provenance.json').exists():shutil.copyfile(figs/'figure-data-and-provenance.json',out/'figures/figure-data-and-provenance.json')
 for name,caption in [('latency-by-scene.png','图 1：逐场景原始交付时延。官方补下载保留在 T04；自建与 API 的网络测量路径不同。')]:
  if (figs/name).exists():shutil.copyfile(figs/name,out/'figures'/name);add('!['+caption+'](figures/'+name+')')
 add('## 独立稳定性与并发观察')
 phase_rows=[]
 for phase in ['warmup','baseline','stability','concurrency_c2','concurrency_c4','turbo']:
  d=next((x for x in summary['phases'] if x['provider']=='self' and x['phase']==phase),None)
  if not d:continue
  phase_rows.append([PHASES.get(phase,phase),str(d['n_technical_valid'])+'/'+str(d['n_submitted_attempts']),f(d['window_seconds']),f(d['throughput_success_per_hour']),f(d['cost_per_technical_success_usd'],3),'；'.join(OUTCOMES.get(k,k)+str(v) for k,v in d['outcomes'].items())])
 add(table(['阶段','技术有效/提交','窗口 秒','有效条/小时','$/技术有效条','终态口径'],phase_rows))
 latency_rows=[]
 for g in summary['groups']:
  if g['provider']!='self':continue
  latency_rows.append([PHASES.get(g['phase'],g['phase'])+' '+g['task'].upper()+' '+('同步' if g['transport']=='sync' else '异步'),str(g['n_technical_valid'])+'/'+str(g['n_submitted_attempts']),f(g['e2e_p50_s']),f(g['e2e_p95_s']),f(g['e2e_min_s'])+'–'+f(g['e2e_max_s'])])
 add(table(['阶段与任务','有效/提交','P50 秒','P95 秒','最小–最大 秒'],latency_rows))
 add('上表时延仅统计该组技术有效结果，失败与窗口主动取消不伪造时延。预热有4个有效结果及1次已确认输入拒绝，全部排除在稳态基线之外；主基线生成及技术有效均为42/42，即本轮观察到100%。并发和Turbo的少量样本P95只是样本内插值，不能外推尾延迟。')
 sf=load(run/'STABILITY_FINISHED.json') if (run/'STABILITY_FINISHED.json').exists() else None
 if sf:
  add('稳定性窗口实际3600.01秒，提交 '+str(sf['n'])+' 条，技术有效 '+str(sf['technical_valid'])+' 条。最后一条F04、seed4120在60分钟边界主动取消，DELETE返回deleted=true；因此按全部提交计技术有效率19/20即95%，另有1条测试窗口截断，不归作模型生成失败。已完成的19条均通过媒体验证，本窗口未观测到模型请求失败或致命GPU/通信故障。取消后只读过渡检查确认任务GET404、连续20秒GPU空闲且health200。原始取消响应、终态与过渡证据均保留。')
 else:add('独立稳定性仍在执行，最终结果待补。')
 add('C2和C4分别仅一批；C2两条，C4四条。这里的 C 是客户端同时在途请求数，不是模型实际同时执行四个GPU批次的证明。将请求E2E、队列阶段和整个批次窗口一起观察；并发成本只计整机窗口一次。两批均为文生与首帧各半，和主基线30/12的任务比例不同，吞吐差异不能仅归因于并发。样本数量很小，不足以估算生产排队分布或服务SLA。')
 concurrent=[]
 for r in sorted(selfrows,key=lambda r:r.get('submitted_utc','')):
  if r['phase'].startswith('concurrency'):
   stages=r.get('stage_durations') or {};concurrent.append([PHASES.get(r['phase'],r['phase']),r['case_id'],f(r.get('e2e_seconds')),f(stages.get('queue_wait_ms'),1),f(stages.get('stage_0_gen_ms'),1),r.get('status')])
 if concurrent:add(table(['批次','场景','E2E 秒','前端队列 ms','stage_0 ms','状态'],concurrent))
 add('## GPU 遥测')
 trs=[]
 for d in tele['phase_metrics']:
  trs.append([PHASES.get(d['phase'],d['phase']),d['telemetry_samples'],f(d.get('max_single_gpu_memory_mib'),0),f(d.get('max_temperature_c'),1),f(d.get('mean_8gpu_utilization_pct'),1),f(d.get('max_sampled_8gpu_power_w'),0)])
 add(table(['阶段','采样 n','单卡峰值 MiB','最高温 °C','八卡平均忙碌 %','八卡峰值功率 W'],trs))
 add('遥测覆盖 '+datetime.datetime.fromisoformat(tele['first_utc']).strftime('%H:%M:%S')+' 至 '+datetime.datetime.fromisoformat(tele['last_utc']).strftime('%H:%M:%S')+' UTC。有效遥测 '+str(tele['valid_samples'])+' 条，失效记录 '+str(tele['invalid_samples'])+' 条；采样间隔中位数 '+f(tele['median_interval_seconds'],3)+' 秒，最大间隙 '+f(tele['max_interval_seconds'],3)+' 秒。显存是NVML逐卡采样值。运行时peak_memory_mb源码使用max_memory_reserved除以1024平方，表示CUDA内存池保留峰值，原字段另存每条记录；本文的显存结论统一采用NVML的MiB口径。八卡功率为设备采样合计，不是插座功耗或整机能耗。短于采样间隔的瞬态可能漏掉。')
 if (figs/'gpu-telemetry.png').exists():shutil.copyfile(figs/'gpu-telemetry.png',out/'figures/gpu-telemetry.png');add('![图 2：各阶段 GPU 遥测。八张卡的显存分别使用，阴影表示采样中的卡间范围。](figures/gpu-telemetry.png)')
 add((ROOT/'reports/working/api-results.md').read_text().replace('# MiniMax H3 官方 API 对照技术结果','## 官方 API 对照结果').replace('此文件记录','本节记录').replace('人工质量与音画同步尚未评分。','人工质量与音画同步评分见人工评审章节。' if (ROOT/'quality/locked/score-lock.json').exists() else '人工质量与音画同步尚未评分。'))
 add('## 8步配置小样与 Turbo 激活限制')
 ts=load(run/'TURBO_SESSION_FINISHED.json') if (run/'TURBO_SESSION_FINISHED.json').exists() else None;proof=load(run/'environment/turbo-activation-proof.json') if (run/'environment/turbo-activation-proof.json').exists() else {}
 if ts:
  add('该独立会话从下载之前开始计时，共 '+f(ts['wall_seconds'])+' 秒，终态 '+ts['outcome']+'。'+('实际适配器激活证据已通过。' if proof.get('activation_verified') else '六条媒体技术校验均通过，实际更新数均为8；但适配器激活未证实，因此这些结果仅称为8步配置样本，不用于Turbo加速比或成本优势结论。'))
  add('固定仓库revision `30f485ed874aa44c8183348ad361c5eabad318da`，文件 `minimax_h3_fl2v_turbo_8step_v1.0_768p_bf16.safetensors`；SHA256 `9b0efe3613b43a84e30febaa43af27432ea9d0711eac7bba904b2556b175f6d4`。服务器端下载与哈希校验已通过，启动参数指定PEFT及精确文件；文件规格rank128、声明alpha8，请求指定scale1、flow shift6和audio flow shift3。8次更新对应9个sigma点。上述配置字段不能代替运行时加载证明，未混用ComfyUI融合文件。')
  if not proof.get('activation_verified'):
   add('运行日志含3132条INFO记录，却没有LoRA或预期的 MiniMax H3 Turbo adapter 加载签名；已保存入口解析、PEFT管理器及模型加载器的安装源码和哈希。服务按会话要求停止后，补充的只读查询连接失败，未取得实时张量或活动适配器证据。当前不能区分日志缺失与请求或后端激活问题，根因保留未决。没有为补证新增第七条生成，也没有再次付费重跑。复现者应先补齐实际加载、绑定和激活断言，再做新的性能比较。')
  trows=[]
  for r in sorted([r for r in selfrows if r['phase']=='turbo'],key=lambda r:r['submitted_utc']):
   br=next((b for b in base if b['case_id']==r['case_id'] and b['seed']==r['seed']),None)
   trows.append([r['case_id'],r['seed'],f(br['e2e_seconds']) if br else '缺失',f(r.get('e2e_seconds')),str(r.get('actual_denoise_updates')),r.get('status')])
  add(table(['场景','seed','BF16 E2E 秒','8步 E2E 秒','实际更新','媒体状态'],trows))
  turbo_valid=sum(r['phase']=='turbo' and r.get('status')=='validated' for r in selfrows)
  session_cost=ts['wall_seconds']*summary['gpu_disk_hour_rate_usd']/3600
  add('整个可选会话含下载、服务切换和关闭，GPU加磁盘按时估算 $'+f(session_cost,3)+'；摊到'+str(turbo_valid)+'条媒体技术有效结果约 $'+f(session_cost/turbo_valid if turbo_valid else None,3)+'/条。这是本次试验开销，不是已验证Turbo服务单价。该数值与仅请求窗口的费用分别报告，流量只在全PoC账单中核对，不能将阶段金额重复加到整段租赁账单。会话沿用已下载的基础权重和BF16运行历史，未独立重做从零下载的完整冷启动。')
  add('8步试验采用异步接口，BF16匹配样本采用同步接口；第一条同时包含重启后的冷请求影响，六条中不另添预热样本。当前既有适配器证据缺口，也有传输与冷热态差异；仅报告媒体技术有效性和实测延迟，匹配CSV中的加速比保持空白。质量折损需要人工评审，不能由8步或解码成功推断。')
 else:add('本轮未执行或尚未完成Turbo，不能填写速度、质量或成本收益。')
 add('## 匿名配对人工质量评审')
 quality=ROOT/'reports/data/human-quality-summary.json'
 if quality.exists():
  q=load(quality);add(q.get('report_markdown','已有评分文件，但摘要尚未生成；此处保留缺失。'))
 else:add('项目发起人已明确选择先交技术报告、人工评分待补。因此截至封版，人工质量评审状态为待补。已准备十个场景、二十条原始视频的匿名A/B页面，固定选择各场景自建最小seed，与一条官方API配对，没有按观感挑选。所有1–5分、缺陷时间戳、业务可交付性均为空。当前不能给出平均主观分、可交付率、每可交付结果成本，或宣称某方质量更好。人工评审可在释放GPU之后完成，费用不会为等待评分继续累积。')
 add('单个评审者的评分只能作为本轮业务判断；没有第二位评审者，不报告评审一致性。十条自建代表样本的评分覆盖也不能自动推广到全部42条基线样本。')
 add('## 成本与利用率及结算')
 if cost:
  add(cost.get('report_markdown','成本对账明细见reports/data/cost-reconciliation.json；缺少文字摘要。'))
 else:add('实例仍在运行或费用尚未对账，全部PoC成本不能在此工作稿中写为最终数值。固定费率、预算上限与API估算已列于方法章节。')
 if bp:
  add(table(['需求利用率','稳态 GPU+磁盘 $/技术有效条','$/实际视频秒'],[[str(round(u['utilization']*100))+'%',f(u['gpu_disk_cost_per_success_usd'],3),f(u['gpu_disk_cost_per_success_usd']/(124/24),4)] for u in summary['utilization']]))
  cold=(min(epoch(r['submitted_utc']) for r in base)-epoch(rental_info()['started_utc']))/3600*summary['gpu_disk_hour_rate_usd']
  add('冷启动分摊示例：从创建意图至首条基线提交（含下载、启动、输入修正和四条预热）按GPU加磁盘费率估算为 $'+f(cold,2)+'。若一次启动服务100个技术有效输出，额外每条 $'+f(cold/100,3)+'；若服务1000条，额外 $'+f(cold/1000,3)+'。这是业务量假设，未声称本轮实测100或1000条。流量、人工、失败以及质量筛选仍需另计。')
  if (figs/'utilization-cost.png').exists():shutil.copyfile(figs/'utilization-cost.png',out/'figures/utilization-cost.png');add('![图 3：按真实基线推导的利用率成本，浅色部分为一次启动摊100条的明确情景。](figures/utilization-cost.png)')
 add('理想盈亏平衡只是一条成本等式：按本轮精确GPU费率13.86666667美元/小时、API每条标价估算0.40美元，100%利用率允许的技术有效输出间隔约103.85秒；50%约51.92秒，25%约25.96秒。加磁盘、流量、失败、人工或质量筛选后，允许间隔会更短。这里没有把该算式当作预设性能验收门槛。')
 add('## 局限与下一步')
 add('本轮只有一台Vast实例、一次连续租赁、固定8卡配方；没有多节点、四卡、不同拓扑顺序或不同功率限制的对比。P95来自30/12或更小样本，稳定性窗口只有60分钟；不能作为客户生产容量、可用性SLA或长期故障率承诺。API后台版本、seed、步数和优化不公开，业务输入一致并不意味着数学等价。')
 add('建议先由项目发起人完成匿名A/B，尤其检查单人中文台词、双人轮次、脚步和车轮水声同步，以及首帧身份和几何保持。质量锁定后按用途判定可交付，再把分母从“技术有效”替换为“可交付”；如果需要继续投入，优先验证已观测的成本或质量瓶颈，而不是直接扩大分辨率、视频长度或租赁规模。')
 add('本轮缺失项按证据状态列出：人工主观评分及可交付成本；如未取得则官方账户实际账单与提供方最终发票；独立text encoder/denoiser/VAE阶段耗时；人工工时；宿主用户空间OS；插座整机功耗；多次冷启动和长期SLA。缺失值没有用估计或外部Pro5000/5500数据填充。')
 add((ROOT/'reports/working/appendices.md').read_text())
 target=out/'MiniMax_H3_PRO6000_8GPU_PoC_Report.md';target.write_text('\n\n'.join(sections)+'\n');print(str(target))
if __name__=='__main__':main()

"""Separate metered/posted observations, rate estimates and pending budget reserves."""
import datetime,json
from pathlib import Path
from derive_results import ROOT,load,save

def epoch(t):return datetime.datetime.fromisoformat(t.replace('Z','+00:00')).timestamp()
def f(x,n=2):return '缺失' if x is None else f'{x:.{n}f}'
def main():
 release=load(ROOT/'runs/control/release-confirmed.json');ledger_path=ROOT/'private/ledger.json';ledger=load(ledger_path if ledger_path.exists() else ROOT/'reports/data/ledger-public.json');summary=load(ROOT/'reports/data/observed-summary.json');rental=ledger['rentals'][0]
 if not release.get('instance_destroyed_confirmed') or release.get('unneeded_storage_remaining') is not False or not rental.get('ended_utc'):raise SystemExit('Close one verified rental before reconciling costs')
 rental_fields=['instance_id','offer_id','started_utc','ended_utc','hard_release_utc','gpu_hour_usd','disk_hour_usd','disk_GB','image','posted_gpu_charge_usd','posted_disk_charge_usd','posted_billed_gpu_hours_display','posted_billed_storage_hours_display','posted_billing_upper_bound_hours','posted_charge_evidence','formal_consumption_invoice_obtained']
 public_rental={k:rental.get(k) for k in rental_fields};save(ROOT/'reports/data/rental-public.json',public_rental)
 entry_fields=['id','category','reserved_usd','actual_usd','source','request_id','created_utc','settled_source','settled_utc']
 save(ROOT/'reports/data/ledger-public.json',{'entries':[{k:e.get(k) for k in entry_fields} for e in ledger['entries']],'rentals':[public_rental],'scope':'Cost-only sanitized view; credentials, SSH details and user authorization text excluded'})
 hours=(epoch(rental['ended_utc'])-epoch(rental['started_utc']))/3600
 if hours>8 or len(ledger['rentals'])!=1:raise SystemExit('Rental count/hour bounds require explicit investigation')
 gpu=hours*summary['gpu_hour_rate_usd'];disk=hours*summary['disk_hour_rate_usd'];pending_by={k:sum(e['reserved_usd'] if e.get('actual_usd') is None else e['actual_usd'] for e in ledger['entries'] if e['category']==k) for k in ('api','storage_network')}
 billed_upper=max(hours,rental.get('posted_billing_upper_bound_hours',hours));budget_gpu=max(billed_upper*rental['gpu_hour_usd'],rental.get('posted_gpu_charge_usd',0));budget_disk=max(billed_upper*rental['disk_hour_usd'],rental.get('posted_disk_charge_usd',0));upper=budget_gpu+budget_disk+sum(pending_by.values())
 if billed_upper>8:raise SystemExit('Provider billed duration exceeds cap')
 if budget_gpu>120+1e-8 or budget_disk+pending_by['storage_network']>20+1e-8 or pending_by['api']>10+1e-8 or upper>150+1e-8:raise SystemExit('Final conservative budget exceeds a cap; investigate before report')
 before=release.get('credit_before_rental_usd',120);after=release.get('credit_after_release_usd');debit=round(before-after,2) if after is not None else None
 api=summary['official_published_price_estimate_usd'];combined=debit+api if debit is not None else None
 n=summary['self_total_valid'];np=summary['official_total_valid'];bp=next(x for x in summary['phases'] if x['provider']=='self' and x['phase']=='baseline')
 rows=[['GPU租赁','首次释放确认控制窗口 × 精确费率','$'+f(gpu),'控制窗口估算，不含未确定的计费收尾差异'],['500GB磁盘','同一控制窗口 × 磁盘费率','$'+f(disk),'控制窗口估算，不含未确定的计费收尾差异'],['磁盘/流量账本条目','已支付或待核实预留','$'+f(pending_by['storage_network']),'其中待结算不当作已消耗'],['官方API10任务','50计费秒 × $0.08','$'+f(api),'公开单价及返回usage估算，发票待核实'],['官方API预算占用','已支付或待结算预留','$'+f(pending_by['api']),'不与API估算重复相加'],['Vast账户余额下降','$'+f(before)+' → $'+f(after),'$'+f(debit),'账户观测，仍需核对最终发票和同期其他活动'],['含API的PoC支出观测','Vast余额下降 + API标价估算','$'+f(combined),'混合证据口径，不宣称全额已结算'],['保守预算核算','向上取整费率 + 全部待结算预留','$'+f(upper),'用于核对上限，含预留并非实际消费']]
 table='| 项目 | 计算依据 | 金额 USD | 证据状态 |\n| --- | --- | --- | --- |\n'+'\n'.join('| '+' | '.join(r)+' |' for r in rows)
 prose='本次只租用一个实例，未替换。创建到首次独立释放确认的控制窗口为 '+rental['started_utc']+' 至 '+rental['ended_utc']+'，共 '+f(hours,4)+' 小时，覆盖部署、全部实验和归档等待。该窗口不是提供方最终计费边界；释放后的延迟入账仍需核对。\n\n'+table
 billpath=ROOT/'runs/control/vast-usage-final.json'
 bill=load(billpath) if billpath.exists() else None
 if bill:
  posted=[['GPU',str(bill.get('gpu_hours_display','缺失'))+'小时','$'+f(bill.get('gpu_charge_usd'))],['磁盘',str(bill.get('storage_hours_display','缺失'))+'小时','$'+f(bill.get('storage_charge_usd'))],['下载',str(bill.get('download_GB_display','缺失'))+' 提供方计量GB','$'+f(bill.get('download_charge_usd'))],['上传',str(bill.get('upload_GB_display','缺失'))+' 提供方计量GB',bill.get('upload_charge_display','缺失')],['本实例已记账合计','instance '+str(rental['instance_id']),'$'+f(bill.get('usage_total_usd'))]]
  prose+='\n\n提供方最终页面观测的已记账消费明细如下。该页面提醒近期活动可能延迟，小时、计量GB与金额均按页面显示精度保留；充值发票不列为消费。\n\n| 费项 | 已记账用量 | 页面金额 USD |\n| --- | --- | --- |\n'+'\n'.join('| '+' | '.join(x)+' |' for x in posted)
  prose+='\n\n最新账单观察时间为 '+bill['observed_utc']+'。GPU显示4.61小时、磁盘4.62小时，均高于首次释放确认的控制窗口4.5846小时；未取得准确的计费截止时刻，不能强行用本机时间覆盖提供方用量。预算检查按较大显示时长加0.005小时舍入上界核对，不超8小时。Credit Usage摘要为 $'+f(bill.get('credit_usage_summary_usd'))+'，条目与余额下降为 $'+f(bill.get('usage_total_usd'))+'，0.01美元显示差异保留；最新消费快照仍不等于正式结算发票。'
 if debit is not None:
  prose+='\n\nVast余额差额观测为 $'+f(debit)+'，按本轮单实例、无其他观测消费归属本次自建支出；若同一账户有未显示的其他活动需重算。对全部 '+str(n)+' 份自建技术有效视频直接平均约 $'+f(debit/n,3)+'/条，该混合分母含预热、基线、稳定性、并发和可选Turbo，只用于本次PoC回顾，不能当作单一配置生产单价。含官方对照的 '+str(n+np)+' 份技术有效视频，按余额观测加API标价估算约 $'+f(combined/(n+np),3)+'/条，仍不是最终发票口径。'
 prose+='\n\n所有并发费用按完整窗口计一次；阶段资源估算已经包含在整段租赁费用内，不再叠加进PoC总额。没有完整人工质量判定时，每可交付结果成本为缺失。人工工时未计时、无虚构小时费率，单列缺失。充值本金120美元与账本预留都没有冒充消费。最终发票状态：'+release.get('final_invoice_status','未取得，保留缺失').rstrip('。')+'。'
 obj={'generated_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'currency':'USD','instance_id':rental['instance_id'],'rental_count':len(ledger['rentals']),'replacement_count':0,'protected_charge_window_hours':hours,'gpu_rate_time_estimate_usd':gpu,'disk_rate_time_estimate_usd':disk,'traffic_budget_entry_usd':pending_by['storage_network'],'api_usage_price_estimate_usd':api,'api_budget_occupancy_usd':pending_by['api'],'credit_before_usd':before,'credit_after_usd':after,'vast_observed_credit_decrease_usd':debit,'whole_poc_observation_plus_api_estimate_usd':combined,'conservative_budget_with_pending_usd':upper,'self_technical_valid_outputs':n,'official_technical_valid_outputs':np,'labor_hours':None,'whole_poc_cost_per_deliverable':None,'final_invoice_status':release.get('final_invoice_status','not_retrieved'),'release_evidence':'runs/control/release-confirmed.json','vast_usage_final_evidence':'runs/control/vast-usage-final.json' if bill else None,'vast_posted_usage_display_total_usd':bill.get('usage_total_usd') if bill else None,'report_markdown':prose}
 save(ROOT/'reports/data/cost-reconciliation.json',obj);print(json.dumps({k:v for k,v in obj.items() if k!='report_markdown'},ensure_ascii=False))
 save(ROOT/'runs/control/final-budget-check.json',{'observed_utc':release.get('final_billing_observed_utc'),'open_rentals':0,'protected_control_window_hours':hours,'billed_duration_upper_bound_hours':billed_upper,'category_upper_usd':{'gpu':budget_gpu,'storage_network':budget_disk+pending_by['storage_network'],'api':pending_by['api']},'total_upper_usd':upper,'caps_usd':{'gpu':120,'storage_network':20,'api':10,'total':150},'passed':True,'basis':'Maximum of control interval, provider posted charges and rounded billed-hours bound; API/network pending reserves retained'})
if __name__=='__main__':main()

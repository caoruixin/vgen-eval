## 实验范围和方法

本轮以单机八卡为单位，测试 MiniMax H3 的文生有声视频 T2VA 和首帧条件生成 FL2VA。标准配方采用固定版本 vLLM Omni、BF16 主配置、TP4 与 Ulysses2、ring1、text encoder TP8，以及八卡 VAE tile 并行。测试的业务交付规格为 1344×768、24 fps、名义五秒、有音轨的视频。官方国际站 API 对照使用相同场景提示词和首帧文件，名义五秒、768P；API 的内部 seed、步数、软件版本和优化不可见。

样本设计为六个 T2VA 场景各五个 seed，共三十条；四个 FL2VA 场景各三个 seed，共十二条。T2VA seed 为1101至1105，FL2VA为2101至2103。预热为每种任务两条，分别检查同步和异步接口。独立稳定性窗口为六十分钟；并发观察为客户端 C2 和 C4 各一批。可选 Turbo 设计为最多六条匹配样本，三条 T2VA 和三条 FL2VA，并与标准配方分开报告。逐场景提示词、检查清单和素材哈希在冻结矩阵及附件中保留。

## 实测硬件环境

| 项目 | 观测值 |
| --- | --- |
| 提供方和位置 | Vast Texas 单机，offer55456241，instance59566113，machine153605 |
| GPU | 八张 NVIDIA RTX PRO 6000 Blackwell Server Edition |
| 每卡显存 | nvidia-smi 每卡97887MiB，八张卡具有不同PCI bus ID，MIG全部Disabled |
| 负载链路采样 | 八卡均为PCIe Gen5、x16；每卡功率限制600W，未修改功率限制 |
| CPU | 两路AMD EPYC9555，每路64个物理核、每核两个线程，共256线程 |
| 内存 | 宿主811074256896字节，约755.37GiB；容器有效内存上限778630594560字节，约725.15GiB |
| 磁盘 | 实例分配500GB；df返回总容量536870912000字节。最大可租磁盘不作为已分配容量 |
| NUMA | 两个节点；GPU0至3属于节点0，GPU4至7属于节点1 |
| P2P | Torch查询八卡两两可达；NCCL八进程all-reduce和all-to-all正确性通过 |

每卡显存单独使用，八张卡不会形成单卡统一显存。Vast 的 Verified、历史可靠度99.42%与DLPerf1333.1只用于选择现货；这些不是本轮的稳定性或H3性能结论。PCIe代际、链路宽度和功率限制来自运行采样；没有进行独立存储性能、卡数或拓扑顺序对比。

32MiB每rank的NCCL小样包含三次预热和十次计时。all-reduce的最慢rank平均为3.3165ms，按每rank payload计算为10.1174GB/s；all-to-all为1.8461ms、18.1757GB/s。这些是正确性和小消息样例数据，不是完整nccl-tests总线带宽，也不能换算成H3速度。

## 软件和权重

| 项目 | 观测值或固定身份 |
| --- | --- |
| 容器系统 | Ubuntu24.04.3 LTS，x86_64 |
| 共享宿主内核 | Linux6.8.0-100146-generic；宿主用户空间操作系统在容器内不可见 |
| 驱动 | 615.71.09 |
| Python | 3.12.3 |
| Torch | 2.13.0+cu130，torch.version.cuda为13.0 |
| cuDNN | torch查询返回92000 |
| NCCL | Torch运行时查询2.29.7 |
| vLLM和vLLM Omni | 均为0.32.0 |
| Diffusers | 0.40.0 |
| Transformers | 5.19.0 |
| Hugging Face Hub | 1.33.0 |
| PEFT | 0.21.2 |

镜像固定为 `vllm/vllm-omni@sha256:6b44ee504a0dc3a21eacf72e7dc5da318b7bcc8ed0e349dc928e67278414430c`。源码参考tag为v0.32.0，解析到commit `5917730a4b02aedb44d374173cdc1b10484ccd52`。已安装MiniMaxH3Pipeline文件与该参考源码仅有末尾空行差异，Python AST相同；实际文件哈希与对比记录均保留。完整镜像身份以摘要为准。

模型来自MiniMaxAI/MiniMax-H3，revision固定为 `42ed227ee7df40d41602854ae760620d6eb651fe`，仅下载FL2VA分区。81个文件共144051182625字节，逐文件大小、LFS SHA256或Git blob SHA1校验通过。下载和校验从12:08:35Z至12:25:04Z，共989.668秒。下载器、权重、H3运行时和完整媒体工具都在租用服务器上运行；笔记本只承担标准库控制、官方API请求、结果回传和既有文档工具导出。

运行配置类的dtype默认值为torch.bfloat16。权重头元数据中，text encoder为BF16，transformer含BF16和F32张量，音频和视频VAE权重为F32；不能将所有组件写成统一的BF16存储。组件内部类型转换遵循固定实现。本轮未额外启用FP8、FP4或近似加速。配置声明中的缓存策略为none，prompt embed cache为false，CPU、layerwise和distributed layerwise offload为false；编译粒度默认regional、dynamic为true。配置声明与实际启动参数分别留存；运行中引擎对象的全量有效配置未导出。

## 实际启动和协议修正

本机真实拓扑下采用GPU顺序0至7。固定源码的RankGenerator顺序为tp-sp-pp-cfg-dp，TP4的连续rank位于同一NUMA节点，Ulysses2跨节点。该选择保留了相邻PIX对，但未进行其它rank顺序对比，不主张这是最优映射。

初次 `numactl --interleave=0,1` 启动返回Operation not permitted。保留失败日志后，验证并采用 `numactl --cpunodebind=0,1`；内存策略为容器默认，未成功启用interleave，也未放宽宿主安全策略。实际命令为：

```bash
CUDA_VISIBLE_DEVICES=0,1,2,3,4,5,6,7 \
VLLM_WORKER_MULTIPROC_METHOD=spawn \
VLLM_OMNI_VIDEO_SYNC_TIMEOUT=1800 \
numactl --cpunodebind=0,1 \
vllm serve /data/models/MiniMax-H3/FL2VA --omni \
  --host 127.0.0.1 --port 8091 --trust-remote-code \
  --num-gpus 8 --tensor-parallel-size 4 --usp 2 --ring 1 \
  --text-encoder-tp-size 8 --vae-patch-parallel-size 8 \
  --vae-parallel-mode tile --vae-use-tiling \
  --diffusion-attention-backend CUDNN_ATTN
```

自建请求显式发送宽高、24fps、50步、seed、flow shift12与audio flow shift3，并请求stage metrics。T2VA必须显式传aspect_ratio=16:9，FL2VA采用首帧比例；请求同时传short_edge=768和名义duration=5。初次适配漏传T2VA比例被HTTP400拒绝，服务日志确认输入阶段终止后才作下一次独立尝试。原attempt1、确认依据和成功attempt2全部保留。

固定运行时中，请求50步对应51个sigma点；实际执行更新数以每条串行请求日志区间中的完整50/50计数器为证据。不能将参考配方中的49次更新直接套用。并发请求的日志区间重叠时，不作逐请求计数器归因。输出帧数、像素、视频和音频时长按ffprobe逐条读取，不强行裁成五秒。

## 统计和质量口径

自建客户端与服务位于同一容器，经127.0.0.1请求并将MP4下载到服务器；官方API客户端在笔记本，经公网访问API和CDN。主E2E从各自客户端提交开始计到MP4完整下载，不含后续解码验证时间。官方API结果在租用服务器统一完成媒体校验，下载与校验之间的跨主机等待另保留时间戳，不解释为API推理耗时。自建回传至笔记本的时间另作归档链路观测，不包含在服务端本地E2E中。回传监视器启动前已存在的文件无法回溯首次到达时刻，相关字段保留缺失，且两端时钟未独立校准。两方没有采用相同网络路径，不能将表中差异解释为纯模型推理速度差。同步响应保存HTTP状态、Content-Type、请求ID、推理时长、峰值显存和可用stage字段；异步任务在返回ID后立即落盘，再轮询查询与下载。轮询间隔设为两秒，但实际发现误差上界包含请求往返时间，不能写成精确两秒。官方T04的补下载时间保留在真实交付E2E中。

保存MP4前先检查HTTP状态、类型和ftyp签名，再运行ffprobe和ffmpeg全文件音视频解码。存在音轨、解码成功和音频信号非全零都不能代替人工音画同步、语义或可交付性评分。服务公开的阶段时延主要为queue_wait_ms和stage_0_gen_ms。已安装源码中，queue_wait_ms量的是入队到Orchestrator登记的间隔，扩散调度器另有内部排队指标；该内部指标未出现在本轮HTTP响应中。stage_0_gen_ms按服务阶段返回，不等同于独立去噪计算时间。metrics中未填充的0.0字段也不当作组件耗时实测。独立text encoder、denoiser和VAE时间保留缺失，不作相减或分摊推算。

预热、BF16主基线、六十分钟稳定性、C2、C4和Turbo分别统计。P50、P95按排序后(n−1)乘分位点作线性插值，同时报告n、最小和最大值。三十条T2VA、十二条FL2VA以及更小的并发和Turbo样本只能提供初步尾部参考。六十分钟观测不能当作生产SLA。

吞吐使用整个观察窗口的成功输出数除以窗口墙钟时间。并发成本按整机窗口计一次；不能将每条E2E乘整机单价后相加。GPU遥测约一秒采样，显存峰值为采样最大值，不能排除采样间隙中的短瞬态。模型输入、版本和素材哈希均在逐条数据中保存。

质量采用提示词遵循、画面与运动、首帧保持、音频和音画同步五项1至5分；T2VA首帧保持不适用。可交付性独立选择是、否或不确定。十个场景各选自建固定最小seed与一条官方结果，随机分配A/B标签，不挑选好看结果。项目发起人为首位人工评审，评分锁定后再解盲。没有评分就保留缺失，不虚构评审者、分数或可交付率。

## 成本与预算口径

预算上限为GPU120美元、磁盘和流量20美元、官方API10美元，总额150美元，累计GPU计费不超过八小时。账本在收费动作前检查已花费、待结算、下一任务最坏费用和二十五分钟归档释放预留。部署、下载、失败、等待、回传及释放前的闲置都计入本次自建总成本。

Vast精确价格来自实例接口：八卡GPU为13.86666667美元每小时，500GB磁盘为0.13888889美元每小时，合计14.00555556美元每小时。预算守卫使用向上取整的13.867和0.139。创建意图时间为12:00:31.289889Z；提供方start_date对应12:00:39.198850Z。保护边界按较早的意图时间计算，不能从SSH成功或模型就绪才起算。

成本分为稳态服务、本次自建全部窗口，以及加官方对照后的完整PoC。25%、50%、100%的需求利用率是业务请求占可服务时间的比例，不等同于NVML的GPU忙碌率。冷启动分摊示例必须写明一次启动对应多少个成功输出；一百条或一千条属于情景假设，不是本次实测吞吐或业务需求。

充值120美元是账户资金，不等于本次已消耗费用。官方API当前按五十个计费输出秒和公开单价估算为4美元，实际账单未取得前仍标记估算。人工工时未计时，作为缺失单列，不编造小时费率或将其隐入云费用。关闭服务只停止推理，必须在结果回传并核验后销毁实例、确认没有不再需要的存储，并独立核对计费状态。

## 请求协议与复现顺序

本轮同步接口为 `POST /v1/videos/sync`，异步为 `POST /v1/videos`、`GET /v1/videos/{id}` 和 `GET /v1/videos/{id}/content`。首帧以 multipart 文件字段 `input_reference` 发送，内容与冻结PNG逐字节一致。标准T2VA的核心表单如下，提示词替换为冻结场景全文；FL2VA使用aspect_ratio=adaptive、task=fl2va并附首帧。

```text
prompt = 冻结场景全文
width = 1344
height = 768
fps = 24
aspect_ratio = 16:9
short_edge = 768
num_inference_steps = 50
seed = 1101
flow_shift = 12
return_stage_metrics = true
extra_params = {"task":"t2va","duration":5.0,"audio_flow_shift":3.0}
```

官方使用 `POST https://api.minimax.io/v2/video_generation` 与 `GET /v2/query/video_generation/{task_id}`。T2VA请求的核心JSON如下；FL2VA的ratio改为adaptive，并向content添加role为first_frame的image_url对象。本轮image_url使用同一冻结PNG的data URL，未更换图片或另行收费生成。官方seed与步数未公开。

```json
{
  "model": "MiniMax-H3",
  "resolution": "768P",
  "duration": 5,
  "ratio": "16:9",
  "content": [
    {"type": "text", "text": "冻结场景全文"}
  ]
}
```

复现应在新的、重新授权并验收的8卡服务器和干净结果目录进行。先解压交付包中的冻结harness源码，放入同版cases与config，登记新的租赁时钟、费率和硬释放边界，再下载及校验固定FL2VA文件。不能复用本次实例ID、凭据或已完成请求目录。硬件映射须按新机器拓扑填写，本轮cpu-bind参数的适用性也需重新核实。

本轮主流程在同步及异步各任务预热通过后，运行以下协调器完成42条基线并自动开始独立60分钟稳定性；单次基线窗口到时只续交未提交样本，已有失败或不明提交不会自动重提。之后先人工核对稳定性终态，再执行只读过渡检查；通过后才运行C2/C4，可选Turbo再独立启动并限时。

```bash
python3 scripts/baseline_stability.py
python3 control/review_stability_gate.py
python3 control/concurrency_session.py --execute
python3 control/turbo_session.py --execute
```

以上为本次复现入口，不是对未来资源消费的授权。实际客户端负责先核对HTTP状态、Content-Type和MP4签名，再落为视频，随后ffprobe和全文件解码；直接把任意HTTP响应写成MP4不属于本轮验证流程。超时提交必须优先核实已有任务ID，不以自动重复POST掩盖失败。

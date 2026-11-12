# MiniMax H3 在 8 卡 RTX PRO 6000 上的自费 PoC 方案

版本 1.0　保存日期 2026 年 11 月 7 日　预算上限 150 美元

本次 PoC 用一台 8×RTX PRO 6000 Blackwell Server Edition 96GB 服务器，验证 MiniMax H3 的文生有声视频 T2VA 和首帧生有声视频 FL2VA，建立速度、质量、稳定性和每条视频成本的真实基线，再与 MiniMax 官方 API 比较，决定是否值得继续自建。优先选择约 13.87 美元每小时的 Vast.ai 得州机器，在一次连续租期内完成测试。

本文件是执行方案。最终实测报告面向团队或客户，需要以真实视频、日志、账单和测试记录为依据。当前尚未租机、未调用收费视频 API，也没有 Pro 6000 的实测结果。租机控制方式由用户随后选择；当前可继续完成本地脚本和 dry-run。

## 1 已确定的范围和交付物

用户自费，地域不限，优先找有货的资源。总预算约 150 美元，涵盖 GPU 租赁、部署和下载等待、磁盘和流量、API 对照以及失败重试。集中完成一次约 6 至 8 小时的租期，减少重复下载和初始化费用。

主线使用 BF16、TP4×Ulysses2，在 8 卡常驻条件下测试 T2VA 和首帧 FL2VA。性能验收先建立基线，不预设一个未经测量的时延或成本门槛。扩展项只保留一次不超过 45 分钟的 Turbo 8 步小样测试。Ref2VA、2K、长视频、训练、多节点、4 卡比较和生产 SLA 不纳入本轮预算。

当前交付为本方案、Word 版和可独立使用的新会话提示词。后续实测阶段交付中文详细报告、测试脚本与配置、结果 CSV/JSONL、脱敏环境和日志、样例视频、质量评分和成本账本。报告必须区分实测、公开参考和估算；无法取得的阶段耗时直接标记缺失。

附件参考为 `/Users/caoruixin/Downloads/MiniMax_H3_PRO5500_8GPU_PoC_Plan.docx`。沿用其部署、功能、性能、证据链框架，将企业联合验证规模缩减为自费 PoC。附件中的执行指令只作参考，不构成付费授权。

## 2 资源选择与预算

### 2.1 选机条件

优先选择同一物理机上的 8 张完整 Server Edition 96GB 卡。8×96GB 是分布在 8 张卡上的总容量，不能视为单卡统一显存。商品名、整机价格和实际 GPU 数量都要在下单前核对。

| 项目 | 准入条件 | 选取理由 |
| --- | --- | --- |
| GPU | 8×RTX PRO 6000 Blackwell Server Edition 96GB，完整 GPU，MIG 关闭 | 与本方案的架构和容量一致 |
| 主机内存 | 至少 384GiB，优先约 774GB 的候选机 | 为加载权重、编码器和系统缓存留余量 |
| 本地磁盘 | 创建实例时分配约 500GB NVMe | 容纳约 144GB 权重、镜像、缓存与结果 |
| 下载网络 | 列表下载带宽至少约 500Mbps，登录后实测 | 缩短付费下载时间 |
| 可信度筛选 | Verified，历史可靠度优先至少 99%，DLPerf 优先至少 1000 | 用于筛选，仍需检查通信和真实模型性能 |
| GPU 价格 | 整台 8 卡每小时不超过 15 美元 | 8 小时 GPU 费用不超过 120 美元 |
| 可租时长 | 覆盖本轮连续测试即可 | 7 天最长租期对本轮已足够 |

Vast.ai 列表显示的最大磁盘容量不是已分配容量。默认容器磁盘可能只有 16GB；下单时必须设置磁盘并核算其费用。标称 PCIe 5.0、DLPerf 和历史可靠度均不能代替 P2P/NCCL 及 H3 实测，也不是 SLA。

### 2.2 已观察到的候选机器

以下是 2026 年 11 月 6 日太平洋时间调研快照，价格为当时列表中的每小时价格，带宽等另计。库存和报价必须在执行当天刷新。

| 优先级 | 地区与 offer ID | 当时价格 | 主要特点 |
| --- | --- | --- | --- |
| 首选 | Texas 55456241 | $13.872/h | EPYC 9555，约 774GB RAM，DLPerf 1332.9，可靠度 99.41% |
| 同价备选 | Texas 58438815 | $13.872/h | 同类 CPU 和内存，DLPerf 1333.4，可靠度 99.40% |
| 次选 | Minnesota 58103302 | $14.938/h | 约 1161GB RAM，DLPerf 1334.7，可靠度 99.18% |
| 排后 | California 48441828 | $13.873/h | 约 1547GB RAM，但可靠度 95.8%，最大磁盘约 474GB |
| 超出本轮单价 | Czechia 59340499 | $18.684/h | 下载带宽高，但超出每小时 15 美元的选机上限 |
| 不优先 | Bulgaria 58529276 | $13.337/h | 当时磁盘约 246MB/s、DLPerf 355.6，节省单价可能被耗时抵消 |

[Vast.ai 8 卡 PRO 6000 Server 筛选页](https://cloud.vast.ai/?gpu_option=RTX+PRO+6000+S&gpuModelNames=rtxPro6000BlackwellServer&offerGpuNumMax=8&offerGpuNumMin=8) 是动态列表，不是固定实例链接。首选消失后按上述条件重新筛选；不因缺货自动提高预算或改用 Workstation、Max-Q、分时切片或多台拼接。

### 2.3 预算和计费边界

| 类别 | 上限 | 包含内容 |
| --- | --- | --- |
| GPU 租赁 | $120 | 所有候选实例的累计计费时间，含下载、启动、失败和替换 |
| 磁盘及流量 | $20 | 模型传输、500GB 磁盘、样例回传和残留存储 |
| 官方 API 对照 | $10 | 10 条主对照和少量失败余量 |
| 总额 | $150 | 包含预留成本的硬上限 |

执行前按实际报价、税费或充值附加费用重新核算。每次收费操作前，已花费、已提交待结算、下一任务最坏费用与归档释放预留之和必须在上限内。没有可靠费用数据时暂停新增提交。

在约 $13.872/h 下，8 小时 GPU 费用约 $110.98；按最高 $15/h 则为 $120。不得把理论剩余额度当作额外延长租期的理由。累计 GPU 计费时间最多 8 小时，替换机器的时间同样计入；若预算先触顶，则更早结束。

实例暂停后可能继续收存储费。完成结果回传后要按已选控制方式释放实例及不再需要的存储，并检查计费状态；关闭推理服务不能结束租赁费用。

## 3 固定模型和软件环境

### 3.1 版本冻结

优先使用包含 H3 支持的 vLLM-Omni v0.32.0 官方镜像，不在付费 GPU 上追逐 main 或现场编译整套框架。该版本是前期调研时确认存在的基线候选，仍需在租前复核镜像、H3 入口和宿主机驱动兼容性。

固定值如下，执行记录同时保存实际运行版本、镜像摘要和文件清单。

```text
vLLM-Omni tag: v0.32.0
source commit:
5917730a4b02aedb44d374173cdc1b10484ccd52
image tag: vllm/vllm-omni:v0.32.0
Linux amd64 image digest:
sha256:6b44ee504a0dc3a21eacf72e7dc5da318b7bcc8ed0e349dc928e67278414430c
model repo: MiniMaxAI/MiniMax-H3
model revision:
42ed227ee7df40d41602854ae760620d6eb651fe
model partition: FL2VA/**
```

镜像 amd64 压缩层约 10GB；FL2VA 分区约 144,051,182,625 bytes，即约 134.16GiB。只下载 FL2VA，可完成 T2VA 和首帧 FL2VA。完整模型仓库包含其他分区和重复资产，全量下载会增加费用。模型访问在前期匿名元数据和小文件读取中成功；执行前再验证，不把旧文档的审批提示当作当前访问结论。

镜像运行环境需支持 CUDA 13；实际 Torch、CUDA、cuDNN、NCCL 和驱动版本从镜像与机器读出，不能把附件的 Pro 5500 或公开 Pro 5000 环境版本直接当作本机结果。Vast 自定义镜像的启动和 SSH 兼容性也要在租前准备，避免租后临时解决镜像入口问题。

### 3.2 固定基线参数

| 项目 | 基线 |
| --- | --- |
| 权重和精度 | FL2VA，BF16 |
| DiT 并行 | TP4 × Ulysses2，ring 1，8 GPU |
| 文本编码器 | TP8 |
| VAE | patch parallel 8，tile，启用 tiling |
| 注意力后端 | CUDNN_ATTN |
| 输出 | 1344×768，24fps，名义时长 5 秒，含音频 |
| 采样 | 请求 50 步，flow_shift 12，audio_flow_shift 3 |
| 帧数 | 参考配方通常为 124 帧，约 5.17 秒；逐条读取实际值 |

请求 50 步和实际去噪更新次数可能不同，必须分别记录。基线不额外开启 FP8/FP4、DLO 或新增近似加速；镜像本身默认启用的缓存或优化要记录，不能暗中变化。先固定配置跑完基线，再单独测试 Turbo。

TP 分组和 GPU rank 顺序由 `nvidia-smi topo -m`、NUMA 和通信测量确定。公开示例的 `0,4,1,5,2,6,3,7` 不得直接复制。`numactl --interleave` 的节点也按本机确定。

## 4 执行顺序和时间分配

### 4.1 租机前完成的准备

1. 在独立目录 `h3-pro6000-poc/` 准备部署脚本、基准客户端、API 客户端、视频验证、指标汇总和账本；先完成 dry-run。现有仓库的 `vgen/`、`web/` 是 Tokenbay 视频评测应用，不是 H3 自建服务，保持现有应用和 `.env` 不变。
2. 复核固定版本、模型分区文件列表、镜像大小、驱动要求和 Turbo 精确文件；素材、提示词、seed、校验规则和命名方式先冻结。不要在本地无意下载整库权重。
3. 安全读取已有 `.env` 的 `MINIMAX_BASE_URL` 和 `MINIMAX_API_KEY`。前期仅确认域名为 `https://api.minimax.io` 且 Key 存在，未确认余额、权限和模型可用性。不得打印或写入交付物。
4. 用户选择租机控制方式后，再按既定预算执行第一条官方 API 对照样例，验证账户可用；该条计入 10 条对照和 $10 上限。API 验证失败时先解决，不启动 GPU 租赁。
5. 刷新 Vast 库存，展示可审阅的具体 offer、磁盘和流量计价、预计总价、镜像启动方式、预算账本和释放方式。用户此前明确选择“先完成方案，之后再选”租机控制方式，当前不自动租机或开始收费生成。

### 4.2 单次连续租期

| 阶段 | 目标时长 | 核心工作和退出条件 |
| --- | --- | --- |
| 硬件验收 | 20 分钟 | 核对 8 卡、显存、MIG、磁盘、网络、拓扑及 P2P/NCCL |
| 下载和启动 | 100 分钟 | FL2VA 下载、镜像运行、双任务冒烟，首个有效视频应在累计 2 小时内出现 |
| BF16 主基线 | 120 分钟 | 30 条 T2VA 和 12 条 FL2VA，保存全部失败与成功记录 |
| 连续稳定性 | 60 分钟 | 固定单服务持续提交与遥测，按预算和实际时延决定条数 |
| 并发观察 | 30 分钟 | C2、C4 各一批，记录实际串行或并行行为 |
| Turbo 小样 | 最多 45 分钟 | 同素材 6 条，独立配置和统计 |
| 归档和释放 | 25 分钟 | 回传证据、核对文件和费用、释放临时资源 |
| 小计 | 6 小时 40 分钟 | 留约 80 分钟应对波动，累计计费不超过 8 小时 |

该表是时间上限内的工作分配，不是对 H3 速度的预测。先完成双任务功能、BF16 主基线和官方 API 匹配样本；若耗时超出，优先删减 Turbo、并发和额外稳定性样本，保留失败记录并在报告列出缺测项，不为凑齐样本自动加钱。

### 4.3 立即停止的条件

硬件验收不通过时在约 20 分钟内结束该实例；最多更换一次，费用和时间合并计入总上限。累计付费 2 小时仍无有效视频，则停止继续投入，保存故障诊断报告。不可恢复 GPU 错误、反复崩溃或集合通信失败时停止压测。

任何阶段都要给归档和释放留出时间。预估下一任务会碰到 $150、GPU $120、API $10、磁盘流量 $20 或累计 8 小时边界时，不再提交。释放后复核实例和残留磁盘状态，账本记录确认时间和最终费用。

## 5 测试矩阵和验收方法

### 5.1 标准样本

| 测试 | 数量和规则 | 用途 |
| --- | --- | --- |
| 双任务预热 | T2VA、FL2VA 各 2 条，不纳入稳态统计 | 发现加载、编译与接口问题 |
| T2VA 主基线 | 6 个场景 × 5 个 seed，共 30 条 | 时延、成功率、质量与场景覆盖 |
| FL2VA 主基线 | 4 个场景 × 3 个 seed，共 12 条 | 首帧保持、动作与音频表现 |
| 官方 API | 6 条 T2VA 和 4 条 FL2VA，各取一个匹配场景 | 业务效果、交付时延和价格比较 |
| 稳定性 | 60 分钟，单服务持续运行 | 发现显存增长、崩溃、失败与吞吐波动 |
| 并发观察 | C2 和 C4 各一批，分别提交 2 和 4 条 | 观察排队、内存与完成节奏 |
| Turbo | 3 条 T2VA 和 3 条 FL2VA，匹配基线样本 | 8 步加速的速度与质量代价 |

建议场景如下，执行前写入固定清单；同一个配对使用完全相同的提示词和首帧文件。T2VA 每场景 seed 为 1101 至 1105；FL2VA 每场景 seed 为 2101 至 2103。官方 API 若不提供 seed 字段，则不声称随机性一致。

| ID | 场景 | 主要核查点 |
| --- | --- | --- |
| T01 | 白猫沿溪边行走，伴脚步声与流水声 | 主体稳定、步态、环境音 |
| T02 | 森林中的连续航拍，伴风声与鸟鸣 | 运镜连续性、场景结构 |
| T03 | 香水瓶产品近景，镜头缓慢环绕，伴轻微环境音 | 产品几何、反光、镜头控制 |
| T04 | 人物按给定一句短台词说话 | 台词遵循、口型与音画同步 |
| T05 | 两人对话，明确轮次和镜头约束 | 人物区分、动作与说话归属 |
| T06 | 雨中街道，车辆缓慢驶过，伴雨声与轮胎声 | 多物体运动、声音和视觉事件对应 |
| F01 | 人像首帧自然转头并眨眼 | 身份保持、首帧一致性 |
| F02 | 产品首帧缓慢推近或转动 | 外形和材质保持 |
| F03 | 动物首帧开始行走 | 主体一致、运动合理性 |
| F04 | 风景首帧产生缓慢运镜与环境运动 | 构图、空间和连续性 |

首帧素材采用用户有权使用或明确开放许可的文件，记录来源与 SHA256，不引入额外付费素材服务。最终完整提示词在测试前冻结，并按各场景核查点准备人工评分表。

### 5.2 视频有效性与质量

每条输出先核对 HTTP 状态和内容类型，避免把错误 JSON 保存成 MP4。使用 ffprobe 读取视频、音频、分辨率、帧率、帧数和实际时长，再以 ffmpeg 完整解码。技术有效性至少要求视频可完整解码、包含可解码音频、规格符合请求或有明确偏差记录。

人工按提示词遵循、画面与运动、首帧保持、音频质量、音画同步各 1 至 5 分评分，并标记可交付与否及缺陷时间戳。只在适用任务评分首帧保持。API 和自建样例以匿名 A/B 顺序配对；默认由用户完成一轮复核，不假设存在第二位独立评审员。人工复核可以在释放 GPU 后完成。

成功率的分母包含所有已提交任务；另报可交付率。预热、基线、稳定性、并发和 Turbo 分开统计。主基线给出 P50、P95、最小/最大值和样本量；30 条与 12 条样本的尾延迟只能作初步参考，尤其 12 条的 P95 接近最大值。

### 5.3 Turbo 的独立配置

固定仓库 `lightx2v/Minimax-h3-Turbo` 的 Diffusers 文件：

```text
minimax_h3_fl2v_turbo_8step_v1.0_768p_bf16.safetensors
num_inference_steps: 8
flow_shift: 6
audio_flow_shift: 3
LoRA backend: peft
LoRA rank: 128
declared alpha: 8
activation scale: 1
```

执行前固定该文件的 revision 和 SHA256，确认 v0.32.0 的服务与请求两端都正确激活 LoRA。通过 `--task-type fl2va`、`--lora-backend peft` 和精确文件路径启动，按该版本 API 显式传递激活项。不要混用 ComfyUI 融合文件，也不要仅加载文件却未在请求中启用。

最多 45 分钟，复用 6 个基线场景，独立记录启动成本、时延、质量和成本。若支持不符或频繁失败，则跳过并说明。Turbo 结果不能用于替代 BF16 50 步基线，也不能把上游不同精度、帧数或硬件的测试直接拿来算加速比。

## 6 官方 API 对照与成本判定

### 6.1 调用方式

仅使用 `.env` 中已配置的官方国际站 `MINIMAX_BASE_URL`、`MINIMAX_API_KEY`，不接入 Tokenbay。以下为前期核对的接口结构，执行前再与官方文档复核。

```text
POST https://api.minimax.io/v2/video_generation
GET  https://api.minimax.io/v2/query/video_generation/{task_id}
Authorization: Bearer <仅从环境读取>
model: MiniMax-H3
resolution: 768P
duration: 5
ratio: 16:9
content: 文本；FL2VA 另加 role=first_frame 的 image_url
```

任务 ID 返回后立即落盘。POST 发送后发生超时或连接中断，先判断是否已经创建任务，不盲目重提以免双重计费。建议轮询间隔 2 秒并记录间隔；API 交付时延包含排队、执行和轮询发现误差。保留原始脱敏响应、usage、失败原因和下载时间。

前期调研的 MiniMax-H3 768P 标准价为每输出秒 $0.08，名义 5 秒约 $0.40，10 条约 $4；本轮单首帧不超过文档当时的免费图片额度。执行前复核当前价格和账户余额，不把标价当成已结算费用。

### 6.2 可比范围

两边使用相同提示词、首帧素材、名义 5 秒、768P 级别和有声视频任务。API 不暴露的 seed、采样步数或内部优化无法控制，因此这是业务效果和交付成本比较。自建基线 1344×768、通常 124 帧约 5.17 秒；API 16:9 的实际像素和时长可能不同。逐条记录实际值，并同时报告每条与每有效输出秒成本，不宣称逐像素或采样过程完全一致。

对两侧分别记录客户端提交至完整文件可用的交付时延，并在可用时补充服务端核心时延。自建的编码器、DiT、VAE、编码耗时仅从真实日志或测量获得；API 未暴露的阶段耗时不得反推为实测。

### 6.3 成本口径

至少报告以下三类成本：自建稳态服务成本、自建含冷启动和失败的本次实验成本、包含官方 API 对照在内的整个 PoC 总花费。人工时间另列工时，不默认为零，也不混入未约定的美元时薪。

自建一个测量窗口的成本按该窗口的实际租赁与相关费用除以有效输出数计算。并发时不能逐条将 E2E 时延乘整机单价后求和，否则会重复计算同一时段。失败费用计入，另列按成功和按人工判定可交付结果计算的两种单位成本。

以 $13.872/h 和 API 每条 $0.40 为例，100% 利用率下，成功输出平均间隔约 104 秒才与 API 标价相当，尚未计存储、部署、失败和人工成本。按相同简化假设，50% 与 25% 利用率分别约需 52 秒和 26 秒。该值是成本筛选线，不是速度预测或通过门槛；最终依真实吞吐、质量和账单判断。

最终成本模型给出 25%、50%、100% 利用率三档，写清冷启动摊销的使用次数或产量假设。若质量不满足使用要求，便宜的失败样片不能算成性价比优势。

## 7 部署与验证操作清单

下列命令是后续执行骨架，当前保存方案不执行。镜像、路径、NUMA 和 rank 映射必须在真实机器验收后填写；按固定版本的 help/API 文档检查参数后才开始负载。

### 7.1 硬件与运行时记录

```bash
nvidia-smi -L
nvidia-smi topo -m
nvidia-smi nvlink -s
numactl -H
nvidia-smi --query-gpu=index,name,memory.total,power.limit,temperature.gpu --format=csv
```

补充 `df -h`、网络小样测试、驱动/运行时版本、P2P 测试和 NCCL all-reduce/all-to-all 小样。没有 NVLink 不等于验收失败；重点是实际 PCIe/NUMA 和 P2P 行为。先记录测量工具版本与原始输出。

### 7.2 下载与启动

```bash
hf download MiniMaxAI/MiniMax-H3 \
  --revision 42ed227ee7df40d41602854ae760620d6eb651fe \
  --include 'FL2VA/**' \
  --local-dir /data/models/MiniMax-H3

# GPU_ORDER 必须先按本机拓扑设置；不可填入未经验证的示例顺序
CUDA_VISIBLE_DEVICES="${GPU_ORDER:?先完成拓扑映射}" \
VLLM_WORKER_MULTIPROC_METHOD=spawn \
VLLM_OMNI_VIDEO_SYNC_TIMEOUT=1800 \
vllm serve /data/models/MiniMax-H3/FL2VA \
  --omni --host 127.0.0.1 --port 8091 --trust-remote-code \
  --num-gpus 8 --tensor-parallel-size 4 --usp 2 --ring 1 \
  --text-encoder-tp-size 8 --vae-patch-parallel-size 8 \
  --vae-parallel-mode tile --vae-use-tiling \
  --diffusion-attention-backend CUDNN_ATTN
```

在选定镜像内执行启动命令，保留完整 stdout/stderr。只在本机 NUMA 信息确认后添加相应 `numactl --interleave` 前缀。服务绑定回环地址，通过 SSH 隧道访问。

### 7.3 请求与输出验证

自建 `/v1/videos/sync` 用于单请求冒烟；异步 `/v1/videos` 及该固定版本的查询、下载接口用于并发和队列观察。冻结请求字段，T2VA 与 FL2VA 分别显式指定 task；首帧通过 `input_reference` 发送。实现时检查该版本返回结构，不假设新版 API 字段兼容。

```text
prompt: 来自固定场景清单
width: 1344
height: 768
fps: 24
num_inference_steps: 50
flow_shift: 12
seed: 来自固定清单
extra_params: task=t2va 或 fl2va，duration=5.0，audio_flow_shift=3.0
input_reference: 仅首帧 FL2VA 传入固定 PNG
```

输出到临时文件，HTTP 和媒体类型验证通过后再命名为 MP4。记录请求时钟、完成时钟、下载完成时钟；用 monotonic 计算耗时，以 UTC 时间戳关联日志。GPU 利用率、显存、功耗、温度和时钟约每秒采集一次，诊断工具的额外开销单独标注。

```bash
ffprobe -v error -show_format -show_streams -of json output.mp4
ffmpeg -v error -i output.mp4 -f null -
```

## 8 证据目录和最终报告

### 8.1 证据结构

```text
h3-pro6000-poc/
  PLAN.md
  MiniMax_H3_PRO6000_8GPU_PoC_Plan.docx
  NEW_SESSION_PROMPT.txt
  scripts/                 后续实现
  cases/                   提示词、seed、素材清单与许可
  runs/<run_id>/
    environment/           GPU、拓扑、镜像、版本、模型校验
    deploy/                启动命令、阶段日志和时间
    requests.jsonl         脱敏请求与状态，task_id 即时保存
    results.csv            每次尝试及其指标，保留失败
    telemetry/             GPU、系统与可用的通信测量
    outputs/               MP4、ffprobe 与解码日志
    quality/               匿名配对、评分、缺陷时间戳
    cost/                  估算、预留、结算、释放证明
  report/                  实测阶段生成的中文详细报告
```

单条记录至少包括 run_id、case_id、task、variant、seed、配置哈希、素材哈希、provider、model_revision、task_id、状态与失败原因、提交/完成/下载时间、服务端可用阶段耗时、实际帧数/时长/分辨率、音视频解码结果、费用来源、估算/实际金额、可交付评分和输出路径。证据中不包含 API Key、完整 `.env` 或认证请求头。

### 8.2 实测报告结构

1. 决策摘要：是否适合继续自建、适合的场景和负载、与 API 的取舍、下一轮需要解决的问题。
2. 环境与复现：云厂商/offer、完整硬件、拓扑、软件和模型版本、激活优化、成本计价和复现步骤。
3. 功能与质量：T2VA、FL2VA、解码通过率、人工可交付率、API 匿名配对、失败案例与视频索引。
4. 性能与稳定性：各独立组的样本量、P50/P95、吞吐、排队/阶段时延、峰值显存、功耗、60 分钟记录和通信证据。
5. 成本：本次实际花费、冷启动占比、失败成本、每条/每输出秒/每可交付条成本，以及三档利用率模型。
6. Turbo 与限制：匹配样例的速度和质量变化、缺测项、样本量影响和追加验证建议。

报告不把 60 分钟观察写成生产可靠性承诺，也不把上游 Pro 5000、Pro 5500 或 SGLang 不同配置的数据当成本次 Pro 6000 实测。没有完成测试时交付故障与费用报告，而不是填入预测数值冒充结果。

## 9 后续会话的起点

先读取本方案和 `NEW_SESSION_PROMPT.txt`，检查当前目录与 git 状态，保留已有研究文件。可自主完成独立脚本、测试清单、dry-run、预算核算和刷新库存。下一次需要用户输入的实质事项是租机控制方式；在这之前准备好具体 offer、费用和操作方案，避免重复询问已经确定的地域、预算、目标或对照方。

仓库原 `HANDOFF.md` 的 Tokenbay 约 $21 历史预算属于另一任务。当前 H3 任务预算为 $150，但该预算共识不等同于已选择租机控制方式或当前已启动收费流程。

## 10 参考来源

库存、价格、模型仓库和软件版本均为前期调研记录，实际执行前重新核对。以下来源供复现和追溯。

- [MiniMax H3 模型仓库](https://huggingface.co/MiniMaxAI/MiniMax-H3)
- [MiniMax H3 模型元数据](https://huggingface.co/api/models/MiniMaxAI/MiniMax-H3?blobs=true)
- [vLLM Omni v0.32.0 发布](https://github.com/vllm-project/vllm-omni/releases/tag/v0.32.0)
- [v0.32.0 的 RTX PRO 5000 H3 参考配方](https://github.com/vllm-project/vllm-omni/blob/v0.32.0/recipes/MiniMaxAI/MiniMax-H3-RTX-PRO-5000.md)
- [v0.32.0 的 H3 配方与 Turbo 配置](https://github.com/vllm-project/vllm-omni/blob/v0.32.0/recipes/MiniMaxAI/MiniMax-H3.md)
- [官方镜像标签元数据](https://hub.docker.com/v2/repositories/vllm/vllm-omni/tags/v0.32.0)
- [MiniMax H3 Turbo 仓库](https://huggingface.co/lightx2v/Minimax-h3-Turbo)
- [MiniMax 官方价格](https://platform.minimax.io/docs/pricing/overview)
- [MiniMax H3 创建任务](https://platform.minimax.io/docs/api-reference/video-generation-v2-create)
- [MiniMax H3 查询任务](https://platform.minimax.io/docs/api-reference/video-generation-v2-query)
- [Vast.ai 实例计价](https://docs.vast.ai/guides/instances/pricing)
- [Vast.ai 实例限制与常见问题](https://docs.vast.ai/guides/reference/faq/instances)

## 11 执行准备增补（2026-11-07，尚未收费执行）

- 按用户最新要求，H3、CUDA、vLLM、模型权重及完整媒体工具环境只部署在租用的 8×PRO 6000 服务器。笔记本仅使用已有 Python 标准库运行控制/请求脚本并保存素材、报告与证据；不创建本地 H3 虚拟环境，不下载权重，不在本机编译或安装推理框架。
- 已准备 `scripts/`、`config/`、10 个准确场景及逐场景评分清单、4 个 CC0/公有领域首帧、SHA256 清单、68 条固定请求以及独立的60分钟稳定性策略。当前实测仍为空；dry-run/mock 不能作为 H3 性能或质量结果。
- v0.32.0 配方的 Turbo 表格写 `num_inference_steps=9`，但同一固定 commit 的 `pipeline_minimax_h3.py::_validate_turbo_sampling` 及 `test_minimax_h3_turbo_matrix.py` 明确要求请求 **8**，9 会被拒绝。最终配置保留用户指定的8次更新/请求8，9仅为 sigma 点数；服务器加载权重前用 `runtime_contract.py` 检查已安装镜像。若镜像契约不符，保留诊断，暂停 Turbo，不现场重编框架。
- BF16 保持请求50步。参考文档中的49次更新及124帧均不能硬编码为本次实际值；以本次输出/逐请求日志为准，无证据则标记缺失。请求启用 `return_stage_metrics=true`，完整保存服务返回的阶段指标及推理时间，标明观测设置。
- 官方首帧 API 的比例以输入图决定，`ratio` 使用 `adaptive`；T2VA 使用 `16:9`。两侧读取同一份 1344×768 PNG 字节，输出实际规格另测。
- 列表旧价约$13.872/h含小额默认磁盘；当前500GB得州样本为 GPU约$13.866667/h、磁盘约$0.138889/h，合计约$14.005556/h，流量另计。库存、税费与实际下单仍需刷新；详见 `EXECUTION_REVIEW.md` 与原始报价 JSON。
- 首条 API 样例可在笔记本用标准库提交/轮询/下载以验证账户。按用户环境要求，本机不安装 ffmpeg；此时状态为 `downloaded_pending_server_validation`，在服务器完成 ffprobe与全文件解码后才记技术有效。该条计入10条，不重提。
- 本地已完成的测试、未完成的GPU/媒体检查，以及一次测试日志凭据暴露及清理记录，见 `qa/` 与 `research/credential-incident.md`。原 Word 方案保留为v1.0参考，后续实测报告另行生成与逐页渲染验收。

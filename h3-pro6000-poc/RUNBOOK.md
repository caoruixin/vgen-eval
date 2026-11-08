# 授权后的执行手册

此手册是可审阅的操作安排，不是已经租机或调用API的记录。总上限$150，GPU$120、磁盘流量$20、官方API$10；所有实例累计最多8小时，单机完整8卡GPU不高于$15/h，最多替换一次。等待、镜像拉取、下载、失败、回传和释放都计费。

## 1 租前：在笔记本只用已有标准库

1. 用户明确选择控制方式和收费执行范围后，执行者把原话、时间、offer、分项预算，以及正常/紧急释放范围写入`private/authorization.json`。模板中的`false`不得自行改成用户已同意。Key不进入该文件。
2. 正式执行前建议用户轮换曾在测试工具日志暴露的MiniMax Key；本机日志已清理，详见`research/credential-incident.md`。不读取或打印完整.env，不修改.env，不要求粘贴已有Key。
3. 先在本机提交一条T01官方API对照，验证账户能创建任务并成功下载。此时只做HTTP、Content-Type、MP4签名检查；完整解码在服务器执行。样例计入10条与$10，不能因之后跑整组再次提交。

```bash
# 仅在真实授权记录已写入后执行；不安装本机环境
python3 scripts/budget.py init
python3 scripts/run.py --phase comparison --case T01 --limit 1 \
  --run-dir runs/api --execute --defer-media-validation-to-server
```

若账户/模型/余额失败，保存脱敏响应，不租GPU。POST断开或5xx后保存“提交状态不明”，先用`--list-tasks`或官方控制台核实；有ID用`--resume REQUEST_ID`仅查询/下载，禁止重复POST。

4. 刷新`preflight.py --offers`，核对具体offer、8完整Server卡、500GB、GPU/磁盘/上下行费率、最大租期、税费和充值附加费。控制台余额目前为0，充值和余额不是已消耗成本；不自动充值，不开自动续费，实际收费必须仍在分项与总额内。
5. `python3 qa/run_checks.py`与`python3 scripts/bundle.py`完成后，把生成的payload摘要及报价供最后审阅。镜像不在笔记本拉取。SSH使用已有密钥；不把私钥或MiniMax Key复制到服务器。

## 2 创建实例与计费时钟

在Vast选择on-demand、固定官方镜像摘要、SSH launch、500GB容器本地磁盘。不租独立持久卷；不使用默认16GB，不在Vast容器内运行Docker-in-Docker。

```text
vllm/vllm-omni@sha256:6b44ee504a0dc3a21eacf72e7dc5da318b7bcc8ed0e349dc928e67278414430c
```

镜像配置已核查：Linux amd64、压缩层9,954,971,979字节、CUDA_VERSION=13.0.2、Entrypoint/Cmd均为空。Vast的SSH模式会注入启动脚本。把`scripts/onstart.sh`的内容设为onstart；其只保留provider本来提供的实例级凭据在服务器`/data/h3-control/`，权限0600，不进入证据包。实际SSH/驱动行为必须租后验证；CUDA13.x按NVIDIA说明至少R580驱动，不用列表版本代替实际版本。

从发起创建/最早计费时刻记`started_utc`，不能从SSH成功才记。创建返回实例ID立即保存；创建超时先查Instances，不再次Rent。写`private/rental.json`，填实际GPU/磁盘/流量单价、实例ID、offer、创建证据；`hard_release_utc`至少比累计8小时提前5分钟，替换时扣除前一个实例的全部已付费时间。

账本主本保留在笔记本。先登记该实例，按报价预留正常+可能替换的流量包（得州当前建议保守预留$2，磁盘随租期另计）。再把不含Key的授权、租赁与账本快照复制到服务器。服务器GPU任务只读这份快照；官方API在本机记账。分项上限始终分别生效，API新增提交后在无账本写入时原子刷新服务器快照；最终以笔记本主本与账单核对。

```bash
python3 scripts/budget.py register --json private/rental.json
python3 scripts/budget.py reserve --category storage_network --usd 2 \
  --source traffic_envelope_quote_330GB_in_20GB_out
```

把`output/h3-server-payload.tar.gz`复制到服务器`/data/`并解包。只复制明确的授权/租赁/账本文件到`/data/h3-pro6000-poc/private/`，不要递归复制本机private目录，不复制.env。复制文件前后用SHA256核对payload。

## 3 下列环境准备全部在服务器执行

```bash
cd /data/h3-pro6000-poc
mkdir -p private runs/self/environment runs/self/deploy runs/self/telemetry runs/self/cost
export RUN_DIR=/data/h3-pro6000-poc/runs/self

# 先启用实例级guardian。启动失败不得继续下载模型。
nohup python3 scripts/guardian.py --run-dir "$RUN_DIR" --rental private/rental.json \
  > "$RUN_DIR/cost/guardian-console.txt" 2>&1 &
echo "$!" > "$RUN_DIR/cost/guardian.pid"
```

如SSH session未继承provider变量，guardian会从onstart生成的0600文件读取。若镜像未执行onstart或实例Key缺失，只核实缺失，不把账户Key放上服务器；在20分钟内修复或释放。SSH尚未建立时，笔记本控制端持续观察Instances，20分钟验收边界仍然生效。

使用镜像预装环境，不源码编译vLLM/Torch。仅当必要工具缺失时，在该Ubuntu容器内安装`ffmpeg numactl pciutils ca-certificates`等发行版包；此等待也计入20分钟验收。不要把这段命令在笔记本执行。

```bash
python3 scripts/hardware.py --out "$RUN_DIR/environment"
timeout 180s torchrun --standalone --nproc-per-node=8 scripts/nccl_check.py \
  --out "$RUN_DIR/environment/nccl.json"
python3 scripts/runtime_contract.py --out "$RUN_DIR/environment/runtime-contract.json"
python3 qa/server_media_checks.py --execute --out "$RUN_DIR/environment/media-qa"
```

`hardware.py`记录真实8卡名称/显存/MIG、PCI bus ID、宿主/容器RAM、驱动、Torch/CUDA/cuDNN/NCCL、磁盘、PCIe/NUMA、P2P；NCCL小样检查all-reduce/all-to-all的正确性与测得耗时。没有NVLink不单独判失败。`df`可能反映宿主文件系统，500GB已分配量另从Vast实例合同/创建证据确认。

按真实topo和NUMA填写`private/topology-mapping.json`，保留映射理由；不照抄旧机器的GPU顺序。记录实例实际500GB和精确镜像摘要的JSON证据，路径写入mapping。之后：

```bash
python3 scripts/accept_hardware.py --mapping private/topology-mapping.json \
  --rental private/rental.json --run-dir "$RUN_DIR"
nohup python3 scripts/telemetry.py --out "$RUN_DIR/telemetry/gpu.jsonl" \
  > "$RUN_DIR/telemetry/collector-console.txt" 2>&1 &
echo "$!" > "$RUN_DIR/telemetry/collector.pid"
```

20分钟内没有`HARDWARE_ACCEPTED.json`则guardian按已授权范围止损释放。最多替换一次；两次计费时间合并。运行时契约检查若显示Turbo接口与固定源码不同，暂停Turbo、保留诊断，不能现场编译修框架或把9步盲试成本藏进基线。

## 4 只下载FL2VA，启动固定BF16

```bash
python3 scripts/download_model.py --run-dir "$RUN_DIR" \
  --out "$RUN_DIR/environment/model-files-verified.json"
nohup setsid python3 scripts/launch.py --execute --mapping private/topology-mapping.json \
  --out "$RUN_DIR/deploy/bf16" > "$RUN_DIR/deploy/server-bf16.log" 2>&1 &
echo "$!" > "$RUN_DIR/deploy/server-bf16.pid"
```

下载器固定revision，只允许`FL2VA/**`并校验81个文件的大小/LFS SHA256或Git blob SHA1，记录实际SHA256。下载/启动目标100分钟，但不是承诺。镜像加载、依赖/下载失败都计费；累计付费2小时仍无技术有效的自建视频就止损。官方样例、合成媒体QA不能满足这个条件。

服务绑定127.0.0.1:8091，不公网暴露。准备就绪后保存`/openapi.json`、启动日志与实际默认优化。读取`launch-command.json`核对TP4×Ulysses2、encoder TP8、VAE tile8、CUDNN_ATTN，未开启FP8/FP4/DLO/近似优化。默认regional compile保留，预热4条独立统计。Torch/cuDNN/NCCL版本只来自本机environment记录。

```bash
python3 scripts/run.py --phase warmup --run-dir "$RUN_DIR" --execute
python3 scripts/run.py --phase baseline --run-dir "$RUN_DIR" --execute
```

预热中每任务一条sync和一条async，确认两套接口及FL2VA上传。基线默认sync，保存X-Request-Id/X-Inference-Time-S/X-Stage-Durations/X-Peak-Memory-MB；异步也保存完整stage/metrics。`return_stage_metrics=true`明确记录；它是观测设置，报告说明开销。实际denoise更新数有日志/指标才填写，不把参考49或调度点数当作执行计数。所有输出ffprobe读取真实规格，ffmpeg解码所有音视频流；人工音画同步仍待用户。

## 5 官方API余9条与其他组

自建双功能有效后，可在本机用标准库提交剩余对照，与服务器基线并行调度以减少GPU等待；不在服务器测量窗口运行官方视频的CPU解码。命令跳过已有首条成功下载，绝不重复POST：

```bash
# 笔记本，本机没有H3环境
python3 scripts/run.py --phase comparison --run-dir runs/api --execute \
  --defer-media-validation-to-server --skip-existing-completed
```

官方视频下载后复制`runs/api`到服务器，在非性能测量窗口执行`python3 scripts/validate_downloads.py runs/api`。复制回来更新的验证JSON/CSV。计费响应usage仅支持标价估算，实际API扣费另结算；$1/条保守待结算预留不会自动冒充$1实付。API未暴露seed/steps/内部优化，比较业务效果与交付成本，不能声称采样等价。

先完成BF16与API主对照，再按实际时间/预算决定以下组；每组独立窗口、独立variant。稳定性最多60分钟，C2/C4各一批共30分钟。首次异常先诊断，不连续自动重试；不可恢复GPU、反复崩溃、NCCL失败停止压测。

```bash
python3 scripts/run.py --phase stability --run-dir "$RUN_DIR" --execute
python3 scripts/run.py --phase concurrency_c2 --run-dir "$RUN_DIR" --execute
python3 scripts/run.py --phase concurrency_c4 --run-dir "$RUN_DIR" --execute
```

Turbo可选：从下载、服务切换到最后一条完成合计最多45分钟。先停止已记录进程组中的BF16服务，确认显存释放，下载锁定Turbo文件；不杀guardian。用同一个拓扑和`launch.py --turbo`启动，PEFT和精确路径；请求显式激活LoRA scale1、8步、flow shift6/3。共3T2VA+3FL2VA匹配样本，绝不并入BF16。

```bash
python3 scripts/download_model.py --turbo --run-dir "$RUN_DIR" \
  --out "$RUN_DIR/environment/turbo-file-verified.json"
# 在记录Turbo准备开始时刻、停止BF16、核对余量后启动：
nohup setsid python3 scripts/launch.py --execute --turbo --mapping private/topology-mapping.json \
  --out "$RUN_DIR/deploy/turbo" > "$RUN_DIR/deploy/server-turbo.log" 2>&1 &
python3 scripts/run.py --phase turbo --run-dir "$RUN_DIR" --execute
```

`run.py`的2700秒限制覆盖请求阶段。下载和重启也要从45分钟总额扣除，执行者据时间减少可提交条数；不把45分钟再额外分给下载/重启。若速度不足，优先删减Turbo/并发/额外稳定性样本，不延长总租期。

## 6 归档、释放和真正结束计费

笔记本每约1分钟增量回传runs证据，避免最后才复制所有文件。日志/计费状态约每15分钟核对，20分钟、2小时、硬释放边界由guardian和本机控制端共同观察。guardian只知道本实例，不能代替本机检查充值、账单和残留卷。

至少提前25分钟停止提交，等当前已提交任务结束或明确记录取消。关闭推理服务、停止遥测、让日志落盘。关闭推理服务不代表结束租赁；不使用暂停实例作为释放证明。

```bash
# 服务器，停止提交后收尾
touch "$RUN_DIR/STOP_SUBMISSIONS"
touch "$RUN_DIR/telemetry/gpu.stop"
# 按保存的PID/进程组结束推理服务；不要全局pkill python
python3 scripts/archive.py manifest --run-dir "$RUN_DIR"
```

把完整runs目录回传到笔记本，再在本机运行`archive.py verify`。该验证只算哈希，不装媒体或推理环境。`cost/archive-verified.json`通过后把它传回服务器，正常释放动作先dry-run，再在已授权范围内执行：

```bash
# 服务器，先查看具体实例和归档验证绑定
python3 scripts/release.py --run-dir "$RUN_DIR" --rental private/rental.json \
  --verified-copy private/archive-verified.json
# 用户已授权正常释放时才加 --execute，不再逐步重复征求确认
```

`destroy success`代表请求已接受。还要从笔记本核对Vast Instances与Storage、保存终态截图/API和账单，确认实例与不需要的存储不再计费，然后关闭账本租期。若SSH断开、destroy响应丢失，先查状态，不重新租赁或假称已释放。8小时/分项/总预算的边界优先于补样本；预算紧急释放范围必须在本轮授权记录中明确。

## 7 人工质量与最终报告

释放后运行`analyze.py`分组统计；并发单位成本按整批窗口除成功数，不能相加每条E2E×整机时价。小样本P95和60分钟观察的限制必须保留。账本中的预留/标价估算/结算分开，附invoice/截图和释放证据；稳态、自建含冷启动和整个PoC三种口径分开，人工工时另列。

`quality.py`只对哈希匹配的成功输出生成匿名A/B和空白CSV，映射单独保存在private。由用户作为首位人工评审完整观看/听音并打分，不能预填分数或编造其他评审者。可交付率须有人工结果，不能用技术解码成功率替代。

最终中文Markdown与Word包含决策摘要、实际环境/复现、功能质量、性能稳定性、成本/利用率、Turbo/缺测、下一步，附数据和样例。Word届时使用documents技能并逐页渲染检查。当前不生成伪“实测报告”。

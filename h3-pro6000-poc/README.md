# MiniMax H3 八卡 RTX PRO 6000 实测结果

本目录保存2026年11月7日自费PoC的可复核结果。服务器释放前，自建与官方API两份原始归档分别通过948项和107项文件校验；87条原始视频已回传本机。可分享的完整结果快照位于 [results/](results/)，已从脱敏交付包逐项验SHA256提取。

## 报告和视频

| 内容 | 入口 |
| --- | --- |
| 中文详细报告 Word 共20页 | [Word报告](results/reports/final/MiniMax_H3_PRO6000_8GPU_PoC_Report.docx) |
| 中文详细报告 Markdown | [Markdown报告](results/reports/final/MiniMax_H3_PRO6000_8GPU_PoC_Report.md) |
| 自建原始视频77条 | [自建输出](results/runs/self/outputs/) |
| 官方API原始视频10条 | [官方输出](results/runs/api/outputs/) |
| 十场景二十条匿名A/B并排播放 | [评审网页](results/quality/blind-review/review.html) |
| 提示词 首帧 许可和哈希 | [cases/](results/cases/) |
| 逐请求CSV 分组时延 遥测和费用 | [reports/data/](results/reports/data/) |
| 费用与服务器释放证据 | [runs/control/](results/runs/control/) |
| 全部交付文件清单 | [PACKAGE-MANIFEST.json](results/PACKAGE-MANIFEST.json) |
| 原始回传及交付包核验记录 | [SOURCE-ARCHIVE-VERIFICATION.json](results/SOURCE-ARCHIVE-VERIFICATION.json) |

在GitHub中点击HTML会显示源码。克隆仓库后，用Chrome打开 `h3-pro6000-poc/results/quality/blind-review/review.html` 即可播放；同目录已带齐视频和首帧。评分可以留空，A/B身份尚未解盲。播放时一次只听一侧，避免声音叠加。

## 实测结论

BF16基线42/42技术有效，T2VA的P50为178.23秒，FL2VA为191.61秒；整体吞吐19.74条/小时，GPU加磁盘稳态估算0.709美元/技术有效条。独立稳定性60分钟产生19条有效结果，末条在窗口边界主动取消；C2/C4共6条有效。另有6条8步配置样本，Turbo适配器激活未证实，不能据此宣称Turbo收益。用户已选择先交技术报告，人工质量、可交付率和每可交付成本待补。

2026-11-07T17:10:38Z的Vast余额支出观测为64.90美元，官方API按50计费秒的标价估算4美元，合计约68.90美元。正式消费发票和API实际账单未取得，费用与账单时长差异在报告中单列。实例及不再需要的存储已销毁，独立页面观测显示实例0、卷0、当前费率0美元/小时；自动充值关闭。

## 克隆后验证

在仓库根目录执行，只使用Python标准库：

```bash
python3 h3-pro6000-poc/results/verify_backup.py
```

校验会核对交付清单中1121个文件的大小与SHA256，再用逐请求的实测媒体哈希独立核对77条自建和10条官方原片，并检查20条匿名评审视频是否齐全。不会调用API、加载权重或安装依赖。

交付包里的JSON/JSONL含23份规范化或临时媒体URL脱敏文件。清单同时保存来源SHA256与包内SHA256；请使用包内哈希核对本目录。原服务器archive-manifest是当时完整回传的证明，本快照按交付白名单整理，重复压缩checkpoint与部分内部控制文件保留在本机原始目录。

87条视频、环境、日志、CSV和报告均不依赖已释放的服务器。模型权重不属于结果备份，始终只在租用服务器；密钥、私有授权、银行卡资料和未解盲映射不进入Git。本机原始工作目录和501MiB ZIP仍保留，Git中的规范结果入口统一为results/。

## 复现资料

[原方案](PLAN.md)、[操作手册](RUNBOOK.md)、[租前执行审阅单](EXECUTION_REVIEW.md)记录实验设计。实际执行源码以 [冻结harness归档](results/runs/self/environment/harness-baseline-source.tar.gz) 为准；快照另带控制与分析工具。未来重新租赁或生成需要新的费用授权，本备份不包含可复用的账户凭据。

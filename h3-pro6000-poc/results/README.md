# MiniMax H3 八卡 PRO6000 实测交付包

先读 reports/final/ 的中文 Word 或 Markdown。报告中的技术有效性、主观质量、公开价格估算、账户扣费观测和最终发票状态分别列示。

- reports/data/：逐次尝试、分组时延、完整窗口成本、场景、遥测与对账数据。
- runs/self/ 与 runs/api/：真实原始视频、终态记录、HTTP/JSONL、全文件解码证据和环境日志。
- cases/、config/：冻结提示词、输入PNG、许可、哈希、模型文件锁和请求矩阵。
- quality/blind-review/：20条匿名A/B原片、共同首帧、离线评审页和评分表。没有人工评分的字段保持空白。
- runs/self/environment/harness-baseline-source.tar.gz：实际执行时冻结的脚本。复现时在干净项目目录解压该文件，再放入 cases/ 与 config/；不要把后续分析工具视作原服务源码。
- runs/control/：本次结果回传与独立释放证据。关闭服务或归档成功本身均不代表已停止租赁计费。

本包不含 .env、私有授权与账号凭据、模型权重、银行卡资料或未解盲映射。服务器推理仅绑定127.0.0.1；复现必须在新租用且重新验收的8卡服务器进行，先明确新的费用授权，不能复用本次已销毁实例身份或租赁时钟。

JSON/JSONL 的临时媒体URL及凭据字段如有出现会脱敏。PACKAGE-MANIFEST.json 同时列出来源文件SHA256、包内SHA256及变更说明。服务器 archive-manifest.json 是当时回传核验的原始证据；本包按白名单整理，重复压缩快照与部分运行控制标记未纳入；请按本包manifest核验，记录中的来源SHA用于关联原始归档。

# 官方 API 元数据说明

records 的 versions 字段来自统一客户端配置，记录对照实验引用的本地部署配方与客户端哈希，不能视为官方 API 的后端版本。相邻 versions_scope 与 official_api_backend 明确此含义；model_revision 为 not_exposed_by_official_api。run.json 初版误复用了自建参考revision，原件保留于 recovery/run-original-reference-manifest.json，现已更正，并将该SHA另存为 benchmark_reference_model_revision。原始HTTP响应和历史JSONL未改写。官方后端软件、权重revision、seed及去噪步数均未公开。

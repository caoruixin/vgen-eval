# PRO6000 PoC 报告架构说明修订版

实验日期为 2026 年 11 月 7 日，架构说明修订日期为 2026 年 11 月 8 日。本次增加 H3-Base 推理流程、八卡并行配置、测量边界与实验阶段对照。没有新增性能样本或修改费用及质量结论。

- [在线阅读报告](https://video.llm-pro.cn/api/benchmarks/h3-pro6000-2026-11-07/artifacts/report.html)
- [H3 推理架构章节](https://video.llm-pro.cn/api/benchmarks/h3-pro6000-2026-11-07/artifacts/report.html#h3-inference-architecture)
- [Word 报告](architecture-v1/report.docx)
- [Markdown 报告](architecture-v1/report.md)
- [修订清单与文件哈希](architecture-v1/revision.json)
- [归档依据与版本](architecture-v1/provenance.json)

原始报告仍位于 `h3-pro6000-poc/results/reports/final/`，原始交付清单和 87 条视频保持不变。发布时只更新实验的报告引用；原报告另保留为 `archive-report.md` 和 `archive-report.docx` 下载项。旧修订文件不覆盖。

`architecture.md` 为新增章节源文件，`build_report.py` 使用原归档 Markdown 转 Word 工具与 Pillow 生成完整修订版。架构图同时保存 PNG 和可编辑 SVG。文档生成与排版检查使用 Codex 打包的文档运行时；中文字体为 Hiragino Sans GB。渲染器使用原项目的 `qa-fontconfig.xml`，使中文字体可被打包的 LibreOffice 发现。

发布之前先完成 Word 渲染和逐页检查，再部署应用代码并执行增量报告发布：

```bash
vgen import-report reports/h3-pro6000/architecture-v1/revision.json --dry-run
deploy/deploy.sh
deploy/publish-report.sh reports/h3-pro6000/architecture-v1/revision.json
```

发布命令校验所有文件哈希、归档报告来源和既有修订冲突，数据库写入前创建 SQLite 一致性备份。正式发布只修改对应实验的 `artifacts`，不会修改任务、评审、benchmark 请求或 Tokenbay 账本，也不调用生成接口。报告发布使用独立锁，可在站点运行时完成。

# 本地验收记录（2026-10-29）

环境：macOS Apple Silicon、本机 Ollama（Qwen3 4B Instruct / BGE-M3）、MinerU Basic、PostgreSQL、Redis、Qdrant、MinIO。官方 PDF 位于未提交的 `科研小助手/pdf/`。

| 场景 | 实测结果 |
| --- | --- |
| 版本去重 | v7 后上传 v1，识别同一 arXiv ID，建议保留较新版本；选择保留后旧版仍可解析。 |
| 矢量 PDF | v1 15 页、167 Block、24 标题、6 公式、4 表格；公式含 LaTeX，Block 坐标可定位。Redis 派发到 Celery `solo` 解析 Worker 后，任务在 30.1 秒内完成并写入 PostgreSQL、MinIO、Qdrant。 |
| 扫描 PDF | v1 扫描版 15 页、166 Block、6 公式、4 表格；OCR 入库任务完成。 |
| 超长教材 | 541 页、11,270 Block、904 Chunk；向量索引 72.8 秒完成，查询“Universal approximation theorem”返回第 43 页附近原文。 |
| 单篇与多篇问答 | `scripts/evaluate_rag.py` 经 Redis → Celery → Qdrant → Ollama 实测 4/4：Adam 参数 β₁=0.9、β₂=0.98；8 张 NVIDIA P100；Transformer 与 GPT-3 注意力架构及研究目标对比；无证据问题拒答。答案引用真实 Block ID，来源分别定位至原文第 1、2、7、8 页。逐行核验证据，不合格的事实会改写或拒答。 |
| 文献发现 | OpenAlex 与 arXiv 检索返回真实条目；Graph API 对 GPT-3 → Transformer 返回一条 OpenAlex 引用边。 |
| 阅读与翻译 | 浏览器点击正文引用 `[18]` 跳到参考文献并可返回；标题与摘要批量翻译返回译文。 |
| 绘图与脚本 | CSV 折线图生成 PNG 和原始 CSV Artifact，图注 `0.71 → 0.84` 与数据一致；Mermaid 脚本在浏览器渲染预览。 |
| 运行与质量 | `uv run pytest -q`（12 项）、`uv run ruff check services/api scripts`、`uv run ruff format --check services/api scripts`、`npm run format:check`、`npm run lint`、`npm run build` 通过；`npm audit` 0 项漏洞；API/Web Docker 镜像构建通过，本地 API 与登录页均返回 HTTP 200。 |

详细解析指标见 [official_evaluation.json](official_evaluation.json)，固定问答和证据页见 [rag_evaluation.json](rag_evaluation.json)。公网域名和外部模型/OCR API 尚未配置，因此云端调用适配器只做了接口测试。4 道固定题仅覆盖当前官方样例，不能代表开放问题的事实准确率；模型核验也不能替代人工查阅引文原文。

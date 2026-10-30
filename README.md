# 科研小助手 · AI Research Agent

面向学术论文的可溯源研究工作台。支持 PDF 导入与版本管理、原文定位问答、双语阅读、论文发现与订阅、引用图谱、写作与 CSV 绘图。项目依据根目录的[需求文档](AI科研助手Agent_项目需求文档.md)与[技术架构设计](AI科研助手Agent_技术架构设计.md)实现。

## 项目结构

- `apps/web`：Next.js 前端
- `services/api/app`：FastAPI、LangGraph、解析、检索与后台任务
- `services/api/migrations`：Alembic 数据库迁移
- `infra`：Docker Compose、镜像与 Caddy
- `scripts`：官方样例评测

## 本地运行

需要 Python 3.12、[uv](https://docs.astral.sh/uv/)、Node.js 24、Docker。官方 PDF 位于 `科研小助手/pdf`；该目录默认不加入 Git。

```bash
cp .env.example .env
# 编辑 .env：至少设置 APP_SECRET、DEMO_PASSWORD；模型端点按下节配置。
uv sync --extra dev --extra parser
docker compose --env-file .env -f infra/compose.yml up -d postgres redis qdrant minio
uv run alembic upgrade head
```

Compose 的基础镜像可通过 `.env` 中的 `POSTGRES_IMAGE`、`REDIS_IMAGE`、`QDRANT_IMAGE`、`MINIO_IMAGE`、`NODE_IMAGE` 替换；这台 Mac 的 Docker Hub/Quay 拉取不稳定时，已验证预装的 `postgres:17`、`redis:7`、`cgr.dev/chainguard/minio:latest` 及 `mirror.gcr.io/library/node:24-alpine` 可用。

分别在终端启动服务：

```bash
uv run uvicorn services.api.app.main:app --reload --port 8000
uv run celery -A services.api.app.worker.celery_app worker -l info -Q default
uv run celery -A services.api.app.worker.celery_app worker -l info -Q parser --pool=solo -n parser@%h
uv run celery -A services.api.app.worker.celery_app beat -l info
cd apps/web && npm ci && npm run dev
```

浏览器打开 `http://localhost:3000`，用 `.env` 中的演示账号登录。API 文档位于 `http://localhost:8000/docs`。若只验证 API，可设置 `DATABASE_URL=sqlite:///./dev.db`、`S3_ENDPOINT=`、`QDRANT_URL=`、`CELERY_TASK_ALWAYS_EAGER=1`，免启动容器运行确定性测试。

登录后访问 `/debug` 可查看 API 请求、SQLAlchemy 数据库查询活动、任务事件、Redis 队列和在线 Worker；页面每 4 秒更新。该诊断流保留最近约 3000 条事件，不记录 SQL 参数或请求正文。公网模式下需额外设置 `DEBUG_CONSOLE_KEY` 才能读取调试接口；未设置时接口保持关闭。该页面显示的是应用侧数据库活动和 PostgreSQL 统计，不直接读取数据库容器的原始日志。论文目录在 parser 队列任务完成后出现；若始终处于等待状态，先在调试后台确认 `parser` Worker 在线。只导入元数据的记录没有 PDF，也不会生成目录。

论文详情页在同一地址切换阅读、AI 辅助、专注与双语模式。主导航可折叠；文献大纲可在章节目录和 PDF 缩略图之间切换，也可收起。拖动面板分隔条或用方向键调整宽度，“恢复默认比例”可重置布局。双语模式下 PDF 与译文连续滚动、按内容块联动；PDF 仅渲染视口附近页面，内容块和已缓存译文按页加载。“翻译当前页”只翻译未缓存的内容块，结果持久化。MinerU 解析的独立公式以 LaTeX 保存，行内公式会在翻译时保留，译文用 KaTeX 渲染。旧版本可在论文详情页重新解析；与旧内容完全一致的段落译文缓存会被保留，发生变化的内容需要重新翻译。

### 本地模型与云端 API 切换

LLM、Embedding、Reranker 和扫描件 OCR 有独立配置。开发机可运行 Ollama 等兼容 OpenAI 接口的服务；示例：

```bash
ollama serve
ollama pull qwen3:4b-instruct
ollama pull bge-m3
```

在 `.env` 中设置 `LLM_PROVIDER=ollama`、`LLM_BASE_URL=http://localhost:11434/v1`、`LLM_MODEL=qwen3:4b-instruct`、`EMBEDDING_BASE_URL=http://localhost:11434/v1`、`EMBEDDING_MODEL=bge-m3`。Ollama 适配器使用原生接口；云端设 `LLM_PROVIDER=openai` 并填外部兼容 API 地址。本机 API 直接运行时使用 `localhost`；API 容器访问宿主机模型时使用 `host.docker.internal`。Reranker 可设置兼容 `/rerank` 的服务；未设置时使用混合召回结果。

扫描 PDF 使用本机 MinerU Worker：`uv run mineru-kit models download --tier basic` 后以 `PARSER_BACKEND=local` 运行。默认对 50 页以内的矢量 PDF 也做版式增强，提取公式 LaTeX 和表格；长文档使用分批 PyMuPDF 解析。如需快速开发测试，可设 `PARSER_VECTOR_ENHANCE=0`。公网部署时将 `LLM_*`、`EMBEDDING_*`、`RERANK_*` 指向云端服务，并设置 `PARSER_BACKEND=remote`、`MINERU_API_URL`、`MINERU_API_KEY`；业务代码和数据模型无需更改。远程解析适配 MinerU V1 `/v1/file_parse`；所选服务需要支持该接口。[接口说明](https://github.com/opendatalab/MinerU/blob/master/docs/en/usage/http_api.md)

### 公网部署准备

设置 `DEPLOYMENT_MODE=cloud`、`SITE_DOMAIN`、`PUBLIC_API_BASE_URL=https://你的域名`、`POSTGRES_PASSWORD`、`S3_SECRET_KEY`、强演示账号密码与所有外部 HTTPS 模型端点，并使用 `LLM_PROVIDER=openai`、`PARSER_BACKEND=remote`，再运行：

```bash
docker compose --env-file .env -f infra/compose.yml --profile cloud up -d --build
```

Caddy 统一代理网站和 `/api`。云端只有 80/443 对外开放；数据库、对象存储与向量库端口绑定本机回环地址。正式上线前需要配置域名、证书、备份、模型配额和评审账号；本仓库不含服务密钥。

## 测试

```bash
uv run pytest -q
uv run ruff check services/api
uv run ruff format --check services/api scripts
cd apps/web && npm run format:check && npm run lint && npm run build
```

官方样例评测：`uv run python scripts/evaluate_official.py`，结果写入 `reports/official_evaluation.json`。扫描版首次运行需先下载 MinerU Basic 模型，耗时取决于本机与模型源。报告记录版本识别、页数、Block/公式/表格数、定位坐标与解析用时；学术事实准确率仍需用固定问答集人工核验，不能从解析成功推断。

将官方 Transformer 论文和《Language Models are Few-Shot Learners》上传、解析并索引后，可运行 `uv run python -m scripts.evaluate_rag`。脚本通过实际 API 提问 Adam 参数、训练硬件、跨论文注意力架构与无依据问题，检查答案关键词、证据 UUID、引用论文覆盖与拒答，报告写入 `reports/rag_evaluation.json`。自动检查只是回归门槛，结论仍应人工核对原文。

## 设计约束

PDF 原件始终保留；回答的证据包含论文、版本、页、Block、坐标与原文片段。引文图的边来自 OpenAlex 已解析关系，CSV 图注中的数值来自实际数据。模型与 OCR 服务缺失时任务明确失败，不以模拟结果冒充真实输出。

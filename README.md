# 科研小助手 · AI Research Agent

面向学术论文的可溯源研究工作台。支持 PDF 导入与版本管理、原文定位问答、双语阅读、论文发现与订阅、引用图谱、写作与 CSV 绘图。项目依据根目录的[需求文档](AI科研助手Agent_项目需求文档.md)与[技术架构设计](AI科研助手Agent_技术架构设计.md)实现。

## 项目结构

- `apps/web`：Next.js 前端
- `services/api/app`：FastAPI、ReAct Agent、解析、检索与后台任务
- `services/api/migrations`：Alembic 数据库迁移
- `infra`：Docker Compose、镜像与 Caddy
- `scripts`：官方样例评测

## 本地运行

需要 Python 3.12、[uv](https://docs.astral.sh/uv/)、Node.js 24、Docker。官方 PDF 位于 `科研小助手/pdf`；该目录默认不加入 Git。

```bash
cp .env.example .env
# 编辑 .env：至少设置 APP_SECRET、DEMO_PASSWORD；模型端点按下节配置。
uv sync --extra dev --extra parser
docker compose --env-file .env -f infra/compose.yml up -d postgres redis qdrant elasticsearch minio
uv run alembic upgrade head
```

Compose 的基础镜像可通过 `.env` 中的 `POSTGRES_IMAGE`、`REDIS_IMAGE`、`QDRANT_IMAGE`、`ELASTICSEARCH_IMAGE`、`MINIO_IMAGE`、`NODE_IMAGE` 替换；这台 Mac 的 Docker Hub/Quay 拉取不稳定时，已验证预装的 `postgres:17`、`redis:7`、`cgr.dev/chainguard/minio:latest` 及 `mirror.gcr.io/library/node:24-alpine` 可用。

首次按上面的步骤配置并安装依赖后，在**项目根目录**使用以下命令管理本地全部服务（PostgreSQL、Redis、Qdrant、Elasticsearch、MinIO、API、默认/解析 Worker、Beat、前端；使用本地 Ollama 时也会按需启动）：

```bash
python3 scripts/local_services.py start    # 启动
python3 scripts/local_services.py stop     # 关闭
python3 scripts/local_services.py restart  # 重启
python3 scripts/local_services.py status   # 查看状态
```

脚本会运行数据库迁移；各宿主机服务的日志位于 `.local_services/*.log`。`stop` 会关闭脚本启动的进程、从当前项目目录手动启动的旧进程、当前项目的旧 `ai4s.*` macOS LaunchAgent，以及本项目的 Compose 基础服务；不会删除数据库或论文文件，也不会关闭其他项目的服务。已由 Ollama 应用独立启动的模型服务不会被脚本关闭。

分别在终端启动服务：

```bash
uv run uvicorn services.api.app.main:app --reload --port 8000
uv run celery -A services.api.app.worker.celery_app worker -l info -Q default --pool=solo --concurrency=1 -n default@%h
uv run celery -A services.api.app.worker.celery_app worker -l info -Q parser --pool=solo -n parser@%h
uv run celery -A services.api.app.worker.celery_app beat -l info
cd apps/web && npm ci && npm run dev
```

浏览器打开 `http://localhost:3000`，用 `.env` 中的演示账号登录。API 文档位于 `http://localhost:8000/docs`。若只验证 API，可设置 `DATABASE_URL=sqlite:///./dev.db`、`S3_ENDPOINT=`、`QDRANT_URL=`、`CELERY_TASK_ALWAYS_EAGER=1`，免启动容器运行确定性测试。

登录后访问 `/debug` 可查看 API 请求、SQLAlchemy 数据库查询活动、任务事件、Redis 队列和在线 Worker；页面每 4 秒更新。该诊断流保留最近约 3000 条事件，不记录 SQL 参数或请求正文。公网模式下需额外设置 `DEBUG_CONSOLE_KEY` 才能读取调试接口；未设置时接口保持关闭。该页面显示的是应用侧数据库活动和 PostgreSQL 统计，不直接读取数据库容器的原始日志。论文目录在 parser 队列任务完成后出现；若始终处于等待状态，先在调试后台确认 `parser` Worker 在线。只导入元数据的记录没有 PDF，也不会生成目录。

论文详情页使用固定视口的 PDF 阅读工作区，页面本身不滚动。左侧文献大纲可在章节目录和 PDF 缩略图之间切换，也可收起；中间 PDF 阅读器始终保留。右侧“划词翻译”和“双语滚动”最多打开一个，“AI 问答”可独立开关；三个工具全部关闭时就是专注阅读。只有打开“划词翻译”后，选中的 PDF 文字才会进入翻译侧栏。主导航可折叠，拖动面板分隔条或用方向键调整宽度，“恢复默认比例”可重置布局。引用只有点击 PDF 中的编号文字才跳转，跳转后可返回引用处。双语滚动时 PDF 与译文按内容块联动；PDF 仅渲染视口附近页面，内容块和已缓存译文按页加载。“翻译当前页”只翻译未缓存的内容块，结果持久化。MinerU 解析的独立公式以 LaTeX 保存，行内公式会在翻译时保留，译文用 KaTeX 渲染。旧版本可在论文详情页重新解析；与旧内容完全一致的段落译文缓存会被保留，发生变化的内容需要重新翻译。

科研问答使用持久化会话：可新建、删除并在刷新后继续追问。标准 ReAct 循环保存 assistant tool calls 和对应结果；模型可并行调用 `search_bm25`、`search_semantic`，用 `read` 按 Block Index 阅读原文，或用启用 Thinking 的 `plan` 获取检索策略。Elasticsearch 持久化 BM25 倒排索引，Qdrant 持久化向量索引，两者直接返回命中片段；PDF 解析完成时建立索引。界面在答案前显示可折叠的工具过程，每次新增证据也可展开查看；最终引用按当前答案重新编号为 `[1]`、`[2]`，点击来源卡片可回到 PDF 原文。升级后先启动 Elasticsearch，设置 `ELASTICSEARCH_URL`，运行 `uv run alembic upgrade head` 和 `uv run python scripts/reindex_retrieval.py` 为已有论文重建索引。

研究主题追踪由 Celery Beat 定期检查各订阅的默认 7 天自动刷新周期；新增订阅会立即排入默认 Worker。订阅可暂停、恢复或手动刷新。检索近 30 天的 OpenAlex 和 arXiv 论文，结果存入独立的 `paper_candidates`，不会自动进入个人 `papers` 文献库。摘要经小批量 LLM 评分，引用量和 GitHub Stars 只取实际可验证的数据；缺失的客观指标在评分时重新分配权重。推荐页展示摘要、理由和评分明细，支持主题、状态、分页及反馈。只有用户点击“入库”才创建正式文献并尝试下载解析 PDF。LLM 暂不可用时总分显示“待评分”，客观指标仍可查看；后续刷新会重试尚未评分的推荐和缺失的外部指标。OpenAlex 或 arXiv 其中一方失败时不推进增量检索时间。

引用图谱有两种方式：“库内关系分析”选择至少 2 篇已入库论文，只核验并显示它们之间的引用边，按内部入度和 PageRank 展示这批论文中的基石候选与边缘衍生论文；这是所选集合内的相对位置，不能当作整个领域的绝对排名。缺少学术标识或查询失败时会标明核验覆盖率。“扩展引用探索”可用一篇或多篇库内论文、或 DOI / arXiv / OpenAlex ID 作为种子，异步获取其参考文献与后续引用论文。外部节点只存入图谱，点击“加入文献库”才创建个人文献记录。探索模式下点击节点查看元数据，分别查找它引用的论文或引用它的论文；重新查询入口放在“数据维护”中。图谱、引用边及分页状态持久化。OpenAlex 为主数据源，Semantic Scholar 为备用；可选配置 `SEMANTIC_SCHOLAR_API_KEY`。升级已有数据库后，运行 `uv run python -m scripts.backfill_citation_works` 为旧论文补建统一身份关联；脚本可重复运行。

选中已保存的图谱后，可删除该图谱（不会删除文献库中的论文），或生成基于图内论文的自动综述与未来工作探索。综述按内部 PageRank、入度及全局引用量挑选最多 20 篇论文，结合摘要和 Related Work 一次调用 LLM；上下文较长时缩减为 10 篇。未来工作探索结合已有综述和论文的局限性、展望等原文提出候选方向，再用 OpenAlex 检索；候选方向即使存在高度相近的研究也会展示，并附上相近论文、链接和核查状态。相近程度会结合检索论文的标题与摘要初筛，仍需核对全文；检索未发现重复也不保证想法绝对新颖。两类结果随图谱保存，图谱更新后需重新生成。

### 本地模型与云端 API 切换

LLM、Embedding 和扫描件 OCR 有独立配置。开发机可运行 Ollama 等兼容 OpenAI 接口的服务；示例：

```bash
ollama serve
ollama pull qwen3:8b
ollama pull bge-m3
```

在 `.env` 中设置 `LLM_PROVIDER=ollama`、`LLM_BASE_URL=http://localhost:11434/v1`、`LLM_MODEL=qwen3:8b`、`OLLAMA_NUM_CTX=16384`、`EMBEDDING_BASE_URL=http://localhost:11434/v1`、`EMBEDDING_MODEL=bge-m3`。16K 是应用请求的上下文窗口，低于该模型提供的 40K 上限；可根据实际内存与延迟调整。Ollama 适配器使用原生工具调用；主 Agent 关闭 Thinking，`plan` 工具单独启用 Thinking；云端设 `LLM_PROVIDER=openai` 并填外部兼容 API 地址。本机 API 直接运行时使用 `localhost`；API 容器访问宿主机模型时使用 `host.docker.internal`。问答检索使用 Elasticsearch BM25 与 Qdrant 向量索引，由 Agent 自主选择工具。

扫描 PDF 使用本机 MinerU Worker：`uv run mineru-kit models download --tier basic` 后以 `PARSER_BACKEND=local` 运行。默认对 50 页以内的矢量 PDF 也做版式增强，提取公式 LaTeX 和表格；长文档使用分批 PyMuPDF 解析。需要增强长论文公式时可调高 `PARSER_VECTOR_MAX_PAGES`，解析耗时与模型调用量会相应增加。如需快速开发测试，可设 `PARSER_VECTOR_ENHANCE=0`。公网部署时将 `LLM_*`、`EMBEDDING_*`、`RERANK_*` 指向云端服务，并设置 `PARSER_BACKEND=remote`、`MINERU_API_URL`、`MINERU_API_KEY`；业务代码和数据模型无需更改。远程解析适配 MinerU V1 `/v1/file_parse`；所选服务需要支持该接口。[接口说明](https://github.com/opendatalab/MinerU/blob/master/docs/en/usage/http_api.md)

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

将官方 Transformer 论文和《Language Models are Few-Shot Learners》上传、解析并索引后，可运行 `uv run python -m scripts.evaluate_rag`。脚本通过实际 API 提问 Adam 参数、训练硬件、跨论文注意力架构与无依据问题，检查答案关键词、数字引用与证据对应关系、引用论文覆盖与拒答，报告写入 `reports/rag_evaluation.json`。自动检查只是回归门槛，结论仍应人工核对原文。

## 设计约束

PDF 原件始终保留；回答的证据包含论文、版本、页、Block、坐标与原文片段。引文图的边来自 OpenAlex 已解析关系，CSV 图注中的数值来自实际数据。模型与 OCR 服务缺失时任务明确失败，不以模拟结果冒充真实输出。

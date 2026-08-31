# xiniubot 搜索引擎

xiniubot 是一个自建垂直搜索引擎：异步爬虫采集 → 中文分词 → 索引构建 → 全文检索 → 可视化后台管理，开箱即用。**一个 Docker 镜像即完整系统**（内置 Meilisearch），支持双后端切换（自研索引 / Meilisearch）。

## ✨ 功能特性

- **异步爬虫**：aiohttp 并发抓取、遵守 robots.txt、URL 规范化与追踪参数清洗、优先级队列、断点续爬（任务中断后自动恢复未完成队列）
- **内容质量**：SimHash 64 位近似去重、链接权威分（简化 PageRank，归一化到 0~1）
- **检索与排序**：BM25 + 标题/描述/正文字段加权 + 权威分融合 + 时间衰减；搜索建议（suggest）与拼写纠错（correct）
- **高级查询语法**：`"精确短语"`、`-排除词`、`A OR B`、`site:域名`、`intitle:词`、`inurl:串`，local 与 meili 双后端均有等价实现
- **可视化管理后台**：登录 + CSRF 防护；仪表盘（实时抓取趋势）、爬虫控制、种子管理、队列监控、索引管理（含权威分重建）、任务历史、系统日志
- **单镜像开箱即用**：镜像内置 Meilisearch 进程，`docker compose up` 或单个 `docker run` 即完整系统，零外部依赖、零手动配置

## 🧱 技术栈

Python 3.12 · aiohttp · BeautifulSoup4 · lxml · Flask · Meilisearch · Docker

## 📁 项目结构

```
xiniubot/
├── crawler/          # 爬虫：frontier(优先级队列/断点续爬)、downloader(异步抓取)、parser(正文提取)
├── indexer/          # 索引：tokenizer(中文分词)、inverted_index(倒排索引)、ranker(BM25)、authority(PageRank)、simhash(去重)
├── search/           # 搜索：engine(查询解析/排序)、backends(local/meili 双后端抽象)
├── admin_*.py        # 管理后台：server(Flask) / runner(任务管理) / config(配置 schema)
├── search_server.py  # 搜索 Web 服务（默认端口 5050）
├── main.py           # 爬虫命令行入口（支持断点续爬）
├── tests/            # 回归测试：双后端 / 专业对齐 / 后台全链路
├── Dockerfile / docker-compose.yml / entrypoint.sh   # 单镜像：内置 Meilisearch
└── .github/workflows/docker-build.yml   # CI：测试 + 多架构构建并推送 ghcr.io 镜像
```

## 🚀 Docker 快速启动（一个镜像即完整系统）

```bash
# 可选：设置 Meilisearch 主密钥（生产环境务必修改）
# echo "MEILI_MASTER_KEY=你的强密码" > .env

docker compose up -d --build
```

启动后访问：

| 服务 | 地址 | 说明 |
| --- | --- | --- |
| 搜索首页 | http://localhost:5050 | 全文搜索 |
| 管理后台 | http://localhost:8081/admin | 爬虫配置 / 任务 / 索引管理（首次启动打印随机管理密码） |
| 内置 Meilisearch | http://localhost:7700 | Meilisearch 管理面板（可选） |

### 只用 docker run（零依赖）

```bash
docker run -d --name xiniubot \
  -p 5050:5050 -p 8081:8081 -p 7700:7700 \
  -v xiniubot_data:/app/data \
  ghcr.io/<owner>/<repo>:latest
```

不设任何环境变量也能跑：容器内默认启用 `meili` 后端、默认 master key、数据落在 `/app/data`。

## ⚙️ 环境变量（全部可选，均有默认值）

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `XINIU_SEARCH_BACKEND` | `meili`（容器内） | 搜索后端：`local`（自研索引）/ `meili`（内置 Meilisearch） |
| `MEILI_HOST` | `http://127.0.0.1:7700` | 内置 Meilisearch 地址（同容器内无需改） |
| `MEILI_MASTER_KEY` | `xiniubot-change-me-master-key` | Meilisearch 主密钥，生产建议通过 `.env` 覆盖 |
| `MEILI_DB_PATH` | `/app/data/meili_data` | 内置 Meilisearch 数据目录 |
| `MEILI_INDEX` | `pages` | Meilisearch 索引名 |

## 🧪 测试

```bash
python tests/test_backends.py        # 双后端测试（local 真实索引回归 + meili 内存契约）
python tests/test_pro_alignment.py   # 专业对齐回归测试
python tests/test_admin.py           # 管理后台全链路测试
```

推送到 `main`/`master` 或打 `v*` tag 即触发 GitHub Actions：先跑语法检查 + 双后端测试，通过后自动构建 `linux/amd64`、`linux/arm64` 镜像并推送至 `ghcr.io/<owner>/<repo>:latest`。首次构建后需在 GitHub → 仓库 → Packages 将镜像设为 **Public**，外部即可直接拉取。

## 📜 许可证

本项目遵循仓库内声明的开源许可。

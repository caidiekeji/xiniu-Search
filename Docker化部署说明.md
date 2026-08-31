# xiniubot 搜索引擎 — Docker 单镜像部署说明

## 1. 形态与架构

项目按 **单镜像** 打包：**一个 Docker 镜像即完整系统**，内置 Meilisearch 搜索引擎进程，
无需再单独部署任何外部组件、无需手动配置即可运行。

```
┌─────────────────────────────────────────────────────────────┐
│                     xiniubot 单容器镜像                       │
│                                                             │
│   ┌────────────────────────┐                                │
│   │  内置 meilisearch 进程  │   python:3.12-slim 基础镜像      │
│   │  meilisearch-linux     │                                │
│   │  :7700 中文全文检索      │                                │
│   └───────────▲────────────┘                                │
│               │ 127.0.0.1:7700                              │
│   ┌───────────┴────────────┐                                │
│   │  app: 爬虫 + 搜索 + 后台 │                                │
│   │  :5050 搜索服务         │                                │
│   │  :8081 管理后台         │                                │
│   │  数据卷 /app/data       │                                │
│   └────────────────────────┘                                │
└─────────────────────────────────────────────────────────────┘
```

- **一个镜像 = 三个端口**：搜索(5050) + 管理后台(8081) + 内置 Meilisearch(7700)。
- **开箱即用**：`docker compose up -d --build`（或单个 `docker run`）即可，无需先起 meilisearch、无需配 master key（有内置默认值兜底）。

## 2. 相关文件

| 文件 | 作用 |
| --- | --- |
| `Dockerfile` | 单镜像：python:3.12-slim + 按架构下载 meilisearch 二进制 + 项目代码 |
| `docker-compose.yml` | 单服务编排，映射 5050 / 8081 / 7700 |
| `entrypoint.sh` | 容器入口：启动内置 meili → 等就绪 → 起 5050 + 8081 |
| `.dockerignore` / `.gitignore` | 构建上下文 / Git 忽略规则 |
| `.github/workflows/docker-build.yml` | GitHub Actions：测试 + 多架构构建/推送 ghcr.io |
| `search/backends.py` | 双后端抽象：`local`（自研 pickle 索引）/ `meili`（Meilisearch） |
| `config.py` | `SEARCH_BACKEND`、`MEILI`（支持环境变量覆盖） |

## 3. 快速启动（装有 Docker 的机器）

```bash
# 可选: 设置强主密钥 (生产环境建议)
# echo "MEILI_MASTER_KEY=你的强密码" > .env

docker compose up -d --build
```

启动后：

| 服务 | 地址 | 说明 |
| --- | --- | --- |
| 搜索首页 | http://localhost:5050 | 全文搜索 |
| 管理后台 | http://localhost:8081/admin | 爬虫配置 / 任务 / 索引管理（首次启动打印随机密码） |
| 内置 Meilisearch | http://localhost:7700 | Meilisearch 管理面板（可选） |

### 也可以只用 docker run（零依赖单镜像）

```bash
docker run -d --name xiniubot \
  -p 5050:5050 -p 8081:8081 -p 7700:7700 \
  -v xiniubot_data:/app/data \
  ghcr.io/<owner>/<repo>:latest
```

> 不设任何环境变量也能跑：容器内默认启用 `meili` 后端、默认 master key、数据落在 `/app/data`。

## 4. GitHub 打包发布（推 ghcr.io 镜像）

1. 把项目推送到 GitHub 仓库（推 `main`/`master` 或打 tag `v*` 即触发 Actions）。
2. Actions 流程：`test`（语法 + 双后端测试）→ `build`（多架构 `linux/amd64` + `linux/arm64` 构建并推送 `ghcr.io/<owner>/<repo>:latest` 与 `:sha`）。
3. **首次推送后**到 GitHub → 仓库 → **Packages** 把该镜像包改为 **Public**（否则外部 `docker pull` 需要登录）。之后任意机器可直接拉取运行。

## 5. 环境变量（全部可选，有默认值）

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `XINIU_SEARCH_BACKEND` | `meili`（容器内） | 搜索后端：`local`（自研索引）/ `meili`（内置 Meilisearch） |
| `MEILI_HOST` | `http://127.0.0.1:7700` | 内置 Meilisearch 地址（同容器内无需改） |
| `MEILI_MASTER_KEY` | `xiniubot-change-me-master-key` | Meilisearch 主密钥，生产建议通过 `.env` 覆盖 |
| `MEILI_DB_PATH` | `/app/data/meili_data` | 内置 Meilisearch 数据目录（容器内） |
| `MEILI_INDEX` | `pages` | Meilisearch 索引名 |

## 6. 数据与持久化

- `xiniubot_data:/app/data`：爬虫状态、后台配置/密码/日志、**Meilisearch 全部索引数据**（`/app/data/meili_data`）。
- 删除容器不会丢数据；`docker compose down -v` 才会清空卷。

## 7. 后端切换与高级语法映射

搜索后端通过 `XINIU_SEARCH_BACKEND` 切换，管理后台/搜索服务/爬虫写入全链路自动跟随：

| 高级语法 | local 实现 | meili 实现 |
| --- | --- | --- |
| `"精确短语"` | 短语 BM25 | Meili 引号短语 |
| `-排除` | 硬过滤 | Meili 查询排除 |
| `A OR B` | 分组 OR | Meili 默认 OR |
| `site:域名` | 域后缀匹配 | Meili filter（`domain` 字段） |
| `intitle:词` / `inurl:串` | 字段硬过滤 | 拉候选集后本地过滤 |
| 字段加权 | title/desc/body 加权 | searchableAttributes 顺序 |
| 权威分 | 本地简化 PageRank | rankingRules 追加 `authority:desc` |

## 8. 验证方式与边界

- **本机已验证**：`tests/test_backends.py` 20 项断言全绿（local 真实索引回归 + meili 内存 fake 全接口）；搜索/后台服务改造后启动冒烟通过。
- **未在本机验证**：Docker 镜像的实际 `build/run`（当前机器未安装 Docker）。代码经语法编译 + 双后端单元测试校验；**镜像内行为需在装有 Docker 的机器上（或 GitHub Actions `test` 通过后的 `build` 产物）执行 `docker compose up` 验证**。
- **镜像体积**：约 1.5~1.8 GB（内含约 130MB 的 meilisearch 二进制，多架构各一份），属正常范围。
- **内置 Meili 中文分词**：使用 Meili 内置 Jieba pipeline，无需额外配置；若领域词典需求，可后续在 `MEILI` 配置中接入自定义词典或预分词。

# xiniubot 搜索引擎 — Docker 化部署说明

## 1. 形态与架构

项目按 **Docker 应用** 打包（GitHub Actions 自动构建镜像并发布到 GitHub Container Registry），
由两个容器组成：

```
┌─────────────────────────────────────────────────────────────┐
│                        docker compose                        │
│                                                             │
│  ┌──────────────────────┐          ┌──────────────────────┐  │
│  │   app  (xiniubot)    │  HTTP    │  meilisearch         │  │
│  │   python:3.12-slim   │ ──────►  │  getmeili/...:v1.53  │  │
│  │                      │ 7700     │  中文全文检索/typo    │  │
│  │   :5050 搜索服务      │          │  索引数据卷 meili_data│  │
│  │   :8081 管理后台      │          └──────────────────────┘  │
│  │   数据卷 /app/data    │                                    │
│  └──────────────────────┘                                    │
└─────────────────────────────────────────────────────────────┘
```

- **app**：爬虫、搜索 Web 服务、管理后台（一个容器内启动 5050 + 8081 两个进程）。
- **meilisearch**：官方镜像，负责开箱即用的中文全文检索（内置 Jieba 中文分词、typo 容错、相关性排序）。

## 2. 新增/改动文件

| 文件 | 作用 |
| --- | --- |
| `Dockerfile` | 应用镜像（python:3.12-slim + 项目代码） |
| `docker-compose.yml` | 一键编排 meilisearch + app |
| `entrypoint.sh` | 容器入口：等待 Meili 就绪 → 启动 5050/8081 |
| `.dockerignore` / `.gitignore` | 构建上下文 / Git 忽略规则 |
| `.github/workflows/docker-build.yml` | GitHub Actions：测试 + 构建/推送镜像到 ghcr.io |
| `search/backends.py` | 双后端抽象：`local`（自研 pickle 索引）/ `meili`（Meilisearch） |
| `config.py` | 新增 `SEARCH_BACKEND`、`MEILI`（支持环境变量覆盖） |
| `main.py` / `search_server.py` / `admin_server.py` / `admin_runner.py` | 改为通过后端工厂 `create_backend()` 获取引擎 |
| `requirements.txt` | 追加 `meilisearch` |

## 3. 本机快速启动（装有 Docker 的机器）

```bash
# 可选: 设置 master key
# echo "MEILI_MASTER_KEY=你的强密码" > .env

docker compose up -d --build
```

启动后：

| 服务 | 地址 | 说明 |
| --- | --- | --- |
| 搜索首页 | http://localhost:5050 | 全文搜索（宿主机 5050 → 容器内 5050） |
| 管理后台 | http://localhost:8081/admin | 爬虫配置 / 任务 / 索引管理（首次启动打印随机密码） |
| Meilisearch | http://localhost:7700 | 搜索引擎管理面板（可选） |

## 4. GitHub 打包发布（推 ghcr.io 镜像）

1. 把项目推送到 GitHub 仓库（推 `main`/`master` 或打 tag `v*` 即触发 Actions）。
2. Actions 流程：`test`（语法 + 后端双后端测试）→ `build`（多架构构建并推送 `ghcr.io/<owner>/<repo>:latest` 与 `:sha`）。
3. 首次推送后到 GitHub → 仓库 → **Packages** 里把该镜像包改为 **Public**（否则外部 `docker pull` 需要登录）。

在任意装有 Docker 的机器上部署：

```bash
docker run -d --name meilisearch \
  -p 7700:7700 -e MEILI_ENV=production \
  -e MEILI_MASTER_KEY=你的强密码 \
  -v meili_data:/meili_data getmeili/meilisearch:v1.53.1

docker run -d --name xiniubot \
  -p 5050:5050 -p 8081:8081 \
  -e XINIU_SEARCH_BACKEND=meili \
  -e MEILI_HOST=http://127.0.0.1:7700 \
  -e MEILI_MASTER_KEY=你的强密码 \
  -v xiniubot_data:/app/data \
  ghcr.io/<owner>/<repo>:latest
```

## 5. 环境变量

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `XINIU_SEARCH_BACKEND` | `local` | `local` = 自研 pickle 索引；`meili` = Meilisearch |
| `MEILI_HOST` | `http://127.0.0.1:7700` | Meilisearch 地址（compose 内为 `http://meilisearch:7700`） |
| `MEILI_MASTER_KEY` | 空 | Meilisearch 主密钥（与 meilisearch 容器一致） |
| `MEILI_INDEX` | `pages` | Meilisearch 索引名 |

## 6. 数据与持久化

- `xiniubot_data:/app/data`：爬虫状态、后台配置/密码/日志、Meili 本地去重元数据。
- `meili_data:/meili_data`：Meilisearch 全部索引数据。
- 删除容器不会丢数据；`docker compose down -v` 才会清空卷。

## 7. 后端切换与高级语法映射

搜索后端通过 `SEARCH_BACKEND` 切换，管理后台/搜索服务/爬虫写入全链路自动跟随：

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
- **未在本机验证**：Docker 镜像的实际 `build/run`（当前机器未安装 Docker）。代码经语法编译 + 双后端单元测试校验；容器内行为需在装有 Docker 的机器（或 GitHub Actions `test` 通过后的 `build` 产物）上执行 `docker compose up` 验证。
- **Meili 中文分词**：使用 Meili 内置 Jieba pipeline，无需额外配置；若领域词典需求，可后续在 `MEILI` 配置中接入自定义词典或预分词。

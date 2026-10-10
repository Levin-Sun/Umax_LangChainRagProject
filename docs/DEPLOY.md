# 一键交付手册（半天交付是硬指标，照此执行）

> 目标：新客户 = clone 仓库 + 填一份 `.env` + 跑一条 `docker compose up -d`，然后走初始化向导。
> 其中**配模型只要两步**：选厂商、粘一把 key（接口地址与模型名由内置标本给出，不需要客户去查）。

## 前置条件

- 客户机器：Linux（x86_64/arm64）或 macOS，4 核 8G 起步，磁盘 ≥ 20G（镜像 + 文档 + 向量数据）
- **全新机器先确认两样东西装好了**（缺哪样就先装哪样）：

  ```bash
  git --version            # 没装：macOS 装 Xcode 命令行工具或 brew install git；Linux apt/dnf install git
  docker --version         # 没装：装 Docker Desktop（macOS/Windows）或 docker-ce（Linux）
  docker compose version   # 要 v2；若是 docker-compose（带横杠，v1）请升级
  ```

  三条都能打印版本号，再往下走。Docker Desktop 装完要**先启动它**（菜单栏出现图标、状态 running），
  否则后面会报 `Cannot connect to the Docker daemon`。

## 第 1 步：取代码与配置

```bash
# 第一次取代码用 clone（"把项目从网上复制到这台机器"）；
# 已经取过的机器上用 git pull 更新到最新（二者别混：全新机器上 git pull 会报"不是 git 仓库"）
git clone https://github.com/Levin-Sun/Umax_LangChainRagProject.git umax
cd umax                  # 进入刚下载下来的项目目录，后面所有命令都在这一层执行
cp .env.example .env     # 把配置模板复制成真正生效的配置文件（.env 是隐藏文件，ls 看不到，用 ls -a）
```

编辑 `.env`，必填两项：

| 项 | 说明 |
|----|------|
| `ADMIN_PASSWORD` | 初始管理员口令（至少 8 位强口令） |
| `GATEWAY_SECRET` | `openssl rand -hex 32` 生成，模型 key 加密主密钥 |

模型 key 推荐部署后在后台登记（客户 BYO-key，密钥加密存储、界面打码、不回传明文）。

## 第 2 步：一条命令起全套

**先站对地方**——下面的命令必须在**仓库根目录**执行，也就是**能看到 `docker-compose.yml` 的那一层**
（第 1 步 `cd umax` 之后你就在那儿）。先自检一眼：

```bash
pwd          # 当前目录
ls           # 列表里必须能看到 docker-compose.yml 和 .env
```

`ls` 里看不到 `docker-compose.yml`，就是站错地方了（常见：进到了 `backend/` 这种子目录、
停在了仓库的上一级、或者解压下载的包里多套了一层目录）。`cd` 到对的那层再执行。

```bash
docker compose up -d --build
```

> **报错对照**：`未提供配置文件：未找到`（英文原文 `no configuration file provided: not found`）
> **不是**配置写错了，而是 **Compose 在你当前目录里没找到 `docker-compose.yml`**——一律先按
> 上面那两行 `pwd`/`ls` 核对目录。顺带确认第 1 步的 `cp .env.example .env` 也做在同一层，
> 否则容器会带着空的管理员口令和空主密钥起来（能启动，但登录/加密会失败）。

- 异步入库档（文档量大时）：`.env` 里 `QUEUE_BACKEND=arq`，然后
  `docker compose --profile arq up -d --build`（多起 redis + worker）
- 国内镜像拉取不畅：见 `docker-compose.yml` 头部注释（镜像源 + 重新打 tag）
- 想让 `/health` 的 `commit` 显示本次版本（排查「改了不生效」就看它）：
  `BUILD_COMMIT=$(git rev-parse --short HEAD) docker compose up -d --build`
  ——不给就是 `unknown`：镜像里没有 `.git`，代码问不出自己是谁

验证：`docker compose ps` 三个容器 healthy；浏览器打开 `http://<服务器IP>:3000`。

## 第 3 步：初始化向导（首登强改密）

1. 打开 `http://<服务器IP>:3000` → 登录页，用 `.env` 里的 `ADMIN_EMAIL/ADMIN_PASSWORD` 登录
2. **首登强制改密**：系统弹「首次登录，请修改初始口令」，改完才能进任何页面（后端 428 门闸兜底，绕过界面也进不去）
3. **`/admin/models` 模型接入**——两步：① 选厂商（下拉可搜，支持别名与拼音，如 `de`/`百炼`/`abl`/`jyx`）；
   ② 粘贴该厂商的 API 密钥，点「一键配齐并测试」。系统按这家厂商能提供的能力自动配好
   对话 / 向量 / 视觉 / 精排，**接口地址与模型名由内置标本给出**。

   > **为什么不让客户自己填地址和模型名**：真机交付时为此连踩四个坑——地址少 `/v1`、
   > 厂商根本没有向量端点（DeepSeek 只有对话）、模型名写错（`Step 3.7 Flash` ≠ `step-3.7-flash`）、
   > 向量维度不匹配（`text-embedding-v2` 是 1536 维，而库里的列是 `VECTOR(1024)`）。
   > 这四件事都该由产品判断，不该让客户查文档。

   点「一键配齐并测试」后系统会**逐个试调**一次，把厂商的真实返回（包括原始报错）直接显示在下面。

   三点要知道的：
   - **一个能力只用一个模型**；要换模型或换 key，到列表里点「编辑」（**不用删了重登**——那样中间会有一段没有模型可用）。
   - **同场景登记多个模型时按「回退优先级」取用**（小的先用，同号按登记先后）。主用挂了会自动切下一个；
     不设优先级就会出现"想主用 A，系统却在调 B"。
   - 表格上方的「模型能力状态」常驻显示四类能力**配没配、没配会怎样**（例如"语义检索：未配置 → 文档只能按字面搜"）。
     内置标本可能过期（厂商上新/改名），这时用「高级：手动登记」手填，或放覆盖文件（见下）。

   **rerank（可选）**：一键配齐会把它一起带上；检索会在"BM25+向量 RRF 融合"之后再精排一道，明显更贴题，
   代价约 +250ms/次。不想要就在列表里停用或删掉那条——不启用就用融合结果，问答与评测都不受影响。
   **要不要开由数据决定**：到 `/admin/eval` 跑一轮对比（开/关各跑一次，看 MRR 与通过率）。
4. `/admin/kb` 建知识库 → 上传第一批文档 → 状态轮询到「就绪」
5. `/admin/apikeys` 签发 API key（可选）：给客户的钉钉/企微/内部系统用——OpenAI SDK 把 `base_url` 设为
   `http://<服务器IP>:8000/api/v1/openai`、key 填签发明文即可调 `/chat/completions`（回答自带 citations）
6. 聊天页提问验证「带引用回答」；`/admin/usage` 看用量；`/admin/users` 建成员账号并授权知识库（成员首登同样强制改密）
7. **先传了文档、后配的模型 key（或中途换了向量模型）→ 必须重建索引**：旧向量与新模型不在同一个空间，
   不重建就搜不准。**系统会主动提示**：只要库里有「ready 但没向量」的文档，`/admin/kb` 上方就会出现
   「有 N 篇文档还没有向量」+ 一个「重建本库索引补齐」按钮，照它点即可；文档表对应行也会标「无向量（只能按字面搜）」。
   重建后台跑、可关页面，进度在文档列表下方（也可以只对少数文档点单篇「重试」）。
8. **`/admin/eval` 跑第一轮评测**（建议每次调完检索参数都跑一遍）：系统已预置 20 条金标准题（电商脏文档场景，
   可改可删可停用），点「开始评测」即对全库跑一遍完整问答链路，产出**通过率 / 检索命中率 / MRR** 与逐题明细，
   并可导出 Markdown 报告贴进交付文档。客户问「凭什么说更准」时这一页就是答案；它同时是配置变更的防退化记录——
   **同一份配置、同一份语料连跑两次分数必须一致**，不一致说明链路里有不确定性，先去查那个，别急着调参数。

   > **第一轮评测要注意语料**：金标准题期望的是仓库 `DirtyDocs/` 那 20 篇文档（`rag_dirty_doc_01.txt` 等）。
   > 库里没有这些文档时，判据「引用正确」和「检索命中金标准文档」永远不成立，跑出来必然全 0。
   > **系统会在起评测前拦住**并列出缺哪些文档，给两条出路：把 `DirtyDocs/` 传进一个知识库，
   > 或者到「金标准集」把这些题的期望文档改成你库里实际的名字（客户自己的语料就该走后者）。
   > 另外评测页是按**全部知识库**跑的，所以当基线用时要固定语料范围——混进无关的库会让 MRR 变化，
   > 那不是链路抖动，是范围变了。
   > 评测记录里会写明本轮**实际使用**的 chat / 向量模型，便于以后对照"这次分数是哪套模型跑的"。
   勾上「同时请裁判模型评分」会额外跑一遍 LLM 裁判（faithfulness / 相关性，每题多一次模型调用）——
   **裁判分不并入通过率**，它专门抓关键词判据看不见的「编造资料外内容」。注意裁判要和生成模型分开看：
   同一模型既当选手又当裁判会有自偏好，换一个更强的模型当裁判更有说服力；交付前建议先用几条
   已知好坏的答案（有据的 / 编造数字的 / 答非所问的）验一下裁判会不会真的扣分。

> 没有模型 key 时也能自证链路：`python backend/scripts/stub_model_server.py --port 8099` 起一个 OpenAI 兼容
> 桩服务（答案=抄资料，只验检索与链路，不验语言能力）。它是"非产品组件"，不在内置标本的下拉里，
> 所以走 `/admin/models` 的**「高级：手动登记」**：场景选 chat、接口地址 `http://127.0.0.1:8099/v1`、
> 模型名如 `stub-chat-1`、key 随便填；向量同理（`stub-embed-1`，1024 维）。
> 登记后「网关路由 → 密钥解密 → HTTP 调用 → 用量台账 → 评测」整条链路即可跑通。
> 拿到真 key 后同一条链路直接换成真模型，无需改配置以外的东西。

> **用自己的内网网关 / 私有厂商？** 不必每次走「手动登记」——放一份覆盖文件即可：
> `data/catalog/model_catalog.local.json`（compose 已把 `./data/catalog` 挂到容器 `/app/catalog`）。
> 形状与内置标本一致，**同 `id` 的厂商整条替换内置那条，新 `id` 追加**：
>
> ```json
> { "vendors": [
>   { "id": "my-gw", "name": "公司内网网关", "aliases": ["内网"], "pinyin": ["neiwang"],
>     "verified": true,
>     "profiles": [{ "id": "metered", "name": "按量计费", "base_url": "http://10.0.0.9/v1" }],
>     "capabilities": [
>       { "key": "chat", "scenario": "chat", "model": "qwen-plus", "profile": "metered" },
>       { "key": "embedding", "scenario": "embedding", "model": "text-embedding-v4",
>         "profile": "metered", "dim": 1024 }
>     ] } ] }
> ```
>
> 改完 `docker compose up -d backend` 生效（标本进程内缓存）。三点保证：文件**缺失**只用内置标本；
> **写坏**（JSON 不合法）记 warning 后用内置标本；**合并不合法**（例如 embedding 维度不是库里的
> 1024）整份覆盖忽略——标本可以过期，但不许把不合法的配置塞进下拉、更不许阻塞启动。

## 第 4 步：License 授权（商业闭环；开发/试用可跳过）

授权文件用厂商私钥签名，客户部署只带公钥验签——**授权文件自身即信任根**，改库或手改文件内容都会验签失败。

1. 客户打开「管理后台 → 授权」，复制页面上的**机器指纹**发给服务商
2. 服务商在自己机器上签发（**私钥绝不外发**）：
   ```bash
   cd backend
   python scripts/issue_license.py keygen          # 首次：生成密钥对，私钥存档好
   python scripts/issue_license.py issue --private-key <私钥> \
       --customer "客户名" --fingerprint <客户指纹> --days 365 --out license.json
   ```
3. 客户把 `license.json` 放到部署根目录下的 **`data/license/`**（即 `data/license/license.json`），
   `.env` 填写公钥并固定指纹：
   ```
   LICENSE_PUBLIC_KEY=<服务商下发的公钥>
   MACHINE_FINGERPRINT=<交付时固定下来的标识>
   ```

   > **报错对照**：若「授权」页显示**「授权文件格式错误」**（而不是「未找到授权文件」），多半是文件没放对位置。
   > compose 挂的是 `./data/license` 这个**目录**：早先把挂载点写成文件（`./license.json`）时，宿主机上还没有
   > 那个文件，Docker 会按挂载点自动建一个**同名目录**，客户随后拷进去的 `license.json` 只会落到那个目录里面，
   > 授权永远读不到。确认容器里它是文件不是目录：`docker compose exec backend ls -l /app/license/license.json`。
4. 重启：`docker compose up -d`，回到「授权」页确认状态为**有效**

**行为**：授权到期或失效后系统转为**只读**——问答与查看照常，建库/上传/改模型等管理操作返回 403 并说明原因；自助改密不受影响。续期只需替换 `license.json`（每请求现读，**无需重启**）。

> ⚠️ `MACHINE_FINGERPRINT` 必须显式设置：容器重建会改变容器内 machine-id，不固定会导致授权突然失效。

## 第 5 步：交付验收（一条命令走完整链路）

部署完、初始化向导走完、License 放好之后，用自检脚本替客户把第二天要做的事先做一遍——
它逐项断言：服务与版本（给了 `--frontend-url` 就含前端代理 → 登录页那条路）→ 登录与会话 → 匿名面/白标 → 授权门闸 → 模型通路 → 建库 → 上传 →
入库（异步档自动轮询到 ready）→ 切块与向量 → 混合检索 → **带引用问答** → 用量台账与会话历史 →
**一键重建索引与后台作业**，最后打印回答原文供人眼确认。

```bash
# 容器里跑（依赖都在镜像里，最省事）。**已经改过密就必须显式给 --password**：
# 不给的话脚本会拿 .env 里的初始口令去登录，改过之后那串已经失效了（会停在「登录失败 401」）
docker compose exec backend python scripts/smoke_delivery.py --password '你的口令' \
    --frontend-url http://frontend:3000
# 账号还卡在「首登强改密」门禁上时，改成 --new-password '你的新口令' 顺手改掉

# 或从宿主机打已发布端口
python scripts/smoke_delivery.py --base-url http://<IP>:8000 \
    --email admin@umax.local --password '你的口令' \
    --frontend-url http://<IP>:3000
```

常用参数：`--frontend-url`（**建议都带上**：浏览器走的是「前端代理 → 后端」这条路，只打 `:8000`
是另一条链，前端 rewrite 打不到后端时这里才会红）、`--new-password`（账号还卡在"首登强改密"门禁上时顺手改掉）、`--skip-reindex`、
`--cleanup`（结束时删掉自检知识库；默认保留，方便你到界面上看效果）、`--ready-timeout` / `--job-timeout`。

**退出码 0=全绿、1=有硬失败**，失败项会直接给出下一步怎么修。看到 `⚠️` 不必惊慌：
未启用 License 校验、没登记 chat/embedding 模型这类是"能跑但生产不该这么交"的提醒，
脚本会继续往下走并把真实影响跑出来（比如问答是否退化成了"未命中兜底"、入库是否退化成"只建 BM25"）。

> 自检脚本的请求面被 CI 钉在契约上（`tests/test_smoke_delivery.py` 断言它调的每个端点都在
> `contracts/openapi.json` 里），所以端点改名不会让脚本在客户现场才失效。

交付时建议把**第一轮评测的 Markdown 报告**（`/admin/eval` → 导出）连同自检输出一起贴进交付文档：
客户问「凭什么说更准」时，那份报告就是答案；它也是以后"改完有没有退化"的对照基准。

## 日常运维

| 操作 | 命令 |
|------|------|
| 看状态 | `docker compose ps` |
| 看日志 | `docker compose logs -f backend` |
| 升级版本 | `git pull && BUILD_COMMIT=$(git rev-parse --short HEAD) docker compose up -d --build`（带 BUILD_COMMIT 才有版本印记，见上） |
| 备份 | 停写后备份 `data/pg/`（全库含向量）与 `data/uploads/`（原始文件） |
| 数据不清盘重建容器 | 数据都在 `data/` 目录挂载里，`docker compose down` 不删数据 |
| 看数据库结构版本 | `docker compose exec postgres psql -U rag -d umaxrag -c "select version_num from alembic_version"` |
| 手动升级结构（正常不用做） | `docker compose exec backend alembic -c alembic.ini upgrade head` |

**数据库结构由 Alembic 管理**（`backend/migrations/`），后端每次启动自动执行一次
`upgrade head`——全新库一次建齐，既有库只做增量，重复启动是空转。所以「升级版本」那条
命令（`git pull && up -d --build`）已经把结构升级带上了，**不需要单独跑迁移命令**。
表里那行 `alembic_version` 就是「当前库的结构版本」，排查时先看它。

## 开放 API 的 key 语义（客户接进去之前要知道的三件事）

开放 API（`POST /api/v1/openai/chat/completions` + `/api/v1/api-keys`）的 key 是
**服务凭据**——代表一个调用方系统，不是某个人的登录态：

- **创建者被停用/删除，key 照常可用**（`created_by` 只做溯源、不参与鉴权）。要停一个系统的
  访问，请停用或删除**那枚 key**，而不是创建者的账号。
- **吊销只有三条路**：把 key 停用（`enabled=false`，PATCH 即时生效）、删除 key、或改小它的
  作用域 `kb_ids` 让召回为空。
- **用量与配额独立**：key 的花费记在 `apikey:{id}` 名下，与登录用户的配额分开算；
  超出该 key 的 `monthly_token_quota` 时返回 429。要"整系统限额"就设这个字段。

（名单与历史在「管理后台 → API」。key 明文只在创建响应里出现一次，库里只存 SHA-256。）

## 常见报错对照（按 症状 → 真因 → 怎么办）

| 症状 | 真因 | 怎么办 |
|---|---|---|
| 登录页 `Internal Server Error`；前端日志 `Failed to proxy http://127.0.0.1:8000/...` | 前端把 `/api/v1/*` 代理到了它自己：`BACKEND_ORIGIN` 只在运行阶段设过，而 Next 的 rewrite 是**构建期**烧进产物的 | `docker compose up -d --build frontend` 重建镜像——**只重启容器无效** |
| `/health` 的 `commit` 是 `unknown` | 构建时没注入 `BUILD_COMMIT`（镜像里没有 `.git`，代码问不出自己是谁） | 按第 2 步那条命令，带 `BUILD_COMMIT=$(git rev-parse --short HEAD)` 重建 |
| 上传成功但文档变 `failed`，错误 `GatewayError: 全部 embedding 模型均调用失败` | 向量模型调不通：厂商没有 embeddings 端点（DeepSeek 就没有）、模型名写错、地址填错 | 到「模型」页点该行「编辑」核对：地址要用厂商文档里的**兼容模式**地址、模型名逐字照抄；保存即生效，不用删了重登 |
| 授权页显示**「授权文件格式错误」**（而不是「未找到授权文件」） | 授权文件放错了位置（挂载点被 Docker 建成了同名目录） | 见第 4 步的报错对照 |
| 聊天回答一直是「资料里没有相关内容，无法回答。」 | 两种可能：检索真的没命中，**或**模型调用失败——两者文案相同 | 看回答**下方**的提示，它会说明是「检索没有命中」还是「模型调用失败」；模型失败时去「模型」页核对地址/密钥，后端日志有原因 |
| 换个说法就搜不到（同义词问题） | 文档没有向量：当时还没有可用的向量模型 | `/admin/kb` 上方会提示「有 N 篇文档还没有向量」，点「重建本库索引补齐」；文档行也会标「无向量」 |
| 想主用 A 模型，系统却在调 B | 同场景多模型按「回退优先级」取用（小的先用，同号按登记先后） | 「模型」页用「编辑」把主用设小（0）、备用设大（1） |
| 评测 0/20，或分数突然变低 | 库里没有金标准题期望的文档；或评测的语料范围变了 | 起评测前的提示会列出缺哪些文档；当基线用就固定同一个库范围 |

## 健康自检（减少售后的第一道闸）

- 后端：`curl http://<IP>:8000/api/v1/health` → `{"status":"ok","commit":"a3b97c6","started_at":"..."}`
  —— `commit` 是当前运行的代码版本，`started_at` 是进程启动时刻。
  **遇到「改了配置/功能不生效」先看这两个字段**：若 commit 不是你期望的版本，说明跑的是旧代码（改完记得重启后端）；
  若 started_at 早于你的改动时间，同理。（容器部署可用 `BUILD_COMMIT` 环境变量注入镜像版本号。）
- 前端：`curl -I http://<IP>:3000` → 200
- 组件异常先看 `docker compose ps` 哪个容器 unhealthy，再 `docker compose logs <服务名>`

## 已知边界（一期）

- HTTPS 由客户侧反代终结（nginx/caddy 对 3000 端口），编排内不自带证书
- MinerU 扫描件解析为可选外挂服务（`MINERU_BASE_URL`），未启用时上传扫描件会明确报错提示
- 白标设置在「管理后台 → 品牌」；License 见上第 4 步
- pgvector 扩展由后端启动时自动 `CREATE EXTENSION IF NOT EXISTS vector` 建好（建表需要它，
  真机踩过：全新数据库没这一步会直接死在 `chunks.embedding VECTOR(1024)` 上）。
  客户若用托管 PG 且 DBA 已预装扩展，这一步是空转，不影响
- 从「`create_all` 时代」升上来的库（容器里有表但没有 `alembic_version`）会在启动时
  被**按基线盖章接管、不重跑建表 DDL**，日志里会有一条 WARNING 说明这件事；
  这只会发生一次，此后都走正常增量迁移
- 结构变更只走「新增一条 revision」（`backend/migrations/versions/`），
  测试 `tests/test_migrations.py` 会用 Alembic 自己的比较器断言
  「迁移结果 == app/models」——改了模型忘加 revision 会红

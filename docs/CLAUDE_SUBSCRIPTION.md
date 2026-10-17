# 使用 Claude 订阅运行 WorkGenesis

这条入口使用本机已经登录的 Claude Code 订阅，通过 `claude -p` 调用 **Claude Opus 5.5**。模型调用不需要 `ANTHROPIC_API_KEY`；公开资料搜索仍使用 SerpAPI，任务中的代码和文件操作仍由 Stirrup 在 Docker 沙箱里执行。

Anthropic 官方说明，Claude Agent SDK、`claude -p` 和第三方应用可以使用订阅额度。订阅额度有使用限制，并不等于无限调用；是否允许额外付费用量还取决于账号设置。依据：[Use the Claude Agent SDK with your Claude plan](https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan)，2026-10-15 核对。

## 当前入口：Opus 5.5 max，单路串行，只造题

新配置 [`claude_opus55_subscription_serial.json`](../configs/claude_opus55_subscription_serial.json) 默认生成 **200 个新任务**，一个接一个运行。只执行 `researcher → companion_author → task_author`，不做题、不评分、不归因、不改题、不采集 teacher 轨迹；保留文件格式和来源等基础校验，结果标记为 `authored_unverified`。

```bash
claude auth login
bash scripts/run_claude_subscription.sh
# 如只想先试一个新任务：
bash scripts/run_claude_subscription.sh 1
# 中断后沿用启动时打印的目录和保存的配置：
bash scripts/run_claude_subscription.sh --resume runs/实际输出目录
```

脚本检查本机 Docker 镜像及 SerpAPI，只有终端缺少 `SERPAPI_API_KEY` 时才提示隐藏输入。每次不带 `--resume` 都新建目录，不修改已有任务；不能用这个入口恢复原来的 Codex 并行批次。失败或额度耗尽仍会停止并保留输出，尚未完成的请求不会自动重复发送。

所有角色预设为 `claude_subscription`、`claude-opus-5-5`、`effort: max`，只有前三个角色会运行。`subscription.fallback_models` 为 `claude-opus-5`、`claude-opus-4-8`，`switch_models_on_flag: true`。官方 CLI 处理过载/不可用时的备用模型链，以及有官方备用路由的内容标记；拒绝若未恢复仍停止。主模型和备用模型都沿用明确指定的 `max`。每次调用记录 `requested_model`、`provider_model`、`observed_models`、`fallback_used` 和 `cli_model_usage`。不改写为“全部由 Opus 5.5 完成”。

fallback 不绕过5小时/每周订阅限额，不切换到 API 密钥计费。较高的本地累计预算只是防止旧工作量阈值提前停止，不代表真实订阅额度。此配置每角色累计上限沿用100万估算美元／10亿 token，campaign 沿用10亿估算美元／1万亿 token；API 等价实际用量以 CLI 返回的模型明细为准，含 fallback 用量。

依据：[官方模型与 fallback 配置](https://code.claude.com/docs/en/model-config)。下面保留原来的完整流程配置说明。历史本地镜像 ID 不是下载地址；其他电脑按 [Docker 说明](../docker/README.md) 构建或加载。

## 保留的完整流程入口配置

| 配置 | 默认目标 |
| --- | --- |
| [`claude_opus55_subscription_one.json`](../configs/claude_opus55_subscription_one.json) | 先完成 1 个被接收的任务及其独立 teacher 轨迹 |
| [`claude_opus55_subscription.json`](../configs/claude_opus55_subscription.json) | 完成 100 个被接收的任务及其独立 teacher 轨迹 |

八个角色仍是 `researcher`、`companion_author`、`task_author`、`solver`、`judge`、`attributor`、`repairer`、`teacher`，全部使用 `claude-opus-5-5`，`effort` 为 `high`，各自拥有独立阶段上下文。数量可以修改 `planning.count`，也可以在启动新 campaign 时传 `--count`。

原来的 Anthropic API 配置保留，文件名含 `paper`；订阅配置的文件名含 `subscription`。订阅入口不会在额度不足或登录失败后自动改走 API。

## 与论文和 API 入口的关系

上层阶段保持一致：职业规划 → 搜索并检查真实文件 → 构造与检查可选附件 → 出题及 rubric → 独立试做 → 逐项评分 → 对不满足项归因 → 定向修复并重做 → 接收任务 → 独立采集 teacher 轨迹。原始参考文件不可修改，rubric 对 solver 不可见，Agent-only 问题不改题，同一项目持续三轮存在 Task/Rubric 缺陷时丢弃重建。具体阶段说明见 [PAPER_PIPELINE.md](PAPER_PIPELINE.md)。

订阅入口改动的是模型调用层：Stirrup 将当前历史和可用工具序列化给 Claude CLI，CLI 返回动作 JSON，然后由 Stirrup 执行真实沙箱工具。图像使用 CLI 的原生图像输入。论文对应的 Stirrup 外层上下文设定仍为 256K、70% 触发总结，但 CLI 自身还有内部上下文与请求处理。

因此，这条入口**保留论文公开的流程阶段，但不是论文或原生 Anthropic API 协议的逐字节复现**。模型、消息序列化、thinking 的保留方式和 CLI 内部重试都可能不同。保存的轨迹应标注 `claude_subscription`，不能当作作者原始 GLM-5.2 轨迹或原生 Anthropic API 消息直接混用。

`subscription.max_turns_per_call = 3` 限制一次 CLI 调用内部的轮数，不是每个工作任务最多做三步。任务仍遵守原来的 Stirrup 步数、请求和总用量限制。

## 运行前需要什么

- 当前项目 `.venv` 中的 Python 和固定版本 Stirrup。已有环境已修好时直接使用它，**不用重新安装，也不要重新执行会访问 GitHub 的 `pip install -e '.[paper]'`**。
- 本机 **Claude Code 2.1.280 或更新版本**，以及有效的官方 `claude.ai` 订阅登录。可运行 `claude auth status` 查看登录状态；状态检查不会发起模型生成。
- `SERPAPI_API_KEY`：用于搜索真实公开资料，计费独立于 Claude 订阅。
- 正在运行的 Docker，以及已构建的 `workgenesis-paper:local` 镜像。`WGFORGE_PAPER_IMAGE` 指向它的不可变镜像 ID。尚未构建时，先按 [现有镜像准备说明](PAPER_PIPELINE.md#7-以后正式运行前的环境准备)处理；重新安装 Python 依赖不能代替构建镜像。

本机后续出现的 HTTP 400 明确提示：Claude Code 2.1.267 不支持所选模型，至少需要 2.1.280。项目现在会在登录状态检查或模型调用前拦截过旧版本。对于本机的 Homebrew 安装，先退出旧的 Claude Code 会话，再升级并检查版本；升级后重新启动 Claude Code：

```bash
brew update
brew upgrade --cask claude-code
claude --version
```

版本满足要求只解决客户端兼容问题，不代表此前的 403 或账户访问问题已验证解决；下面保留原始错误记录。

2026-10-15 后续已将本机 Homebrew 客户端升级至 **2.1.292**，实际 `claude --version` 与订阅调用层的版本/本地登录预检查通过，保存的身份仍为 `claude.ai / firstParty / pro`。此次升级未调用模型；现有 Claude Code 会话需要退出后重新启动。安装校验记录见 `build/claude-code-2.1.292-1b1n898p/verification.json`。

订阅桥接会在子进程中移除 API 密钥、认证 token、替代服务端等环境变量，强制使用官方 `claude.ai` 登录，并隔离可能改变认证路由的 CLI 配置。它不读取或另存订阅凭据，也不会自动回退到 API。即使如此，**如果账号已经启用付费额外用量，不能保证运行不会产生额外费用**；实际订阅额度与费用以 Claude 账号页面为准。

网络代理例外：桥接保留启动终端显式设置的 `HTTP_PROXY`、`HTTPS_PROXY`、`NO_PROXY` 及其小写形式，让状态检查和模型调用使用同一网络设置。它不会自动读取 macOS 系统代理，也不会把这些代理值写入运行记录。这些变量只控制网络连接，不允许通过 `ANTHROPIC_BASE_URL` 等变量改成其他模型服务。

`--safe-mode` 关闭普通用户与项目的定制，但组织管理员的强制策略仍可能适用。隔离保证针对本项目提供的工作工具；不能由此宣称官方 CLI 本身不受任何宿主机管理策略影响。

## 本机连接验证状态

2026-10-16，本机 `workgenesis-paper:local` 已构建成功，不可变 ID 为 `sha256:4c4789b77981e9682bb0c60c0e8a430b3cb9d87f59b910ce235bbcd1adffe811`。容器使用 Python 3.12.15、UID 1000；23 个固定版本全部匹配，`pip check` 通过。实际 Stirrup Docker provider 已完成断网检查，以及 DOCX、PPTX、PDF、CSV 的生成、读回与 `finish` 导出；导出文件哈希一致，容器和临时目录清理成功。构建及依赖记录见 `build/paper-image-build-aliyun-g4nf3ckc/`，实际沙箱验证见 `build/stirrup-sandbox-smoke-qxmy8q0j/result/report.json`。此项验证没有模型调用、搜索或生成正式任务。

本轮 Codex 进程没有继承用户终端中的 SerpAPI key，因此预检查仍只列出 `SERPAPI_API_KEY` 待设置；必须从已导出该变量的用户终端启动下面的单任务脚本。用户报告 key 已准备好，不等于本项目已验证其搜索权限或额度。

后续验证已通过：升级至 Claude Code 2.1.292 并显式继承 HTTP/HTTPS 代理后，项目的 `SubscriptionProvider` 使用 `claude-opus-5-5` 完成一次真实最小请求，返回 `OK`。记录见 `build/subscription-connectivity-hkcn2pdo/`；CLI 报告输入 1,667 tokens、输出 78 tokens。此检查验证了程序的订阅调用路径，没有搜索资料、执行任务工具或生成任务。下面保留此前故障记录。

2026-10-15，配置及本地 Pro 登录检查通过；一次仅要求回复 `OK` 的真实连接检查返回 **403 `Request not allowed` / `authentication_failed`**。没有执行任务工具、启动容器或生成任务，也没有自动重试。CLI 报告该请求的 token 和费用为零；这不是对账户账单的独立核对。错误现场保留在 `build/subscription_validation_20261015_205308/`。

登录状态有效不代表服务端一定允许当前请求。仅凭这条错误无法区分登录凭据、账户权限、客户端版本或访问环境问题。先在你的终端通过官方入口重新登录，然后验证所选模型：

```bash
claude auth login
claude --model claude-opus-5-5
```

在第二条命令打开的 Claude Code 中发送一句简单的话，确认实际能够回复。若仍出现 403，先按 Claude Code 的认证错误提示检查账户与客户端；不要反复启动整个 campaign。此处模型测试会使用订阅额度。

### 网页可用，但终端登录返回 403

2026-10-15 本机检查发现：macOS 系统 HTTP/HTTPS 代理指向 `127.0.0.1:7890`，该端口可连接，而检查所用终端环境没有代理变量。用户确认同一订阅账号可在网页聊天。这说明浏览器与 CLI 的网络路径可能不同，**尚不能确定这就是 403 的原因，也不表示登录已修好**。

在需要登录、之后运行 pipeline 的同一个终端中，按已核实的本机代理设置执行：

```bash
export HTTP_PROXY="http://127.0.0.1:7890"
export HTTPS_PROXY="$HTTP_PROXY"
export http_proxy="$HTTP_PROXY"
export https_proxy="$HTTPS_PROXY"
export NO_PROXY="localhost,127.0.0.1,::1"
export no_proxy="$NO_PROXY"
claude auth login --claudeai
```

以上端口是这台机器此次检查的结果，其他机器需要使用自己的 HTTP 代理地址。设置仅对当前终端及其后续子进程生效。浏览器打开后使用订阅账号授权；若返回验证码，只粘贴到本地终端的登录提示处，不要发到聊天或写入项目配置。

也可以先运行 `claude`，再在交互界面输入 `/login`。它是另一种登录入口，仍使用同一服务端授权流程。`claude setup-token` 同样需要浏览器 OAuth 授权，并不能绕过服务端拒绝；当前项目只接受本机保存的订阅登录，不接受 `CLAUDE_CODE_OAUTH_TOKEN`。不要改用 `--console` 来尝试使用订阅，它对应 Console/API 计费。

依据：[官方网络配置](https://code.claude.com/docs/en/network-config)、[官方认证说明](https://code.claude.com/docs/en/authentication)。官方说明支持 HTTP/HTTPS 代理环境变量，不支持 SOCKS；网页可用也不能证明 `platform.claude.com` 上的 OAuth 交换一定可用。如果采用一致网络设置后仍然返回 403，应进一步核查 Claude Code 账号授权与客户端问题，而不是重复启动造题。

## 先检查，再运行 1 个任务

镜像构建完成、SerpAPI key 已在当前终端中 `export` 后，可以用下面这一条命令启动 **一个新的单任务 campaign**，不必手动设置镜像 ID：

```bash
bash scripts/run_subscription_one.sh
```

脚本沿用当前终端的登录和网络环境，检查 Docker 与本地镜像，并读取预检查结果中的 `ready_for_operator_live_check`；只有其值为 `true` 才创建新目录并开始使用订阅及搜索额度。运行前会打印结果目录。**每次执行此脚本都会开启新的 campaign，不是断点恢复。** 如果中断，请保留现场，用下方原始 `campaign` 命令、相同配置和原输出目录检查恢复；不要重跑脚本代替恢复。结果不明的请求仍会阻止自动重放。

镜像查询优先使用当前终端的 `WGFORGE_PAPER_IMAGE`，未设置时查询 `workgenesis-paper:local`。脚本最多重查三次本地镜像（间隔一秒），持续失败会保留 Docker 的实际错误并停止，**不会重试模型调用或 campaign**。2026-10-16 曾出现一次标签查询返回 `No such image`，之后在没有重建或重打标签的情况下恢复；两个本地 Docker context 指向同一 daemon，后续标签和固定 ID 的 12 次查询均成功。具体暂时失败原因尚未确定，不能据此认定镜像被删除。只读记录见 `build/docker-image-visibility-8v50a62t/checks.json`。本机可显式选择上面已验证的镜像 ID：

```bash
export WGFORGE_PAPER_IMAGE="$(docker image inspect --format '{{.Id}}' workgenesis-paper:local)"
bash scripts/run_subscription_one.sh
```

在项目终端运行以下离线检查。`preflight` 会检查配置、依赖、必要环境变量和 CLI 登录状态，不调用模型、不搜索网页，也不启动容器：

```bash
cd /path/to/WorkGenesis-Forge

.venv/bin/python -m wgforge preflight \
  --config configs/claude_opus55_subscription_one.json \
  --count 1
```

如果当前终端还没有 SerpAPI 密钥，macOS 默认 zsh 可以这样输入，输入内容不显示，也不写入 shell 命令历史：

```zsh
read -rs 'SERPAPI_API_KEY?输入 SerpAPI API key：'
export SERPAPI_API_KEY
```

已经构建好镜像时，设置它的不可变 ID，然后再次运行上面的 `preflight`：

```bash
export WGFORGE_PAPER_IMAGE="$(docker image inspect --format '{{.Id}}' workgenesis-paper:local)"
```

预检查通过后，下面才是实际运行。它会消耗 Claude 订阅额度和 SerpAPI 搜索额度；这里提供的是命令，**不表示已经执行或生成成功**。每次新实验使用新目录，保留先前证据：

```bash
cd /path/to/WorkGenesis-Forge
mkdir -p runs
WG_SUBSCRIPTION_RUN_DIR="$(mktemp -d "$PWD/runs/opus55-subscription-one-XXXXXXXX")"

.venv/bin/python -m wgforge campaign \
  --config configs/claude_opus55_subscription_one.json \
  --count 1 \
  --out "$WG_SUBSCRIPTION_RUN_DIR" \
  --approve-live
```

确认这一轮结果后，100 任务配置使用同样的命令结构，换成 `configs/claude_opus55_subscription.json`、`--count 100`，并创建一个新的输出目录。其他数量同样可调。已经启动的 campaign 绑定其配置和数量；不能给旧目录换数量来冒充同一次恢复。

数量是**接收任务的目标**，不是已完成数量，也不保证当前订阅额度足够。以输出目录内 `campaign.json` 的 `counts.accepted_tasks`、`counts.completed_trajectories` 与 `status` 为准。配额、超时或其他错误不会被算成成功任务。

## 这里的“美元预算”是什么意思

为了保留原来的本地运行保护机制，订阅配置仍保留 token、请求和折算美元数值。单任务配置的 campaign 折算阈值是 25，100 任务配置是 500；这些数值使用配置中的 token 单价计算，**只是估计工作量的停止阈值**。

它们不是实际订阅账单、不是信用卡扣费上限，也不是剩余 Pro 配额。`max_requests` 统计本地 CLI 调用，无法把 CLI 内部 HTTP 请求或内部重试逐一当作已知请求。请求 token、缓存用量和折算金额只应按运行报告注明的可见范围解释；不能用它们推断剩余订阅额度。

SerpAPI 的计量另算。Docker 在本机运行仍会占用本机资源；本配置没有把它们计入模型的折算美元阈值。额度不足时应保留已有输出并检查账号状态，不能通过删除账本来宣称这些调用免费或没有发生。

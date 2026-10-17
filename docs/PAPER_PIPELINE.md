# 论文流程版：Claude Opus 5.5，默认 100 个任务

> This guide documents the retained native Anthropic API profile. For the current GPT-6 Astra / xhigh subscription entry, use [README](../README.md) and [CODEX_SUBSCRIPTION.md](CODEX_SUBSCRIPTION.md). Historical preparation-only statements below describe that earlier change.

这次修改把论文已经公开的任务构造、试做、逐项评分、错误归因、修复和轨迹收集步骤接成了一个独立配置。原生 API 入口是 [`configs/claude_opus55_paper.json`](../configs/claude_opus55_paper.json)，协议名是 `paper_v1`。

**你选择的是 Claude 订阅接入。请使用 [订阅版运行指南](CLAUDE_SUBSCRIPTION.md) 和 `configs/claude_opus55_subscription_one.json` 先试一个任务。** 本页下面的 Anthropic API key 和 API 预算说明属于保留的 API 对照版本。订阅版保留本文的论文阶段和 Stirrup 沙箱执行，但通过官方 Claude Code 转发每步模型判断；历史序列化、结构化动作和内部上下文行为存在明确差异，不能称为原生接口的完全复现。

**本次只修改了 pipeline，没有启动 Claude 任务生成，也没有生成新的 100 个任务。** 此前的 MiniMax 试验属于旧配置，不能当作新版 Claude 流程的运行结果。这次也没有训练学生模型或运行官方 benchmark。

这里的“按论文复现”指按公开说明实现步骤和信息隔离。论文没有公开原始职业手册、完整环境镜像、确切 Stirrup 版本及完整 SFT 数据处理代码；这些部分不能声称逐字节还原。你指定的 Claude 和 100 个任务，也是相对于论文 GLM-5.2 和 20,000 条轨迹的明确调整。

## 1. 整体怎么走

```text
按职业分配任务名额
  → 搜索、下载、检查真实资料
  → 按需要制作补充附件，并打开检查
  → 基于已经确定的资料写题目，再写评分标准
  → 独立做题，生成实际交付文件
  → 逐项评分
  → 对未满足的评分项判断错误原因
      ├─ 只有做题者出错：任务可以接收
      └─ 题目或评分标准有错：修复后重新做题、评分
          └─ 同一评分项连续三轮仍有缺陷：丢弃并重新构造
  → 任务接收后，再用干净上下文收集一条完整做题轨迹
  → 继续下一个名额，直到达到目标或明确中止
```

一个任务包含三类东西：给做题者的工作要求、参考文件、隐藏的逐项评分标准。一个任务可能需要多个交付文件；“任务数”“文件数”“模型调用数”“做题轨迹数”是不同的统计量。

## 2. 有几个 agent，分别干什么

配置中有 **8 个 LLM 角色**，全部使用 **同一个模型 `claude-opus-5-5`**。这不是 8 个不同模型，也不代表每题只调用 8 次 API。每个角色有自己的上下文，可以多次调用工具；修复时还会重复试做和评分。

| 角色 | 通俗解释 | 能看到的主要内容 |
| --- | --- | --- |
| `researcher` | 找资料的人：搜索、下载、打开资料，判断是否能出题 | 职业手册、构造要求、之前用过的来源记录 |
| `companion_author` | 补材料的人：真实资料缺少必要的业务背景时，制作附件并检查 | 真实资料、职业背景、需要补齐的信息 |
| `task_author` | 出题的人：先确定工作要求，再写评分标准 | 已经完成并检查过的整套参考材料 |
| `solver` | 试做的人：按题目完成实际文件 | 公开题目和参考文件；看不到评分标准或作者笔记 |
| `judge` | 打分的人：针对每个评分项检查文件、计算或查看图像 | 题目、评分标准、参考文件、实际交付物 |
| `attributor` | 查错因的人：区分做题错误、题目问题、评分标准问题 | 未满足项、评分理由和相关证据 |
| `repairer` | 修题的人：只处理被定位的题目或评分缺陷 | 当前任务、诊断结果、实际参考文件和试做结果 |
| `teacher` | 最后做示范的人：对接收后的题目再独立完成一次 | 最终公开题目和参考文件；看不到评分及修复记录 |

职业名额分配由确定性程序完成，不额外算第 9 个 LLM 角色。新配置也不加入旧版的独立 `repair_reviewer`、学生难度筛选或人工签名作为任务接收门槛。

Claude 使用 Anthropic 原生 Messages API，模型名按[官方 Opus 5.5 文档](https://platform.claude.com/docs/en/models/opus-5-5/whats-new-opus-5-5)确认；代码保留工具循环中的原生 thinking/signature 数据，不会静默换成另一个模型。该配置选择 `effort=high`。

## 3. 信息从哪里来

第一层是职业背景。当前覆盖论文 Table 2 的全部 **65 个职业**。职业介绍、职责、工作活动、技术工具和工作情境来自缓存的官方 O*NET 30.0 数据；记录来源、版本、原始行和 SHA-256。它们被重新整理成职业手册，明确标记为“依据官方数据重建”，不是声称拿到了论文作者未公开的原始手册。

其中 `Buyers and Purchasing Agents` 是论文使用的宽泛职业名称，映射到三个 O*NET 子职业并保留映射说明。Financial and Investment Analysts、Project Management Specialists 在此版 Work Activities 表中缺行，使用它们真实存在的 Task Statements 等资料，不编造活动记录补齐。

第二层是每题的真实参考文件，也叫 anchor。研究 agent 用 SerpAPI 搜索，通过受控下载工具取得实际文件，再用代码和图像工具检查。控制程序验证下载记录、URL、文件摘要和文件本身；仅有模型声称“我下载了”不算来源证据。至少需要一个可用真实文件，不要求每题硬凑固定数量或固定格式。同一构造谱系内，已经使用过的 URL 或文件内容不能再用，丢弃任务用过的来源也算已消耗。

第三层是生成的补充附件，也叫 companion。它们可以是业务表格、说明、邮件或其他必要材料，也可以是零个。附件必须实际生成、重新打开检查，并把完全相同的文件交给出题者和做题者。真实 anchor 不允许修改。附件的生成来源、假设和检查记录保留在私有清单；公开工作材料不插入用于暴露构造过程的标签。真实来源与生成附件仍在后台严格区分。

## 4. 打分和修复如何工作

评分由 LLM judge 完成。它可以通过 Stirrup 的 `code_exec` 读取文件、重新计算数字、渲染文档，再用 `view_image` 查看图像。程序检查评分项是否齐全、分数范围是否合法、文件和任务版本是否匹配；程序本身不是一套写死的领域答案评分器。

例如分组计数有错误，judge 应根据原始文件重新检查，再给具体评分项扣分。attributor 接着判断：是做题者算错，还是题目给错数据，或者评分标准写错答案。只有后两类会触发修题，不能为了让做题者得满分而降低要求。

“任务接收”表示本轮没有诊断出 Task/Rubric 缺陷，**不等于做题者满分，也不等于已经证明所有事实都正确**。LLM 仍可能漏检。保留实际文件、逐项理由、工具轨迹和版本记录，是为了能够复核结果。

当前将论文“三轮同一项仍有缺陷”实现为：同一评分项在连续三次审核中存在题目或评分缺陷就丢弃；评分项更名或映射到后继项会保留追踪关系。不同缺陷交替出现不会被当成“总共只能三轮”。另外设置的费用、调用数和构造次数上限负责控制运行规模；触顶只会得到未完成或中断状态，不会冒充任务通过。

## 5. 默认 100 个，如何调整

`planning.count=100` 的含义是 **目标接收 100 个任务**。默认同时为每个接收任务收集一条独立 `teacher` 轨迹，因此完整完成时应有 100 个接收任务、100 条完成的示范轨迹。被丢弃的构造尝试和审核试做轨迹另外保留，不充当这 100 条最终示范。

100 个名额在 65 个职业间尽量平均分配：35 个职业各 2 个，30 个职业各 1 个。顺序由种子 42 决定。数量较少于 65 时只能覆盖其中一部分职业；不能把小规模计划报告成完整职业覆盖。

可以改配置的 `planning.count`，也可以在 `preflight`、`plan`、`campaign` 命令后加 `--count 100`、`--count 200` 等。20,000 个名额也支持，职业手册只保存一份，名额引用其 ID 和摘要。但 **20,000 个计划名额不是 20,000 个已生成任务**。

已经启动的 campaign 绑定配置和数量，恢复时必须保持相同。更换模型、数量或其他配置应使用新的输出目录，不能覆盖旧运行。

## 6. 先离线检查

在项目根目录、当前虚拟环境中运行：

```bash
python -m wgforge preflight \
  --config configs/claude_opus55_paper.json \
  --count 100
```

这条命令不调用模型、不搜索网页、不启动容器。它检查全部角色的原生接口配置、模型一致性、预算和职业覆盖，并列出尚缺的 Python/依赖、环境变量或沙箱设置。即使输出 `ready_for_operator_live_check: true`，也只是本地配置和依赖检查通过，尚未证明 API 账户可用、Docker 镜像能正常运行或能完成真实任务。

需要把职业计划保存下来时，可以另用一个新的文件：

```bash
python -m wgforge plan \
  --config configs/claude_opus55_paper.json \
  --count 100 \
  --out runs/opus55-paper-plans-new.json
```

这也只生成计划，不生成真实任务。

## 7. 以后正式运行前的环境准备

下面是环境准备方法。最初的 pipeline 改造没有安装用户环境；后续处理 GitHub 连接故障时，已按下述本地 wheel 方式安装当前 `.venv`。助手尚未执行镜像构建或付费 campaign。

基础离线程序支持 Python 3.11+；新 paper profile 使用的固定版本 Stirrup 需要 **Python 3.12+**。如果原 `.venv` 是旧 Python，可以单独建立新虚拟环境，保留之前 MiniMax 使用的环境：

```bash
python3.12 -m venv .venv-paper
source .venv-paper/bin/activate
python -m pip install -e '.[paper]'
```

可选依赖会安装[官方 Stirrup](https://github.com/ArtificialAnalysis/Stirrup/tree/247f24d56b2108235880ed2a2baea5d35b5a67ee)的固定提交。该提交是本项目选择并检查的版本；论文没有公开其原始提交，不能称为论文相同版本。

如果 `git clone` 因连接不上 `github.com:443` 失败，本机已缓存同一提交构建的 wheel，可使用下面的替代安装命令。源码已按固定提交清单逐文件校验；安装没有改用其他 Stirrup 版本。这个路径只适用于保留了该本地缓存的 checkout，其余依赖仍可能需要从 PyPI 下载。

```bash
python -m pip install --constraint requirements.lock -e . \
  'build/dependency-wheels/247f24d56b2108235880ed2a2baea5d35b5a67ee/stirrup-0.2.0-py3-none-any.whl[docker,e2b]'
python -m pip check
```

此时使用 `-e .`，不要再加 `[paper]`，否则 pip 仍可能重新访问 GitHub。宿主环境的 Pillow 固定为 11.3.0，以满足 MoviePy 2.2.1 的 `Pillow<12` 依赖；不要用 `--no-deps` 隐藏冲突。沙箱镜像内的软件依赖单独管理。

默认沙箱使用本机 Docker。先准备 Docker，再明确选择一个 Python 3.12 Debian 基础镜像的真实 SHA-256 摘要，然后构建：

```bash
bash scripts/build_paper_image.sh 'python:3.12-slim@sha256:<替换成已核实的64位摘要>'
```

这里的占位符不能直接使用。脚本通过 `BASE_IMAGE` 传入固定摘要；不会替你猜一个版本。[Dockerfile.paper](../docker/Dockerfile.paper)按论文披露的软件类别准备办公文档、表格、PDF、图像及科学计算工具，具体包版本是本地选择。它是重建的工作环境，**不是作者未公开的原始镜像**。构建需要联网下载系统和 Python 包，完成后记录本地不可变镜像 ID。

如果 Debian 下载较慢，可以仅为这次构建选择镜像站，例如：

```bash
WGFORGE_DEBIAN_MIRROR='https://mirrors.ustc.edu.cn' \
  bash scripts/build_paper_image.sh 'python:3.12-slim@sha256:<替换成已核实的64位摘要>'
```

`WGFORGE_DEBIAN_MIRROR` 默认留空，保持原地址；指定时只替换 Debian 来源文件中的主机前缀，保留 `/debian`、`/debian-security`、签名检查、包清单及版本。地址只能是 HTTP(S) 主机，可带端口，不能包含账号、路径、查询或片段。需要代理时，可另将 `WGFORGE_BUILD_PROXY` 设为 Docker 构建环境能访问的 HTTP(S) 代理地址；脚本通过 Docker 预定义的 `HTTP_PROXY`、`HTTPS_PROXY` 及小写对应参数传递，不写入镜像 `ENV`，也不打印代理值。容器中的 `127.0.0.1` 不代表宿主机。这些是本地重建环境的可选网络设置，不是论文公布的实验参数；不设置时维持原构建行为。

Python 包下载也可以单独选择公开索引；不设置 `WGFORGE_PIP_INDEX_URL` 时保持 pip 原有默认行为。下面的示例使用 USTC Debian 镜像和阿里云 PyPI 索引；这是可选的本地网络选择，所有 `paper-requirements.txt` 版本固定值仍保持不变，不代表镜像已构建成功：

```bash
WGFORGE_DEBIAN_MIRROR='https://mirrors.ustc.edu.cn' \
WGFORGE_PIP_INDEX_URL='https://mirrors.aliyun.com/pypi/simple' \
  bash scripts/build_paper_image.sh 'python:3.12-slim@sha256:<替换成已核实的64位摘要>'
```

索引地址必须使用公开 DNS 主机和 HTTP(S)，路径仅接受 `/simple` 或 `/pypi/simple`（可带结尾斜杠），不能包含账号、查询或片段；不关闭 TLS 检查，也不添加混用来源的 `extra-index-url`。这个可选参数只在 apt 安装之后生效，不更改前面的系统包步骤；基础镜像、Debian 镜像与这些步骤保持相同时，可复用已完成的 apt 层。中断后重建会重新执行尚未完成的 pip 层。

运行终端需要配置以下环境变量；密钥不要写进 JSON 或提交进项目：

| 环境变量 | 用途 |
| --- | --- |
| `ANTHROPIC_API_KEY` | 8 个角色调用 Claude API |
| `SERPAPI_API_KEY` | 检索公开参考文件 |
| `WGFORGE_PAPER_IMAGE` | 已构建的本地 Docker 镜像 ID |

可以用下面的命令指定刚构建的镜像，然后重新运行离线 `preflight`：

```bash
export WGFORGE_PAPER_IMAGE="$(docker image inspect --format '{{.Id}}' workgenesis-paper:local)"
```

运行时通过官方 Stirrup 提供 `code_exec`、`view_image`、`finish`、`abandon`；研究与补充材料角色还可使用受控 `web_search`/`web_fetch`。实际代码在隔离的 Linux 沙箱中执行，没有宿主机 shell 回退。上下文窗口按论文设为 256,000 tokens，70% 时由 Stirrup 进行模型总结；单次沙箱命令上限 600 秒。原始上下文、总结后的分段和 provider 请求都保留，不能把被总结的对话简单拼接成假装连续的训练样本。

## 8. 以后启动一次新的 campaign

**下面会产生真实 Claude API 和 SerpAPI 消耗；本次没有执行。** 环境准备完成后，为每次新实验创建独立目录：

```bash
mkdir -p runs
WG_PAPER_RUN_DIR="$(mktemp -d "$PWD/runs/opus55-paper-XXXXXXXX")"

python -m wgforge campaign \
  --config configs/claude_opus55_paper.json \
  --count 100 \
  --out "$WG_PAPER_RUN_DIR" \
  --approve-live
```

当前模型调用总预算上限是 **500 美元、75,000,000 tokens、25,000 次请求**，先达到哪项就按那项限制；每个角色运行另有更小的预算。它们是运行保护上限，**不是预计花费，也不是 500 美元保证生成 100 个任务**。SerpAPI 搜索单独计次和记录，它的账单及沙箱费用不包含在这个模型费用上限里。未知结果的请求保留预留用量，不当作免费失败。

当前还选择了 seed=42、每次构造目标 40 个评分项、每个启用网页工具的角色运行最多 8 次搜索、每个计划名额最多 10 次构造尝试。论文没有公布这些具体数值；40 和 8 对应论文提示词中的可配置占位符。它们都应按本地实现选择记录。

只接收了 37 个任务时，就报告 37 个；不能因为启动时写了 `--count 100` 就说生成成功 100 个。以 `campaign.json` 的 `counts` 和 `status` 为准，分别查看 `accepted_tasks`、`completed_trajectories`、`discarded_scenarios` 和 `acquisition_failures`。

## 9. 结果保存在哪里，失败后怎么处理

| 路径 | 内容 |
| --- | --- |
| `campaign.json` | 配置摘要、计划、每次构造状态、接收任务与完成轨迹数 |
| `lineage_registry.json` | 已用来源 URL/摘要、任务摘要、接收或丢弃状态 |
| `tasks/<id>/acquisition/` | 搜索、下载及来源检查记录 |
| `tasks/<id>/synthesis/` | 补充材料、出题记录、初始任务包 |
| `tasks/<id>/evolution/` | 每轮试做、逐项评分、错误归因和修复版本 |
| `tasks/<id>/demonstration/` | 最终 teacher 的实际文件、请求账本与轨迹 |
| `dataset/trajectories.jsonl` | 已完成示范轨迹的索引及摘要 |

任务包的 `public/` 只包含做题者应该看到的要求和文件；`private/` 保留评分标准及构造来源。完成阶段会先核对文件摘要再复用，不重复付费执行。若仅在启动前缺少镜像等环境准备阶段失败，而且没有模型请求、搜索、工具阶段或输出，修好环境后可重试准备过程，记录仍保留。

如果 Stirrup 已经进入 agent 运行后中断，或发生结果不明的付费请求，则保存现场并阻止自动重放。它**不是任意中断都能自动续跑**。不要反复换目录强行重试或删除账本；先查看保留的请求与阶段记录，明确新的尝试及其累计费用。旧 MiniMax 的恢复目录不用于新版 Claude 运行。

最终 `dataset` 是真实原始轨迹的索引，不代表已经复现论文的 SFT 预处理、loss mask、过滤规则或训练。当前每个接收任务重新收集一条示范，不追加论文未公开的分数过滤门槛；“重新收集而非复用审核轨迹”是明确记录的实现选择。

## 10. 论文内容对应到哪里

依据：[WorkGenesis v1](https://arxiv.org/html/2609.39325v1)，以及本次用户提供的同版 PDF。附录提示词文本及抽取页码、SHA-256 保存在 [`paper_prompts/source.json`](../wgforge/data/paper_prompts/source.json)，实际执行另有工具和输出格式绑定。

| 论文位置 / 说明 | 当前实现 |
| --- | --- |
| Table 2：65 个职业 | `configs/paper_occupations.json`；`wgforge/planning.py`；重建的 `occupation_playbooks.json` |
| §3、Appendix D.1：搜索、下载、检查真实参考资料 | `wgforge/paper_acquisition.py`；Stirrup 的网页与代码/图像工具 |
| Appendix D.2：先准备必要补充材料并检查 | `wgforge/synthesis.py` 的 companion 阶段；实际文件摘要与重新解析 |
| Appendix D.3：依据完成的材料写请求，再写 rubric | `wgforge/synthesis.py` 的 task-author 阶段；公共/私有任务包 |
| Appendix D.4：独立试做并提交真实交付物 | `wgforge/stirrup_runtime.py`；`solver` 仅获得公开任务 |
| Appendix D.5：逐项评分 | `wgforge/workflow.py::audit_run`；LLM judge 加代码和图像检查 |
| Appendix D.6：Agent / Task / Rubric 错误归因 | `audit_run` 中独立 `attributor` 调用 |
| Appendix D.7、§3：定点修复、再次验证、反复同项缺陷丢弃 | `wgforge/workflow.py::evolve_scenario`；不可变 anchor 和评分项追踪 |
| §3、Appendix A：任务接收后收集工具交互轨迹 | `wgforge/paper_campaign.py`；每题一条独立原始 teacher 轨迹 |
| 披露的 256K / 70% / 600 秒工具设置 | 官方 Stirrup 接口、上下文总结和沙箱超时配置 |

未公开的原始职业手册、原始 sandbox 镜像、原始 Stirrup 提交、精确采样设置、SFT 序列化/筛选/loss mask 仍明确列为缺失信息。当前代码实现公开流程并记录本地选择；没有把离线测试、旧 MiniMax 试验或新配置当作论文指标复现。更完整的历史与当前边界见 [REPRODUCTION_CONTRACT.md](REPRODUCTION_CONTRACT.md)。

# MiniMax 国际站 M3：所有角色使用同一模型

`configs/minimax_m3.json` 将 author、solver、judge、attributor、repairer、repair_reviewer、teacher、student 全部配置为 `MiniMax-M3`，共用本机的 `MINIMAX_API_KEY` 环境变量。各角色仍有独立上下文。

- 完整接口：`https://api.minimax.io/v1/chat/completions`。
- 默认规划一个地球科学职业任务，输入为已保存的 USGS 公开资料；新任务内容由 M3 生成。
- 单角色费用上限沿用模板的 5 美元，整次 generate 的累计费用上限为 15 美元；这些是上限，不是预计花费。
- 单次输出上限为 32,768 tokens，包含思考 tokens；单次请求超时为 600 秒。输入保守上限保持 262,144 tokens。
- 上述为原始配置。真实 solver 试跑触及了 32,768 的输出上限，完整试跑请使用 `configs/minimax_m3_long_output.json`；具体预算及重试方式见本文最后一节。
- 已为指定角色配置资料上传许可，实际调用仍必须显式传入 `--approve-live`。
- 配置面向 `generate --sources ...`。自动搜索关闭；如以后使用 `campaign`，需另行配置实际来源。

## 本机准备

首次使用时，在项目根目录执行以下命令。已有虚拟环境且依赖安装完成时，直接激活现有环境即可。

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

在 macOS 默认 zsh 中，以隐藏输入的方式设置密钥；输入内容不会成为 shell 命令历史中的一部分：

```zsh
read -r -s "MINIMAX_API_KEY?MiniMax API key: "
printf '\n'
export MINIMAX_API_KEY
```

## 生成并审核一个新任务

已有的 `runs/minimax-m3-REctlfMm` 在试做中遇到过工具参数和累计预算问题；继续该任务请使用本文最后一节的 `minimax_m3_retry.json` 命令。以下命令适用于新建 run，不能视为已验证可完整跑通的流程。

以下命令的 generate 步骤会调用 MiniMax API。每次使用一个新的输出目录：

```bash
mkdir -p runs
WG_RUN_DIR="$(mktemp -d "$PWD/runs/minimax-m3-XXXXXXXX")"
python -m wgforge plan \
  --config configs/minimax_m3_long_output.json \
  --out "$WG_RUN_DIR/plan.json"
python -m wgforge generate \
  --sources examples/anchors/usgs_manifest.json \
  --plan "$WG_RUN_DIR/plan.json" \
  --config configs/minimax_m3_long_output.json \
  --out "$WG_RUN_DIR/generation" \
  --approve-live
```

仅需生成任务、附件和评分标准时，在 generate 命令末尾加 `--author-only`。此时状态是 `authored_unverified`，尚未经过独立执行与审核。默认完整流程的状态保存在 `generation/evolution/workflow.json`；模型执行失败与任务自身缺陷仍分开记录。

`generate` 不会自动采集最终 teacher 训练轨迹，也不会训练模型。teacher 和可选 student 调用已配置为 M3，可在后续显式运行对应命令时使用。

## 接口兼容和费用口径

依据 2026-10-14 查阅的 MiniMax 官方文档：

- 使用 `reasoning_split=true` 分离思考内容与 JSON 正文。运行时保留返回的 `reasoning_content` 和 `reasoning_details`，用于后续工具调用及轨迹记录。
- MiniMax 的 Chat Completions 文档未列出 `response_format`，因此此配置不发送该参数；模型通过提示词收到 JSON 格式要求。提示词模式下允许一层包裹整个 JSON 对象的代码块，解析时只去除该包装，原始回复保持不变；额外说明文字、多个代码块、非对象或损坏的 JSON 仍被拒绝，任务结构校验继续执行。
- 使用 `max_completion_tokens`，保持模型默认思考和采样设置。
- 按量计费 standard 档、输入不超过 512K tokens 时，文档价格为输入 0.30 美元/百万 tokens、输出 1.20 美元/百万 tokens。配置的输入上限处于该档位内。
- 本项目按所有输入均未命中缓存来估算费用，未减去缓存折扣；该数字不是服务商账单。订阅 key 的额度消耗也不由此估算表示。

官方依据：[接口参数](https://platform.minimax.io/docs/api-reference/text-chat-openai)、[工具调用与思考字段](https://platform.minimax.io/docs/guides/text-m3-function-call)、[按量价格](https://platform.minimax.io/docs/guides/pricing-paygo)。

## 2026-10-14：首个真实 author 响应的离线恢复

`runs/minimax-m3-REctlfMm` 已记录一次成功的国际站 M3 API 响应。原流程在解析时失败：回复包在 Markdown JSON 代码块内，且 `professional_operations` 返回了文字而非数组。修复后允许上述单层包装，并将操作说明文字无损转换为单元素数组，同时保留原有内容、来源引用及任务校验。

已从保存的响应生成 `runs/minimax-m3-REctlfMm/recovery/scenario`，当前状态为 `authored_unverified`。恢复期间新增 API 调用为 0；原始 checkpoint、run、trajectory、role_config 的哈希均未改变。恢复记录见该目录上一层的 `recovery.json`。

当时从恢复后的任务继续执行和审核所用的命令如下，保留作历史记录。这次尝试现已因 solver 输出截断而中止；请使用下一节的新目录重试命令：

```bash
python -m wgforge evolve \
  --scenario runs/minimax-m3-REctlfMm/recovery/scenario \
  --config configs/minimax_m3.json \
  --out runs/minimax-m3-REctlfMm/generation \
  --approve-live
```

这里将 `--out` 指向已有 generation 目录，使累计预算包含原来的 author 调用。代码修改后的旧 author checkpoint 保留为证据，不跨运行时版本恢复；evolve 从已恢复的任务开始。此路径的后续状态文件为 `generation/workflow.json`。

已验证真实 author 请求返回，完整执行、评分、修复流程及任务质量仍待验证。本次修复验证：运行时测试 35/35、核心测试 21/21、来源选择验收测试 6/6 通过。macOS 的 `/var` 临时路径含软链接，测试使用实际临时路径：

```bash
TMPDIR=/private/tmp .venv/bin/python -B -m unittest discover -s tests -p 'test_runtime.py' -v
TMPDIR=/private/tmp .venv/bin/python -B -m unittest discover -s tests -p 'test_core.py' -v
TMPDIR=/private/tmp .venv/bin/python -B -m unittest discover -s qa -p 'test_source_selection_acceptance.py' -v
```

## 2026-10-14：solver 输出截断与独立重试

`generation/round_000/executor/checkpoint.json` 记录了 12 次成功返回的 API 请求。最后一次的 `finish_reason` 是 `length`，`completion_tokens` 恰好为 32,768；回复只有思考字段，没有正文或工具调用。此次试做未写出任何输出文件，尚未进入独立评分。已生成的 `recovery/scenario` 题目、附件和私有评分标准仍有效，但任务质量尚未通过执行与审核验证。

运行时现在会明确报告输出截断，原始响应与用量保存在 checkpoint，不执行截断回复中的工具调用，也不会在恢复时自动重发它。JSON 角色同样先检查截断，避免将其误报成 JSON 格式问题。

新增的 `configs/minimax_m3_long_output.json` 对 solver、teacher、student 使用以下限制；其余 JSON 角色保持原始设置，所有角色仍为国际站 `MiniMax-M3`：

| 限制 | 原始配置 | 新配置 |
| --- | ---: | ---: |
| 单轮输出（含思考） | 32,768 | 131,072 |
| 保守输入上限 | 262,144 | 524,288 |
| 每角色请求数 / 步数 | 16 / 16 | 32 / 32 |
| 每角色总 token 上限 | 400,000 | 1,500,000 |
| 单次请求超时 | 600 秒 | 3,600 秒 |
| 每角色总时限 | 1,800 秒 | 7,200 秒 |

131,072 是 [MiniMax 官方接口文档](https://platform.minimax.io/docs/api-reference/text-chat-openai) 对 M3 的建议输出上限。更大的输入与总 token 余量用于容纳后续工具调用及保留的思考字段。每角色费用上限仍为 5 美元；共享 campaign 上限仍为 15 美元、80 次请求、2,000,000 tokens。这些是硬上限，不是预计消耗或运行时长。

原配置和旧运行证据保留。运行时与预算变更后不能原地恢复旧 checkpoint。以下命令复用已生成的任务，在新的子目录重新试做，并通过 `--campaign-root` 继续计入旧 author 和 solver 的消耗（会产生新的付费调用）：

**历史命令：** 此命令已创建 `retry-cVfFK5h4` 并触发下文记录的累计预算保护，当前不要原样再次执行。

```bash
WG_RETRY_DIR="$(mktemp -d "$PWD/runs/minimax-m3-REctlfMm/generation/retry-XXXXXXXX")"
python -m wgforge evolve \
  --scenario runs/minimax-m3-REctlfMm/recovery/scenario \
  --config configs/minimax_m3_long_output.json \
  --out "$WG_RETRY_DIR" \
  --campaign-root runs/minimax-m3-REctlfMm/generation \
  --approve-live
```

`--out` 必须位于 `--campaign-root` 内，后续恢复必须保留相同的预算根目录。累计消耗可用 `python -m wgforge stats --root runs/minimax-m3-REctlfMm/generation` 查看。修改前旧 author 和 solver 合计 13 次请求、276,142 tokens；按原配置费率估算为 0.1181946 美元，未抵扣缓存折扣，非服务商账单。

本次修复新增真实 API 调用为 0。已离线回放保存的 12 次响应，复现并准确报告截断；运行时单元测试 39/39、重试预算测试 4/4、运行时回归验收 15/15、工作流验收 7/7 通过。新配置尚未完成付费实跑，放宽输出额度不代表试做必定完成或评分通过。当前工具配置未启用 Python 计算，统计仍依赖模型使用已有受限工具与推理；这里只修复截断诊断并提供预算更充足的试跑配置。

如果只需要生成真实任务，`recovery/scenario/public` 中已有题目与附件，`recovery/scenario/private/scenario.json` 中已有评分标准；无需为取到这些文件再次调用 API。以后生成新任务时可使用 `generate --author-only` 跳过试做和审核，并保留 `authored_unverified` 状态。

## 2026-10-14：真实 retry 的工具参数缺陷与累计预算保护

`generation/retry-cVfFK5h4/round_000/executor` 中的 18 次 API 请求全部成功返回，无未知状态请求。本轮输入为 968,699 tokens、输出为 57,687 tokens，合计 1,026,386。角色上限为 1,500,000，因此剩余 473,614。原工具定义下，下一轮保守输入预留为 400,482，输出预留为 131,072，合计 531,554，超过余量；运行器在第 19 次请求发送前停止。旧错误文案中的 “including uncertain requests” 不表示本次存在未知状态请求。

直接导致无效消耗的是原生写文件工具的参数契约：工具没有完整声明嵌套类型，M3 将 `headers` 写成 `"Field,Value"` 一类字符串，写文件器实际要求 `["Field", "Value"]` 数组。五次此类调用均失败，旧运行时只返回笼统的 “Tool input or artifact is invalid.”。中间一次仅传入标题的调用生成了只有空 Notes 页的 `analysis.xlsx`。该文件不构成完成的分析结果，PPT 尚未生成，审核未开始。反复调用还会重复计入很长的历史上下文输入。

本次修复限定为：为原生写文件工具声明完整嵌套 schema，并在写入前给出具体字段错误；拒绝只有标题的空工作簿；说明整文件覆盖与分页单位；预算报错显示已用量、余量及下一次预留，并明确没有发送该次请求。原始记录和预算配置保持原样。

离线验证回放了真实 18 次响应，确认五次表头错误都变为明确提示、空工作簿被拒绝，累计预算仍被执行。另将第一次失败调用的表头表示转换为数组，在临时目录验证了五张原生工作表及其单元格内容；没有改动数值、进行评分或将这个本地测试冒充模型完成结果，临时文件已清理。相关测试共 83 项通过，1 项需要预装 Docker 镜像的集成测试跳过。新增真实 API 调用为 0；这不等于新的 MiniMax 端到端试跑成功。

当前整个 campaign 累计 31 次请求、1,302,528 tokens，距离 2,000,000 的上限还剩 697,472 tokens。按原配置费率估算累计费用约 0.4780 美元，未抵扣缓存折扣，非服务商账单。直接新建 retry 仍可能因共享预算不足而中止；代码变更后旧 checkpoint 也不能直接恢复。任务题目、附件和评分标准已经存在，获取这些文件无需再次试做。

## 此前继续运行方式：修复后的工具与新的累计 token 配置

`configs/minimax_m3_retry.json` 复用已修复的工具接口，为 solver、teacher、student 设置每角色 4,000,000 的累计 token 上限，将共享 campaign token 上限设为 8,000,000。单轮输出仍为 131,072；每角色 5 美元、campaign 15 美元以及请求数和步数上限保持原值。全部角色仍使用国际站 M3。

在项目根目录、原有虚拟环境和 `MINIMAX_API_KEY` 环境变量就绪后执行一次以下命令。该命令会产生付费调用，复用已有题目，在新目录重新试做并审核；历史调用继续计入共享预算：

```bash
WG_RUN_ROOT="$PWD/runs/minimax-m3-REctlfMm"
WG_RETRY_DIR="$(mktemp -d "$WG_RUN_ROOT/generation/retry-fixed-XXXXXXXX")"

python -m wgforge evolve \
  --scenario "$WG_RUN_ROOT/recovery/scenario" \
  --config "$PWD/configs/minimax_m3_retry.json" \
  --out "$WG_RETRY_DIR" \
  --campaign-root "$WG_RUN_ROOT/generation" \
  --approve-live
```

准备时已验证任务包、八个角色的配置、CLI 参数及共享预算计算。现有 1,302,528 tokens 已计入，按新上限还剩 6,697,472；solver 有完整 4,000,000 的角色预算。保存的上一轮停止位置按当前工具定义所需的累计用量与下一次预留合计为 1,562,047，可被新角色预算覆盖。上述检查使用占位凭证并阻断网络，新增 API 调用为 0；它们验证配置与预算，不代表新一次完整试做已经成功。旧配置及全部旧运行文件保留。

## 此前继续运行方式：HTTP 504 后保留读取历史并改用流式

`generation/retry-fixed-gBFyeSI3/round_000/executor` 的前 12 次请求成功返回，第 13 次收到 HTTP 504；这是上游网关超时，并非这一次又触发了本地 token 预算。成功响应报告输入 209,828、输出 2,842，合计 212,670 tokens。第 13 次实际用量未知，仍预留 242,884 tokens、0.19083 美元，不能视为免费失败。没有生成 XLSX/PPTX，审核未开始；日志不能确定超时具体源于排队、模型生成还是网关自身。

旧请求使用 `stream: false`。新配置 `configs/minimax_m3_stream.json` 为全部八个角色启用流式，模型、思考默认设置、预算和权限沿用 retry 配置。MiniMax 官方文档支持[流式 Chat Completions](https://platform.minimax.io/docs/api-reference/text-chat-openai)。流式可以在生成过程中持续接收数据，但不能保证消除服务端 504。增加本地 timeout 也无法覆盖上游网关的超时策略。

已离线准备 `generation/retry-stream-lbjv9hep`：保留原来的 28 条消息、12 次成功请求和最后一次未知请求的账目，从超时处继续。旧运行与旧配置均保留。恢复通过父 checkpoint 哈希绑定；相同历史在共享预算中只计算一次，未知费用预留不会删除。新尝试计时从用户首次运行开始，之后的普通 resume 不会重置时限。

在原来设置过 API Key 的终端执行：

```bash
cd /path/to/WorkGenesis-Forge
.venv/bin/python -m wgforge evolve \
  --scenario "$PWD/runs/minimax-m3-REctlfMm/recovery/scenario" \
  --config "$PWD/configs/minimax_m3_stream.json" \
  --out "$PWD/runs/minimax-m3-REctlfMm/generation/retry-stream-lbjv9hep" \
  --campaign-root "$PWD/runs/minimax-m3-REctlfMm/generation" \
  --approve-live
```

此目录已经含恢复状态，直接使用以上固定路径。准备时共享账目仍为 44 次请求、1,758,082 个已用或预留 tokens、费用上界 0.7352175 美元，其中包含一次未知费用；这是配置费率下的保守账目，不是服务商账单。下一请求需要预留 242,923 tokens，已通过本地预算预检。准备及测试没有发送真实 API 请求，运行上述命令会继续付费调用。流式接收、断流保护、恢复历史、费用去重与工作流继续均经过模拟响应验证，实际 MiniMax 端到端完成情况仍待验证。

## 此前继续运行方式：兼容没有 `[DONE]` 的完整流式响应

`generation/retry-stream-lbjv9hep` 新发出的一次请求报错 `Provider stream ended before [DONE]`，没有执行新工具或生成文件。上一版接收器把 `[DONE]` 设为必要条件，这是适配缺陷：[MiniMax 官方 M3 测试](https://github.com/MiniMax-AI/MiniMax-Provider-Verifier/blob/main/m3_format_check/m3_text_tests.py) 的 `test_02_03_sse_done_marker` 将缺少这个标记列为已知问题。

已经用包含完整工具参数、终止 `finish_reason` 和最终 usage、但没有 `[DONE]` 的模拟响应复现原错误并验证修复。现在允许有充分完成证据的正常 EOF，保留对缺少最终用量、截断参数及真实连接异常的拒绝。旧版本没有保存失败流的片段或统计，所以无法确认这次实际响应是否完整，也无法离线取回丢失的内容。新版本会保存有大小限制、凭证脱敏的部分消息和接收统计，仅供诊断，绝不会执行未完成的调用。

已从最后可靠的 12 次成功请求准备新的恢复目录 `generation/retry-stream-eof-3d5cimea`。之前 HTTP 504 和本次流式失败的用量都仍未知，两个预留均计入共享 campaign；旧记录、配置及模型保持不变。在原来有 API Key 的终端运行：

```bash
cd /path/to/WorkGenesis-Forge
WG_RUN_ROOT="$PWD/runs/minimax-m3-REctlfMm"
.venv/bin/python -m wgforge evolve \
  --scenario "$WG_RUN_ROOT/recovery/scenario" \
  --config "$PWD/configs/minimax_m3_stream.json" \
  --out "$WG_RUN_ROOT/generation/retry-stream-eof-3d5cimea" \
  --campaign-root "$WG_RUN_ROOT/generation" \
  --approve-live
```

准备时共享账目为 45 次请求、2,001,005 个已用或预留 tokens，按配置费率估算的费用上界为 0.9260592 美元；这不是服务商账单。其中本次未知请求的预留是 242,923 tokens、0.1908417 美元。准备恢复没有增加请求计数或重算历史费用。下一请求的本地预算预检通过。91 项相关离线测试通过，准备和测试新增真实 API 调用为 0；实际任务完整试做和审核仍未成功验证。

## 当前继续运行方式：保留第二轮产物，扩展累计请求次数

`generation/retry-stream-eof-3d5cimea` 已通过真实 MiniMax 调用完成第一轮试做，生成 `analysis.xlsx` 和 `seismicity_briefing.pptx`，并完成模型审核、缺陷归因、题目修订及修订审核。第一轮模型评分为 26/27；它指出 c07 要求的 magType 计数没有在公开任务说明中明确要求。修订版本 `revisions/001` 已补充说明，评分标准保持原样。这是模型审核结果，未经人工认证。

第二轮 `round_001/executor` 的 14 次请求全部成功，新的 Excel/PPT 也已生成；还没有调用 finish 或完成第二轮审核。此时共享 campaign 恰好累计 80 次请求，达到 `campaign_budget.max_requests=80`。第二轮启动时只剩 14 次共享额度，因此其保存的有效角色上限被压缩为 14。新一轮请求在发送前被预算检查拦下，并没有发生新的服务商错误。

本次仅准备请求次数扩展：新配置 `configs/minimax_m3_continue.json` 将共享请求上限从 80 改为 128；当前第二轮的有效上限从 14 恢复到角色配置原定的 32。全体 M3 模型、单次输出上限、其他 token 预算、15 美元总费用上限、800 万总 token 上限和运行时限均保持原样。保持同一输出目录，复用已生成文件与完整历史。

为使原断点按新额度继续，已更新工作流配置指纹及当前第二轮的预算配置指纹。变更前的配置、checkpoint、run、trajectory 和 workflow 原始文件完整备份于 `budget_adjustments/request-cap-80-to-128/`，其中 `adjustment.json` 保存变更记录和哈希，`verification.json` 保存离线续跑检查结果。历史请求记录、工具结果、产物及第一轮审核没有改动，累计账目没有清零或重复计费。备份使用 `.before.json` 文件名，不进入 checkpoint 请求计数。

在原来设置过 API Key 的终端执行以下命令，继续原来的第二轮：

```bash
cd /path/to/WorkGenesis-Forge
WG_RUN_ROOT="$PWD/runs/minimax-m3-REctlfMm"
.venv/bin/python -m wgforge evolve \
  --scenario "$WG_RUN_ROOT/recovery/scenario" \
  --config "$PWD/configs/minimax_m3_continue.json" \
  --out "$WG_RUN_ROOT/generation/retry-stream-eof-3d5cimea" \
  --campaign-root "$WG_RUN_ROOT/generation" \
  --approve-live
```

准备时累计账目仍为 80 次请求、4,465,598 个已用或预留 tokens、按配置费率估算的费用上界 1.816098 美元，包含此前两次未知费用；不是服务商账单。新共享上限下剩余 48 次，当前角色还有 18 次。已在临时副本上验证真实 resume 路径能从请求 index=14 继续，下一次预留为 615,321 tokens，未重放已完成工具调用。没有新增真实 API 请求；第二轮完成和最终审核尚待实际继续运行。

# GPT-6 Astra / extra high 订阅入口

所有八个角色使用 `gpt-6-astra`，`effort: xhigh`（extra high）。通过官方 Codex CLI 的 ChatGPT 登录调用，不使用 Claude 登录、OpenAI API key 或自动模型回退。

模型与强度目前在适配器中固定校验为 `gpt-6-astra / xhigh`，不能仅改 JSON 换模型。登录账号没有写死，每个人使用自己的 Codex ChatGPT 登录。

## 重启电脑后运行

首次安装请先完成 [README 的依赖、登录及镜像构建步骤](../README.md#install)。之后在项目根目录的普通终端中执行：

```bash
bash scripts/run_codex_subscription.sh
```

默认目标是 **1 个被接收的任务，以及 1 条独立 teacher 解题轨迹**。脚本使用已有 `.venv/bin/python`，无需再次创建虚拟环境或重装依赖。脚本会：

1. 检查 Codex CLI；预检查确认当前登录方式为 ChatGPT。未登录时，先运行 `codex login` 并选择 ChatGPT 登录。
2. 若之前使用的本地 HTTP 代理 `127.0.0.1:7890` 正在监听，且终端尚未设置 HTTPS 代理，则沿用它。
3. macOS 上可启动并等待 Docker Desktop；Linux 上需先启动 Docker。通过与 Stirrup 相同的 Python Docker SDK 查找 `workgenesis-paper:local` 并解析成本机不可变镜像 ID。显式设置的 `WGFORGE_PAPER_IMAGE` 优先；不会自动替换、下载或重建镜像。参见 [镜像构建与导入](../docker/README.md)。
4. 本地环境检查通过后，若终端没有 `SERPAPI_API_KEY`，在本地隐藏输入提示处读取；不写入配置、日志或 shell 历史。
5. 调用 SerpAPI 的账号接口检查密钥、账号状态、剩余搜索次数及当前小时用量；只显示允许的状态字段，丢弃接口回传的密钥、邮箱和账号 ID。该接口[免费且不占搜索额度](https://serpapi.com/account-api)。未通过检查时不启动模型。
6. 运行其余预检查，通过后在 `runs/gpt6-astra-1-XXXXXXXX` 下开始一个全新的 campaign。

搜索失败记录包含脱敏的错误类别和可用的 HTTP 状态码，例如认证失败、额度或限速、服务器错误、网络超时。历史记录只有 `uncertain` 而没有状态码时，无法事后确定具体原因。新的失败也不会自动重放可能已计费的搜索。

每次执行脚本都会创建新 campaign。发生中断时应保留输出目录和日志，不能把再次执行脚本称作断点恢复。旧 Claude 结果与记录完整保留，新运行从资料搜集阶段开始，避免混合模型历史。

数量可以调整，例如：

```bash
bash scripts/run_codex_subscription.sh 100
```

配置分别为 `configs/gpt6_astra_subscription_one.json`（1 个）和 `configs/gpt6_astra_subscription.json`（100 个）；启动脚本用 `--count` 明确覆盖目标。

## 保留的流程与差异

O*NET 职业规划 → 搜索、下载和检查真实资料 → 制作与检查可选附件 → 编写任务和 rubric → 独立试做 → 评分 → 归因 → 必要时修复和重做 → 接收任务 → 独立 teacher 轨迹。

Stirrup 继续在 Docker 沙箱执行工具，原始资料不可改写。Codex 只接收序列化的阶段上下文并提出结构化动作，工作工具由外层执行。适配器通过 CLI 的工具开关及临时模型目录覆盖关闭内置工具；模型名称、推理强度和图像输入能力保持明确配置。外层上下文仍为 256K、70% 触发总结。Codex 自身的上下文与内部请求行为另外记录。

动作接口使用 `typed_tool_arguments_v2`：工具参数直接放在 `arguments` 对象里，并按每个工具的参数定义生成输出格式和检查返回值。旧版把参数放在 `arguments_json` 字符串中，需要两次 JSON 转义，长代码里的正则表达式曾因此解析失败。新版不修补或执行旧的错误命令；旧调用记录保持原样。图片通过 CLI 的 `--image` 原生传入，终端显示的图片字节文本只是日志展示。

CLI 的 `item.type=error` 按诊断事件记录，与内置工具动作分别判断。官方 CLI 的有界 `Reconnecting... n/m (...)` 重连提示及 WebSocket→HTTPS 切换提示，只有在同一次 CLI 调用最终返回完整有效结果、有效用量且退出码为 0 时才视为已恢复。判断依据是重连事件和最终完成状态，不依赖括号内可能变化的网络错误措辞；例如 HTTPS 重连的 `Connection failed: error sending request`。认证、限额、终止失败和不属于重连提示的未知错误仍会停止本次阶段；不会自动重放失败调用。事件语义依据 [Codex 0.154.0 重连实现](https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/core/src/responses_retry.rs)。

每次调用保存明确请求的模型、推理强度、CLI 版本和配置摘要。当前 CLI 的 JSONL 不独立返回服务端模型身份，因此记录中的 `provider_model_verified` 为 `false`，不能把请求参数当成服务端身份证明。

这是用户选择的模型和订阅调用方式，**不是原论文 GLM-5.2 模型或原生 API 协议的完全复现**。轨迹标记为 `codex_subscription`、`native_message_replay=false`，不能冒充无损原生 API 训练轨迹。

## 本地阈值与真实订阅额度

沿用用户要求的高累计预算：每角色估算金额 1,000,000、累计 tokens 1,000,000,000；整个 campaign 估算金额 1,000,000,000、累计 tokens 1,000,000,000,000。它们是本地运行计数器，不是实际订阅账单、付款承诺或剩余额度。估算采用配置明确列出的保守 GPT-6 Astra API 费率。

Codex 的实际订阅用量限制、单次输入输出限制、执行步数及超时仍然有效。CLI 不暴露服务端的单次输出 token 上限；配置里的 `max_output_tokens` 用于本地预留与响应后检查，不保证服务端提前停止。SerpAPI 使用独立密钥和额度。输入 tokens 包含缓存输入，不能把缓存 tokens 再加一次。未确认的调用保留预留记录，不会自动重放。

预检查或离线测试通过只表示配置与接入代码检查通过，不等于已经生成了任务。结果以新目录内 `campaign.json` 的 `status`、`counts.accepted_tasks` 和 `counts.completed_trajectories` 为准。

依据：

- [GPT-6 Astra 模型说明](https://developers.openai.com/api/docs/models/gpt-6-astra)
- [Codex 非交互模式](https://learn.chatgpt.com/docs/non-interactive-mode)
- [Codex 配置参考](https://learn.chatgpt.com/docs/config-file/config-reference)

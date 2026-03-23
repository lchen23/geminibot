# GeminiBot Task List

基于 `specs/implementation-plan.md` 与当前代码实现情况，对各阶段任务状态做如下梳理。

状态说明：
- 已完成：已有明确代码实现，且基本满足该任务目标
- 部分完成：已有骨架或局部实现，但未形成完整闭环
- 未完成：当前仓库中未见对应实现

---

## Phase 0 — Technical Validation

### 任务状态
- 部分完成：验证 Gemini CLI invocation model
- 已完成：验证 Feishu WebSocket event subscription in Python
- 已完成：验证 Feishu reply message/card API from Python
- 已完成：确定 Gemini tool bridge 最终方案（本地命令包装）

### 依据
- 已实现 Gemini CLI subprocess 调用、JSON 输出解析、resume 参数拼装：`app/agent/engine.py`
- 已补充 Feishu tenant token 获取与消息发送 API 调用：`app/gateway/feishu.py:92`, `app/gateway/feishu.py:115`
- 已补充 WebSocket client 启动线程、事件 handler 注册和消息回调入口：`app/gateway/feishu.py:129`, `app/gateway/feishu.py:155`, `app/gateway/feishu.py:163`
- 已用提供的 App ID / Secret 建立真实 WebSocket 连接，并收到 Feishu 入站消息：`app/gateway/feishu.py:41`, `app/gateway/feishu.py:162`
- 运行时已生成 dedup 记录与会话状态，且未产生 `unsent_messages.json` 回落文件，说明消息处理与回复发送链路已跑通：`data/dedup.json`, `data/sessions.json`
- 已补充验证记录：`notes/gemini-cli-validation.md`, `notes/feishu-validation.md`
- 已确定 v1 采用 workspace 内本地命令包装方式桥接 tool，并生成工具脚本与使用说明：`app/agent/workspace.py:12`, `app/agent/workspace.py:169`, `app/agent/workspace.py:235`

---

## Phase 1 — Bootstrap Project Skeleton

### 任务状态
- 已完成：创建 `app/` 下的 package/module 结构
- 已完成：添加 `pyproject.toml`
- 已完成：添加 `.env.example`
- 已完成：添加基础配置加载
- 已完成：添加 structured logging utility
- 已完成：添加启动文件 `app/main.py`
- 已完成：初始化缺失的数据文件

### 依据
- 目录与文件已存在：`app/`, `pyproject.toml`, `.env.example`
- 配置加载：`app/config.py`
- 日志配置：`app/utils/logging.py`
- 启动流程：`app/main.py`
- 初始化数据文件：`app/config.py:53`

### 阶段结论
- **已完成**

---

## Phase 2 — Feishu Gateway Vertical Slice

### 任务状态
- 已完成：实现 `app/gateway/feishu.py`
- 已完成：client initialization
- 已完成：WebSocket startup
- 已完成：event handler registration
- 已完成：text extraction
- 已完成：card sending helper
- 已完成：实现 message dedup store in `data/dedup.json`
- 已完成：定义 normalized `IncomingMessage` model
- 已完成：添加 temporary echo/stub dispatcher

### 依据
- `FeishuGateway.start()` 会初始化 Feishu tenant token，并启动 WebSocket client 线程：`app/gateway/feishu.py:37`, `app/gateway/feishu.py:129`
- 已注册 `register_p2_im_message_receive_v1` 回调，并将真实消息事件接入现有 Dispatcher 流程：`app/gateway/feishu.py:155`, `app/gateway/feishu.py:163`
- `handle_text_message()` 已处理真实入站文本并写入 dedup：`app/gateway/feishu.py:46`
- `IncomingMessage` 已定义：`app/dispatcher.py:14`
- `deliver()` 已支持调用 Feishu send message API，运行时未出现 `unsent_messages.json` 回落文件：`app/gateway/feishu.py:75`, `app/gateway/feishu.py:237`
- 已确认真实 WebSocket 连接建立、收到测试消息、生成会话状态，并返回 Feishu 回复：`data/dedup.json`, `data/sessions.json`

### 阶段结论
- **已完成**

---

## Phase 3 — Dispatcher and Core Request Lifecycle

### 任务状态
- 已完成：实现 `app/dispatcher.py`
- 部分完成：统一 gateway/scheduler 输入到内部 request shape
- 已完成：内建命令解析 `/help`
- 已完成：内建命令解析 `/clear`
- 已完成：内建命令解析 `/tasks`
- 已完成：添加 card rendering adapter

### 依据
- Dispatcher 主逻辑已存在：`app/dispatcher.py`
- Gateway 与 scheduler 都通过 `IncomingMessage` 进入 `handle()`：`app/dispatcher.py:32`, `app/dispatcher.py:73`
- card rendering：`app/rendering/cards.py`

### 阶段结论
- **已完成**

---

## Phase 4 — Gemini CLI Adapter and Chat Loop

### 任务状态
- 已完成：实现 `app/agent/engine.py`
- 已完成：实现 `app/agent/workspace.py`
- 已完成：实现 `app/agent/session_store.py`
- 已完成：按模板初始化 per-conversation workspace
- 已完成：从 persona files 构建 base system prompt
- 已完成：通过 subprocess 调用 Gemini CLI
- 已完成：解析输出并保存 session metadata
- 已完成：向 Dispatcher 返回 structured result object

### 依据
- engine 主流程：`app/agent/engine.py:38`
- workspace 初始化：`app/agent/workspace.py:18`
- session store：`app/agent/session_store.py`
- system prompt 注入与 `GEMINI.md` 写入：`app/agent/engine.py:85`, `app/agent/engine.py:99`

### 风险 / 缺口
- 当前 Gemini adapter 已验证 `-p`、`--output-format json`、`--resume latest` 与 workspace cwd 行为，并已向 workspace 注入本地 tool bridge 脚本与上下文环境，但 Gemini CLI 对这些工具的自然语言调用效果仍需真实对话验收
- README 仍将 Gemini adapter 标记为待完善：`README.md:11`

### 阶段结论
- **已完成**

---

## Phase 5 — Persona and Workspace System

### 任务状态
- 已完成：创建模板文件 `SOUL.md` / `IDENTITY.md` / `USER.md` / `AGENT.md`
- 已完成：实现 workspace initialization from templates
- 已完成：确保 persona files 在每次 agent run 时注入
- 部分完成：添加 session metadata file per workspace

### 依据
- 模板文件已存在于 `templates/`
- workspace 初始化复制模板：`app/agent/workspace.py:25`
- 每次运行都会读取 persona 文件构建 prompt：`app/agent/engine.py:85`
- session metadata 当前存于全局 `data/sessions.json`，不是“per workspace”文件：`app/agent/session_store.py`

### 阶段结论
- **部分完成**

---

## Phase 6 — Scheduler v1

### 任务状态
- 已完成：实现 `app/scheduler/store.py`
- 已完成：实现 `app/scheduler/loop.py`
- 已完成：在 `main.py` 启动 polling loop
- 已完成：支持 cron 和 one-time schedules
- 已完成：将 due tasks 路由回 Dispatcher
- 已完成：添加 `/tasks` 命令
- 已完成：添加 schedule execution logging

### 依据
- SchedulerStore 已支持 create/list/due/delete/mark executed：`app/scheduler/store.py`
- `SchedulerLoop` 已轮询 due task、调用 `dispatcher.dispatch_scheduled_task()`、投递消息并记录执行日志：`app/scheduler/loop.py:35`, `app/scheduler/loop.py:40`, `app/scheduler/loop.py:59`
- `main.py` 已在启动时注入 `gateway.deliver` 并启动 scheduler：`app/main.py:16`, `app/main.py:18`
- 已通过一次到期任务 smoke test 验证执行链路，任务被消费且写入 `data/schedule_runs.json`
- `/tasks` 命令：`app/dispatcher.py:43`

### 阶段结论
- **已完成**

---

## Phase 7 — Tool Bridge for Gemini

### 任务状态
- 已完成：确定最终 tool bridge 方案（workspace 本地命令包装）
- 已完成：暴露 scheduler tools
- 已完成：定义 input/output schemas
- 已完成：添加 audit logging for tool invocations
- 已完成：将 tool 使用说明注入 Gemini workspace 上下文
- 部分完成：完成自然语言自主调用验收（执行链路已验证，受 Gemini API 429 容量限制阻塞）

### 依据
- `app/scheduler/tools.py` 已存在，并通过 `tools/tool_bridge.py` 统一暴露给 workspace：`app/scheduler/tools.py:7`, `app/agent/workspace.py:12`
- workspace 初始化会自动生成 `tools/tool_bridge.py` 与 `tools/README.md`，提供命令入口和参数 schema：`app/agent/workspace.py:219`, `app/agent/workspace.py:235`
- Gemini 运行环境已注入 conversation/chat/user 上下文，供工具脚本读取：`app/agent/engine.py:124`
- system prompt 已注入工具说明，便于 Gemini 在 workspace 中发现工具：`app/agent/engine.py:87`, `app/agent/engine.py:132`
- 每次工具调用都会写入 workspace 下的 `tool_audit.jsonl`：`app/agent/workspace.py:53`
- 已用 smoke test 验证 `list_tasks` 命令可执行并返回 JSON：`workspaces/tool-bridge-smoke/tools/tool_bridge.py`, `workspaces/tool-bridge-smoke/tool_audit.jsonl`
- 已使用真实 Gemini CLI 触发自然语言验收请求，但当前调用被 Gemini 服务端 `429 MODEL_CAPACITY_EXHAUSTED` 阻塞，尚未产生预期的 `tool_audit.jsonl` 或 `schedules.json` 新增变更：`workspaces/tool-bridge-acceptance/GEMINI.md`, `data/schedules.json`

### 阶段结论
- **部分完成**

---

## Phase 8 — Hardening and Operator Experience

### 任务状态
- 部分完成：改进错误消息和 fallback cards
- 已完成：为 JSON stores 添加 atomic file writes
- 未完成：为重叠的 scheduled task runs 添加 lock/skip 逻辑
- 未完成：添加 required config 的 startup self-checks
- 部分完成：添加 README 和 operator runbook

### 依据
- 原子写已在 `app/utils/state.py` 实现
- 错误消息已有少量处理，如 Gemini CLI 未找到、非零退出：`app/agent/engine.py:54`, `app/agent/engine.py:73`
- `README.md` 已有基础内容，但未达到 operator runbook 水平
- 未见 scheduler overlap control 与启动自检

### 阶段结论
- **部分完成**

---

# 总结

## 按阶段汇总
- 已完成：Phase 1, Phase 3, Phase 4, Phase 6
- 部分完成：Phase 0, Phase 2, Phase 5, Phase 7, Phase 8
- 未完成：无

## 当前整体开发进展
项目已完成基础骨架、Dispatcher 主流程、Gemini CLI adapter 真实调用验证、Feishu -> Dispatcher -> Gemini -> Feishu 最小闭环、workspace/persona/scheduler 存储层、Scheduler 的 due-task dispatch 与执行日志闭环，以及 v1 本地命令包装式 scheduler tool bridge 骨架。当前 spec 已移除 detailed memory 与 skills 目标，仓库里旧的 memory 相关实现可视为遗留代码，不再属于当前主线交付范围。当前主要缺口是：per-workspace `session.json` 仍未完全落地、scheduler tool 的真实自然语言验收仍受 Gemini 服务端 `429 MODEL_CAPACITY_EXHAUSTED` 阻塞、以及若干运维加固项尚未完成。

## 建议下一步优先级
1. 在 Gemini 服务容量恢复后，重跑 scheduler tool 的真实自然语言验收
2. 将 session metadata 真正落到 per-workspace `session.json`
3. 补充 operator runbook / README
4. 评估 scheduler overlap lock/skip 逻辑
5. 补 required config 的 startup self-checks

# 下一轮开发待办

## P0：完成 scheduler tool 的真实自然语言验收
**目标**：让 agent 能从自然语言中自主创建和管理 schedule，而不依赖 Dispatcher 的硬编码命令。

### 当前进展
- 已确定 tool bridge 方案为 workspace 内本地命令包装
- 已接入 `app/scheduler/tools.py`
- 已生成工具脚本、参数 schema 文档和 audit log
- 已完成 `list_tasks` smoke test
- 已发起真实 Gemini 对话验收请求，但调用被 Gemini 服务端 `429 MODEL_CAPACITY_EXHAUSTED` 阻塞

### 待办
1. 在 Gemini 服务容量恢复后，重跑 schedule 创建触发验收
   - “明天下午三点提醒我开会”
2. 观察 `tool_audit.jsonl` 与 `schedules.json` 是否正确变化
3. 如 Gemini 对工具发现不稳定，再迭代 prompt / tool docs

### 交付标准
- agent 能从自然语言中自主调用 scheduler 能力
- 不依赖 `/schedule` 这类硬编码入口

### 相关代码
- `app/scheduler/tools.py:7`
- `app/agent/workspace.py:12`
- `app/agent/engine.py:124`

## P1：补齐 per-workspace session metadata
**目标**：让当前简化后的持久化模型真正只围绕 session continuity 展开。

### 当前进展
- 已有全局 session store：`data/sessions.json`
- Gemini adapter 已支持 resume/session restore
- spec 已要求 workspace 内保存 `session.json`

### 待办
1. 将当前 `data/sessions.json` 模型同步到 workspace 内 `session.json`
2. 明确 `session_store` 与 workspace 文件之间的单一事实来源
3. 验证服务重启后同一会话仍能稳定续接
4. 验证 `/clear` 只清理当前会话 session

### 交付标准
- 每个 conversation workspace 都有清晰的 `session.json`
- 服务重启后能续接上下文
- `/clear` 后会话重新开始，但不影响其他 conversation

### 相关代码
- `app/agent/session_store.py`
- `app/agent/workspace.py`
- `app/dispatcher.py`

## P2：补齐 Gemini CLI 适配验证与稳定性
**目标**：把现在“能调用”提升到“行为确认稳定”。

### 待办
1. 验证 Gemini CLI 的真实参数行为
   - `--resume`
   - JSON 输出
   - cwd/workspace
2. 确认 session 恢复在多轮会话里的表现
3. 补 validation notes
4. 明确异常场景处理：
   - CLI 不存在
   - 返回非 JSON
   - 非零退出码
5. 用真实 Feishu 消息做一次多轮对话验收

### 交付标准
- 单轮对话稳定
- 多轮上下文续接稳定
- 出错时用户能收到可理解反馈

### 相关代码
- `app/agent/engine.py:54`
- `app/agent/engine.py:73`
- `app/agent/session_store.py`

## P3：补运维加固项
**目标**：把项目从“开发态”推进到“可日常运行”。

### 待办
1. 增加 startup self-checks
   - Feishu 配置
   - Gemini CLI 路径
   - data/workspace 目录
2. 增加 scheduler overlap lock/skip 逻辑
3. 补 fallback cards / 更清晰错误提示
4. 补 operator runbook
5. 增加最基本手工验收 checklist

### 交付标准
- 服务重启后能恢复状态
- 配置缺失时能明确报错
- 定时任务不会重复重入

### 相关代码
- `app/config.py:30`
- `app/utils/state.py:34`
- `README.md:1`

## 推荐的下一轮 Sprint 切分

### Sprint 1（必须先做）
1. **P0 scheduler tool 自然语言验收**
2. **P1 per-workspace session metadata**

> 这两项完成后，项目的“简化状态模型”才和新 spec 完全一致。

### Sprint 2
3. **P2 Gemini CLI 稳定性验证**
4. **P3 运维加固**

## 一句话版优先级
**先验收 scheduler tool 的自然语言调用，再收敛 session 持久化模型，最后补稳定性和运维加固。**
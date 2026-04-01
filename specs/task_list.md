# GeminiBot Task List

基于 `specs/implementation-plan.md` 与当前代码实现情况，对各阶段任务状态做如下梳理。

状态说明：
- 已完成：已有明确代码实现，且基本满足该任务目标
- 部分完成：已有骨架或局部实现，但未形成完整闭环
- 未完成：当前仓库中未见对应实现

---

## Phase 0 — Technical Validation

### 任务状态
- 已完成：验证 Gemini CLI invocation model
- 已完成：验证 Feishu WebSocket event subscription in Python
- 已完成：验证 Feishu reply message/card API from Python
- 已完成：确定 Gemini tool bridge 最终方案（本地命令包装）

### 依据
- 已实现 Gemini CLI subprocess 调用、JSON 输出解析、resume 参数拼装：`app/agent/engine.py`
- 当前运行时配置已切换为 `GEMINI_CLI_PATH=gemini`，与 adapter 的 `--resume latest` 语义对齐：`.env`, `.env.example`, `app/agent/engine.py:112`
- 已补充 Feishu tenant token 获取与消息发送 API 调用：`app/gateway/feishu.py:92`, `app/gateway/feishu.py:115`
- 已补充 WebSocket client 启动线程、事件 handler 注册和消息回调入口：`app/gateway/feishu.py:129`, `app/gateway/feishu.py:155`, `app/gateway/feishu.py:163`
- 已用提供的 App ID / Secret 建立真实 WebSocket 连接，并收到 Feishu 入站消息：`app/gateway/feishu.py:41`, `app/gateway/feishu.py:162`
- 运行时已生成 dedup 记录、会话状态与对话日志，且未产生 `unsent_messages.json` 回落文件，说明消息处理与回复发送链路已跑通：`data/dedup.json`, `data/sessions.json`, `workspaces/oc_453c37b1e78cac629e8e944384400f59/logs/2026-03-22.md`
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
- 已确认真实 WebSocket 连接建立、收到测试消息、生成会话与日志，并返回 Feishu 回复：`data/dedup.json`, `data/sessions.json`, `workspaces/oc_453c37b1e78cac629e8e944384400f59/logs/2026-03-22.md`

### 阶段结论
- **已完成**

---

## Phase 3 — Dispatcher and Core Request Lifecycle

### 任务状态
- 已完成：实现 `app/dispatcher.py`
- 部分完成：统一 gateway/scheduler 输入到内部 request shape
- 已完成：内建命令解析 `/help`
- 已完成：内建命令解析 `/clear`
- 已完成：内建命令解析 `/remember`
- 已完成：内建命令解析 `/tasks`
- 已完成：添加 daily log append hook
- 已完成：添加 card rendering adapter

### 依据
- Dispatcher 主逻辑已存在：`app/dispatcher.py`
- Gateway 与 scheduler 都通过 `IncomingMessage` 进入 `handle()`：`app/dispatcher.py:32`, `app/dispatcher.py:73`
- daily log：`app/dispatcher.py:63`
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
- 当前 Gemini adapter 已验证 `-p`、`--output-format json`、`--resume latest` 与 workspace cwd 行为，并已完成本地 tool bridge 的真实自然语言验收
- README 仍将 Gemini adapter 标记为待完善：`README.md:11`
- 仍需补充多轮 session 恢复与真实 Feishu 场景下的稳定性验收

### 阶段结论
- **已完成**

---

## Phase 5 — Persona and Workspace System

### 任务状态
- 已完成：创建模板文件 `SOUL.md` / `IDENTITY.md` / `USER.md` / `AGENT.md` / `MEMORY.md`
- 已完成：实现 workspace initialization from templates
- 已完成：确保 persona files 在每次 agent run 时注入
- 部分完成：添加 session metadata file per workspace

### 依据
- 模板文件已存在于 `templates/`
- workspace 初始化复制模板：`app/agent/workspace.py:25`
- 每次运行都会读取 persona 文件构建 prompt：`app/agent/engine.py:85`
- session metadata 当前存于全局 `data/sessions.json`，不是“per workspace”文件：`app/agent/session_store.py`

### 阶段结论
- **已完成**

---

## Phase 6 — Memory System v1

### 任务状态
- 已完成：实现 `app/memory/store.py`
- 已完成：daily log append
- 已完成：memory read/write
- 已完成：summary read/write
- 已完成：实现 `/remember` 命令
- 已完成：将 `MEMORY.md` 注入 agent prompt
- 已完成：recent summary loading logic
- 已完成：定义并接通初始 tool bridge interfaces（`memory_search` / `memory_list_by_date` / `memory_save`）

### 依据
- MemoryStore 已覆盖日志、记忆、summary：`app/memory/store.py`
- `/remember`：`app/dispatcher.py:39`
- `MEMORY.md` 与 recent summaries 注入：`app/agent/engine.py:85`, `app/agent/engine.py:94`
- MemoryTools 已通过 workspace 本地 tool bridge 接入 Gemini，并完成真实自然语言 `memory_save` 验收：`app/memory/tools.py`, `workspaces/tool-bridge-acceptance-gemini-memory/tool_audit.jsonl:1`, `workspaces/tool-bridge-acceptance-gemini-memory/MEMORY.md:8`

### 阶段结论
- **部分完成**

---

## Phase 7 — Memory Consolidation

### 任务状态
- 已完成：实现 `app/memory/consolidate.py`
- 已完成：在 `/clear` 时触发 consolidation
- 部分完成：总结新的 log segments
- 已完成：重写 `MEMORY.md` 并去重
- 未完成：consolidation failure-safe 处理

### 依据
- consolidate 逻辑存在：`app/memory/consolidate.py`
- `/clear` 调用 consolidation：`app/dispatcher.py:86`
- 当前 summary 仅统计日志行数并提取 Q/A，较基础：`app/memory/consolidate.py:17`
- 未见异常兜底或失败保护逻辑

### 阶段结论
- **部分完成**

---

## Phase 8 — Scheduler v1

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

## Phase 9 — Tool Bridge for Gemini

### 任务状态
- 已完成：确定最终 tool bridge 方案（workspace 本地命令包装）
- 已完成：暴露 memory tools
- 已完成：暴露 scheduler tools
- 已完成：定义 input/output schemas
- 已完成：添加 audit logging for tool invocations
- 已完成：将 tool 使用说明注入 Gemini workspace 上下文
- 已完成：完成自然语言自主调用验收

### 依据
- `app/memory/tools.py` 与 `app/scheduler/tools.py` 已存在，并通过 `tools/tool_bridge.py` 统一暴露给 workspace：`app/memory/tools.py:7`, `app/scheduler/tools.py:7`, `app/agent/workspace.py:12`
- workspace 初始化会自动生成 `tools/tool_bridge.py` 与 `tools/README.md`，提供命令入口和参数 schema：`app/agent/workspace.py:219`, `app/agent/workspace.py:235`
- Gemini 运行环境已注入 conversation/chat/user 上下文，供工具脚本读取：`app/agent/engine.py:124`
- system prompt 已注入工具说明，便于 Gemini 在 workspace 中发现工具：`app/agent/engine.py:87`, `app/agent/engine.py:132`
- 每次工具调用都会写入 workspace 下的 `tool_audit.jsonl`：`app/agent/workspace.py:53`
- 已用 smoke test 验证 `memory_save` 与 `list_tasks` 命令可执行并返回 JSON：`workspaces/tool-bridge-smoke/tools/tool_bridge.py`, `workspaces/tool-bridge-smoke/tool_audit.jsonl`
- 已使用真实 Gemini CLI 完成自然语言验收，成功触发 `memory_save` 与 `schedule_task`，并观察到 `MEMORY.md` 与 `data/schedules.json` 的新增变更：`workspaces/tool-bridge-acceptance-gemini-memory/tool_audit.jsonl:1`, `workspaces/tool-bridge-acceptance-gemini-memory/tool_audit.jsonl:2`, `workspaces/tool-bridge-acceptance-gemini-memory/MEMORY.md:8`, `data/schedules.json:2`

### 阶段结论
- **已完成**

---

## Phase 10 — Skill Extension Framework

### 任务状态
- 未完成：定义 `skills/` directory contract
- 未完成：skill discovery and mounting into workspaces
- 未完成：instruction loading rules
- 未完成：local Python tool wrappers for skill APIs
- 未完成：reference skill stub

### 依据
- 当前仓库未见 `skills/` 相关实现

### 阶段结论
- **未完成**

---

## Phase 11 — Hardening and Operator Experience

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
- 已完成：Phase 0, Phase 1, Phase 3, Phase 4, Phase 6, Phase 8, Phase 9
- 部分完成：Phase 2, Phase 5, Phase 7, Phase 11
- 未完成：Phase 10

## 当前整体开发进展
项目已完成基础骨架、Dispatcher 主流程、Gemini CLI adapter 真实调用验证、Feishu -> Dispatcher -> Gemini -> Feishu 最小闭环、workspace/persona/memory/scheduler 存储层、Scheduler 的 due-task dispatch 与执行日志闭环，以及 v1 本地命令包装式 tool bridge；本轮已将运行时 `GEMINI_CLI_PATH` 切换为 `gemini`，并完成 Gemini 对 memory/scheduler tools 的真实自然语言自主调用验收，确认 `MEMORY.md`、`tool_audit.jsonl` 与 `data/schedules.json` 会按预期变更。

## 建议下一步优先级
1. 补充 operator runbook / README
2. 评估 scheduler overlap lock/skip 逻辑
3. 补 required config 的 startup self-checks
4. 在真实 Feishu 场景补多轮对话与定时任务验收
5. 最后再推进 skills 扩展框架

# 下一轮开发待办

## P0：打通真实 Feishu 收发闭环
**目标**：先把“Feishu 发消息 → Python 服务 → Gemini → Feishu 回消息”跑通。

### 当前进展
- 已完成 tenant token 获取与真实消息发送 API 封装：`app/gateway/feishu.py:92`, `app/gateway/feishu.py:115`
- 已完成 WebSocket client 启动线程、事件 handler 注册与消息回调骨架：`app/gateway/feishu.py:129`, `app/gateway/feishu.py:155`, `app/gateway/feishu.py:163`
- 已完成真实环境联调：确认 WebSocket 连接、收到测试消息、进入 dedup + Dispatcher 流程，并写入会话与日志

### 待办
1. 补充 `notes/feishu-validation.md` 联调记录
2. 如需增强稳定性，可补充更细的入站事件日志
3. 如需支持更多消息类型，再扩展 event payload 解析

### 交付标准
- 在 Feishu 发 `hello`
- 服务收到消息
- Dispatcher 正常处理
- 返回内容真实出现在 Feishu 会话里，而不是只写本地文件

### 相关代码
- `app/gateway/feishu.py:21`
- `app/dispatcher.py:32`
- `app/agent/engine.py:38`

## P1：补完 Scheduler 执行闭环
**目标**：让 reminder / cron 不只是存下来，而是真正触发执行。

### 当前进展
- 已完成 due task 轮询、Dispatcher 路由、消息投递、任务状态更新与执行日志落盘
- 已通过一次到期任务 smoke test，确认任务被消费且写入 `data/schedule_runs.json`
- 已通过真实 Gemini CLI 自然语言调用创建一次性 reminder，并成功写入 `data/schedules.json`

### 待办
1. 在真实 Feishu 场景验证一次性提醒自动投递
2. 在真实 Feishu 场景验证 cron 任务的 next_run_at 更新
3. 如有需要，补充更细的失败重试/告警策略

### 交付标准
- 创建一次性 reminder
- 到点后自动触发
- 结果通过 Feishu 发出
- `schedules.json` 状态被正确更新

### 相关代码
- `app/scheduler/loop.py:17`
- `app/scheduler/store.py:13`
- `app/main.py:16`
- `app/dispatcher.py:73`

## P2：验收 Gemini 对 memory / scheduler tools 的自主调用
**目标**：让 agent 不依赖 Dispatcher 硬编码命令，也能从自然语言中真正调用 memory 和 schedule。

### 当前进展
- 已确定 tool bridge 方案为 workspace 内本地命令包装
- 已接入 `app/memory/tools.py` 与 `app/scheduler/tools.py`
- 已生成工具脚本、参数 schema 文档和 audit log
- 已完成 `memory_save` / `list_tasks` smoke test
- 已使用真实 Gemini CLI 完成 memory 保存触发验收
  - “记住我喜欢简洁回复”
- 已使用真实 Gemini CLI 完成 schedule 创建触发验收
  - “2026-04-02 15:00 提醒我开会”
- 已观察到 `tool_audit.jsonl`、`MEMORY.md`、`schedules.json` 按预期变化

### 待办
1. 如 Gemini 对工具发现不稳定，再迭代 prompt / tool docs
2. 在真实 Feishu 会话里补一轮端到端验收

### 交付标准
- agent 能从自然语言中自主调用 memory / scheduler 能力
- 不依赖 `/remember` 或 `/schedule` 这类硬编码入口

### 相关代码
- `app/memory/tools.py:7`
- `app/scheduler/tools.py:7`
- `app/agent/workspace.py:12`
- `app/agent/engine.py:124`

## P3：补齐 Gemini CLI 适配验证与稳定性
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

## P4：补运维加固项
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

## P5：最后再推进 skills 框架
**目标**：在核心链路稳定后，再做扩展机制。

### 待办
1. 定义 `skills/` 目录约定
2. 实现 skill discovery
3. 挂载到 workspace
4. 设计 instruction loading rules
5. 做一个 reference skill stub

### 交付标准
- 新增一个 skill 文件夹后，无需改核心模块即可生效

## 推荐的下一轮 Sprint 切分

### Sprint 1（必须先做）
1. **P0 Feishu 真实接入**
2. **P1 Scheduler 执行闭环**

> 这两项完成后，项目才算真正具备“收消息 + 主动发消息”的基础能力。

### Sprint 2
3. **P2 Tool bridge**
4. **P3 Gemini CLI 稳定性验证**

> 这两项完成后，agent 才会从“被动调用模型”升级为“可自主操作能力”。

### Sprint 3
5. **P4 运维加固**
6. **P5 skills 框架**

## 一句话版优先级
**先打通 Feishu，接着打通 Scheduler，再做 Tool Bridge，之后补稳定性和扩展性。**

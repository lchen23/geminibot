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
- 部分完成：验证 Feishu WebSocket event subscription in Python
- 部分完成：验证 Feishu reply message/card API from Python
- 未完成：确定 Gemini tool bridge 最终方案（MCP 或命令包装）

### 依据
- 已实现 Gemini CLI subprocess 调用、JSON 输出解析、resume 参数拼装：`app/agent/engine.py`
- 已补充 Feishu tenant token 获取与消息发送 API 调用：`app/gateway/feishu.py:92`, `app/gateway/feishu.py:115`
- 已补充 WebSocket client 启动线程、事件 handler 注册和消息回调入口：`app/gateway/feishu.py:129`, `app/gateway/feishu.py:155`, `app/gateway/feishu.py:163`
- 未见 `notes/gemini-cli-validation.md`、`notes/feishu-validation.md`
- 仍缺少真实环境下的端到端验证记录

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
- 部分完成：实现 `app/gateway/feishu.py`
- 部分完成：client initialization
- 部分完成：WebSocket startup
- 部分完成：event handler registration
- 部分完成：text extraction
- 已完成：card sending helper
- 已完成：实现 message dedup store in `data/dedup.json`
- 已完成：定义 normalized `IncomingMessage` model
- 部分完成：添加 temporary echo/stub dispatcher

### 依据
- `FeishuGateway.start()` 现在会在配置存在时初始化 Feishu tenant token，并启动 WebSocket client 线程：`app/gateway/feishu.py:37`, `app/gateway/feishu.py:129`
- 已注册 `register_p2_im_message_receive_v1` 回调，并将消息事件接入现有 Dispatcher 流程：`app/gateway/feishu.py:155`, `app/gateway/feishu.py:163`
- `handle_text_message()` 可接收文本并转交 Dispatcher：`app/gateway/feishu.py:46`
- dedup 已使用 `data/dedup.json`：`app/gateway/feishu.py:30`
- `IncomingMessage` 已定义：`app/dispatcher.py:14`
- `deliver()` 已支持调用 Feishu send message API，失败时再落盘到 `unsent_messages.json`：`app/gateway/feishu.py:75`, `app/gateway/feishu.py:237`
- 当前实现依赖 `lark-oapi`，且尚未完成真实环境联调

### 阶段结论
- **部分完成**

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
- 与真实 Feishu gateway 仍未形成稳定端到端闭环
- README 仍将 Gemini adapter 标记为待完善：`README.md:11`

### 阶段结论
- **部分完成**

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
- **部分完成**

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
- 部分完成：定义初始 tool bridge interfaces（`memory_search` / `memory_list_by_date` / `memory_save`）

### 依据
- MemoryStore 已覆盖日志、记忆、summary：`app/memory/store.py`
- `/remember`：`app/dispatcher.py:39`
- `MEMORY.md` 与 recent summaries 注入：`app/agent/engine.py:85`, `app/agent/engine.py:94`
- MemoryTools 已定义，但尚未桥接给 Gemini：`app/memory/tools.py`

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
- 部分完成：实现 `app/scheduler/loop.py`
- 已完成：在 `main.py` 启动 polling loop
- 已完成：支持 cron 和 one-time schedules
- 未完成：将 due tasks 路由回 Dispatcher
- 已完成：添加 `/tasks` 命令
- 未完成：添加 schedule execution logging

### 依据
- SchedulerStore 已支持 create/list/due/delete/mark executed：`app/scheduler/store.py`
- `SchedulerLoop` 当前仅启动线程并 sleep：`app/scheduler/loop.py:22`, `app/scheduler/loop.py:30`
- `main.py` 已启动 scheduler：`app/main.py:16`
- `/tasks` 命令：`app/dispatcher.py:43`

### 阶段结论
- **部分完成**

---

## Phase 9 — Tool Bridge for Gemini

### 任务状态
- 未完成：确定最终 tool bridge 方案
- 部分完成：暴露 memory tools
- 部分完成：暴露 scheduler tools
- 未完成：定义 input/output schemas
- 未完成：添加 audit logging for tool invocations

### 依据
- `app/memory/tools.py` 与 `app/scheduler/tools.py` 已存在
- 但未见这些工具被真正接入 Gemini CLI 调用链
- 未见 schema 定义与审计日志实现

### 阶段结论
- **未完成**

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
- 已完成：Phase 1, Phase 3
- 部分完成：Phase 0, Phase 2, Phase 4, Phase 5, Phase 6, Phase 7, Phase 8, Phase 11
- 未完成：Phase 9, Phase 10

## 当前整体开发进展
项目已完成基础骨架、Dispatcher 主流程、Gemini CLI adapter 雏形、workspace/persona/memory/scheduler 存储层，并补上了 Feishu tenant token、消息发送 API，以及 WebSocket client 启动与消息事件回调骨架；但真实环境联调、scheduler 执行闭环、Gemini tool bridge、skills 框架仍未完成。

## 建议下一步优先级
1. 完成 Feishu WebSocket 真实环境联调与端到端验证
2. 完成 SchedulerLoop 的 due-task dispatch 与执行日志
3. 将 memory/scheduler tools 真正桥接到 Gemini
4. 完成 tool bridge schema 与审计日志
5. 再推进 skills 扩展框架

# 下一轮开发待办

## P0：打通真实 Feishu 收发闭环
**目标**：先把“Feishu 发消息 → Python 服务 → Gemini → Feishu 回消息”跑通。

### 当前进展
- 已完成 tenant token 获取与真实消息发送 API 封装：`app/gateway/feishu.py:92`, `app/gateway/feishu.py:115`
- 已完成 WebSocket client 启动线程、事件 handler 注册与消息回调骨架：`app/gateway/feishu.py:129`, `app/gateway/feishu.py:155`, `app/gateway/feishu.py:163`
- 仍缺少真实环境联调与端到端验证记录

### 待办
1. 在真实 Feishu 环境验证 WebSocket 连接成功
2. 验证消息事件能进入现有 dedup + Dispatcher 流程
3. 验证 Dispatcher 返回结果能真实发回 Feishu
4. 补一轮端到端手工验证并记录结果
5. 视联调结果修正 event payload 字段映射

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

### 待办
1. 在 `app/scheduler/loop.py` 中轮询 due tasks
2. 把 due task 转成统一请求对象并路由到 Dispatcher
3. 执行成功后更新任务状态
4. 增加 schedule execution logging
5. 处理 one-time 与 cron 的不同执行后状态
6. 验证 `/tasks` 展示结果与实际执行一致

### 交付标准
- 创建一次性 reminder
- 到点后自动触发
- 结果通过 Feishu 发出
- `schedules.json` 状态被正确更新

### 相关代码
- `app/scheduler/loop.py:22`
- `app/scheduler/store.py:13`
- `app/main.py:16`
- `app/dispatcher.py:43`

## P2：把 memory / scheduler tools 真正桥接到 Gemini
**目标**：让 agent 能自己调用 memory 和 schedule，而不是全靠 Dispatcher 写死命令。

### 待办
1. 先确定 tool bridge 方案
   - MCP
   - 或本地命令包装
2. 接入 `app/memory/tools.py`
3. 接入 `app/scheduler/tools.py`
4. 定义统一输入输出 schema
5. 增加工具调用审计日志
6. 做一次自然语言触发验证：
   - “记住我喜欢简洁回复”
   - “明天下午三点提醒我开会”

### 交付标准
- agent 能从自然语言中自主调用 memory / scheduler 能力
- 不依赖 `/remember` 或 `/schedule` 这类硬编码入口

### 相关代码
- `app/memory/tools.py:7`
- `app/scheduler/tools.py:7`
- `app/agent/engine.py:106`

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

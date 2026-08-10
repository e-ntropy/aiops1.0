# 2026-08-10 AIOps 重构方案、流程与成果

## 1. 今天确定的重构目标

把项目从“Fast/Deep 两套诊断 Demo + 若干独立工具”重构为一个面向 SRE/OnCall 的完整产品闭环：

```text
知识咨询
  -> Query 深度理解与任务拆分
  -> Scope 确认
  -> Capability / Skill / 只读 Tool 规划
  -> 知识回答或真实状态查询
  -> 发现异常后进入自适应故障诊断
  -> Fast Triage
  -> Evidence Gate
  -> 证据不足时保留现场证据并升级 Deep
  -> 结构化 Evidence 与根因报告
  -> 人工确认/纠正根因
  -> 生成只读验证和处置建议
  -> 人工确认计划
  -> 同 Scope 新快照验证恢复
  -> 脱敏关闭事故
  -> 沉淀 Memory 候选和 Benchmark 样本
  -> 下一版本回归评测
```

核心产品约束：知识库只能产生 Reference Evidence；现场结论必须来自受信 ToolCall；所有 Observed
Evidence 必须绑定明确 Scope；系统只生成优化和处置建议，不自动执行变更。

## 2. 今天的修改流程

### 阶段一：统一入口和状态契约

- 将知识问答、状态查询、系统巡检、自适应诊断、只读优化、容量性能、事故复盘和评测统一为
  Capability。
- Query Understanding 负责意图识别、改写、目标拆分、实体提取、风险判定和二次确认。
- WorkflowState 统一保存 Query、Scope、Plan、Evidence、Outcome、Failure、Lifecycle 和 Memory 决策。

### 阶段二：Fast/Deep 合并为自适应诊断

- Fast Triage 先收集最小证据并生成候选报告。
- Evidence Gate 根据报告、证据源多样性、失败比例和置信度判断是否结束或升级。
- 升级 Deep 时保留 Fast Evidence，并记录 parent/child run 关系。
- Deep 失败或没有可验证报告时进入 `failed`，不由模型补写成功结果。

### 阶段三：补齐事故生命周期和 Memory 闭环

- 根因需要人工确认、纠正或拒绝。
- 处置计划只允许 observe、verify、recommendation，不执行写操作。
- 恢复必须采集同一 Scope 的新快照，不能用报告文字宣称恢复。
- 事故只有通过恢复、脱敏和关闭门禁后，才能产生评测候选并晋升 verified memory。

### 阶段四：建立分层离线评测

- 32 条 Query 契约：覆盖正常、模糊、多意图、否定、越权、Prompt Injection 和目标伪装。
- 9 条 Lifecycle 契约：覆盖确认、拒绝、采集失败、恢复、脱敏和 Memory 晋升。
- 16 条 Diagnosis Fixture：覆盖正常/边界、正例/反例/未知、简单/复杂，以及 Fast、Deep、失败路径。
- 为两套 Benchmark 固定 SHA-256 和质量阈值，通过 `--enforce` 阻断数据集漂移或行为回归。

### 阶段五：修复真实机器数据污染

- 旧 E2E 会让合成事故读取运行评测的 Windows 宿主机，导致真实 CPU/内存混入 MySQL、Redis、Kafka
  等虚构事故。
- 新增结构化 Evidence Runner 协议；Observed Evidence 必须同时具备受信标记和 ToolCall ID。
- Runner 传入的 Scope 不被信任，Evidence 强制绑定当前 Workflow Scope。
- 非法 status、content、metadata、confidence 关闭式降级，不让畸形事件击穿主循环。
- 使用完全离线 FixtureDiagnosisRunner，禁止本机采集器、网络、Docker、LLM 和数据库参与夹具评测。

### 阶段六：扩充 Skill、Playbook 和 Runbook

- 内置 Skill 从 4 个扩展为 7 个。
- 新增数据库/缓存、应用运行时、消息队列三个专科 Skill。
- 新增 MySQL/Redis、JVM/Nginx、Kafka 三份只读 Runbook。
- Skill 测试发现 `container_diagnosis` 暴露 `docker_restart`，随后从默认白名单移除，只保留人工建议。

## 3. 量化成果

| 维度 | 当前结果 | 可以证明什么 |
| --- | ---: | --- |
| Query Contract | 32/32 Exact Match | 当前确定性 Query/Scope/Capability 契约无回归 |
| Lifecycle Contract | 9/9 Exact Match | 当前确认、验证、关闭和 Memory 状态机无回归 |
| Diagnosis Fixture | 16/16 Exact Match | 当前 Fast/Deep 自适应流程符合夹具 Gold |
| Evidence Isolation | 16/16 | 当前夹具没有真实宿主机数据污染 |
| 内置 Skill | 4 → 7 | 扩展数据库/缓存、应用运行时、消息队列故障域 |
| 离线测试 | tests 68 + benchmark 19 | 当前提交的离线回归均通过 |
| 版本管理 | 4 个连续里程碑提交 | 生命周期、工作流评测、对抗门禁、证据隔离可分别追溯 |

这些结果只适用于当前版本化小型数据集，不代表真实生产准确率、真实 MTTR 改善或线上稳定性。

## 4. STAR 项目故事

### Situation

原项目已经具备 Fast/Deep Agent、RAG、MCP、队列和报告能力，但用户入口、状态字段和结束条件分散；
知识咨询、现场查询、故障诊断与事故关闭没有形成统一生命周期。旧 E2E 还暴露出真实宿主机数据污染
合成事故，以及只读诊断 Skill 暴露容器重启工具的问题。

### Task

在复用原有 Agent、RAG、MCP、Redis Streams 和 Postgres 模块的基础上，重构一个 Query → Scope →
Capability → Evidence → RCA → 人工确认 → 恢复验证 → Memory/Evaluation 的闭环；建立工具失败、越权、
格式异常、数据源不可用和 Deep 失败的兜底机制，并提供可重复的离线发布门禁。

### Action

- 设计统一 WorkflowState、Capability Planner、Query 改写/拆分和 Scope 确认流程。
- 将 Fast/Deep 合并为 Evidence Gate 驱动的自适应诊断，保留父子 Run 与已有证据。
- 建立受信 ToolCall、Scope 和 fixture 三元绑定，畸形 Evidence 关闭式处理。
- 实现人工根因确认、只读计划确认、同 Scope 恢复验证、脱敏关闭和 Memory 晋升。
- 构建 57 条纯离线契约/夹具样本，并用数据集指纹和 100% 安全/隔离阈值阻断回归。
- 扩展 3 个专科 Skill 和 3 份 Runbook，移除默认容器重启权限。

### Result

形成从知识咨询到事故关闭再到评测沉淀的完整 AIOps 生命周期；当前 32 条 Query、9 条 Lifecycle 和
16 条 Diagnosis Fixture 均通过版本化离线门禁，Evidence Isolation 为 16/16；内置 Skill 从 4 个
扩展为 7 个，并通过只读 ToolMeta 校验。与此同时保留 K8s、远程 Evidence Provider、真实事故 Gold、
Python 3.12 CI 和全仓 lint 基线等未解决项，避免把参考实现包装成生产系统。

## 5. 简历中建议使用的 STAR 压缩表达

> 针对原 Fast/Deep Agent、RAG 与工具调用链路彼此割裂、现场 Evidence 可能读取错误宿主机的问题，
> 设计 Query/Scope/Capability/WorkflowState 统一契约，将 Fast/Deep 重构为 Evidence Gate 驱动的
> 自适应诊断，并补齐人工根因确认、同 Scope 恢复验证、脱敏关闭与 Memory/Evaluation 闭环；构建
> 32 条 Query、9 条 Lifecycle 和 16 条正反例/边界事故夹具，三套离线门禁在当前数据集上均 100%
> 通过，且 Evidence 隔离 16/16，同时将只读专科 Skill 从 4 个扩展到 7 个。

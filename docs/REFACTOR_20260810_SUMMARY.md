# AIOps 统一闭环实现摘要

## 1. 目标与产品流程

项目面向 OnCall/SRE 的完整事故生命周期，而不是两个独立诊断模式的展示：

```text
知识咨询 / 告警
  → Query 理解、改写、子任务拆分与二次确认
  → Target Scope 校验
  → Capability / Skill / 只读 Tool 规划
  → 知识检索或真实状态取证
  → Evidence Gate 驱动的自适应故障诊断
  → 按需启动隔离 Specialist Agent
  → RCA 与只读处置计划
  → 人工确认
  → 同 Scope 重新取证并验证恢复
  → 脱敏、事务关闭事故
  → Verified Memory、画像、成败经验与隔离评测样本
  → 下一次发布回归
```

核心约束：知识库只产生 Reference Evidence；现场结论必须来自受信只读 ToolCall；Observed Evidence
必须绑定已验证 Scope；模型文本不能证明工具成功或系统恢复；优化助手不自动执行变更。

## 2. 已实现架构

### 服务端状态与事实库

- `WorkflowState` 统一 Query、Scope、Capability、Plan、Evidence、Failure、Budget、Memory、Outcome
  和 Lifecycle。
- Postgres 保存 Workflow Run/Event、Incident、AgentRun、ToolCall、Evidence、HumanDecision、Memory、
  Profile、Experience 和 EvaluationSample。
- 客户端只传 `run_id + expected_revision`；CAS 冲突返回 409，执行 Lease 阻止同一 Run 并发执行。
- SSE 在发送响应前完成 Claim；异常和取消会关闭审计 Run、记录失败并释放 Lease。

### 自适应故障诊断

- 初步阶段收集最小证据，Evidence Gate 根据来源多样性、错误比例、Scope 一致性和置信度做确定性决策。
- 证据不足时在同一 Workflow 中启动 Metric、Log、Infra、Runbook Specialist；初步 observed/reference
  Evidence 直接作为专业阶段 seed。
- 专业 Agent 不共享私有推理，只返回压缩 Evidence；RCA、建议和报告进入统一 Outcome。
- 结构化 Evidence 缺少受信 ToolCall ID、Scope 不一致或格式畸形时 fail-closed。

### 事故关闭与学习

- 根因可由人工确认、纠正或拒绝；计划只允许 observe、verify、recommendation。
- 恢复必须采集同一 Scope 的新快照并与基线比较，结果为 recovered/not_recovered/inconclusive。
- 只有 recovered、人工确认和脱敏通过时，关闭事务才会生成 Verified Memory、成功经验、画像和隔离
  Benchmark 样本；Candidate 随后归档并指向 Verified 记录。
- Session 报告缓存按 Session 哈希隔离；长期诊断只召回未过期、未 supersede 的 Verified Knowledge。

### Skill、Tool 与 RAG

- Capability → Skill 元数据 → Playbook/Tool 按需加载，实现渐进式披露。
- 7 个领域 Skill 覆盖主机、网络、容器、数据库/缓存、应用运行时、消息队列和通用 OnCall。
- Tool 同时受 allowlist、ToolMeta、PermissionMode、Guardrail 和预算约束；默认诊断工具均为只读。
- RAG 使用 Parent-Child、Milvus Dense、BM25 与 RRF，并严格区分知识 Reference 和现场 Observed。

## 3. 评测资产与结果

仓库包含 207 条版本化 Benchmark：

| 数据层 | 数量 | 当前证据 |
| --- | ---: | --- |
| Query Contract | 32 | Intent/Capability/Scope/Safety Exact Match 100% |
| Lifecycle Contract | 9 | HITL/Verification/Closure/Memory Exact Match 100% |
| Skill Router | 40 | 历史报告保留，不外推生产准确率 |
| Retrieval | 50 | Hybrid hit@3 0.860；Dense 对照 0.800 |
| RAG QA | 50 | Faithfulness 0.869 |
| Diagnosis Fixture | 16 | Phase/Evidence/Isolation/Exact Match 100% |
| Diagnosis E2E | 10 | 保留为 Provider/环境相关评测，不作为离线 Gate |

本轮验证：87 个本地确定性 unittest 全部通过；`workflow --enforce` 为 41/41；
`fixture --enforce` 为 16/16；CompileAll、前端语法、Compose 配置和 `git diff --check` 通过。

## 4. 工程问题与解决案例

- 合成事故混入真实宿主机 CPU/内存：使用 Fixture/Scope/ToolCall 绑定和数据源黑名单阻断。
- 跨 Session 共享诊断报告：Redis Key 改为 Session 哈希，缺 Session 不读写。
- 未确认报告污染长期知识：关闭文件 Wiki 默认写入/召回，只允许 Postgres Verified Knowledge。
- 客户端完整 State 并发覆盖：服务端 State + revision CAS + Lease。
- SSE 已 200 后才发现冲突：Claim 移到响应创建之前。
- 生产 Runner 参数演进导致 Fixture 失效：同步 Runner Contract，并把 seed Evidence 传递加入 Gate。
- Memory 到期只过滤不治理：创建 Run 时将到期 Active/Candidate/Verified 标为 expired，保留审计。

详细 STAR 记录见 [REFACTOR_STAR_LOG.md](REFACTOR_STAR_LOG.md)，面试表述见
[RESUME_INTERVIEW_GUIDE.md](RESUME_INTERVIEW_GUIDE.md)。

## 5. 验证边界

这些结果证明当前代码的确定性契约与版本化数据集行为，不代表真实生产 MTTR、跨租户安全或所有
Provider 集成。当前 Windows 环境的 Docker daemon 未运行，因此本轮未执行真实 Postgres/Redis/Milvus
集成；完整容器栈、故障注入、Python 3.12 CI、远程 Target Agent 和身份认证仍需在对应环境验证。

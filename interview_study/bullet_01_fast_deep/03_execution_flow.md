# 双模式编排：运行流程

## Fast 状态变化示例

```text
S0 {input}
Router
S1 {selected_skill, skill_reason}
Planner
S2 {plan:[A,B,C], iteration:0}
Executor(A)
S3 {past_steps:[(A,resultA)], iteration:1, plan:[A,B,C]}
Replanner
S4a {plan:[B,C]} -> Executor
S4b {selected_skill:new, plan:[], pending_reroute:true} -> Planner
S4c {response:report, plan:[]} -> END
```

Replan 覆盖 `plan`，`past_steps` 通过 reducer 继续累加。Skill reroute 清空旧计划但不清历史证据。

## Deep 状态变化示例

```text
D0 {input}
Incident/Correlation
D1 {evidences:[history?]}
EvidencePlan
D2 {evidence_plan:{agents:[metric,log],strategy:keyword_match}}
四节点静态 fan-out：metric/log 真执行，infra/runbook guard skip
D3 {evidences:[history?,metric,log]}
Reducer
D4 {candidates:[...]}
RCAJudge
D5 {rca:{...}, evidences:+[rca]}
RemediationPlanner
D6 {remediation:{steps,requires_human_confirm:true}}
Report
D7 {response:markdown} -> END
```

## 异常分支

- Router LLM 失败：关键词判断 OOS 或 generic Skill。
- Planner 结构化输出失败/空 steps：Harness fallback plan。
- Tool 失败：转成失败文本，Replanner可继续或收尾。
- Replanner 失败/空 plan/max step/repeat：模板报告。
- Deep Specialist 失败：error Evidence，不阻塞 join。
- RCAJudge 失败：Reducer 第一候选。
- 图级异常：runner 发 error；后台 Worker retry/DLQ，同步 SSE 返回错误。

## 需要主动说明的主链缺口

后台 audit 调 runner 时没有把 task/group/incident ID放入 graph state，所以 Deep 的 IncidentManager/CorrelationContext 当前无法充分利用 PostgreSQL 事故上下文；Deep `evidence` 事件也未在 audit 中逐条持久化。这不否定双图和并行节点的真实性，但限制了“完整证据闭环”的表述。


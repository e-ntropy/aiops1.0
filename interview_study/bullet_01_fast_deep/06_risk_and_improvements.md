# 双模式编排：风险与改进

| 风险 | 当前证据 | 改进位置 | 验收方式 |
| --- | --- | --- | --- |
| Deep 无动态 Replan | 固定 DAG | Deep 增加 evidence sufficiency 条件边与有界补采 | 缺证据 fixture 触发一次补采，达到上限收尾 |
| Worker 元数据未传 Deep | runner graph_input 缺 IDs | 扩展 `run_diagnosis_graph()` 参数与 audit 调用 | Deep IncidentManager 能读指定 task/group |
| Deep Evidence 未逐条落库 | audit 忽略 `evidence` event | audit 增加 evidence persistence/map真实 ID | Report 引用可反查 DB Evidence |
| Deep 权限未统一 | `decisions=None` | Specialist 工具也经 ToolFilter/PermissionDecision | read_only/ask/deny 集成测试 |
| 重复检测过弱 | 仅三步完全 fingerprint 相同 | 规范化 action/tool/target，或相似度+次数窗口 | 语义改写重复样本可终止，正常相邻步骤不误杀 |
| 同步 Deep 缺整图 timeout | 只有 Worker wait_for | service 层按模式配置 timeout/cancel | 模拟挂起 Specialist 返回结构化 timeout Evidence |
| 无 checkpoint/HITL | compile 无 checkpointer | Postgres/Redis saver + thread_id + Command resume | 进程重启后从审批节点继续且不重复 Tool |
| Deep 真实证据污染 | 历史 E2E Top-1=0 | incident_id→fixture/replay datasource 隔离 | 同一 Gold 下复跑 10×2 |
| Fast Token/延迟偏高 | 历史 Fast 均值高 | Replanner fast path、压缩 past_steps、Tool 去重 | 同数据比较质量/Token/P95 |

面试中不要把这些风险藏掉。推荐说法是：“架构骨架和主链已实现，评测暴露了证据隔离与跨层契约问题；我能明确指出应在哪些函数补齐，并设计可验收测试。”


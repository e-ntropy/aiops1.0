# 第四条简历：风险与改进

| 风险 | 当前证据 | 面试表述 | 改进方向 |
| --- | --- | --- | --- |
| DB/Redis 双写不原子 | enqueue 与 task transaction 分离 | 存在明确故障窗口 | Transactional Outbox + dispatcher |
| 重复副作用 | at-least-once，只对 succeeded task 短路 | 任务级防重不等于工具级幂等 | idempotency key、effect ledger |
| 所有异常统一重试 | Worker `_handle_failure()` | 未接错误分类 | 接入 classify_error、retry budget |
| 无退避 | 失败后直接 XADD | 可能形成重试风暴 | exponential backoff + jitter + delayed queue |
| PEL 永久悬挂 | 依赖 stale claim | poison message 可循环 | delivery count、age alarm、自动 DLQ |
| DLQ 无完整 replay 管理面 | 仅见写入 | 可观测但运维闭环不足 | list/inspect/replay/skip API + 审批 |
| 160 口径易误导 | 四层相加 | 不是 160 条 E2E | 分层报告样本和运行单元 |
| E2E 成功≠正确 | 历史 Fast/Deep 均跑通，Top-1 较低 | 分开报告可靠性和效果 | 人工 gold、失败分类、重复实验 |
| RAGAS judge 偏差 | 单 judge/model 历史报告 | 是代理评估 | 多 judge、人工抽检、置信区间 |

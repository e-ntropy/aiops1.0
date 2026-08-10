# 应用运行时与网关只读 Runbook

## JVM Full GC

对齐 heap 使用率、分配速率、GC 次数和暂停时间、线程数、容器 limit 与应用 P99。持续上升且 GC 后基线不回落才支持泄漏候选；一次 Full GC 或单点高 heap 不足以证明泄漏。

## Nginx 502/504

将网关状态码、upstream connect time、response time、健康实例数、应用错误率和下游依赖延迟放在同一时间窗。502/504 是入口症状，根因可能在应用、连接池、下游或网络。健康探针成功也不能反证业务路径异常。

## 证据输出

报告至少包含 Scope、时间窗、数据源、Observed/Reference 区分、根因候选、反证和缺失证据。需要 heap dump、线程 dump 或 Trace 时，只提出脱敏采集建议，不自动采集或上传。

---
name: application_runtime_diagnosis
display_name: 应用运行时与网关诊断
description: JVM Full GC、应用 P99、线程池/连接池耗尽、Nginx upstream 502/504 和服务超时的只读诊断。
category: oncall
tags: [jvm, nginx, latency, gateway, application]
triggers:
  - full gc
  - jvm 暂停
  - p99 延迟
  - nginx 502
  - upstream timed out
  - 线程池耗尽
  - 接口超时
allowed_tools:
  - search_knowledge_base
  - get_current_time
  - prom_query
  - prom_query_range
  - prom_active_alerts
  - prom_label_values
  - http_check
  - dns_lookup
  - check_port
  - docker_ps
  - docker_stats
  - docker_logs
  - docker_inspect
  - web_search
risk_level: low
---

# 应用运行时与网关诊断 Playbook

## 推荐排查步骤

1. 锁定服务、实例、版本、环境和故障时间窗，将客户端错误率、网关状态码与应用延迟对齐。
2. JVM 分支：比较 heap 使用趋势、GC 次数/暂停、分配速率、线程数与容器 limit；单次 Full GC 不能直接证明内存泄漏。
3. 网关分支：比较 502/504、upstream connect/response time、后端健康实例数和应用日志；避免把下游超时误判为 Nginx 自身故障。
4. 容器证据只能来自目标 Scope 对应的只读 Docker 调用，禁止用 Runner 所在宿主机的容器状态替代远程服务。
5. 证据不足或冲突时升级 Deep，列出尚需补充的 heap dump、线程 dump、trace 或网关日志，但不自动采集高敏数据。
6. 输出用户影响、时间线、结构化证据、根因候选及反证、只读验证和人工处置建议。

## 兜底规则

- HTTP 健康检查成功只说明探针路径可达，不代表业务请求正常。
- 日志关键词命中是线索，不是根因；必须与指标时间窗或 Trace 对齐。
- 无历史趋势时只能描述当前状态，不做容量预测。
- 不执行重启、扩容、配置变更或流量切换。

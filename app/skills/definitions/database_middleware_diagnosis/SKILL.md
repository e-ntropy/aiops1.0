---
name: database_middleware_diagnosis
display_name: 数据库与缓存中间件诊断
description: MySQL 锁等待、慢查询、连接池耗尽，以及 Redis maxclients、OOM command、连接超时等只读诊断。
category: oncall
tags: [mysql, redis, database, connection-pool]
triggers:
  - mysql 锁等待
  - slow query
  - connection pool exhausted
  - 数据库连接池
  - redis maxclients
  - redis oom command
  - redis 连接超时
allowed_tools:
  - search_knowledge_base
  - get_current_time
  - prom_query
  - prom_query_range
  - prom_active_alerts
  - prom_label_values
  - http_check
  - check_port
  - web_search
risk_level: low
---

# 数据库与缓存中间件诊断 Playbook

## 目标与边界

建立“用户影响 → 指标异常 → 中间件状态 → 应用调用链”的证据链，只给出只读判断与处置建议。当前没有数据库管理写工具，不执行 kill session、改参数、清缓存、重启或故障切换。

## 推荐排查步骤

1. 确认 Scope：实例、集群、服务、环境和时间窗；缺失时先询问，禁止读取本机状态代替目标实例。
2. 用 `prom_active_alerts` 和 `prom_query_range` 核对连接数、等待数、延迟、错误率、吞吐量是否同时异常；单点值不能证明趋势。
3. MySQL 分支：对齐活跃连接、锁等待、长事务、慢查询和复制延迟；必须区分“锁等待症状”与“制造锁的事务”。
4. Redis 分支：对齐 connected_clients、rejected_connections、used_memory、evicted_keys、命中率与应用连接池超时；OOM 文本必须有日志或指标佐证。
5. 使用 `check_port` 或 `http_check` 只验证连通性/健康端点。端口通不代表查询健康，端口不通也不能直接归因于数据库进程。
6. 用知识库补充 Runbook，但知识内容只能作为 Reference，不能作为当前事故的 Observed Evidence。
7. 输出根因候选、反证、缺失证据、只读验证步骤和需要人工批准的处置建议。

## 兜底规则

- 指标源不可用：有限重试后标记 unavailable，继续使用独立数据源；全部不可用则失败关闭。
- 指标互相冲突：升级 Deep，不强行给单一根因。
- 只有告警标题或用户描述：输出证据采集计划，不宣称已定位。
- 所有 Evidence 必须绑定 ToolCall ID、事故 Scope 和时间窗。

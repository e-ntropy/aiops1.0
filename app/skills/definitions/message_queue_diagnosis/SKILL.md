---
name: message_queue_diagnosis
display_name: 消息队列诊断
description: Kafka consumer lag、消费停滞、分区倾斜、broker 压力和重平衡风暴的只读诊断。
category: oncall
tags: [kafka, queue, consumer-lag, rebalance]
triggers:
  - kafka lag
  - consumer lag
  - 消费积压
  - 消费停滞
  - 分区倾斜
  - rebalance
allowed_tools:
  - search_knowledge_base
  - get_current_time
  - prom_query
  - prom_query_range
  - prom_active_alerts
  - prom_label_values
  - check_port
  - web_search
risk_level: low
---

# 消息队列诊断 Playbook

## 推荐排查步骤

1. 确认集群、topic、consumer group、环境和时间窗，先判断积压是持续增长、稳定高位还是正在恢复。
2. 对齐生产速率、消费速率、group lag、活跃 consumer 数、分区分布与 rebalance 次数。
3. 按分支验证：消费实例减少、单分区热点、下游处理变慢、broker/网络异常或 offset 提交异常。
4. 只在时间窗和 Scope 一致时关联应用日志、发布记录与指标；不要把高 lag 直接等同于 broker 故障。
5. 输出积压速度、预计追平所需的输入条件、根因候选、反证、补数要求和人工处置建议。

## 兜底规则

- 只有 lag 单点：不能判断增长速度，也不能承诺恢复时间。
- 指标冲突：保留多个候选并升级 Deep。
- 不自动扩容 consumer、不重置 offset、不迁移分区、不修改 topic 配置。

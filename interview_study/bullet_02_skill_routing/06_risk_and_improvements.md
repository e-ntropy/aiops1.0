# Skill 路由：风险与改进

| 风险 | 改进 | 验收 |
| --- | --- | --- |
| confidence未参与阈值 | 校准confidence；低置信度走generic/二阶段判别 | reliability diagram+分桶准确率 |
| 证据门槛只数past_steps | 定义Evidence sufficiency：来源、成功状态、关键反证 | 无效两步不得reroute |
| Router错误域集中 | 增加Redis/K8s/Nginx专用Skill或层级路由 | 场景macro-F1/混淆矩阵 |
| OOS仅5条 | 扩充近域负样本、prompt injection、多语言 | OOS precision/recall而非只看accuracy |
| 参数级权限未实现 | `effective_read_only(args)`与Tool专属策略 | 相同Tool只读参数allow、危险参数deny |
| Deep未统一PermissionDecision | 复用ToolFilter/decision到Specialist | 四种mode集成测试 |
| 外部Skill重名后者覆盖 | 显式优先级/签名/冲突拒绝 | 重名启动失败或可审计选择 |


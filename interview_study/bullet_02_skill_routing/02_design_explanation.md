# 渐进式 Skill 路由：设计解释

## 四层职责

- Agent：执行推理/工具循环的运行主体。
- Skill：某类任务的可发现能力包，含metadata、Playbook与allowed_tools。
- Playbook：被选中后给Planner的完整排查方法。
- Tool：真正访问知识、指标、系统或外部服务的执行原语。

## 为什么渐进披露

一次给模型全部Playbook和Tool会增加Token、降低路由注意力、提高误调用面。Router只看短卡片做意图选择；Planner只见选中的完整正文；Executor只绑定筛选后的工具。收益是上下文更小、职责更清晰、安全边界更硬；代价是前置路由一旦错，后续能力受限，因此需要generic fallback与有证据的reroute。

## OOS与低置信度

OOS表示输入不属于OnCall，不应启动诊断；低置信度表示仍在OnCall范围但Skill选择不确定。当前schema同时有 `is_oncall` 和 `confidence`，但confidence尚未形成代码阈值。OOS会直接response结束；未知Skill或LLM失败可回generic。

## 为什么权限不能只靠Prompt

Prompt只能影响模型意图，不能阻止模型伪造Tool名或被注入诱导。项目在模型前隐藏deny工具，在执行前检查tools_by_name和PermissionDecision；ask会创建Postgres审批记录并等待决定。Layer3参数级规则仍未实现，所以不能说已支持任意命令参数策略。

## reroute为什么需要证据和上限

无门槛切Skill会形成Router抖动。当前至少执行默认2步才允许切换，最多1次，并禁止当前Skill、已尝试Skill和不存在Skill。收益是有界且可解释；局限是past_steps数量不代表证据质量。


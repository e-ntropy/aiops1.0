# 双模式编排学习检查表

- [ ] 能在 60 秒内画出 Fast 图及三向条件边。
- [ ] 能在 60 秒内画出 Deep fan-out/fan-in 图。
- [ ] 能说出两套 State 至少 8 个关键字段及 reducer。
- [ ] 能解释 mode 是请求/告警规则选择，不是 LLM 自动判断复杂度。
- [ ] 能逐行说明 Replan 覆盖 plan、保留 past_steps。
- [ ] 能说明 reroute 的六个代码门槛和默认次数。
- [ ] 能手算 `recursion_limit=max_steps*3+5`。
- [ ] 能描述最近三步 fingerprint 重复算法及漏检。
- [ ] 能区分图级并行与单 Specialist 内 Tool 并行。
- [ ] 能解释未派遣 Specialist 为什么仍在图上出现。
- [ ] 能说明 Evidence 冲突如何经 Reducer/RCA 处理。
- [ ] 能列出 Router、Planner、Tool、Replanner、Specialist、RCA 六类失败兜底。
- [ ] 能解释 Worker retry 与 Graph checkpoint 的区别。
- [ ] 能明确主链没有 checkpointer/HITL resume。
- [ ] 能说明历史 E2E 为什么不能证明 Deep 更准确。
- [ ] 能用源码证明这不是“几个普通函数的集合”。

必读：`app/agents/{graph,state,skill_router,planner,executor,replanner}.py`、`app/agents/state_deep.py`、`app/diagnosis_graphs/deep_diagnosis_graph.py`、四个 Specialist、`app/orchestration/diagnosis_runner.py`、`app/runtime/{agent_harness,tool_runner}.py`。

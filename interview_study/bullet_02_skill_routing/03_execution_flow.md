# 渐进式 Skill 路由：执行流

```text
Registry扫描SKILL.md
-> Skill(metadata, playbook, allowed_tools)
-> Router menu = metadata cards
-> SkillChoice(is_oncall,skill,confidence,reason)
   -> OOS: response -> END
   -> unknown: generic fallback
   -> valid: selected_skill
-> Planner读取selected Skill完整playbook -> plan
-> Executor get_all_tools
-> filter_tools_for_skill
-> evaluate_permission allow/ask/deny
-> bind visible tools
-> Tool runner执行前再次检查
-> Evidence/past_steps
-> Replanner需要时提出reroute
-> 代码验证门槛/次数/回环/存在性
-> 新Skill -> Planner重新加载新Playbook
```

异常：Registry空、仅generic、Router LLM失败、OOS、未知Skill、Playbook结构失败、工具伪造、审批超时和reroute拒绝均有明确出口/transition。


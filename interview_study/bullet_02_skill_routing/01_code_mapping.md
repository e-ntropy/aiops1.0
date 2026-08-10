# 第二条简历：渐进式 Skill 路由——代码映射

## 简历原句与主叙事

> 针对误路由和工具越权问题，设计“元数据路由—Playbook 按需加载—工具权限校验”机制，引入 OOS 识别、证据门槛与重路由上限，在 40 条 Router/OOS 测试集上取得 75.0% 准确率。

这条的真实主链是：`SKILL.md` 被解析为 metadata+Playbook+allowed_tools；Router prompt只披露 metadata card；选中后Planner才把完整Playbook放入prompt；Executor按Skill与PermissionMode过滤工具，Tool runner在执行前再拒绝伪造工具名；Replanner只有在past_steps达到门槛且未超reroute次数时才能切Skill。

| 能力 | 文件/符号 | State/配置 | 主链结论 |
| --- | --- | --- | --- |
| Skill模型 | `app/skills/models.py::Skill` L36 | name/description/triggers/allowed_tools/risk/playbook | 已实现 |
| SKILL.md解析 | `loader.py::load_skill_from_file()` L103 | frontmatter→metadata，body→playbook | 已实现 |
| Registry | `registry.py::get_skill_registry()` L164 | 内置+外部目录、平台/禁用过滤 | 已实现 |
| 元数据卡 | `Skill.to_router_card()` L102 | 不含playbook/tools正文 | Router主链 |
| Router | `skill_router.py::skill_router_node()` L93 | input→SkillChoice | Fast主链 |
| OOS | `SkillChoice.is_oncall`、Router L149 | response+ROUTER_OUT_OF_SCOPE | 主链 |
| Playbook披露 | `planner.py::plan_node()` L27-L54 | selected_skill→skill.playbook | 主链；对象已预加载，prompt按需披露 |
| Tool过滤 | `tool_filter.py::filter_tools_for_skill()` L73 | selected_skill+mode→visible+decisions | Fast主链 |
| 权限决策 | `permissions.py::evaluate_permission()` L99 | allow/ask/deny | Fast主链 |
| 执行前拒绝 | `tool_runner.py` L335-L418 | tools_by_name+decision | Fast主链 |
| 参数校验 | Tool schema/`BaseTool.ainvoke` | Pydantic/LangChain Tool schema | 存在；权限Layer3参数规则仍TODO |
| 证据门槛 | `replanner.py::_validate_reroute()` L160 | `past_steps>=agent_reroute_min_past_steps` | 默认2；是数量门槛非质量门槛 |
| reroute上限 | config L370；validator L187 | 默认1 | 主链 |
| 防回环 | selected/tried/registry检查 L193-L202 | `tried_skills` add | 主链 |
| 40条/75% | `benchmark/skill_router_eval.jsonl`；报告080603Z | 30/40 | 历史实跑；非OOS 25/35、OOS 5/5 |

## 推荐面试边界

- “按需加载”准确说成“完整Playbook按需进入Planner上下文”；Registry会预先解析文件。
- 只读Tool可按runtime策略跨Skill补充；写/通知/高风险Tool仍需显式allowlist。
- Deep四Agent使用限定工具集，但当前未统一接 `PermissionDecision`，所以权限亮点主要证明Fast主链。
- 75%是特定模型、40条集合的历史点估计，不是线上路由SLA。


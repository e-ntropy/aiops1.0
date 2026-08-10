# 双模式 Agent 编排：30 道面试题

> 分布：Q01-Q06 基础，Q07-Q12 框架与代码，Q13-Q18 流程与数据流，Q19-Q23 异常与可靠性，Q24-Q27 选型权衡，Q28-Q30 高级质疑。回答先讲简历主叙事，再给源码证据与边界。

## Q01：请整体介绍 Fast/Deep 双模式 Agent 编排

### 难度
基础

### 面试问题
“你简历里写了 Fast/Deep 双状态图，这两个模式分别做什么？”

### 面试官考察点
项目认知、架构表达、真实性验证。

### 推荐回答
我把诊断拆成两种执行策略，而不是让所有请求都跑同一条重链路。Fast 是 Skill Router→Planner→Executor→Replanner 的有界循环，适合大多数单域故障：先选 Playbook，再逐步调用工具，根据结果改计划或收尾。Deep 是独立的证据型 DAG：先形成 EvidencePlan，再让日志、指标、基础设施和 Runbook Specialist 并行调查，只回传结构化 Evidence，最后由 Reducer、RCAJudge 和 Report 汇总。

代码上它们是两个独立 `StateGraph`，在统一 runner 里按请求 mode、功能开关和 Alertmanager 严重度规则选择。我要限定一点：动态 Replan 属于 Fast，Deep 的“动态”是选择派哪些专家，不是循环重规划。这种拆分让简单请求控制成本、复杂事故扩大证据覆盖，同时保留统一 SSE/Worker 事件协议。

### 代码证据
- 文件：`app/agents/graph.py` L95-L135；`app/diagnosis_graphs/deep_diagnosis_graph.py` L894-L933。
- 函数：`build_aiops_graph()`、`build_deep_graph()`、`resolve_effective_mode()`。
- 调用链：API/Worker→`run_diagnosis_graph()`→Fast/Deep `astream()`。
- State：`PlanExecuteState`、`DeepDiagnosisState`。

### 面试官可能继续追问
1. 两个模式是 LLM 自动选择吗？
2. 为什么 Deep 不直接复用 Fast 的 State？

### 追问简答
1. 不是。人工请求显式传 mode；Webhook 用 severity/告警数量规则；Deep 开关关闭会回落 Fast。
2. 两者控制流不同：Fast 需要 plan/past_steps/iteration，Deep 需要 evidences/candidates/rca，分开能避免无关字段和 reducer 冲突。

### 我需要掌握的知识
两张构图文件、统一 runner、两套 State、Webhook 模式规则。

## Q02：LangGraph 与普通 LangChain Chain 有什么区别

### 难度
基础

### 面试问题
“为什么这里不用普通 LangChain Chain？”

### 面试官考察点
框架理解、技术选型。

### 推荐回答
普通 Chain 更适合固定的线性数据流，比如 retrieve→prompt→LLM→parse；本项目的诊断有条件路由、循环重规划、并行 fan-out/fan-in、共享状态累加和多种终止条件。LangGraph 把这些控制流显式建成节点和边，State 的 reducer 还能定义字段是覆盖还是累加。

在 Fast 图里，Replanner 后可能去 Executor、回 Planner 或 END；Deep 图里四个 Specialist 并行写 `evidences`，Reducer 等它们汇合。用 Chain 可以勉强嵌套 if/loop，但状态变化和异常出口会隐藏在业务函数里，不利于观测与测试。代价是必须严格设计 TypedDict、Reducer 和 recursion limit，而且当前项目还没把 checkpointer 用起来。

### 代码证据
- `app/agents/graph.py::should_end()` L56、`build_aiops_graph()` L101-L133。
- `app/agents/state_deep.py` L43-L46：`evidences` 使用 `operator.add`。
- Deep 构图 L917-L925：fan-out/fan-in。

### 面试官可能继续追问
1. LangGraph 只是把 if/else 画出来吗？
2. 当前用了 LangGraph 的 checkpoint 吗？

### 追问简答
1. 不只是可视化；它负责 State 合并、条件调度、并行 join、递归限制和流式节点事件。
2. 没有。两图 `compile()` 未传 checkpointer，Worker 重跑是任务级恢复，不是图断点恢复。

### 我需要掌握的知识
StateGraph、conditional edge、Reducer、fan-in、checkpoint 概念。

## Q03：为什么使用 StateGraph

### 难度
基础

### 面试问题
“StateGraph 对这个项目最核心的价值是什么？”

### 面试官考察点
框架理解、源码理解、系统设计。

### 推荐回答
核心价值是把“诊断进度”变成显式、可合并、可检查的数据，而不是散落在 prompt 或局部变量里。Fast 的 `plan`、`past_steps`、`iteration`、`response` 决定下一步；Deep 的 `evidence_plan`、`evidences`、`candidates`、`rca` 形成证据流水线。节点只返回 patch，Graph 按 reducer 更新共享状态。

例如 Executor 每次只返回一条 `past_steps`，因为该字段用 `operator.add`，历史不会丢；Replanner 返回新 `plan` 时使用覆盖语义。Deep 四个 Agent 同时追加 Evidence，不会互相覆盖。这让有界执行和审计可以依据真实状态，而不是相信模型说“完成了”。

### 代码证据
- `app/agents/state.py::PlanExecuteState` L34-L72。
- `app/agents/state_deep.py::DeepDiagnosisState` L25-L57。
- `executor.py` L171-L176；`replanner.py` L382-L404。

### 面试官可能继续追问
1. 哪些字段不能用 add reducer？
2. TypedDict 能做运行时校验吗？

### 追问简答
1. `plan/response/rca` 是当前版本的唯一值，应覆盖；若累加会产生歧义。
2. TypedDict 主要是静态结构提示，不等同 Pydantic 运行时校验；Planner/Replanner 的 LLM 输出另用 Pydantic schema。

### 我需要掌握的知识
State patch、覆盖/累加 reducer、TypedDict 与 Pydantic 的区别。

## Q04：Fast Graph 的定位和完整结构

### 难度
基础

### 面试问题
“Fast Graph 从开始到结束怎么跑？”

### 面试官考察点
源码理解、完整执行流程。

### 推荐回答
Fast 从 SkillRouter 开始。Router只看 Skill 元数据，选定 Skill 或判 OOS；OOS 直接写 `response` 结束。Planner加载选中 Skill 的完整 Playbook，生成结构化步骤。Executor每次执行 `plan[0]`，先按 Skill 和 PermissionMode 过滤工具，再用工具型 Agent执行，把 `(step,result)` 追加到 `past_steps`。Replanner读取剩余计划和历史结果，决定生成报告、给出新 plan，或者在证据表明方向错误时切 Skill回 Planner。

循环由 Replanner 后的条件边控制，最大步骤、最近三步重复和 LangGraph recursion limit共同保证收敛。它适合大多数需要逐步验证、但不必同时拉起多证据域的日常诊断。

### 代码证据
- `app/agents/graph.py` L104-L133。
- Router/Planner/Executor/Replanner 分别位于对应模块的 `*_node()`。
- 条件函数：`route_after_skill()`、`should_end()`。

### 面试官可能继续追问
1. Executor 执行后谁删除 plan[0]？
2. OOS 为什么在 Router 直接结束？

### 追问简答
1. Executor不删除；Replanner返回的新 plan 覆盖旧 plan，Harness fast path通常取 `plan[1:]`。
2. 防止非运维问题继续加载 Playbook、暴露工具和产生成本，也降低越权面。

### 我需要掌握的知识
Fast 图节点、三向条件边、每一步 State patch。

## Q05：Deep Graph 的定位和完整结构

### 难度
基础

### 面试问题
“Deep Graph 为什么叫深度诊断，它有哪些阶段？”

### 面试官考察点
项目认知、多 Agent 架构。

### 推荐回答
Deep 的重点不是循环更多，而是证据域更广、角色更隔离。它先由 IncidentManager和CorrelationContext准备事故/历史背景，再由确定性 EvidencePlan判断需要哪些专家；图上静态扇出到 Log、Metric、Infra、Runbook 四个节点，未派遣节点由 guard直接跳过。每个真实 Specialist有自己的限定工具和 prompt，只返回压缩 Evidence。

之后 EvidenceReducer过滤错误证据、形成候选根因；RCAJudge只看结构化 summary重排，失败时确定性取第一候选；RemediationPlanner把建议分为只读验证和需人工确认的写操作；ReportAgent格式化最终 Markdown。它的深度来自证据覆盖和汇聚，不应简单理解为“比 Fast 多跑几轮”。

### 代码证据
- `deep_diagnosis_graph.py` L1-L33、L60-L89、L894-L933。
- `state_deep.py` L25-L57。
- 四个 `app/agents/*_agent.py::run_*_agent()`。

### 面试官可能继续追问
1. EvidencePlan 是 LLM 吗？
2. 四个 Agent 每次都会运行吗？

### 追问简答
1. 当前是关键词规则，强调确定性和低成本；未来可升级结构化 LLM router。
2. 图节点都会被调度，但 guard 对未派遣节点立即 skip，不调用 LLM或工具。

### 我需要掌握的知识
Deep 八阶段、dispatch guard、Evidence 黑板模式。

## Q06：为什么不统一成一个图

### 难度
基础

### 面试问题
“为什么不做一个大图，然后条件分支决定走 Fast 还是 Deep？”

### 面试官考察点
架构权衡、复杂度意识。

### 推荐回答
可以统一，但我选择分图是为了让状态和失败语义保持内聚。Fast 是循环型控制流，核心字段是 plan、past_steps、iteration和reroute；Deep 是证据型 DAG，核心字段是并行 evidences、candidates和rca。如果硬塞进一个 TypedDict，节点会看到大量无关字段，Reducer更复杂，条件边和事件测试也更难。

分图还能独立演进成本策略和模型档位，并由统一 runner维持外部协议。代价是公共能力可能漂移：例如当前 Deep尚未统一 Fast权限策略，Worker元数据也未完整传入 Deep。这正是分图架构需要用共享 runtime contract和集成测试治理的地方。若两图将来共享大多数节点和State，再考虑子图复用而不是提前合并。

### 代码证据
- 两套 State：`state.py` 与 `state_deep.py`。
- runner L160-L177 共用输入外壳并选择不同 graph。
- Deep Agent `decisions=None` 显示公共能力存在漂移。

### 面试官可能继续追问
1. 分图是否导致重复代码？
2. 可以用 subgraph 吗？

### 追问简答
1. 会，因此统一 runner、RuntimeEvent和Tool runner；仍需补统一权限/审计契约。
2. 可以，适合抽出通用报告、审批或检索子图，但当前源码是两个顶层独立图，没有图嵌套。

### 我需要掌握的知识
内聚/耦合、State schema、公共 runtime contract、LangGraph subgraph。

## Q07：Fast/Deep 路由判断依据是什么

### 难度
中等

### 面试问题
“系统怎么判断一个请求走 Fast 还是 Deep？”

### 面试官考察点
源码理解、真实性验证。

### 推荐回答
当前不是由 LLM自动判断复杂度，而是显式和确定性规则结合。人工 SSE/submit请求传 `diagnosis_mode`；runner先做别名归一化，再检查 `deep_diagnosis_enabled`，只有请求Deep且开关打开才真正走Deep。Alertmanager入口会把 critical/page/p0/p1，以及一次payload告警数不少于10的情况设为Deep，其余设Fast。

简历里“面向常规与复杂故障”是产品抽象；源码层我会坦诚当前复杂度判定是severity/batch-size规则。优点是可解释、可回滚；不足是业务影响面、跨域程度等信号没有进入分类。下一步可以用结构化规则加小模型分类，但必须保留人工 override和成本上限。

### 代码证据
- `diagnosis_runner.py::normalize_diagnosis_mode()` L60、`resolve_effective_mode()` L80。
- `webhook.py::_diagnosis_mode_for()` L81-L88。
- State：requested/effective mode。

### 面试官可能继续追问
1. Deep 开关关闭会怎样？
2. 未知 mode 字符串会怎样？

### 追问简答
1. 回落 Fast，并通过 `group_agent_reserved`/mode_selected事件说明。
2. 归一化默认 Fast，避免非法值把请求送进重链路。

### 我需要掌握的知识
三类入口、功能开关、显式override与自动分类的权衡。

## Q08：Router 的输入输出是什么

### 难度
中等

### 面试问题
“Fast 图中的 Router 到底路由什么？和 Fast/Deep 路由一样吗？”

### 面试官考察点
源码理解、概念区分。

### 推荐回答
不是一层路由。Fast/Deep是图级模式选择，发生在 runner；Fast图里的 SkillRouter是在选定Fast后，从已注册的Skill中选择适合的Playbook。它输入用户现象、Skill metadata menu和可选Wiki经验，结构化输出 `is_oncall、skill_name、confidence、reason`。

如果非OnCall，它直接写最终response；如果模型返回不存在的Skill，回退 `generic_oncall`；如果LLM失败，用关键词规则判OOS或放行generic。正常输出写 `selected_skill/skill_reason`，Planner随后才读取完整Playbook。面试中把这两层路由区分开，是证明真正读过代码的关键。

### 代码证据
- `skill_router.py::SkillChoice` L22-L26；`skill_router_node()` L93-L199。
- `Skill.to_router_card()` L102-L109。
- `graph.py::route_after_skill()` L81-L92。

### 面试官可能继续追问
1. confidence 是否参与代码阈值？
2. Router 会看到全部工具吗？

### 追问简答
1. 当前主要记录观测，不以固定阈值阻断，这是可改进点。
2. 不会。它只见Skill卡片；工具在Executor阶段按选中Skill过滤。

### 我需要掌握的知识
模式路由 vs Skill路由、结构化输出、OOS分支。

## Q09：State 字段和 Reducer 如何设计

### 难度
中等

### 面试问题
“挑几个关键 State 字段，解释为什么这么设计。”

### 面试官考察点
LangGraph机制、数据建模。

### 推荐回答
Fast中 `plan` 是当前待执行计划，用覆盖语义；`past_steps` 是历史事实，用 `operator.add`，Executor每次只追加一条；`response` 非空触发END；`iteration`用于硬预算；`tried_skills`累加失败记忆，`pending_reroute`是一次性控制标志。Deep中 `evidences`和`transition_history`必须累加，因为多个并行节点会返回patch；`candidates/rca/remediation/response`则是阶段性唯一产物，使用覆盖。

设计原则是：可重复发生且需要保留历史的事件用add；当前决策/快照用覆盖。Reducer选错会有严重后果，比如 evidences覆盖会丢并行结果，plan累加则可能重复执行旧步骤。

### 代码证据
- `state.py` L34-L72。
- `state_deep.py` L25-L57。
- Executor返回 L171；Replanner返回 L376-L404。

### 面试官可能继续追问
1. 并行 append 的顺序稳定吗？
2. transition_history有什么用？

### 追问简答
1. 不应依赖完成顺序表达业务优先级；Reducer/RCA应按显式score/source处理。
2. 记录节点出口原因，runner转换为结构化SSE，便于审计为何fallback/reroute/结束。

### 我需要掌握的知识
Reducer错误案例、并发State合并、事件溯源思路。

## Q10：条件边如何实现和终止

### 难度
中等

### 面试问题
“Fast图有哪些条件边？无限循环怎么结束？”

### 面试官考察点
源码理解、有界执行。

### 推荐回答
第一条条件边在SkillRouter后：有response说明OOS或兜底结束，直接END；否则进Planner。第二条在Replanner后，是三向路由：response非空优先END；`pending_reroute=True`回Planner；plan为空且没有response也强制END；其他情况回Executor。

循环之外还有三道收敛保护：Harness在iteration达到默认5时强制模板报告；最近三步fingerprint完全相同时强制报告；runner给LangGraph传递 `recursion_limit=max_steps*3+5`。所以正常终止靠response，异常终止靠空plan、预算、重复和递归上限。

### 代码证据
- `graph.py::route_after_skill()` L81、`should_end()` L56。
- `agent_harness.py::evaluate_replanner_pre_llm()` L708。
- runner L171-L182。

### 面试官可能继续追问
1. 为什么 response 优先级最高？
2. plan空且无response直接END会不会没报告？

### 追问简答
1. 它代表节点已经形成终局结果，避免reroute/旧plan继续执行。
2. 会存在无报告风险，所以Planner/Replanner有空plan兜底；条件边是最后防死循环保险。

### 我需要掌握的知识
条件函数返回值映射、正常/异常终止路径。

## Q11：Planner 如何保证结构化输出

### 难度
中等

### 面试问题
“Planner输出格式错了怎么办？”

### 面试官考察点
LLM工程、异常处理。

### 推荐回答
Planner定义Pydantic `Plan`，核心字段是 `steps: List[str]`，通过项目的 `ainvoke_structured()`调用模型，而不是手写解析任意文本。Prompt中注入用户问题、选中Skill的display name和完整Playbook，temperature=0、timeout和有限重试提升稳定性。

如果结构化调用抛错，代码返回Harness预设fallback plan并记录 `PLANNER_LLM_FAILED` transition；如果对象合法但steps为空，也走empty-plan fallback。这样格式错误不会让图崩掉。限制是结构正确不代表计划质量正确，仍需Executor结果和Replanner纠偏。

### 代码证据
- `state.py::Plan` L78-L88。
- `planner.py::plan_node()` L27-L100。
- `core/structured.py::ainvoke_structured()`。

### 面试官可能继续追问
1. 为什么不用 Union Plan/Response？
2. fallback plan来自哪里？

### 追问简答
1. 注释说明部分模型对Union结构兼容差，项目改为单schema discriminator主要用于Replanner。
2. `AgentHarness.planner_fallback_plan()`，让策略集中而非散落在节点。

### 我需要掌握的知识
Pydantic structured output、schema正确性与语义正确性的区别。

## Q12：Replanner 的触发条件与动态更新

### 难度
中等

### 面试问题
“什么情况下会Replan？新旧计划怎么处理？”

### 面试官考察点
完整代码机制、状态变化。

### 推荐回答
每次Executor完成一步都会进入Replanner，但不一定调用LLM。Harness先检查最大步数、重复步骤，以及是否满足fast path；fast path可直接把 `plan[1:]`作为next plan，减少一次模型调用。需要判断时，Replanner LLM读取原问题、当前plan、past_steps、当前/候选/tried Skills，结构化输出 `Act`。

如果完成就生成或二次合成报告并清空plan；未完成就用非空 `act.plan`覆盖旧plan，而不是merge；方向明显错误则提议reroute，经代码门槛校验后切Skill、清空旧plan、保留past_steps，再回Planner。已完成步骤的避免重复一部分靠fast path，LLM计划主要靠prompt约束，代码没有语义级逐步去重。

### 代码证据
- `replanner.py::replan_node()` L207-L405。
- Harness L708-L741。
- `state.py::Act` L97-L155。

### 面试官可能继续追问
1. 为什么覆盖而不是merge？
2. 已完成步骤真的不会重复吗？

### 追问简答
1. 新plan代表基于最新证据的剩余工作，merge会把失效旧步骤带回。
2. 不能绝对保证；prompt禁止、history可见和三步重复检测共同降低风险，语义去重是改进项。

### 我需要掌握的知识
Harness fast path、Act schema、State覆盖与历史保留。

## Q13：从 API 到 Fast 最终报告的完整调用链

### 难度
进阶

### 面试问题
“请从接口入口一路讲到Fast报告返回。”

### 面试官考察点
完整执行流程、分层能力。

### 推荐回答
同步入口是 `aiops_diagnose()`，先做IP限流，再创建SSE generator；Service层 `stream_diagnose()`申请Redis分布式 `manual_diagnosis`槽，满了立即返回结构化错误。拿到槽后调用统一 `run_diagnosis_graph()`，解析requested/effective mode，懒加载Fast图并设置 `recursion_limit`。

runner启动graph.astream任务，同时用token queue合并节点事件和Tool token。Fast依次经过SkillRouter、Planner、Executor、Replanner循环。每个node output被 `_convert_node_event()`转为skill_selected、plan、step_complete、replan或report；最终runner汇总usage、发complete，并可把报告写短期chat memory/Wiki。API把每个RuntimeEvent JSON编码成SSE message返回。

### 代码证据
- `app/api/v1/aiops.py` L133-L170。
- `app/services/aiops_service.py` L23-L69。
- `app/orchestration/diagnosis_runner.py` L100-L301、L321-L384。

### 面试官可能继续追问
1. 为什么graph另起task？
2. 客户端断开怎么办？

### 追问简答
1. 为并行消费graph节点输出和stream_sink旁路token/tool事件，统一进queue。
2. Service捕获CancelledError，runner取消graph task并释放分布式槽。

### 我需要掌握的知识
FastAPI SSE、async generator、token queue、取消传播。

## Q14：从 Webhook 到 Deep 最终持久化的完整链路

### 难度
进阶

### 面试问题
“后台Deep诊断从告警进来到ACK经历什么？”

### 面试官考察点
跨层调用链、数据一致性。

### 推荐回答
Alertmanager入口先限流，只处理firing alert，根据严重度/批量大小选mode，通过IncidentRepository归一化并upsert Alert、IncidentGroup、Incident和DiagnosisTask。若新建任务，就向对应优先级Redis Stream XADD并把message id回写PostgreSQL。

Worker每轮先XAUTOCLAIM旧PEL，再XREADGROUP新消息；查Postgres任务、做已成功幂等检查、标running并申请全局worker槽，随后用wait_for调用带审计的graph runner。Deep图完成后，audit至少保存报告、Run和相关事件，任务标succeeded，最后XACK。失败则在最大次数内pending→重新XADD→ACK旧消息，耗尽后写DLQ并ACK。

我会主动说明当前缺口：audit调用统一runner时没把task/group IDs传进Deep State，Deep专业Evidence事件也未逐条落库，所以“后台Deep完整证据闭环”是部分实现，但投递、图执行、任务状态和最终报告链是真实的。

### 代码证据
- `webhook.py` L109-L193。
- `queue/redis_streams.py` L128-L333。
- `diagnosis_worker.py` L46-L197。
- `orchestration/audit.py` L47-L225。

### 面试官可能继续追问
1. 为什么最后才ACK？
2. DB成功、ACK前崩溃怎么办？

### 追问简答
1. 保证处理未完成时消息仍在PEL，可被回收，形成at-least-once。
2. 消息会被重投；Worker查Postgres已succeeded后直接ACK，避免重复诊断。

### 我需要掌握的知识
Webhook持久化、Streams、Worker audit、崩溃窗口。

## Q15：四个专业 Agent 如何分工

### 难度
进阶

### 面试问题
“Log、Metric、Infra、Runbook Agent 有什么实质区别？”

### 面试官考察点
多Agent设计、工具边界。

### 推荐回答
Metric关注可量化资源和趋势，优先Prometheus，退化到本机CPU、内存、磁盘、进程；Log围绕日志错误模式和告警模板，当前主要通过知识库检索，默认不连真实日志后端；Infra检查容器、端口、DNS/HTTP和依赖健康，硬编码排除docker restart等写工具；Runbook同样使用知识库，但Prompt专注SOP来源和3到5步流程，不做根因判定。

拆分不是为了多建几个类，而是缩小每个Agent的工具菜单、Prompt职责和输出契约。它们都只返回一条压缩Evidence，私有消息不进入共享State。当前Log与Runbook共享KB工具，实际区分主要靠scoped prompt；这也是可能出现证据重复、后续可通过collection/filter进一步隔离的地方。

### 代码证据
- `metric_agent.py::_load_metric_tools()` L34、`run_metric_agent()` L109。
- `log_agent.py::_load_log_tools()` L36、`run_log_agent()` L96。
- `infra_agent.py::_MCP_INFRA_TOOL_NAMES` L20、`run_infra_agent()` L108。
- `runbook_agent.py` L20-L41、L81。

### 面试官可能继续追问
1. Log Agent是真日志Agent吗？
2. Log与Runbook会不会重复？

### 追问简答
1. 当前是日志模式/告警知识检索Agent，不应夸大为已接Loki/Elasticsearch实时日志。
2. 可能；靠prompt错位，未来应用source/type过滤和相似Evidence去重。

### 我需要掌握的知识
四类工具白名单、Prompt、Evidence source/type及当前数据源限制。

## Q16：四个 Agent 是否真正并行

### 难度
进阶

### 面试问题
“你说并行分析，代码里到底哪里并行？会不会只是顺序调用四个函数？”

### 面试官考察点
真实性验证、LangGraph机制。

### 推荐回答
图级并行来自Deep构图：同一个 `evidence_plan` 节点分别向四个Specialist添加边，四个节点又都指向 `evidence_reducer`。在LangGraph里，这形成同一superstep的fan-out以及Reducer前的join barrier，不是源码里for循环顺序await四次。并行节点返回的 `evidences` 用 `operator.add`合并。

但我不会把它说成“每次四个模型请求都并发”。图拓扑固定有四路，EvidencePlan用规则产生agent名单，`_dispatch_guard`让未选中的节点立即返回skip transition。因此广播场景可四个真实Agent并行，普通场景可能只有Metric+Log实际执行。每个Agent内部还可由Tool runner把并发安全的只读Tool用 `asyncio.gather`并行，这是第二层并行。

### 代码证据
- Deep构图 L917-L920。
- `state_deep.py` L43-L46。
- `_dispatch_guard()` L332-L356。
- `tool_runner.py` L461-L487。

### 面试官可能继续追问
1. Reducer会在第一个Agent完成后提前跑吗？
2. 并发完成顺序会影响RCA吗？

### 追问简答
1. 多入边在该图结构中形成join，Reducer接收该superstep的合并State。
2. 不应依赖列表顺序；当前Reducer按显式score/type排序，但Evidence引用下标仍需稳定性测试。

### 我需要掌握的知识
LangGraph superstep、fan-out/fan-in、Tool级并行与图级并行区别。

## Q17：并行 Agent 如何合并 Evidence 和处理冲突

### 难度
进阶

### 面试问题
“日志说A，指标说B，最后听谁的？”

### 面试官考察点
多Agent冲突、证据设计。

### 推荐回答
系统不让Agent互相辩论，也不让后完成者覆盖前者，而是把每个结论包装成带source、type、summary、content、metadata的Evidence，统一追加到黑板。EvidenceReducer先识别error Evidence并按来源/质量规则生成候选和support score；RCAJudge只读候选和关键summary进行一次结构化重排，并给出supporting evidence IDs与confidence。

这样冲突被保留为可比较证据，不会在共享对话里相互污染。若RCA LLM失败，取Reducer排序第一候选，保证流程收敛。局限是当前评分较启发式，RCA只看summary可能丢失原文细节；更成熟的实现应加入时间一致性、来源可靠度、反证关系和校准后的置信度。

### 代码证据
- `evidence_reducer_node()` L432-L500。
- `rca_judge_node()` L589-L644。
- `_rca_fallback()` L576-L586。
- State：`evidences`,`candidates`,`rca`。

### 面试官可能继续追问
1. 为什么不让Agent互相讨论？
2. confidence可信吗？

### 追问简答
1. 无约束讨论增加Token、循环和群体偏差；黑板Evidence更可审计。
2. 当前主要是模型/规则输出，未做统计校准，只能作排序提示，不能当概率承诺。

### 我需要掌握的知识
黑板架构、Evidence schema、候选排序、反证与置信度校准。

## Q18：步数预算、Token预算和重复检测如何协作

### 难度
进阶

### 面试问题
“你说任务有界，具体是哪几个预算？Token超了会停吗？”

### 面试官考察点
有界执行、指标意识、真实性。

### 推荐回答
Fast的硬控制首先是业务步数，默认 `AGENT_MAX_STEPS=5`。Harness在Replanner前看到iteration达到上限就直接生成模板报告；Executor还有 `iteration>max`保险。其次，最近三个步骤规范化后的fingerprint完全相同会强制收尾。第三，runner给LangGraph设置 `recursion_limit=max_steps*3+5`，防控制流异常循环。Tool Agent本身还有max_iters。

Token和时间不是同一回事。Harness会汇总input/output/total tokens和耗时，在结束时产生budget warning/exceeded事件，但当前不等于中途Token达到阈值立即cancel。后台Worker有整任务 `asyncio.wait_for`，同步Deep没有完全同等的整图超时。因此面试中我会说“通过步数、重复、递归和工具轮数实现控制流有界，并做Token/耗时观测”，不说“所有预算都是硬中断”。

### 代码证据
- `config.py` L369；Harness L708-L725、L803-L819。
- `diagnosis_runner.py` L171、L243-L269。
- `diagnosis_worker.py` L140-L143。

### 面试官可能继续追问
1. 为什么recursion limit是三倍加五？
2. 重复检测有哪些误判/漏判？

### 追问简答
1. Fast每步通常经历Executor+Replanner，并预留Router/Planner/reroute余量，是工程上限而非数学最优。
2. 语义改写会漏判；三个同名但目标不同的步骤可能误判，需把tool/target纳入fingerprint。

### 我需要掌握的知识
软/硬预算、recursion limit、Token usage、timeout/cancellation。

## Q19：某个专业 Agent 超时或失败怎么办

### 难度
进阶

### 面试问题
“四个并行Agent中一个挂了，join会不会拖死整张图？”

### 面试官考察点
异常处理、并发可靠性。

### 推荐回答
四个真实Specialist都把依赖加载、LLM和Tool循环包在try/except中；失败时不向图顶层抛，而是返回一条 `metadata.error_type` 和 `content.error=true` 的error Evidence，以及失败transition。Reducer不会把它作为正向根因候选，但把失败信号保留下来，报告也会列出失败Agent。因此普通调用错误不会让fan-in丢失。

超时要分层：LLM/Tool可能有自身timeout，后台Worker还用 `asyncio.wait_for`限制整次诊断；但Specialist节点外没有统一per-agent wait_for，同步Deep也缺Worker层整图timeout。所以“单Agent失败可降级”已实现，“任何挂死都能在固定时间内局部隔离”还不完整。改进是在dispatch wrapper给每个Agent加timeout，超时也转error Evidence。

### 代码证据
- 四个 `run_*_agent()` 的except分支，例如 metric L156-L170。
- Reducer `_is_error_evidence()` L404。
- Report失败Agent统计 L810-L878。

### 面试官可能继续追问
1. error Evidence为什么还保留？
2. 全部Agent失败还能出报告吗？

### 追问简答
1. “某数据域不可用”本身是重要的不确定性，不能静默删除。
2. 可以走无候选RCA和保守报告，但结论质量低，必须明确人工介入。

### 我需要掌握的知识
局部故障隔离、超时层级、error-as-data。

## Q20：Planner 或 Replanner 输出错误如何兜底

### 难度
进阶

### 面试问题
“结构化输出也会失败，你怎么保证图能继续？”

### 面试官考察点
LLM可靠性、工程能力。

### 推荐回答
Planner用Pydantic `Plan`和统一structured invoke。抛异常或steps为空时，返回Harness fallback plan并记录transition，保证Executor有工作可做。Replanner用单一 `Act` schema加 `is_finished`判别，避免部分模型对Union兼容问题；结构化调用失败、未完成却plan为空、达到步数或重复时，都走 `_force_summary()`生成基于past_steps的保守报告。

此外最终报告合成若强模型失败，会回退Replanner draft；draft也空才用模板。这个策略优先保证“可解释地收敛”，而不是无限重试模型。限制是fallback报告可能只汇总证据、不保证根因质量，所以transition和报告措辞应表明降级。

### 代码证据
- `planner.py` L56-L82。
- `replanner.py` L282-L299、L347-L405、`_force_summary()` L408。
- `state.py::Act` L97-L155。

### 面试官可能继续追问
1. 为什么不无限重试LLM？
2. fallback是否会掩盖失败？

### 追问简答
1. 会扩大延迟、成本和雪崩；有限重试后确定性收敛更适合OnCall。
2. transition明确记录失败原因，报告使用保守措辞，监控应统计fallback率。

### 我需要掌握的知识
结构化输出失败模式、有限重试、确定性fallback。

## Q21：无限循环如何终止，重复步骤算法是什么

### 难度
进阶

### 面试问题
“请别只说有防死循环，具体算法是什么？”

### 面试官考察点
源码细节、算法边界。

### 推荐回答
每次Executor把iteration加一，Replanner调用前Harness先看 `iteration>=max_agent_steps`，默认5就force report。重复算法只看 `past_steps`最后三项的step文本：转小写，只保留字母数字和中文，截前100字符；三个fingerprint非空且集合大小为1，就认定重复并force report。Graph外层还有recursion limit。

这个实现是低成本保险，专门挡“同一步骤原样反复出现”，不是语义相似检测。比如“检查CPU使用率”和“再次查看CPU负载”不会命中；反过来三个同名步骤即使目标实例不同也可能误杀。改进时我会把规范化tool name、resource target和query参数组成fingerprint，并设置滑动窗口次数，而不是直接上Embedding增加延迟。

### 代码证据
- `agent_harness.py::_has_repeated_steps()` L803-L819。
- `evaluate_replanner_pre_llm()` L708-L725。
- `executor.py` L97-L125。

### 面试官可能继续追问
1. 为什么窗口是3不是2？
2. Deep会无限循环吗？

### 追问简答
1. 两次可能是合理复核，三次相同更像循环；仍是经验参数，应通过失败集调优。
2. Deep是固定DAG，没有图循环；风险在Agent内部工具轮数和外部调用挂起。

### 我需要掌握的知识
fingerprint实现、滑动窗口、误判/漏判分析。

## Q22：Deep Graph 整体异常会自动降级到 Fast 吗

### 难度
进阶

### 面试问题
“Deep失败以后是不是自动切Fast继续？”

### 面试官考察点
真实性验证、错误恢复。

### 推荐回答
当前要精确回答：Deep功能开关关闭时，runner在执行前会把requested Deep降为effective Fast；但Deep已经开始运行后的图级异常，不会在同一次请求内自动切Fast。runner捕获异常并发 `diagnosis_failed`事件；同步入口把错误返回SSE，后台Worker把本次Run标失败并按attempt重试，最终可能DLQ。

节点级降级很充分，所以多数单Agent/RCA错误不会升级为图级错误；但“Deep整体异常自动回退Fast”没有真实接线。若要实现，我会只对明确可重试且未执行副作用的异常触发一次Fast fallback，并在State/审计中标记degraded_from=deep，防止重复成本和语义混淆。

### 代码证据
- `resolve_effective_mode()` L80-L97。
- runner `_graph_runner()` L180-L209。
- Worker `_handle_failure()` L161-L197。

### 面试官可能继续追问
1. 为什么不无条件回退Fast？
2. 重试会从失败节点继续吗？

### 追问简答
1. 可能双倍成本、重复Tool调用并掩盖系统性故障；需按错误类型和副作用决定。
2. 不会。无checkpointer，Worker重试从图开始重新执行。

### 我需要掌握的知识
前置模式回落 vs 运行时fallback、任务重试 vs checkpoint。

## Q23：Graph 是否支持 checkpoint、人工暂停和恢复

### 难度
进阶

### 面试问题
“LangGraph很强的一点是checkpoint，你项目用了没有？”

### 面试官考察点
诚实度、框架深度、恢复语义。

### 推荐回答
当前主链没有。Fast和Deep都直接 `compile()`，没有传checkpointer；runner config只有recursion_limit，没有thread_id；构图也没有接 `interrupt`节点。仓库中的 `hitl.py` 展示了 `interrupt()`和 `Command(resume=...)`思路，并有直接函数测试，但不能把文件存在当成主链实现。

当前恢复发生在任务层：Redis PEL/XAUTOCLAIM让崩溃消息被其他Worker重领，Postgres记录attempt和状态，但图会从头运行。这满足at-least-once任务恢复，不满足节点级断点续跑。若接checkpoint，还要把Tool副作用幂等、审批状态、State schema版本和thread/task映射一起设计，否则恢复可能重复执行写操作。

### 代码证据
- Fast `graph.py` L133；Deep构图 L927。
- runner L171-L182。
- `app/agents/hitl.py` 仅示例/直接测试，构图无引用。

### 面试官可能继续追问
1. 你会选什么checkpointer？
2. checkpoint能自动解决幂等吗？

### 追问简答
1. 既有PostgreSQL事实库，可优先Postgres saver，并以task_id映射thread_id。
2. 不能；它恢复State，但外部Tool是否已成功仍需幂等键和不确定结果恢复协议。

### 我需要掌握的知识
LangGraph checkpointer、interrupt/resume、幂等副作用。

## Q24：为什么用 Multi-Agent 而不是 Single-Agent

### 难度
高级

### 面试问题
“一个强模型加全部工具不就够了吗，为什么拆多个Agent？”

### 面试官考察点
技术选型、成本收益。

### 推荐回答
单Agent的优势是上下文统一、调用少、实现简单，所以Fast本质上就保留了这种低成本路线。Deep拆Agent是为了解决复杂事故中的工具菜单膨胀、单一路径确认偏差和不同证据域评价标准不一致。每个Specialist只看自己的prompt和工具，返回压缩Evidence；统一RCA再跨域判断，比把所有日志、指标、SOP和基础设施结果塞入一个对话更可控。

代价是调用数、汇聚复杂度和跨Agent信息损失。历史E2E还显示Deep证据覆盖高但根因Top-1低，说明Multi-Agent不会天然更准，输入证据隔离和Reducer质量更重要。因此我的选择不是“多Agent高级”，而是Fast保留单循环、Deep按需要扩展证据面，让模式选择承担成本权衡。

### 代码证据
- Fast/Deep两套构图。
- Specialist限定工具与Evidence契约。
- `FINAL_METRICS_REPORT.md` Fast/Deep E2E反例。

### 面试官可能继续追问
1. 什么时候应该只用Single-Agent？
2. 多Agent最大的系统风险是什么？

### 追问简答
1. 故障域单一、工具少、延迟敏感、证据可顺序获取时。
2. 错误证据被并行放大、成本失控和汇聚后的虚假共识。

### 我需要掌握的知识
单/多Agent适用边界、上下文隔离、证据污染。

## Q25：为什么用 LangGraph 而不是手写状态机

### 难度
高级

### 面试问题
“这张图并不复杂，手写while循环会不会更简单？”

### 面试官考察点
架构权衡、避免框架崇拜。

### 推荐回答
Fast最小版本确实可以用while循环，优点是依赖少、调试栈直接、每个状态转移完全可控。但项目同时需要条件路由、并行join、字段Reducer、astream节点事件，以及未来checkpoint/HITL的演进空间，LangGraph把这些变成统一执行模型，减少自建调度器工作。

选择的代价是学习成本、框架版本兼容和并发State语义隐蔽，例如Annotated reducer配置错误会静默丢数据。如果流程长期固定、团队不熟图框架、也不需要并行和恢复，手写状态机会更合适。本项目的Deep fan-out和Fast三向循环使LangGraph收益超过了引入成本，但我不会把手写方案描述成不可行。

### 代码证据
- Fast conditional edges L111-L130。
- Deep fan-out/fan-in L917-L925。
- runner统一 `graph.astream()` L182。

### 面试官可能继续追问
1. 当前没checkpoint，LangGraph收益是否不足？
2. 如何降低框架锁定？

### 追问简答
1. 仍有显式图、并行join、Reducer和streaming收益；checkpoint是未利用潜力。
2. 保持节点为普通State→patch函数，业务逻辑放service/runtime，避免到处依赖Graph API。

### 我需要掌握的知识
build vs buy、框架锁定、节点纯函数设计。

## Q26：为什么不用 CrewAI、AutoGen 等多 Agent 框架

### 难度
高级

### 面试问题
“既然是Multi-Agent，为什么不用AutoGen或CrewAI？”

### 面试官考察点
框架选型、系统边界。

### 推荐回答
这个项目更需要确定性的业务状态机，而不是开放式Agent对话。OnCall场景要求工具权限、证据引用、最大步骤、失败出口和最终责任节点都可追踪。LangGraph允许我明确写出谁能到哪里、哪些字段能并发累加；Deep Agent不互聊，只通过Evidence黑板交换，避免对话回环和Token失控。

AutoGen/CrewAI在角色协作、快速原型和自治讨论上更方便，如果目标是研究型任务或人类可观察的团队对话，它们很合适；但本项目要把Redis任务、Postgres事实、审批和SSE事件接进可预测控制流，显式Graph更自然。代价是角色通信和动态团队能力需要自己建模。

### 代码证据
- `state_deep.py` L11-L16：隔离subagent、无inbox协议。
- Deep静态边与Evidence reducer。
- Runtime权限/审计模块。

### 面试官可能继续追问
1. Deep Agent算不算自治Agent？
2. 未来会引入Agent互聊吗？

### 追问简答
1. 是限定工具和内部循环的调查型Agent，但不具备长期自治团队协议。
2. 只在有明确收益时引入结构化challenge/review节点，不做无限自由群聊。

### 我需要掌握的知识
编排型 vs 对话型多Agent、确定性与自治度谱系。

## Q27：Fast/Deep 如何评估，为什么模式名不代表质量

### 难度
高级

### 面试问题
“怎么证明Deep比Fast好？你评估过吗？”

### 面试官考察点
指标意识、反事实思维。

### 推荐回答
不能用模式名证明质量，要在同一事故集上成对比较。项目的Diagnosis E2E对10条合成事故分别跑Fast和Deep，评根因Top-1、Evidence group coverage、citation validity/correctness、延迟和Token。历史结果里Deep证据覆盖和引用正确率高，但Top-1是0；Fast Top-1为50%，同时延迟和Token更高。

这不是要隐藏的失败，而是重要发现：Deep读取了真实本机资源，和合成事故Gold不一致，导致“引用真实但与目标事故无关”。因此下一步要做incident_id绑定的fixture/replay数据源隔离，再用相同10×2复跑。面试中我会说“双模式架构和评测已建立”，不会说“Deep已被证明更准确”。

### 代码证据
- `benchmark/run_diagnosis_benchmark.py::score_state()` L171、`summarize()` L282。
- `benchmark/diagnosis_e2e_10.jsonl`。
- `benchmark/reports/diagnosis_e2e_rescored_20260728-091959Z.json`。

### 面试官可能继续追问
1. 为什么Fast反而更慢？
2. 如何定义模式选择收益？

### 追问简答
1. Fast有多轮Executor/Replanner和上下文累积；Deep是固定并行DAG，名称不等于速度。
2. 以质量约束下的P95、Token、成功率和fallback率做多目标比较。

### 我需要掌握的知识
成对实验、质量/成本多指标、环境证据污染。

## Q28：当前双模式设计的最大瓶颈是什么

### 难度
高级

### 面试问题
“如果让你继续做一个月，双图最先改什么？”

### 面试官考察点
架构诊断、优先级判断。

### 推荐回答
第一优先不是再加Agent，而是补证据契约：把Worker已有的task/group/incident IDs传入Deep State，让Specialist只能读取该事故绑定的数据源；同时把Deep `evidence`事件逐条持久化并把内存ev_i映射成真实Evidence ID。否则图结构再复杂，也可能用错现场数据，且报告引用无法完整审计。

第二是统一Deep权限与timeout，让四个Agent都走PermissionDecision，并给每个节点单独超时转error Evidence。第三是压缩Fast past_steps和Tool结果、加强动作级重复检测，降低历史评测暴露的Token/延迟。完成这些后再做Deep证据不足条件边和checkpoint，而不是先上更自治的群聊。

### 代码证据
- runner graph_input L171-L177缺IDs。
- audit L138-L185缺 `event_type==evidence`。
- Specialist `decisions=None`。
- Fast历史E2E Token/延迟报告。

### 面试官可能继续追问
1. 为什么Evidence ID比更强模型优先？
2. 这些改动怎么验收？

### 追问简答
1. 数据关联错时更强模型只会更自信地错；可追溯输入是RCA前提。
2. 用隔离fixture、DB反查引用、权限拒绝/审批、Agent timeout和10×2回归联合验收。

### 我需要掌握的知识
数据契约优先级、可观测性、集成测试设计。

## Q29：简历中的“双图、并行、有界”是否夸大

### 难度
高级

### 面试问题
“你的描述听起来很完整，哪些是真实现，哪些是包装？”

### 面试官考察点
真实性、沟通能力、边界意识。

### 推荐回答
双图是真实现：两个独立StateGraph都由统一runner进入主链；Fast的Router/Planner/Executor/Replanner循环、Deep的四路fan-out/fan-in也是真实代码。有界机制也真实存在，包括Fast最大步骤、三步重复、recursion limit、Tool max_iters和Worker timeout。

包装主要是把两图能力合并成一句招聘语言。精确拆开后，动态Replan只在Fast；Deep按规则动态派遣但无Replan；并行不代表每次四个LLM都执行；异常降级主要是节点级，不是Deep运行中自动切Fast；主链无checkpoint。我的原则是简历讲架构价值，面试用这些限定证明我既做过实现，也理解未完成边界。

### 代码证据
- 两张构图文件和runner。
- Harness有界逻辑。
- 两图compile无checkpointer。
- Deep dispatch guard与Specialist except。

### 面试官可能继续追问
1. 为什么不直接把所有限制写简历？
2. 哪个词最容易被质疑？

### 追问简答
1. 简历需要压缩信息；限制应在面试追问和项目文档中准确展开，不能捏造不存在的机制。
2. “异常降级保证任务有界”容易混淆节点降级、模式回落和硬超时，回答时要拆层。

### 我需要掌握的知识
招聘表达与工程证据的边界、原子声明拆解。

## Q30：如何回应“这只是多个函数，不是真正 Multi-Agent”

### 难度
高级

### 面试问题
“你这不就是几个函数并行调用吗，凭什么叫Multi-Agent？”

### 面试官考察点
高级质疑、概念严谨性、源码证明。

### 推荐回答
如果把Multi-Agent定义成会长期互聊、拥有身份和邮箱的自治团队，那这个项目不是那一类，我不会硬辩。它属于“确定性编排的调查型Multi-Agent”：四个Specialist各自拥有独立system prompt、限定工具集合、内部LLM↔Tool循环和Evidence输出契约；它们在LangGraph不同节点并行运行，私有消息隔离，只通过共享Evidence黑板汇聚，再由独立RCA角色裁决。

普通函数也能实现Agent节点，但判断标准不是文件/类的数量，而是是否存在模型决策、工具使用、独立上下文、角色目标、可迭代执行和结构化协作协议。源码中的 `run_parallel_agent()`提供每个Specialist内部Agent循环，Deep图提供跨Agent调度和Reducer。更准确的称呼是“orchestrated multi-agent diagnosis”，而不是“完全自治Agent社会”。这种限定反而能让架构主张更可信。

### 代码证据
- 四个 `run_*_agent()`：独立prompt、tools、max_iters、Evidence。
- `tool_runner.py::run_parallel_agent()` L203。
- Deep `SPECIALISTS`、fan-out/fan-in、Reducer/RCA。
- `state_deep.py` L11-L16：隔离上下文设计。

### 面试官可能继续追问
1. Router/Planner也算Agent吗？
2. 怎样升级为更自治的Multi-Agent？

### 追问简答
1. 它们是LLM职责节点；最强的Agent证据是带内部工具循环的Executor和四个Specialist，不必把每个函数都叫Agent。
2. 可加入结构化任务委派、证据challenge/review和checkpoint，但仍要保留预算、权限和终止协议。

### 我需要掌握的知识
Agent定义谱系、编排型Multi-Agent、工具循环、上下文隔离与协作协议。

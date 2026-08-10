# 渐进式 Skill 路由：30 道面试题

## Q01：Skill、Tool、Playbook、Agent有什么区别
### 难度
基础
### 面试问题
“这四个概念在你项目里如何落地？”
### 面试官考察点
概念、源码理解。
### 推荐回答
Agent是带模型决策和工具循环的执行主体；Tool是真正访问知识库、指标或系统的原语；Skill是可发现的能力包，包含路由metadata、完整Playbook、allowed_tools和risk；Playbook是选中Skill后给Planner的排查方法。Router选Skill，不直接执行Tool；Planner读Playbook产计划；Executor再绑定允许的Tool。这样把“选方法、定步骤、执行动作”分层，避免一个Prompt承担所有职责。
### 代码证据
`skills/models.py::Skill` L36；`loader.py` L103；`planner.py` L27；`executor.py` L90。
### 面试官可能继续追问
1. Skill是代码还是文档？ 2. Planner算Agent吗？
### 追问简答
1. 两者结合：SKILL.md声明策略，Python模型/Registry/Runtime执行契约。 2. 是LLM职责节点，但主要做一次结构化规划，不是工具循环Agent。
### 我需要掌握的知识
Skill schema、节点职责、Tool调用链。

## Q02：为什么需要渐进式披露
### 难度
基础
### 面试问题
“为什么不把所有Skill和Tool一次给模型？”
### 面试官考察点
Prompt工程、安全、成本。
### 推荐回答
全部披露会扩大Token、稀释注意力、增加误选和越权面。项目分三段：Router只见短metadata card；选中后Planner才见完整Playbook；Executor只绑定Skill和权限策略允许的Tool。收益是上下文更小、每层目标单一、安全边界可执行。代价是前置路由错误会限制后续能力，所以保留generic fallback和有证据的Skill reroute。
### 代码证据
`Skill.to_router_card()` L102；Router L125；Planner L50；ToolFilter L73。
### 面试官可能继续追问
1. 真的按需读磁盘吗？ 2. Token节省测过吗？
### 追问简答
1. Registry会解析全文，按需的是进入Prompt的内容。 2. 当前主要是设计收益，未定位专门Token消融，不能编数字。
### 我需要掌握的知识
Progressive disclosure、上下文预算、误路由代价。

## Q03：元数据路由是什么
### 难度
基础
### 面试问题
“Router具体看哪些元数据？”
### 面试官考察点
数据建模、真实性。
### 推荐回答
每个Skill从frontmatter得到name、display_name、description、triggers、category等；`to_router_card()`主要输出名称、适用场景和触发词。Router拿这些卡片、用户输入和可选Wiki经验做结构化选择，不读取完整Playbook。triggers当前是提示信号，不做硬匹配，最终由LLM给 `SkillChoice`。
### 代码证据
`skills/models.py` L43-L75、L102-L109；`skill_router.py` L110-L138。
### 面试官可能继续追问
1. 为什么triggers不硬匹配？ 2. metadata错了怎么办？
### 追问简答
1. 运维表达多样，硬匹配适合fallback而非主路由。 2. Loader校验结构；语义质量需benchmark和review保证。
### 我需要掌握的知识
Router card、frontmatter、软/硬路由。

## Q04：路由输入与输出结构
### 难度
基础
### 面试问题
“Router的输入输出字段分别是什么？”
### 面试官考察点
源码细节。
### 推荐回答
输入核心是 `state.input`、Registry menu和按signature/query召回的Wiki lessons；模型档位来自Harness。输出是Pydantic `SkillChoice`：`is_oncall`决定是否OOS，`skill_name`给Skill，`confidence`用于观测，`reason`用于可解释。节点最终写 `selected_skill/skill_reason/transition_history`；OOS额外写空plan、response和iteration=0。
### 代码证据
`skill_router.py::SkillChoice` L22、`skill_router_node()` L93-L199。
### 面试官可能继续追问
1. confidence低会怎样？ 2. unknown skill怎样？
### 追问简答
1. 当前不触发阈值，仅记录，是改进点。 2. 代码验证Registry，回退generic并记录transition。
### 我需要掌握的知识
结构化输出、State patch、transition。

## Q05：Metadata和完整Playbook是什么关系
### 难度
基础
### 面试问题
“元数据选完以后，Playbook怎么进入执行？”
### 面试官考察点
完整流程。
### 推荐回答
Loader把同一SKILL.md分为YAML frontmatter和Markdown body，前者填Skill元数据与allowed_tools，后者保存在 `skill.playbook`。Router只消费由元数据生成的card；Planner根据 `selected_skill`从Registry取对象，再把完整playbook注入规划prompt。Playbook不直接执行命令，而是约束Planner生成可由Tool验证的步骤。
### 代码证据
`loader.py::_split_frontmatter()`、`load_skill_from_file()` L115-L149；`planner.py` L32-L54。
### 面试官可能继续追问
1. Skill不存在怎么办？ 2. Playbook内容冲突怎么办？
### 追问简答
1. `get_or_generic()`回generic，generic缺失则配置错误。 2. 当前单选Skill避免跨Playbook冲突；内部冲突依赖维护和评测。
### 我需要掌握的知识
加载器、Registry fallback、Prompt注入。

## Q06：OOS是什么，和低置信度有何区别
### 难度
基础
### 面试问题
“什么叫Out of Scope？低置信度是不是也拒绝？”
### 面试官考察点
分类边界、产品意识。
### 推荐回答
OOS表示不是OnCall/运维诊断，继续调用知识、监控或日志Tool没有价值也增加风险，所以Router直接生成说明并结束。低置信度表示仍可能是OnCall，只是具体Skill不确定，更适合generic或二阶段澄清。Schema同时建模 `is_oncall`与 `confidence`，但当前代码只硬执行is_oncall，confidence未设阈值。
### 代码证据
Router L22-L26、L149-L164；`graph.py::route_after_skill()` L81。
### 面试官可能继续追问
1. OOS误杀有何代价？ 2. fallback规则如何判？
### 追问简答
1. 真事故不进入诊断，通常比误选Skill更严重，应重点看OOS recall。 2. LLM失败时先查OOS/OnCall关键词，非OnCall结束，否则generic。
### 我需要掌握的知识
拒识分类、precision/recall、业务成本矩阵。

## Q07：OOS如何识别和判分
### 难度
中等
### 面试问题
“你的OOS不是看response非空就算吧？”
### 面试官考察点
评测真实性。
### 推荐回答
在线主路径由结构化 `SkillChoice.is_oncall=false`触发，并写 `ROUTER_OUT_OF_SCOPE` transition。Benchmark判分也优先检查这个结构化reason；普通response不能随便算OOS。LLM失败时只有规则fallback明确判为非OnCall才接受，否则记Skill或ERROR，避免异常样本从分母消失或被误算正确。
### 代码证据
`skill_router.py` L149-L164；`run_skill_router_benchmark.py::classify_router_outcome()` L42、`score_router_result()` L71。
### 面试官可能继续追问
1. 为什么transition比文本可靠？ 2. 5/5能说明什么？
### 追问简答
1. 文本可变且可能含“无法”，结构化reason是稳定协议。 2. 只证明该5条历史样本，样本太小不能外推100%。
### 我需要掌握的知识
结构化判分、异常计入分母、OOS样本量。

## Q08：为什么不一次性暴露全部Tool
### 难度
中等
### 面试问题
“LLM看到全部工具，再用Prompt要求别乱调不行吗？”
### 面试官考察点
安全工程、Tool calling。
### 推荐回答
Prompt是软约束，无法防Prompt injection、模型幻觉Tool名或错误参数。项目先按Skill和PermissionMode生成visible tools，deny根本不bind给模型；执行时Tool runner还检查名字是否在 `tools_by_name`，ask则进入Postgres审批。即使模型猜出一个危险Tool名，也只得到拒绝ToolMessage。写/通知/高风险工具必须显式allowlist，只读工具可按runtime策略补充。
### 代码证据
`tool_filter.py` L103-L169；`permissions.py` L127-L196；`tool_runner.py` L335-L418。
### 面试官可能继续追问
1. 两道检查是否重复？ 2. bypass能绕过allowlist吗？
### 追问简答
1. 防御纵深：前者缩小模型选择面，后者防伪造/状态漂移。 2. 只跳过后续mode/guardrail；非只读且不在Skill allowlist仍先被Layer0挡住。
### 我需要掌握的知识
Prompt约束与执行授权、防御纵深。

## Q09：Tool权限从哪里定义
### 难度
中等
### 面试问题
“权限策略散在哪些地方？谁是最终依据？”
### 面试官考察点
源码映射、策略分层。
### 推荐回答
Skill的 `allowed_tools`表达业务能力边界；`ToolMeta`描述read_only、destructive、notification、concurrency_safe等工具属性；`PermissionMode`表达会话策略；settings控制高危/通知guardrail；`evaluate_permission()`按Layer0 allowlist/只读豁免、Mode、静态guardrail、预留参数规则生成最终decision。Tool runner消费decision并执行或审批。
### 代码证据
`skills/models.py` L56；`tools/meta.py`；`runtime/permissions.py` L99；`tool_filter.py` L73。
### 面试官可能继续追问
1. 谁能修改PermissionMode？ 2. 未登记Tool怎样？
### 追问简答
1. 默认来自settings，可由state单会话覆盖，但调用入口需控制授权。 2. ToolMeta保守默认非只读，避免未知工具自动放行。
### 我需要掌握的知识
RBAC/ABAC类比、ToolMeta、会话策略。

## Q10：权限校验发生在模型前还是执行前
### 难度
中等
### 面试问题
“权限到底在哪个时点检查？”
### 面试官考察点
执行安全、调用链。
### 推荐回答
两个时点都有。Executor构建Agent时先调用ToolFilter，allow与ask才进入visible tools，deny不bind；Tool runner收到模型tool_calls后再检查工具是否真实存在于当前 `tools_by_name`，并根据decision执行、审批或拒绝。审批通过后才加入valid_calls，随后按并发安全属性分批调用。
### 代码证据
`executor.py::_get_executor()` L45-L87；ToolFilter L135；Tool runner L335-L487。
### 面试官可能继续追问
1. decision缓存安全吗？ 2. 参数变化会重算吗？
### 追问简答
1. Executor cache key含Skill、Tool列表、runner mode和PermissionMode。 2. 初筛时tool_input=None；参数级动态规则尚未完整二次计算，是现有限制。
### 我需要掌握的知识
TOCTOU、策略缓存、参数感知权限。

## Q11：模型伪造Tool名或非法参数怎么办
### 难度
中等
### 面试问题
“LLM调用一个不存在或不允许的Tool会怎样？”
### 面试官考察点
异常处理、安全。
### 推荐回答
Tool名先被normalize；不在当前tools_by_name就不执行，而是回填带tool_call_id的拒绝ToolMessage，让模型有机会改用合法方案。合法Tool但参数不符合schema时，BaseTool调用会抛验证/执行错误，`_safe_invoke_tool`把它转换为失败ToolMessage，避免并行batch整体崩溃。当前参数级“同一Tool某参数只读、某参数危险”的统一策略仍是TODO，不能夸大。
### 代码证据
`tool_runner.py::_normalize_tool_call()` L45、L335-L348、`_safe_invoke_tool()` L123。
### 面试官可能继续追问
1. 错误会导致无限重试吗？ 2. 错误参数会被记录吗？
### 追问简答
1. Tool Agent受max_iters，Fast还受max steps/repeat。 2. runtime事件/审计会记录调用状态，但敏感参数应脱敏。
### 我需要掌握的知识
Tool schema、错误转数据、迭代上限。

## Q12：四种PermissionMode如何取舍
### 难度
中等
### 面试问题
“read_only、normal、ask_destructive、bypass有什么区别？”
### 面试官考察点
权限模型、生产安全。
### 推荐回答
`read_only`拒绝所有有效非只读工具；`normal`通过Skill边界并应用高危/通知guardrail；`ask_destructive`把destructive、notification或高风险Tool变成ask，创建审批请求等待人工；`bypass`只用于开发，跳过mode和静态guardrail，但Layer0的非只读Skill边界仍在。生产环境不应推荐bypass。
### 代码证据
`permissions.py::PermissionMode` L39、`evaluate_permission()` L129-L195；`approvals.py`。
### 面试官可能继续追问
1. 审批等待会占并发槽吗？ 2. Postgres挂了怎么办？
### 追问简答
1. Tool runner会pause当前分布式槽，决定后resume，减少队头阻塞。 2. 审批异常降级为拒绝，不应默许执行。
### 我需要掌握的知识
fail-closed、人工审批、并发槽暂停。

## Q13：重路由的完整触发与状态变化
### 难度
进阶
### 面试问题
“当前Skill方向错了，系统怎么换Skill？”
### 面试官考察点
完整数据流、动态编排。
### 推荐回答
Executor先产生past_steps；Replanner看到当前/候选/tried Skills和证据，Act可提议 `should_reroute,new_skill,reroute_reason`。代码再检查至少默认2步、次数小于默认1、目标不等于当前、不在tried、不为空且Registry真实存在。通过后覆盖selected_skill，旧Skill+原因追加tried_skills，清空旧plan，reroute_count+1，pending_reroute=true；条件边回Planner，加载新Playbook重新规划，并清pending标志。
### 代码证据
`replanner.py::_validate_reroute()` L160-L204、L301-L332；`graph.py::should_end()` L56。
### 面试官可能继续追问
1. 已完成步骤丢吗？ 2. 会重新经过Router吗？
### 追问简答
1. 不丢，past_steps是add reducer。 2. 不会；Replanner直接选新Skill并回Planner。
### 我需要掌握的知识
Act schema、reroute State patch、条件边。

## Q14：证据门槛到底是什么
### 难度
进阶
### 面试问题
“你写证据门槛，是判断Evidence质量吗？”
### 面试官考察点
真实性、指标定义。
### 推荐回答
当前实现是保守的数量门槛：`len(past_steps)`至少达到 `agent_reroute_min_past_steps`，默认2，才允许接受LLM的reroute提议。设计意图是防止刚选Skill、还没调用工具就凭感觉横跳；reroute reason还要求引用具体结果。

但它不是严格Evidence quality gate：没有验证Tool是否成功、来源是否独立、是否出现关键反证。因此简历可以写“证据门槛”，面试要解释当前代理指标是已执行步骤数；改进为成功Tool结果、关键字段和反证规则组成的sufficiency score。
### 代码证据
config L377-L382；`_validate_reroute()` L180-L185；Harness quota hint L622。
### 面试官可能继续追问
1. 两个失败步骤算证据吗？ 2. 如何升级？
### 追问简答
1. 当前数量上算，这是缺口。 2. 给past_step结构化status/source/observations，按Skill定义required evidence。
### 我需要掌握的知识
代理指标、Evidence sufficiency、反证。

## Q15：路由历史如何保存和防止回环
### 难度
进阶
### 面试问题
“Skill A切B后会不会再切回A？”
### 面试官考察点
状态一致性、有界执行。
### 推荐回答
`tried_skills`是add reducer列表，每次合法reroute把旧Skill和失败原因追加进去。构造候选菜单时排除当前Skill和所有tried Skill；验证阶段又拒绝new_skill等于当前、位于tried或Registry不存在。再配合reroute_count默认1，当前配置下最多切一次，不会A↔B反复横跳。`transition_history`另外记录节点出口，供SSE和审计观察，不等同失败记忆。
### 代码证据
`state.py` L68-L72；`replanner.py::_build_skill_context()` L119、validator L187-L202。
### 面试官可能继续追问
1. tried原因用于模型吗？ 2. 新Planner会清iteration吗？
### 追问简答
1. 会注入Replanner上下文，避免重复方向。 2. Planner当前重置iteration=0，这会让reroute后获得新步数预算，应结合全局预算审视。
### 我需要掌握的知识
failure memory、候选排除、局部/全局预算。

## Q16：如何检测误路由
### 难度
进阶
### 面试问题
“系统怎么知道第一次Skill选错了？”
### 面试官考察点
数据流、异常纠偏。
### 推荐回答
当前不是独立classifier监控，而由执行证据驱动：Replanner看到关键指标均正常、结果指向另一故障域或当前关键Tool全部不可用时，可提议reroute。代码只负责验证提议是否合法，误路由语义判断仍由LLM完成。离线则用Router数据集严格比对expected Skill和场景混淆。线上可进一步用reroute率、generic率、无效Tool率和人工改判建立反馈。
### 代码证据
`state.py::Act.should_reroute` L127；Harness reroute prompt；benchmark runner。
### 面试官可能继续追问
1. reroute等于误路由吗？ 2. 如何减少LLM主观性？
### 追问简答
1. 不完全，可能是事故域演化；需结合reason。 2. 为Skill定义required/contradictory evidence并代码化。
### 我需要掌握的知识
在线反馈、离线Gold、误路由代理指标。

## Q17：Skill加载失败怎么办
### 难度
进阶
### 面试问题
“SKILL.md YAML坏了会拖垮启动吗？”
### 面试官考察点
配置可靠性。
### 推荐回答
Loader把解析/字段错误包装成 `SkillLoadError`；Registry扫描时捕获并跳过该文件、记录error，其他Skill继续加载。平台不匹配和disabled也会跳过。Router遇到Registry空或仅generic有单独分支；指定Skill找不到时Planner用 `get_or_generic()`。若generic本身缺失，视为配置规约错误并抛出，而不是静默运行无边界系统。
### 代码证据
`loader.py` L103-L155；`registry.py` L130-L174；Router L98-L108。
### 面试官可能继续追问
1. 跳过会不会静默降能力？ 2. 如何生产化？
### 追问简答
1. 会，所以readiness应暴露Skill加载错误/数量。 2. CI预校验全部SKILL.md，关键Skill失败阻止发布。
### 我需要掌握的知识
配置校验、fail-open/fail-closed、readiness。

## Q18：Playbook内容冲突怎么办
### 难度
进阶
### 面试问题
“Playbook内部或外部Skill冲突怎么解决？”
### 面试官考察点
知识治理。
### 推荐回答
运行时一次只选一个Skill，因此不会把多个完整Playbook同时混入Planner；外部同名Skill当前后扫描者覆盖前者并记录warning。Playbook内部语义冲突没有自动裁决，属于内容治理问题，Planner只能基于Prompt生成计划。生产化应给Skill加版本/owner/schema、重名显式优先级或拒绝、静态lint和场景回归，并让关键操作仍受Tool权限而非Playbook文字决定。
### 代码证据
`registry.py` L154-L158；Planner L32-L54；Tool权限链。
### 面试官可能继续追问
1. 为什么允许覆盖？ 2. 冲突会造成越权吗？
### 追问简答
1. 便于本地扩展，但生产应显式。 2. 不应；执行权限独立于Playbook文本，最多导致错误计划。
### 我需要掌握的知识
策略内容治理、版本化、权限与建议分离。

## Q19：越权调用如何处理
### 难度
进阶
### 面试问题
“Prompt injection让模型重启容器，会发生什么？”
### 面试官考察点
安全、异常处理。
### 推荐回答
首先危险Tool若不在Skill allowlist或被guardrail deny，不会bind给模型；模型即使伪造名字，Tool runner也因不在tools_by_name拒绝。若配置为ask_destructive且该Tool合法可见，会创建审批请求，未批准、超时或审批存储异常都不执行。只有明确allow/approved才进valid_calls，而且不安全Tool串行。当前Deep没有统一decision链，所以面试要限定这套闭环主要证明Fast。
### 代码证据
Permission L129-L195；Tool runner L335-L487；ApprovalRepository。
### 面试官可能继续追问
1. Prompt能改PermissionMode吗？ 2. 审批通过后参数被替换怎么办？
### 追问简答
1. 模型文本不能直接改State授权；入口必须保护mode覆盖。 2. 审批记录应绑定规范化参数hash，当前需加强TOCTOU校验。
### 我需要掌握的知识
Prompt injection、TOCTOU、审批绑定。

## Q20：Tool参数非法怎么办
### 难度
进阶
### 面试问题
“Tool名合法但参数越界呢？”
### 面试官考察点
参数验证、安全边界。
### 推荐回答
Tool由LangChain schema约束，调用时验证失败会被 `_safe_invoke_tool`转为失败ToolMessage，不让并行批次整体抛出；模型可在有限轮数内纠正。权限层支持 `effective_read_only(tool_input)`概念，但ToolFilter初次评估通常传None，统一Layer3参数规则仍是TODO。因此当前能处理schema非法，不能宣称已覆盖所有语义危险参数；高风险Tool仍需专属校验和审批参数绑定。
### 代码证据
`permissions.py` L127-L189；`tool_runner.py::_safe_invoke_tool()`。
### 面试官可能继续追问
1. schema合法就安全吗？ 2. 怎么补？
### 追问简答
1. 不，路径逃逸、命令注入等是语义规则。 2. Tool内校验+中央参数policy+审批hash三层。
### 我需要掌握的知识
语法/语义校验、输入感知权限。

## Q21：Router LLM失败如何降级
### 难度
进阶
### 面试问题
“Router模型超时会不会整个诊断失败？”
### 面试官考察点
可靠性。
### 推荐回答
不会直接崩图。catch后 `_build_router_fallback_result()`用关键词集合判断：明显非OnCall则生成OOS response结束；看起来像故障则选generic_oncall继续，并写 `ROUTER_LLM_FAILED` transition。未知Skill也回generic。这个策略可用性优先，但关键词会有误判，所以benchmark单独统计llm_failure/fallback，不能把fallback命中混为模型准确率。
### 代码证据
`skill_router.py` L29-L90、L132-L147。
### 面试官可能继续追问
1. 为什么不重试很多次？ 2. generic有什么风险？
### 追问简答
1. Router在入口，长重试放大排队；当前有限重试后确定性降级。 2. 工具/计划更宽泛，质量低但权限仍受控。
### 我需要掌握的知识
规则fallback、可用性/准确性权衡。

## Q22：为什么不用单纯意图分类器
### 难度
进阶
### 面试问题
“训练一个分类器不是更快更稳吗？”
### 面试官考察点
选型权衡。
### 推荐回答
固定标签、海量标注和低延迟场景下分类器很好；本项目Skill数量少但可由SKILL.md/外部目录动态扩展，metadata card天然支持zero/few-shot路由，还需给reason和OOS。LLM减少重新训练成本。代价是波动、成本和可校准性差，所以配规则fallback、generic、benchmark与reroute。规模扩大后可采用embedding/小分类器粗召回Top-N，再由LLM结构化决策。
### 代码证据
Registry动态扫描；Router结构化choice；外部Skill配置。
### 面试官可能继续追问
1. 何时切分类器？ 2. 如何做层级路由？
### 追问简答
1. 标签稳定、样本足、QPS/成本成为瓶颈时。 2. 先域分类，再在域内Skill选择，并保留OOS头。
### 我需要掌握的知识
监督分类、动态标签、级联路由。

## Q23：为什么不用向量检索直接选Skill
### 难度
进阶
### 面试问题
“description做embedding nearest neighbor不就行了？”
### 面试官考察点
检索与分类边界。
### 推荐回答
向量检索适合做候选召回，但最近邻不天然支持OOS、规则边界、reason和多条件判断，description短且多个运维域语义相近时也易混。当前Skill少，直接把metadata cards给Router简单。Skill增多后可以向量Top-N缩小menu，再让结构化LLM做OOS和最终判别；必须设置相似度拒识与fallback，不能把nearest当授权决定。
### 代码证据
`to_router_menu()`当前全量cards；`SkillChoice`含is_oncall/confidence/reason。
### 面试官可能继续追问
1. 向量召回会漏正确Skill怎么办？ 2. Tool权限能依赖相似度吗？
### 追问简答
1. Top-N召回率单独评测，并保留generic/全量回退。 2. 不能；最终selected Skill仍须显式、可审计。
### 我需要掌握的知识
候选召回 vs 最终决策、拒识阈值。

## Q24：为什么不用让LLM看到全部工具
### 难度
高级
### 面试问题
“强模型能自己选工具，过滤是不是限制能力？”
### 面试官考察点
安全权衡。
### 推荐回答
能力上会限制，但这是有意的最小权限。全部工具会增加schema Token、选择混淆和攻击面；更关键的是模型判断不是授权。项目允许只读工具按runtime策略补充，保持调查能力；写/通知/高风险必须显式Skill声明并通过mode/guardrail/审批。需要跨域时通过reroute换Skill，而不是一次给全权限。若Tool数极多，还支持Lazy MCP元工具，但默认关闭以减少额外轮次。
### 代码证据
ToolFilter L103-L145；config `mcp_lazy_tools_enabled` L393。
### 面试官可能继续追问
1. 自动补只读会不会泄密？ 2. 最小权限和召回冲突怎么办？
### 追问简答
1. 只读不等于可公开，生产还需数据授权/租户隔离。 2. 按数据域授权而非只按副作用分类。
### 我需要掌握的知识
最小权限、数据读取风险、Lazy Tools。

## Q25：渐进路由的主要成本是什么
### 难度
高级
### 面试问题
“这套分层比一个Agent复杂在哪里？”
### 面试官考察点
架构权衡。
### 推荐回答
增加一次Router模型调用、Registry/Skill内容治理、跨层契约和误路由恢复；缓存还要随Skill/PermissionMode变化失效。错误可能级联：metadata写差→选错Skill→Playbook和Tool受限。收益是prompt更小、策略可复用、权限可执行、错误可观察。适合Tool/任务域明显分化的系统；只有三五个纯只读Tool时，一个受控Agent可能更划算。
### 代码证据
Router→Planner→Executor三层；Executor cache key；reroute机制。
### 面试官可能继续追问
1. 如何量化收益？ 2. 如何避免配置漂移？
### 追问简答
1. 比较准确率、Tool越权率、Token、P95、reroute/fallback。 2. Skill lint、契约测试、版本owner和发布门禁。
### 我需要掌握的知识
级联误差、配置治理、成本指标。

## Q26：40条Router/OOS数据集如何构建
### 难度
高级
### 面试问题
“40条是不是随便写的？”
### 面试官考察点
数据集设计、指标意识。
### 推荐回答
当前文件有40行，覆盖本机资源、网络、容器、Redis、MySQL、K8s、Kafka、Nginx、JVM、Prometheus、中英混杂、边界/OOS等，并带expected Skill、scenario和difficulty。历史报告显示35条Skill、5条OOS。它是面向项目Skill taxonomy的人工小型回归集，不是公开标准集；价值是稳定发现域间混淆，局限是规模小、OOS仅5、标注独立性与线上分布未充分证明。
### 代码证据
`benchmark/skill_router_eval.jsonl`；runner `load_eval_data()` L77；报告080603Z。
### 面试官可能继续追问
1. 如何防数据泄露？ 2. 如何划分训练测试？
### 追问简答
1. Router prompt不应包含expected，调Prompt后要保留冻结盲测集。 2. 当前非训练集；扩充后按场景/模板族分组切分，避免改写泄露。
### 我需要掌握的知识
taxonomy、边界样本、数据泄露。

## Q27：75%准确率够不够，错误分布是什么
### 难度
高级
### 面试问题
“75%看起来不高，为什么写简历？”
### 面试官考察点
指标解释、诚实度。
### 推荐回答
75%是30/40，证明建立了可复现评测并暴露真实问题，不是宣称生产可用。拆分后非OOS是25/35=71.4%，OOS 5/5；错误集中在Redis、K8s、Nginx和通用告警，而本机资源、网络等较好。对安全关键路由，75%不够直接自动化写操作，但系统还有generic、reroute和Tool权限把错误影响限制在计划/调查层。
### 代码证据
`skill_router_20260728-080603Z.json` per_scenario；`FINAL_METRICS_REPORT.md`。
### 面试官可能继续追问
1. accuracy是否合适？ 2. 为什么仍能上线demo？
### 追问简答
1. 应补macro-F1、OOS P/R、top-k、成本加权错误。 2. 只读诊断+权限保护+人工确认，定位是参考实现非生产SLA。
### 我需要掌握的知识
混淆矩阵、错误成本、分层防护。

## Q28：如何把Router提升到可靠水平
### 难度
高级
### 面试问题
“下一步具体怎么从75%提升？”
### 面试官考察点
系统改进、实验设计。
### 推荐回答
先按混淆矩阵处理，不盲调模型：补Redis/K8s/Nginx metadata和专用Skill，增加近域难例；把路由改为域粗召回→Top-N结构化判别；校准confidence，低置信度走generic/澄清；把Tool可用性和平台信息加入输入；保留冻结盲测集，按macro-F1、OOS recall、top-2 recall、P95/Token评估。最后用线上reroute/人工改判做主动学习，但要脱敏和人工审核。
### 代码证据
现有per_scenario报告、Skill metadata、confidence字段、external Skill扩展点。
### 面试官可能继续追问
1. 先换模型还是改数据？ 2. 如何避免过拟合40条？
### 追问简答
1. 先修taxonomy/metadata和难例，模型A/B后置。 2. 冻结测试、模板族隔离、扩充真实分布并报告置信区间。
### 我需要掌握的知识
错误驱动迭代、校准、级联分类。

## Q29：如何证明工具权限不是Prompt包装
### 难度
高级
### 面试问题
“你这权限是不是system prompt写一句‘不要调用危险工具’？”
### 面试官考察点
真实性验证、安全。
### 推荐回答
不是。第一层Skill `allowed_tools`与ToolMeta共同决定候选；第二层 `evaluate_permission()`输出allow/ask/deny；第三层deny不bind给LLM；第四层Tool runner按当前tools_by_name拒绝伪造名；ask会写Postgres审批并等待approved才执行。Prompt只是补充行为引导，不是授权依据。边界是参数级Layer3和Deep统一decision尚未完成，我会明确说。
### 代码证据
`tool_filter.py`、`permissions.py`、`tool_runner.py`、`approvals.py`与`approval_requests`表。
### 面试官可能继续追问
1. 有安全测试吗？ 2. read_only就绝对安全吗？
### 追问简答
1. 当前缺完整tests套件，需补伪造名/危险参数/审批超时集成测试。 2. 不，读取敏感数据仍需身份与数据授权。
### 我需要掌握的知识
执行授权证据、纵深防御、数据权限。

## Q30：如何准确评价这条简历的真实性和边界
### 难度
高级
### 面试问题
“哪些是完整实现，哪些是包装后的概括？”
### 面试官考察点
真实性、系统边界。
### 推荐回答
完整主链包括metadata Router、选中后Playbook进入Planner、Fast ToolFilter/PermissionDecision/执行前拒绝、结构化OOS、past_steps门槛、reroute上限与历史防回环；40条30/40也有脚本和原始报告。包装点有两个：Playbook不是磁盘懒加载，而是prompt按需披露；“证据门槛”当前是步骤数量代理，不是质量评分。另一个边界是Deep使用限定工具但未统一Fast权限decision。这样表达既保留简历亮点，也不会在源码追问时失真。
### 代码证据
本目录 `01_code_mapping.md` 所列完整调用链；报告080603Z。
### 面试官可能继续追问
1. 你会改简历原句吗？ 2. 最危险的夸大是什么？
### 追问简答
1. 可保留原句，口头补“Prompt级按需披露、Fast主链权限”。 2. 声称全模式统一权限或75%达到生产可靠性。
### 我需要掌握的知识
原子声明审计、招聘表达、证据边界。

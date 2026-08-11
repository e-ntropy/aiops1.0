# AIOps 重构 STAR 问题与结果日志

本文记录重构中实际发现的问题、决策和可复现实证，供后续简历和面试整理。它不是生产事故复盘；
项目是个人公开参考实现，所有指标必须注明数据集规模与运行边界。

## 记录规则

- Situation 写可复现的现象，不用“业界领先”等无法证明的词。
- Task 写约束和验收标准，而不只是“完成开发”。
- Action 写关键判断、失败尝试和安全取舍。
- Result 只引用已保存的测试、Benchmark 或代码事实；未完成项标记为待验证。
- 新问题按本模板追加，不覆盖历史结论。

## STAR-01：真实宿主机 CPU/内存污染合成事故

**Situation：** `diagnosis_e2e_10` 直接运行现场诊断 Agent。虚构 MySQL、Redis、Kafka 等事故
没有稳定目标数据源时，MetricAgent/InfraAgent 可能读取执行评测的 Windows 宿主机；历史报告中
本机内存约 87%–90%，结果被错误拼入事故报告。结构化 Evidence 只能证明“调用过工具”，不能证明
“观察的是正确对象”。

**Task：** 在不访问 LLM、网络、数据库、向量库和真实主机的前提下，验证 Fast → Evidence Gate →
Deep 主流程；所有 Observed Evidence 必须绑定事故夹具、ToolCall ID 和同一 Scope，并覆盖正常/边界、
正例/反例/未知、简单/复杂。

**Action：**

1. 为统一诊断适配器增加结构化 `evidence` 事件；Observed 状态只有携带受信标记和 ToolCall ID 才被接受。
2. 忽略事件传入的 Scope，强制绑定当前 WorkflowState Scope；畸形状态和非字典 content 关闭式降级为 Error。
3. 将 Deep 结构化 Evidence 并入统一 State，同时保留旧图 Reference 兼容路径。
4. 自生成 120 条脱敏事故夹具，并实现零外部依赖的 FixtureDiagnosisRunner。
5. 增加数据集 SHA-256、100% 隔离门槛与单元测试，禁止本机采集来源混入。

**Result：** 120/120 夹具在 phase、mode、正反例行为、evidence type、isolation 和 exact match 上均为
100%，`fixture --enforce` 通过。这个分数仅证明当前确定性流程契约，不代表真实 LLM 根因准确率。

**面试表述：** “E2E 评测让我发现 Evidence 可追溯仍不足以防止观测对象错误，因此我增加了
Scope/ToolCall/fixture 三元绑定，并用完全离线的正反例矩阵把宿主机污染变成发布门禁。”

## STAR-02：只读产品边界与容器重启越权

**Situation：** 新增 Skill 资产测试时，7 个内置 Skill 的只读断言失败。根因是历史
`container_diagnosis` 把 `docker_restart` 放在 allowed_tools 中，虽然工具另有环境开关，但 Skill 层
已经向诊断 Agent 暴露了写操作意图，与“只读诊断/只读优化”定位冲突。

**Task：** 保证内置诊断 Skill 的默认候选工具全部已登记且只读，同时仍能向人工提供处置建议。

**Action：** 从容器 Skill 白名单删除 `docker_restart`，风险等级调整为 low；Playbook 改为只生成重启
风险、前置检查和回滚建议，不执行动作；增加注册表测试遍历全部内置 Skill 与 ToolMeta。

**Result：** 7 个 Skill 均成功加载，默认 allowed_tools 全部通过 ToolMeta 只读校验。Docker MCP 的重启
实现仍保留在受控边界中，但不再由只读诊断 Skill 暴露。

## STAR-03：复杂中间件场景被 generic_oncall 吸收

**Situation：** 原公开版只有主机、网络、容器和 generic 四类 Skill。MySQL 锁、Redis 连接池、JVM
Full GC、Nginx upstream 和 Kafka lag 等复杂样本缺少专科 Playbook，Router 容易误选相近 Skill 或把
所有问题交给通用兜底。

**Task：** 在不虚构专用数据库管理工具、不扩大写权限的前提下，提高故障域覆盖和排查步骤一致性。

**Action：** 新增 database_middleware、application_runtime、message_queue 三个 Skill，复用 RAG、
Prometheus、HTTP/端口和只读 Docker 工具；同步新增 3 份知识库 Runbook，明确趋势要求、反证、数据源
失败和“Reference 不是 Observed Evidence”的边界，并加入 Adaptive Diagnosis 候选 Skill。

**Result：** 内置 Skill 从 4 个扩展为 7 个，覆盖 120 条事故夹具中的主机、网络、数据库/缓存、K8s、
Kafka、JVM 和 Nginx 场景。K8s 当前仍无专用 Tool/Skill，作为后续缺口保留，不宣称已完整支持。

## STAR-04：Query/Scope/生命周期缺少统一离线回归

**Situation：** 不同 API 和后台任务各自维护部分流程，Intent 冲突、Scope 未确认、越权请求、
恢复验证和 Memory 晋升难以用同一套契约验证。

**Task：** 建立无 Provider 依赖的发布前回归，覆盖 Query 深度理解、Capability、Scope、只读安全、
人工确认、恢复、关闭和 Memory。

**Action：** 构建 240 条 Query 与 120 条 lifecycle JSONL，加入对抗输入、模糊目标、高风险请求、采集器
失败和脱敏拒绝；固定数据集指纹与阈值，通过 `workflow --enforce` 阻断回归。

**Result：** 当前版本两套数据共 360 条，Intent/Capability/Scope/Safety/Lifecycle/Exact Match 在该数据集
上均为 100%。不得扩写为“线上准确率 100%”。

## STAR-05：后台 Worker 可能诊断错误主机

**Situation：** 本机/远程现场查询若被普通 Redis Worker 异步消费，Worker 可能读取自己所在主机，
而不是用户指定目标；队列成功不等于观测对象正确。

**Task：** 在没有 target-affine worker 路由和远程 Scope 凭据绑定前，阻止错误主机的后台诊断。

**Action：** 将现场 Scope 能力的后台资格设为关闭式拒绝，只有无现场依赖的任务可安全排队；把
target-affine worker 作为显式后续架构需求。

**Result：** Workflow 安全评测验证本机/远程现场任务不能被普通后台执行。当前取舍牺牲了这类任务的
后台吞吐，但避免生成错误现场事实。

## 尚未解决、不得包装为成果

- 120 条夹具来自 16 个人工种子家族的确定性变体，仍未覆盖真实事故分布和跨组织表达差异。
- Fixture Benchmark 不运行 LLM，不能衡量真实根因推理、Token、延迟或 Provider 抖动。
- K8s、数据库直连、日志平台和 Trace 仍缺统一的 Scope-aware Evidence Provider。
- 真实事故样本必须经脱敏、Gold 审核和事故家族切分后才能进入正式 Benchmark，不能自动晋升。
- 项目尚无 CI Workflow 和完整 tests/ 覆盖，静态检查与本地测试不能证明生产就绪。

## STAR-06：客户端 State 回传导致并发覆盖与越权输入

**Situation：** 工作流接口由客户端回传完整 `WorkflowState`。浏览器多标签、重试或伪造字段会造成
Lost Update，服务重启后也无法从权威事实恢复；事故关闭、Memory 写入和人工决定彼此不是同一事务。

**Task：** 将状态所有权收回服务端，保证每次转换可恢复、可审计，并让并发请求明确冲突而不是静默覆盖。

**Action：** 新增 Postgres `workflow_runs/workflow_events/human_decisions`，客户端后续只传
`run_id + expected_revision`；更新使用 CAS，执行前领取带过期时间的 Lease；事故关闭在一个事务内
保存状态、事件、人工决定、Verified Memory、成功经验、画像、隔离评测样本并关闭 Incident/Group。

**Result：** 新增单元测试覆盖服务端加载、禁止完整 State 提交、revision 409、并行 Lease 和原子关闭
写集；本轮全部 107 个本地确定性测试通过。

## STAR-07：跨 Session 诊断报告污染知识对话

**Situation：** 短期诊断报告使用全局 Redis Key，任意会话的 RAG 对话都可能读取上一位用户的机器
诊断报告，形成隐私和错误上下文污染。

**Task：** 保留“诊断后继续追问”的体验，同时确保报告只在原 Session 内可见。

**Action：** 将报告缓存 Key 改为 Session ID 的 SHA-256 派生值；无 Session 时不写入也不读取；RAG
与 Web Context 显式传递当前 Session。长期关联则只读取 Postgres Verified Knowledge。

**Result：** 回归测试验证两个 Session 的报告互不可见，缺失 Session 不会落入共享默认空间。

## STAR-08：Candidate 与运行时 Wiki 造成长期知识污染

**Situation：** 模型报告即使未人工确认、未验证恢复，也可能被写入文件 Wiki 并在后续诊断中召回；
错误结论会在多次使用后放大。

**Task：** 让长期知识具备明确来源、状态、作用域、过期与替代关系，候选内容不得参与可信召回。

**Action：** 默认关闭诊断图的文件 Wiki 写入和召回；Postgres 将 Session、Candidate、Verified、Profile、
Success/Failure Experience 分表/分层管理；只召回 verified、未过期、未 supersede 且服务作用域匹配的
长期记忆，创建 Workflow 时把到期记录归档为 expired。

**Result：** 单元测试从 SQL 契约和图输入两层验证 Candidate 被拒绝、Verified Memory 才能成为
Specialist 的 Reference Evidence；文件 Wiki 仅保留显式兼容开关，默认关闭。

## STAR-09：统一诊断升级后评测 Runner 接口漂移

**Situation：** 初步 Evidence 开始传入 Specialist 阶段后，生产 Runner 新增 `initial_evidence` 参数，
离线 Fixture Runner 没有同步，Release Gate 直接因 `unexpected keyword argument` 失败。

**Task：** 修复评测基础设施，同时证明专业阶段确实继承了可用的初步证据，而不是仅让函数签名兼容。

**Action：** 更新 Fixture Runner 契约并记录 seed 数量；新增检查：存在 observed/reference 初步证据且
启动 Specialist 时 seed 必须非空；全数据源 unavailable 的边界样本则允许 seed 为空。

**Result：** Diagnosis Fixture 的 Phase、Mode、Fault、Evidence、Isolation、Exact Match 恢复为
120/120，`fixture --enforce` 通过；该问题成为“接口演进必须同步评测替身”的面试案例。

## STAR-11：小样本与整百分比缺乏说服力

**Situation：** 32 条 Query、9 条生命周期和 16 条诊断夹具只适合冒烟回归；Memory 只有“门禁通过”
而没有同一批样本上的前后对照，面试中无法回答提升来自策略还是数据差异。

**Task：** 在不调用付费 Provider、不读取真实宿主机的前提下，将评测规模扩展约 10 倍，同时保留边界、
复杂、正反例与 Family 分组，并用真实运行结果量化 Memory 治理收益。

**Action：** 建立确定性生成器和数据质量检查，将 Query/Lifecycle/Diagnosis 扩为 240/120/120，并新增
240 条 Memory 与 120 条 Tool Safety；固定 ID、Family ID、SHA-256 与门槛。Memory 对同一批 Gold
分别运行 no-memory、按置信度平铺和分层治理，使用 Wilson 区间及 5,000 次固定种子 paired bootstrap。

**Result：** 版本化资产由 207 条扩展至 990 条；扩容的 840 条中边界/复杂用例占 65.00%/59.05%。
分层 Memory 将记录级判定准确率由 67.34% 提升至 98.81%，提升 31.47pp（95% CI：29.74–33.15pp），
Recall 为 95.83%；保留服务别名边界，没有为了整分修改 Gold。平铺策略仅作为离线消融基线。

## STAR-12：扩容暴露 Query 领域边界与错误 Gold

**Situation：** 240 条 Query 首跑 Exact Match 仅 88.33%。其中 20 条越界模板自己包含“运维”二字，
与 out-of-scope 金标冲突；另有 Kafka、JVM、SLO、缓存、连接池、日志、网络、延迟等 8 条合理知识问答
因领域词表缺失被关闭式拒绝。

**Task：** 区分数据错误和产品缺陷，禁止通过放宽评分器或改错 Gold 来制造高分。

**Action：** 按现有 fails-closed 状态机修正越界输入及 expected phase/capability；补齐常用运维知识实体；
把“重启 CPU”等机械样本改为停止进程、修改配置、清理缓存、回滚版本等真实操作，又由此发现并补齐
“结束/修改/调整/清理”等高风险动作识别；保留服务别名等未实现边界并全量复跑。

**Result：** Query Contract 从首跑 88.33% 提升至 100%，且 Scope、Risk、Confirmation 与 Safety 始终
保持 100%。该结果是确定性契约回归，不包装为开放语义或生产请求准确率。

## STAR-10：SSE 开始后才发现并发冲突

**Situation：** 执行 Lease 原先在 SSE 生成器内部领取。HTTP 200 响应头可能已经发出，此时即使
revision 过期或 Run 已被占用，也无法向客户端返回正确的 409。

**Task：** 保证并发拒绝发生在任何流式事件发送之前。

**Action：** 将服务端 State 加载、CAS 校验和 Lease Claim 移到 `EventSourceResponse` 创建之前；
生成器只负责执行、事件流和最终持久化。新增回归测试验证返回响应对象前 Claim 已经完成。

**Result：** 冲突可以使用标准 HTTP 409 表达；执行异常或取消会写 Failure、关闭 AgentRun 并释放
Lease，避免长期悬挂的运行记录。

## 工程验证中发现的债务

### 未固定 Ruff 规则集

对全仓直接运行当前 `.venv` 的新版 Ruff 默认规则会报告 582 个历史问题，主要是既有 typing 写法、
import 顺序和 broad exception。仓库没有 `pyproject.toml` 或共享 Ruff 配置，也明确禁止无关的全量格式化。
本轮采用“变更文件定向 Ruff 零告警 + 全量 compile/tests”验证，不声称全仓 lint clean。后续应单独批准
并提交 lint 配置基线，再按模块清债，避免把数百个机械变更混进功能提交。

### 本地解释器与容器基线可能不一致

仓库 Dockerfile 的声明基线是 Python 3.12，但当前 Windows `.venv` 的标准库路径显示 Python 3.11。
本地测试通过不能替代 Python 3.12 容器验证；交付前需记录 `python --version`，并至少完成 Compose
配置校验。后续 CI 应同时把 3.12 编译和测试设为权威门禁。

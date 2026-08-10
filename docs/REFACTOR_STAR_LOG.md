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

**Situation：** 旧 `diagnosis_e2e_10` 会直接运行 Fast/Deep Agent。虚构 MySQL、Redis、Kafka 等事故
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
4. 自生成 16 条脱敏事故夹具，并实现零外部依赖的 FixtureDiagnosisRunner。
5. 增加数据集 SHA-256、100% 隔离门槛与单元测试，禁止本机采集来源混入。

**Result：** 16/16 夹具在 phase、mode、正反例行为、evidence type、isolation 和 exact match 上均为
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

**Result：** 内置 Skill 从 4 个扩展为 7 个，覆盖 16 条事故夹具中的主机、网络、数据库/缓存、K8s、
Kafka、JVM 和 Nginx 场景。K8s 当前仍无专用 Tool/Skill，作为后续缺口保留，不宣称已完整支持。

## STAR-04：Query/Scope/生命周期缺少统一离线回归

**Situation：** 原 Fast/Deep、API 和后台任务各自维护部分流程，Intent 冲突、Scope 未确认、越权请求、
恢复验证和 Memory 晋升难以用同一套契约验证。

**Task：** 建立无 Provider 依赖的发布前回归，覆盖 Query 深度理解、Capability、Scope、只读安全、
人工确认、恢复、关闭和 Memory。

**Action：** 构建 32 条 Query 与 9 条 lifecycle JSONL，加入对抗输入、模糊目标、高风险请求、采集器
失败和脱敏拒绝；固定数据集指纹与阈值，通过 `workflow --enforce` 阻断回归。

**Result：** 当前版本两套数据共 41 条，Intent/Capability/Scope/Safety/Lifecycle/Exact Match 在该数据集
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

- 16 条夹具规模较小，来自人工构造，尚未覆盖真实事故分布、同义表达和跨组织差异。
- Fixture Benchmark 不运行 LLM，不能衡量真实根因推理、Token、延迟或 Provider 抖动。
- K8s、数据库直连、日志平台和 Trace 仍缺统一的 Scope-aware Evidence Provider。
- 真实事故样本必须经脱敏、Gold 审核和事故家族切分后才能进入正式 Benchmark，不能自动晋升。
- 项目尚无 CI Workflow 和完整 tests/ 覆盖，静态检查与本地测试不能证明生产就绪。

## 工程验证中发现的债务

### 未固定 Ruff 规则集

对全仓直接运行当前 `.venv` 的新版 Ruff 默认规则会报告 581 个历史问题，主要是旧 typing 写法、
import 顺序和 broad exception。仓库没有 `pyproject.toml` 或共享 Ruff 配置，也明确禁止无关的全量格式化。
本轮采用“变更文件定向 Ruff 零告警 + 全量 compile/tests”验证，不声称全仓 lint clean。后续应单独批准
并提交 lint 配置基线，再按模块清债，避免把数百个机械变更混进功能提交。

### 本地解释器与容器基线可能不一致

仓库 Dockerfile 的声明基线是 Python 3.12，但当前 Windows `.venv` 的标准库路径显示 Python 3.11。
本地测试通过不能替代 Python 3.12 容器验证；交付前需记录 `python --version`，并至少完成 Compose
配置校验。后续 CI 应同时把 3.12 编译和测试设为权威门禁。

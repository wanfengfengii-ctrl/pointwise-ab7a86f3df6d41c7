# 业务与现有入口

这是取水数据采集链路的离线合流层。不同采集代理从门户取得原子更正、事务读前值及其前置提交标记；标记形成因果图，没有关系的事件不能假定抓取先后就是提交先后。程序合并已知事件，输出所有证据允许的最终井账。这里不开发网页、网络爬虫或通用命令行产品。

现有文件为 merge.py、samples.py、checks.py 和运行清单。Python 标准库、SQLite；无第三方依赖。业务入口是 from merge import Store, Error；Store(path) 打开或创建数据库，Store(":memory:") 可运行内存样例，close() 关闭连接。每个并发参与者使用自己的 Store。磁盘库开启 WAL、synchronous=FULL。

## 输入、结果与不变量

apply(batch_id, expected_generation, events, fault=None) 返回 JSON 兼容字典。batch_id 是非空字符串，expected_generation 是非负整数，布尔值不算整数。events 是列表；列表内事件 ID 唯一，事件恰有 id、parents、reads、writes 四个字段。字符串均为可 UTF-8 编码的非空文本。

事件样例：
{"id":"east-agent","parents":[],"reads":{},"writes":{"well-a":10,"well-b":20}}
{"id":"withdraw","parents":["east-agent"],"reads":{"well-a":10},"writes":{"well-a":null}}

id 是事件身份；parents 是无重复事件 ID 的列表；writes 是井 ID 到非负整数或 null 的对象，零必须保留，null 删除该井，不存在的井可删除；writes 允许为空，仍保留该事件及其因果作用。reads 采用同样的键和值类型，表示事务开始改写前必须同时满足的井值，null 表示井不存在；reads 允许为空。读取自身将改写的井合法，先校验所有 reads，再一次性应用全部 writes。事件的读校验和多井改写是一个不可拆分的动作。初始井账为空；每个事件恰执行一次。每条父边限定父事件先于子事件执行；合法执行是全部已接纳事件的一次拓扑排序，且每个事件的读前值均与当时井账相符。没有父路径的事件不强加先后，不能因某个前缀读校验失败就认定其他顺序也失败。没有完整合法执行则 INCONSISTENT，整批拒绝，不发布空结果族。

事件不可修改：已有 ID 的完全相同事件可重投，内容不同则 EVENT_CONFLICT。父事件可来自已接纳集合或本批任意位置；批次完成后所有父引用必须存在，且全图无环，否则 MISSING_PARENT 或 CYCLE。本批全部事件共同校验，失败不能留下部分事件。先做输入结构校验，再查批次回执；新批次先核对代次，再检查已有事件冲突，再核对父引用、环和可行执行。缺失父引用优先于环，环优先于 INCONSISTENT。

请求身份由规范化后的 events 决定：事件按 id 排序、parents 排序，所有对象键排序；使用 json.dumps(..., ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)。事件输入顺序、parents 输入顺序及对象键顺序不影响身份。新 batch_id 仅在 expected_generation 等于当前代次时成功；每次首次成功恰增加一代，包括空列表和只含旧事件的批次。回执形如 {"batch_id":"two-agents","generation":1,"events":2,"outcomes":2}；events 是全库事件数，outcomes 是该代互异最终井账数。

同一 batch_id 同一请求返回原回执，优先于代次检查，不求解或发布新代；同一 batch_id 不同请求报 BATCH_CONFLICT。输入结构仍须合法，不能用非法类型绕过校验。新批次代次不符报 GENERATION_CONFLICT。参数或事件结构非法报 INVALID，均为 Error，str(error) 为上述标记。除 after_commit 已提交故障外，失败不产生回执，不改变事件、代次或历史快照。

read(generation=None) 返回 {"generation":N,"outcomes":[{"rows":[["well-a",10],...],"order":["event-id",...]}...]}。默认读取一个完整当前代次；显式非负整数读取永久保留的历史代次，不存在则 NO_GENERATION，非法参数则 INVALID。rows 按井 ID 排序，outcomes 按 rows 的 Python 列表字典序排序。结果族恰为全部合法执行可得到的互异最终井账，每份井账只出现一次；order 必须是产生该 rows 的完整合法拓扑序，同账的不同合法见证任选。各井可能值的笛卡尔积不代表合法结果族。相同数值可以来自不同事件，不能任意固定唯一读来源；null 读可能由初始空账或不同删除事件满足，必须由同一个完整顺序联合解释。不同已执行事件集合即使账面相同，其未来可行执行也可能不同，不能无条件合并中间状态。

 audit() 在一个一致读事务中返回 {"generation":N,"events":[规范事件...],"receipts":[回执...],"snapshots":[代次...]}。events 按 ID 排序，receipts 按 batch_id 排序，snapshots 按代次排序。事件、历史快照、当前代次与批次回执须原子发布；任何成功可见状态的快照代次连续且能由对应已接纳事件解释。历史读取和旧回执重放不得回退当前代次。read、audit、apply 的并发调用须可解释为遵守完成先于调用关系的串行历史；无响应调用可以提交或未提交，由恢复状态及重试结果共同约束，不能直接当失败或忽略。

## 样例与正常运行

python3 checks.py normal 执行既有正常业务断言，成功输出 NORMAL_REGRESSION_OK。python3 checks.py demo 展示真实结果。

samples.NORMAL 首次接纳后，唯一结果是 [["well-a",12],["well-b",20]]。samples.AMBIGUOUS 在空库首次接纳后，结果族恰为 [["well-a",10],["well-b",20]] 和 [["well-a",11],["well-b",19]]；不允许交叉拼出 10/19 或 11/20。再接纳 samples.DELETE 后，结果族恰为 [["well-b",19]] 和 [["well-b",20]]，历史第一代不变，重投 two-agents 返回第一代回执。在 AMBIGUOUS 后单独接纳 OBSERVED，读前值证据仅允许 11/19；在 DELETE 后接纳 demo 的缺值读事件，结果仅为 [["well-b",19]]。没有完整合法顺序的新增证据整批拒绝。

## 现有故障边界

fault(stage) 在 after_plan、after_events、after_snapshot、before_commit、after_commit 调用。after_plan 已完成求解但未开启写事务；after_events、after_snapshot、before_commit 在同一未提交写事务内；after_commit 已提交但尚未返回回执。回调可抛异常、与父进程握手暂停，便于真实杀进程；不得在回调中修改业务数据。前四处中断自身不能留下部分发布，after_commit 的响应丢失须由同请求重试取回原回执。异常回滚只约束本调用，不排除同时存在的合法其他提交。独立进程重开同一路径须续用已提交数据，不重建数据库。after_plan 暂停期间其他进程应能正常提交，旧计划不能覆盖新代；允许公开的代次冲突和有界等待，不能把锁错误、进程崩溃或超时当作业务正确的证据。

## 本轮质量交付约定

保留 merge.py、samples.py、checks.py 的内容及既有断言。新增自动化验证、REPORT.md 和 evidence.json；不修复业务实现，不预设基线一定无缺陷。报告解释判据、独立性、覆盖边界、实际结果和剩余风险，按稳定案例 ID 关联机器证据。evidence.json 形如 {"cases":[{"id":"case-id","input":{},"expected":{},"actual":{},"passed":true,"report_ref":"REPORT.md#case-id"}]}。input 保存可重放输入或调度，expected 是独立期望，actual 是实际观察，passed 是语义判定，report_ref 引用报告位置；这些字段的内部业务结构按案例需要选定，允许添加字段。资源测量须保留负载构造参数、实际规模、计时口径和峰值内存口径，不能把估算写成实测；报告不设未公开的性能门槛。

质量判定器不得调用或复制生产规范化、分组、求解算法来算期望。须同时检查所有结果的合法性、结果族完整性及每个见证；不同合法见证不能误报。小图用全拓扑序穷举交叉核对；大图的独立判据不能枚举所有拓扑序，不可只验证已返回结果或只比总量。覆盖事件集合变化而中间账面相同、同值不同来源、存在与缺值读取、读写自身键、零与删除、只读及空写屏障、跨批父引用、无可行序、重放与冲突、历史读及公开故障边界的相互作用。大图不是固定节点数门槛：须展示完整枚举已不可行而独立判据仍可核对的实例，并报告剩余可扩展性限制。

并发与恢复证据须使用磁盘库和真实独立进程，保留调用/返回区间、操作参数、故障位置、终止与重开观察，独立核对存在一个满足实时先后的完整原子发布解释。随机反例须保存种子并自动缩减为仍触发同一语义偏差的可重跑输入或调度；若基线未发现偏差，可用变异反例演示缩减。可执行变异只作用于隔离副本，至少实质覆盖因果丢失、跨井拼接与回执/状态分裂，保存违反业务判据的期望和实际；语法错误、导入失败、被杀或普通非零退出不算检出。

新增入口 python3 qa.py verify 重跑 checks.py normal 和报告所用的验证证据，并核对 REPORT.md、evidence.json 与本次实际观察一致；不能只检查文件存在或回显成功。未解决语义偏差须保留失败证据并使 verify 非零，完成质量任务不以修改业务源码把失败改绿为条件。python3 qa.py demo 展示真实歧义账面、见证和响应丢失后的旧回执重放。更新运行清单的 verify/demo 到这两个入口，setup 继续采用 argv 数组列表；其余验证的组织、随机规模、报告细节与压力设计由交付者决定。

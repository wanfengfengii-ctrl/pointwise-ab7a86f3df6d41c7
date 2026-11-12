# 取水合流器自动化质量验证报告

本报告对应 `CONTRACT.md` 的"本轮质量交付约定"。生产源码 `merge.py`、`samples.py`、
`checks.py` 及其既有断言**未被修改**；本轮只新增独立验证代码、本报告与
`evidence.json`。所有机器证据按稳定案例 ID 关联到 `evidence.json` 的同名 `cases[].id`，
并用 `report_ref` 指回本文件锚点。

- 验证入口：`python3 qa.py verify`、`python3 qa.py demo`
- 证据文件：`evidence.json`（由 verify 重新生成并与本次实际观察核对）
- 运行环境：见 `evidence.json` 顶层 `python` 与 `generated_at`
- 结论摘要：**单进程/单批语义族在全部功能用例、120 个随机种子与三类可执行变异下
  判定精确完整；发现 1 个真实的多进程并发原子代次偏差（C01），按约定保留失败证据，
  未修改业务源码，verify 因此以非零退出。**

## 1. 判定依据与独立性

质量判定器位于 `qa/oracle.py` 与 `qa/sat.py`，**不 import、不调用、也不复制**
`merge.py` 的规范化、连通分量分组或理想前沿 DP 求解。它实现两套互相独立的精确过程：

1. **小图全拓扑序穷举**（`enumerate_outcomes`）：DFS 枚举全部满足父边的拓扑序，逐条直接
   模拟"先校验全部 reads，再一次性应用全部 writes"，收集互异最终井账与见证。
2. **大图 SAT 判定**（`sat_outcomes` + 自研 DPLL `qa/sat.py`）：把"存在一条完整合法拓扑序"
   编码为布尔可满足性问题，用 All-SAT 逐一枚举**互异最终井账**。它从不枚举拓扑序，
   因此能在全拓扑序已不可行的耦合图上仍给出精确结果族。

两套过程在 400 个随机小图上结果族完全一致（互核），且 SAT 返回的每个见证都再用独立的
直接模拟 `simulate` 验证合法性与账面一致性。DPLL 求解器本身另用 10000 个随机 CNF 对
暴力真值表做过健全性/完备性校验，线性 at-most-one 编码也单独对暴力枚举校验通过。

每份生产返回的 outcome 都经过三项独立检查：结果族**完整性**（不缺合法账）、**精确性**
（无伪账/无跨井拼接）、以及每个 `order` 见证是否为产生该 `rows` 的完整合法拓扑序。

## 2. 功能语义用例（独立判据判定）

| 案例 ID | 覆盖点 | 判据 |
|---|---|---|
| <a id="f01-normal-unique"></a>F01-normal-unique | 唯一账面、有序见证、回执计数 | 穷举+SAT |
| <a id="f02-ambiguous-no-splice"></a>F02-ambiguous-no-splice | 歧义族，禁止 10/19、11/20 交叉拼接 | 穷举+SAT |
| <a id="f03-delete-family-shrink"></a>F03-delete-family-shrink | 删除后结果族收缩、历史代次不变 | 穷举+SAT |
| <a id="f04-observed-read-selects-branch"></a>F04-observed-read-selects-branch | 读前值证据只允许 11/19 分支 | 穷举+SAT |
| <a id="f05-same-value-different-source"></a>F05-same-value-different-source | 同值异源、读来源不可任意固定 | 穷举+SAT |
| <a id="f06-null-read-two-origins"></a>F06-null-read-two-origins | null 读由初始空账或删除共同解释 | 穷举+SAT |
| <a id="f07-zero-and-self-read-write"></a>F07-zero-and-self-read-write | 零值保留、读写自身键 | 穷举+SAT |
| <a id="f08-missing-read-after-delete"></a>F08-missing-read-after-delete | 缺值读与删除的联合解释 | 穷举+SAT |
| <a id="f09-empty-write-barrier"></a>F09-empty-write-barrier | 空写屏障保留因果作用 | 穷举+SAT |
| <a id="f10-infeasible-batch-rejected"></a>F10-infeasible-batch-rejected | 无完整合法序→INCONSISTENT 整批拒绝 | SAT 空族 |
| <a id="f11-cross-batch-parent"></a>F11-cross-batch-parent | 跨批父引用解析 | 穷举+SAT |
| <a id="f12-equal-interval-distinct-future"></a>F12-equal-interval-distinct-future | 中间同账、剩余事件不同→不无条件合并 | 穷举+SAT |
| <a id="f13-delete-nonexistent-well"></a>F13-delete-nonexistent-well | 删除不存在的井合法 | 穷举+SAT |
| <a id="f14-empty-batch-advances-generation"></a>F14-empty-batch-advances-generation | 空批次仍推进代次 | 空图特判 |

14 个功能用例全部通过。F12 特别验证了 CONTRACT 强调的"已执行事件集合不同即使账面相同，
未来可行性也可能不同"：同一中间账 `k=1` 经由不同剩余事件集合，分别收敛到终账 `k=1`
（见证以再次写 1 的事件结尾）与 `k=9`。

## 3. 输入校验、回执与请求身份

<a id="e01-bool-generation-invalid"></a><a id="e02-negative-generation-invalid"></a><a id="e03-empty-batch-id-invalid"></a><a id="e04-structural-invalid"></a><a id="e05-generation-conflict"></a><a id="e06-empty-batch-ok"></a><a id="e07-same-request-replay"></a><a id="e08-batch-conflict-precedence"></a><a id="e09-event-conflict"></a><a id="e10-identical-event-resubmit"></a><a id="e11-missing-parent"></a><a id="e12-cycle"></a><a id="e13-failure-atomic-no-partial-state"></a><a id="e14-no-generation"></a><a id="e15-read-invalid-arg"></a><a id="e16-normalization-identity"></a>

`qa/contract_errors.py` 覆盖 16 个错误路径（E01–E16），全部通过：布尔/负数代次与结构非法
→ INVALID；新批次代次不符 → GENERATION_CONFLICT；同 batch_id 不同请求 → BATCH_CONFLICT
且优先于代次检查；同 ID 异内容 → EVENT_CONFLICT；缺父→MISSING_PARENT 优先于环→CYCLE；
失败批次不留部分状态（audit 前后相等）；NO_GENERATION/INVALID 历史读；以及事件顺序、
parents 顺序、对象键顺序不影响规范化请求身份（同请求重投返回原回执、不推进代次）。

## 4. 并发、故障与恢复（真实独立进程 + 磁盘 WAL 库）

`qa/concurrency.py` 通过 `qa/worker.py` 拉起**真实 OS 进程**，记录调用/返回区间、故障位置、
SIGKILL 终止与重开观察，再独立核对是否存在一个满足实时先后的完整原子发布串行解释。

| 案例 ID | 场景 | 结果 |
|---|---|---|
| <a id="c02-kill-before-commit-no-partial"></a>C02 | before_commit 握手暂停后真杀进程 | 通过：无部分发布，仅余 gen0 快照 |
| <a id="c06-in-txn-exception-rollback"></a>C06 | after_events/after_snapshot 写事务内抛异常 | 通过：异常回滚，无部分发布 |
| <a id="c03-response-loss-replay-no-rollback"></a>C03 | after_commit 已提交、响应丢失后重放 | 通过：重放取回原回执，不回退/不重算 |
| <a id="c04-reopen-continues-committed-state"></a>C04 | 独立进程重开同一路径 | 通过：续用已提交数据，快照代次连续 |
| <a id="c05-stale-plan-yields-to-newer-generation"></a>C05 | after_plan 暂停期间对端提交新代 | 通过：旧计划报 GENERATION_CONFLICT，不覆盖新代 |
| <a id="c01-concurrent-commits-one-winner"></a>C01 | 两进程同代并发提交不同批次 | **未解决偏差（见下）** |

### 4.1 未解决语义偏差：C01 回执/状态分裂（split-brain）

**现象**：两个独立进程对同一磁盘库、同一 `expected_generation=0` 并发提交互不相同的批次。
在 8 次复现实验中有 7 次，**两个进程都返回了 `generation=1` 的成功回执，但数据库中只持久化
了其中一个批次的回执与事件**，另一个客户端的提交被静默覆盖。

**机理（基于代码与实测，非推测）**：`Store.apply` 先用一个普通 `BEGIN` 读事务读取当前代次
（`merge.py` 第 184–196 行），提交该读事务后在内存求解，随后才开启 `BEGIN IMMEDIATE`
写事务（第 217 行）并在第 223–226 行重新核对代次。两个进程可在各自的写事务开始前都读到
gen0 并完成求解；由于读事务已释放，第二个拿到写锁的进程在重核时代次确已变为 1——理论上
应触发 GENERATION_CONFLICT。实测却出现双成功回执，说明代次重读与提交之间的窗口未能把
"已向调用方返回成功"与"实际持久化"约束为单一原子代次。这违反 CONTRACT 第 25 行
"read、audit、apply 的并发调用须可解释为遵守完成先于调用关系的串行历史"。

**为何保留而非改绿**：CONTRACT 明确要求"不修复业务实现""未解决语义偏差须保留失败证据并使
verify 非零，完成质量任务不以修改业务源码把失败改绿为条件"。因此 C01 在 `evidence.json`
中标记 `known_deviation: true`、`passed: false`，完整调用/返回区间与 audit 观察均留存，
`qa.py verify` 据此返回非零。

**适用边界**：该偏差仅在多进程、同代次、写事务窗口真正重叠时显现；单进程串行使用、
after_plan/before_commit/after_commit 故障注入与重开重放（C02–C05）均符合原子代次语义。

## 5. 随机反例与自动缩减

<a id="r01-random-property"></a>

`qa/randomize.py` 以固定基准种子 20260101 起生成 120 个随机事件图（规模 2–9，含父边、
存在/缺值读、零与删除、空写屏障），逐图用独立判据核对生产结果族；失败输入会经贪心缩减
（删事件、修剪 reads/writes 映射）为仍触发同一偏差的最小可重跑输入，并把种子与输入写入
`qaartifacts/seeds/`。**本轮 120/120 通过，未发现单批语义偏差**；缩减机制与种子留存已就绪，
一旦未来出现偏差可直接产出最小复现。

## 6. 可执行变异（隔离副本，证明判据能检出三类缺陷）

`qa/mutation.py` 仅在从源码文本加载的**隔离内存副本**上注入变异，绝不改动磁盘上的
`merge.py`；语法错误、导入失败、被杀或普通非零退出均不计入检出。每个变异都自动缩减触发
输入，并验证同一缩减输入在未变异基线上仍通过（排除误报）。三类全部被检出：

| 变异 ID | 注入缺陷 | 独立判据观察 |
|---|---|---|
| <a id="m01-causal-loss-detected"></a>M01 | 清零求解 need 掩码，丢弃父边因果 | 结果族不精确、出现非法见证（spurious 账面） |
| <a id="m02-cross-well-splice-detected"></a>M02 | 按井做笛卡尔积，跨执行拼接 | 拼接账的见证非法（witness_ok=false） |
| <a id="m03-receipt-state-split-detected"></a>M03 | 回执 outcomes 计数 +1 | 回执计数与持久快照族不一致（receipt_split） |

## 7. 资源实测（因果宽度与共享键耦合）

<a id="p01-resource-measurement"></a>

`qa/resources.py` 用 `tracemalloc` 测 SAT 判据全程 **Python 峰值内存**、`time.perf_counter`
测墙钟时间；保留负载构造参数与实际规模，枚举不可行处只给下界说明、不写估算。完整数字在
`evidence.json` 的 `P01-measured-time-and-peak-memory`。代表性实测：

- 因果宽度（n=8）：宽 2 仅 2 个拓扑序、0.016s；宽 8（全反链）达 40,320 个拓扑序、约 14.5s，
  而峰值内存只从约 239KiB 量级变化——耗时随拓扑序数量指数上升，内存增长平缓。
- 共享键耦合（n=8）：单井强耦合约 13.7s，4 井时降到约 0.47s；耦合把本可独立分解的分量压成
  一个难解分量，是耗时主因。
- 大图实例：n=12 的宽因果 DAG（10,395 个拓扑序，全枚举已不现实）SAT 判据仍给出精确 9 个
  互异账面，实测约 12s、峰值约 655KiB。

## 8. 适用边界与剩余风险

- **判据规模边界**：自研 DPLL 采用时序回溯、无冲突子句学习，全耦合 n≈8–9、宽反链 n≈8 是
  实用拐点（十秒量级）；更大实例的最终 UNSAT 证明呈指数增长。这是判据自身的可扩展性限制，
  不影响其在小图穷举与上述大图实例上的精确性。
- **随机覆盖边界**：120 个种子针对单批图；多批累积状态的随机调度尚未纳入随机化（已由
  确定性的 C02–C05 覆盖），后续可扩展为带调度的状态机随机测试。
- **已知未解决偏差**：仅 C01 并发 split-brain；其余语义、故障、恢复与身份规则均通过。
- 本报告不设未公开的性能门槛；所有性能数字均为本次实测，可由 verify 重新生成核对。

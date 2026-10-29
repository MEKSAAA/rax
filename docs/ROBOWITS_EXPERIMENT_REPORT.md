# RAX 在 RoboWits 上的实验报告

本报告对应 2026 年 10 月 28–29 日的实验。内容来自本地配置、原生评测结果、模型公开输出、控制程序日志与录像。

## 阅读前提：任务范围、框架与 API

**本实验使用 RAX 的 Codex CLI 执行框架，通过 ChatGPT 登录调用 gpt-6-astra；评测对象全部是 RoboWits seed 基础任务，每任务仅测试 5 个官方布局。**

| 项目 | 本次实验实际配置 |
| --- | --- |
| 任务类型 | 全部为 RoboWits seed 基础任务，mutation=False；没有评测 mutation 变体。Seed task 是任务类别，不是随机数种子。 |
| 任务范围 | README 的 robowits-10：01、02、03、04、06、09、13、16、17、25。 |
| 评测规模 | 官方 seed 评测为每任务 50 个布局；本实验仅测其中 5 个（layout_id=0..4），来自 eval_dataset_seed。因此是小规模子集评测，不是完整官方规模。 |
| 探索与测试 | 探索重复源码原始场景（collection=2）；正式测试使用官方独立布局（collection=1），两者仍属于同一个 seed task。 |
| Agent 执行框架 | RAX 的 Codex CLI harness，运行 OpenAI Codex CLI 0.164；RAX 提供机器人 MCP 接口、回合管理、程序冻结和评测流程。 |
| 模型与调用方式 | gpt-6-astra，reasoning effort=xhigh；使用 profile=chatgpt，经 Codex CLI 的 ChatGPT 账号登录链路调用 OpenAI 托管模型，不是本地模型推理。 |
| API / 认证 | 本次模型调用使用 ChatGPT 登录认证，不使用独立 OpenAI API key 或 OpenRouter 模型调用；外部 Gemini 感知关闭。仍存在在线模型请求，不能称作“不消耗模型调用”；不据此虚构按 API token 计费的美元成本。 |
| 机器人接口 | 本地 MCP 工具提供 RGB-D 观察、像素到空间定位、状态查询和双臂动作（如 robowits_observe、robowits_pixel_to_position、robowits_status、robowits_step），连接本地 Genesis 仿真。它们与云端模型调用是不同层的接口。 |
| 何时调用模型 | 探索、分析、编程与调试阶段调用模型；本次正式评测运行冻结 Python 控制程序，不再让模型逐步决定动作，也未启用外部感知模型。 |
| 预算口径 | 九个任务使用 8 次探索，09 首轮 8 次后追加 8 次。最终 15/50（30%）是混合预算汇总；原始统一预算记录另行保留。 |

上述信息描述已执行实验。后续若新增或更换模型、调用服务或 API，先与用户确认后执行；报告不包含账号凭据。

## 1. 主要结果

**十个任务均已取得正式评测结果，纳入 09 续跑后的合计成绩为 15/50，成功率 30%。** 其中 09 使用累计 16 次探索，其余任务使用 8 次，因此这是混合预算汇总。

原始统一 8 回合预算下，09 未完成程序提交；其余九个任务的实测成绩为 **14/45（31.1%）**。如果固定十任务共 50 场的分母、将 09 缺失场次计零，则为 **28%**，但必须同时注明缺失，不应称为 09 实测 0/5。

这些结果仅对应所选十任务、每任务五个正式布局，不是 RoboWits 全任务全集成绩，也不是 RAX 论文原基准的数值复现。

## 2. 实验设置

| 项目 | 设置 |
| --- | --- |
| 任务集 | 01、02、03、04、06、09、13、16、17、25，即 README 所选十任务。 |
| 方法 | 冻结编程大模型通过机器人接口探索，把经验写成代码与记忆；提交后由冻结程序执行正式任务，不训练模型权重。 |
| 模型 | gpt-6-astra，xhigh；Codex CLI 0.164，ChatGPT 登录调用。 |
| 感知 | ego、右腕、左腕三视角 RGB-D，848×480，附标定和机器人状态；不使用外部 Gemini 感知。 |
| 动作 | 14D 双臂绝对末端位姿与夹爪宽度；底层控制和逆运动学由环境提供。 |
| 探索场景 | collection=2，使用 RoboWits 源码定义的原始场景，初始布局重复；探索不读取正式布局。Agent 可用动作自行扰动开发场景。 |
| 探索预算 | 原始每任务 8 回合；交互探索、程序开发与冻结程序复测共用。09 续跑追加 8 回合，总计 16。 |
| 单回合限制 | 最多 1000 个 native 控制步，control_dt=0.0333 秒；另有 7200 秒墙钟上限。回合数和步数是两种不同预算。 |
| 提交资格 | 确切版本须有一次完整、无未处理运行错误的复测；干净退出不等于任务成功，13 是典型例子。 |
| 正式评测 | 官方 collection=1，layout_id=0..4，布局种子 42，每任务五场；按 native is_success 判定。 |
| 冻结协议 | 正式期间不依据测试结果修改程序，也不让大模型决定每一步动作。 |
| 环境 | RoboWits-genesis131，Genesis 1.3.1；仿真 Python 3.11，host Python 3.12。 |
| 硬件 | GPU 1：RTX 6000 Ada 48 GB；后五任务最多两个任务并行。 |
| 数据版本 | RoboWits 源码提交 b3fb06568b6a76d4ee371ff25fb2318336c012b5；逐运行配置与已有哈希保存在证据目录。 |
| 协议修正 | 本报告仅纳入原始场景协议与纹理加载修正后的运行；早期随机重置实验不混入本表。 |
| 辅助指标 | 平均最高原生奖励反映任务内进展，不是成功率，不宜跨任务当成同一种物理尺度。 |

8 回合是本次实验配置，不是 RAX 方法固有限制。任务程序由实验 agent 生成，未由操作者预先手写任务解法。

## 3. 实验结果表

### 3.1 最新结果（09 为追加预算）

复测列只统计最终提交的确切版本；同一原始场景的重复复测不能当作独立布局测试。

| 任务 / Markdown 过程记录 | 探索回合 | 最终版本复测 | 正式成功 | 成功率 | 平均最高奖励 |
| --- | --- | --- | --- | --- | --- |
| [01 Align Blocks](../runtime/robowits-report/cases/robowits_01/process.md) | 8 | 2/2 | 2/5 | 40% | 0.684 |
| [02 Retrieve Cube](../runtime/robowits-report/cases/robowits_02/process.md) | 8 | 1/1 | 0/5 | 0% | 0.313 |
| [03 Gap Retrieve](../runtime/robowits-report/cases/robowits_03/process.md) | 8 | 2/2 | 1/5 | 20% | 0.632 |
| [04 Pinch Card](../runtime/robowits-report/cases/robowits_04/process.md) | 8 | 2/2 | 2/5 | 40% | 0.790 |
| [06 Dominos](../runtime/robowits-report/cases/robowits_06/process.md) | 8 | 2/2 | 5/5 | 100% | 1.000 |
| [09 Hold Cup](../runtime/robowits-report/cases/robowits_09_continued/process.md) | 16（8+8） | 2/2 | 1/5 | 20% | 0.498 |
| [13 Cover with Lid](../runtime/robowits-report/cases/robowits_13/process.md) | 8 | 0/1 | 0/5 | 0% | 0.700 |
| [16 Stand Bulb](../runtime/robowits-report/cases/robowits_16/process.md) | 8 | 2/2 | 1/5 | 20% | 0.560 |
| [17 Ball onto Tower](../runtime/robowits-report/cases/robowits_17/process.md) | 8 | 1/1 | 3/5 | 60% | 0.578 |
| [25 Water into Mug](../runtime/robowits-report/cases/robowits_25/process.md) | 8 | 5/5 | 0/5 | 0% | 0.236 |

### 3.2 09 原始预算与续跑对照

| 运行 | 累计探索回合 | 程序提交 | 正式结果 |
| --- | --- | --- | --- |
| 首轮 | 8 | 未提交：最后一次交互才成功，没有剩余复测回合 | 未执行，不能称为实测 0/5 |
| 续跑 | 16 | 已提交：确切最终版本复测 2/2 成功 | 1/5，20% |

续跑继承首轮代码、记忆与对话；不是从零开始的新 8 回合实验，也不是一次独立重复。原始记录保留，不用续跑结果覆盖。

### 3.3 每个正式布局的结果与步数

表格中的编号是第 1–5 场，对应 layout_id=0..4。

| 任务 | 第 1 场 | 第 2 场 | 第 3 场 | 第 4 场 | 第 5 场 |
| --- | --- | --- | --- | --- | --- |
| 01 Align Blocks | 失败 / 1000 步 | 成功 / 241 步 | 成功 / 514 步 | 失败 / 1000 步 | 失败 / 1000 步 |
| 02 Retrieve Cube | 失败 / 1000 步 | 失败 / 1000 步 | 失败 / 1000 步 | 失败 / 1000 步 | 失败 / 1000 步 |
| 03 Gap Retrieve | 失败 / 1000 步 | 失败 / 1000 步 | 失败 / 1000 步 | 成功 / 972 步 | 失败 / 1000 步 |
| 04 Pinch Card | 失败 / 1000 步 | 失败 / 1000 步 | 失败 / 1000 步 | 成功 / 653 步 | 成功 / 721 步 |
| 06 Dominos | 成功 / 104 步 | 成功 / 102 步 | 成功 / 104 步 | 成功 / 102 步 | 成功 / 104 步 |
| 09 Hold Cup | 失败 / 1000 步 | 失败 / 1000 步 | 失败 / 1000 步 | 失败 / 1000 步 | 成功 / 933 步 |
| 13 Cover with Lid | 失败 / 1000 步 | 失败 / 1000 步 | 失败 / 1000 步 | 失败 / 1000 步 | 失败 / 1000 步 |
| 16 Stand Bulb | 失败 / 1000 步 | 成功 / 729 步 | 失败 / 1000 步 | 失败 / 1000 步 | 失败 / 1000 步 |
| 17 Ball onto Tower | 成功 / 496 步 | 成功 / 514 步 | 成功 / 517 步 | 失败 / 1000 步 | 失败 / 1000 步 |
| 25 Water into Mug | 失败 / 1000 步 | 失败 / 1000 步 | 失败 / 1000 步 | 失败 / 1000 步 | 失败 / 1000 步 |

### 3.4 运行时间

耗时含模型等待、探索、编程与仿真；并行运行和场景开销不同，因此不能当作模型速度基准。

| 任务 | 记录的运行时间（分钟） |
| --- | --- |
| 01 Align Blocks | 34.7 |
| 02 Retrieve Cube | 68.8 |
| 03 Gap Retrieve | 40.3 |
| 04 Pinch Card | 66.4 |
| 06 Dominos | 原批次采用已有运行，未据此重算 |
| 09 Hold Cup | 59.7 |
| 13 Cover with Lid | 91.2 |
| 16 Stand Bulb | 52.6 |
| 17 Ball onto Tower | 49.7 |
| 25 Water into Mug | 59.0 |
| 09 Hold Cup（追加轮） | 45.0 |

## 4. 成功与失败 Demo

以下链接直接打开视频或 Markdown 过程记录，无需使用 HTML 报告。完整过程记录包含全部已保存回合、agent 公开分析、编程工具调用、机器人请求、控制程序和笔记。工具响应详情另附 JSON 时间线。

公开分析不包含隐藏内部推理；原文中的推测须与原生评测结果区分。录像时间是仿真播放时间，不能直接对齐模型分析的墙钟时间。

### 成功案例：多米诺链式推动

[完整 Markdown 过程记录](../runtime/robowits-report/cases/robowits_06/process.md)

- [成功正式录像](../runtime/robowits-report/cases/robowits_06/episodes/robowits_06_formal_0_ac0d132b/sensors.mp4)：104 步，回合 `robowits_06_formal_0_ac0d132b`。
- [工具调用与反馈时间线](../runtime/robowits-report/cases/robowits_06/timeline.json)
- [逐回合失败证据](../runtime/robowits-report/cases/robowits_06/failure-audit.json)

### 成功与失败对照：球放塔顶

[完整 Markdown 过程记录](../runtime/robowits-report/cases/robowits_17/process.md)

- [成功正式录像](../runtime/robowits-report/cases/robowits_17/episodes/robowits_17_formal_0_54b5091f/sensors.mp4)：496 步，回合 `robowits_17_formal_0_54b5091f`。
- [失败正式录像](../runtime/robowits-report/cases/robowits_17/episodes/robowits_17_formal_3_579ab8c7/sensors.mp4)：1000 步，回合 `robowits_17_formal_3_579ab8c7`。
- [工具调用与反馈时间线](../runtime/robowits-report/cases/robowits_17/timeline.json)
- [逐回合失败证据](../runtime/robowits-report/cases/robowits_17/failure-audit.json)

### 软件错误案例：盖子搬运

[完整 Markdown 过程记录](../runtime/robowits-report/cases/robowits_13/process.md)

- [失败正式录像](../runtime/robowits-report/cases/robowits_13/episodes/robowits_13_formal_0_a9f52054/sensors.mp4)：1000 步，回合 `robowits_13_formal_0_a9f52054`。
- [工具调用与反馈时间线](../runtime/robowits-report/cases/robowits_13/timeline.json)
- [逐回合失败证据](../runtime/robowits-report/cases/robowits_13/failure-audit.json)

### 泛化失败案例：接水

[完整 Markdown 过程记录](../runtime/robowits-report/cases/robowits_25/process.md)

- [失败正式录像](../runtime/robowits-report/cases/robowits_25/episodes/robowits_25_formal_0_e681f464/sensors.mp4)：1000 步，回合 `robowits_25_formal_0_e681f464`。
- [工具调用与反馈时间线](../runtime/robowits-report/cases/robowits_25/timeline.json)
- [逐回合失败证据](../runtime/robowits-report/cases/robowits_25/failure-audit.json)

### 试错与修正案例：柠檬取出

[完整 Markdown 过程记录](../runtime/robowits-report/cases/robowits_03/process.md)

- [成功正式录像](../runtime/robowits-report/cases/robowits_03/episodes/robowits_03_formal_3_843d183d/sensors.mp4)：972 步，回合 `robowits_03_formal_3_843d183d`。
- [失败正式录像](../runtime/robowits-report/cases/robowits_03/episodes/robowits_03_formal_0_2aa63a9b/sensors.mp4)：1000 步，回合 `robowits_03_formal_0_2aa63a9b`。
- [工具调用与反馈时间线](../runtime/robowits-report/cases/robowits_03/timeline.json)
- [逐回合失败证据](../runtime/robowits-report/cases/robowits_03/failure-audit.json)

### 追加探索案例：斜坡放杯

[完整 Markdown 过程记录](../runtime/robowits-report/cases/robowits_09_continued/process.md)

- [成功正式录像](../runtime/robowits-report/cases/robowits_09_continued/episodes/robowits_09_formal_4_3eae603e/sensors.mp4)：933 步，回合 `robowits_09_formal_4_3eae603e`。
- [失败正式录像](../runtime/robowits-report/cases/robowits_09_continued/episodes/robowits_09_formal_0_0d886c8e/sensors.mp4)：1000 步，回合 `robowits_09_formal_0_0d886c8e`。
- [工具调用与反馈时间线](../runtime/robowits-report/cases/robowits_09_continued/timeline.json)
- [逐回合失败证据](../runtime/robowits-report/cases/robowits_09_continued/failure-audit.json)

## 5. 逐任务实验分析

### 01 Align Blocks：感知反馈与重试有效，但仍有布局依赖

程序根据当前 RGB-D 定位蓝色方块，选择双臂或顺序搬运，并在遮挡后重新观察。最终版本原始场景复测两次成功；正式布局仅两场成功。失败日志出现多轮 approach、view_clear_check 和 recovery-limit 后等待到上限。可以确认恢复未能完成任务，尚不能把三场失败都归结为同一种视觉或接触错误。探索中的人工扰动测试是 agent 自己调用动作准备的开发场景，并非人类示范。

[证据与全过程](../runtime/robowits-report/cases/robowits_01/process.md)

### 02 Retrieve Cube：双臂约束方案在单一布局成立，正式迁移失败

探索记录显示单臂转腕时容器会在夹爪中转动；最终改为分别夹持容器口沿和下部，以两个接触点约束翻转，最后一次复测在 636 步成功。正式五场全失败：日志既有容器/方块未检出后的搜索，也有执行双臂翻转和 park_cup 后仍未终止。证据支持策略迁移与恢复不足，但不能据此断定五场都是同一种打滑。只有一次最终版本成功复测，覆盖面有限。

[证据与全过程](../runtime/robowits-report/cases/robowits_02/process.md)

### 03 Gap Retrieve：真实滑落推动了程序修正，但恢复余量不足

开发 v2/v3 的滑落触发重抓，重抓后已无足够步数完成放置；v4 加深接触位置，最终两次复测都在 845 步成功。正式仅布局 4 在 972 步成功。失败布局 1/3/5 记录 detection missing，布局 3 另有 lift check failed；布局 2 在 996 步仍处于 place，动作误差约 3.9 cm。成功证明视觉—动作闭环与程序修正能形成可执行技能，失败同时暴露感知稳定性、运动跟踪和 1000 步内恢复的限制。

[证据与全过程](../runtime/robowits-report/cases/robowits_03/process.md)

### 04 Pinch Card：薄卡片获取的接触前提没有覆盖全部布局

最终两次原始场景复测均在 726 步成功，正式布局 4/5 分别在 653/721 步成功。失败布局 1 在 291 步报告 pose tracking stalled；布局 2/3 分别在 314/296 步报告 card not in overhang after push，随后进入 safe_stop 直到 1000 步。因此终止时耗尽步数是结果，最早的明确失效点发生在 acquire 阶段。需要验证不同摆放下推卡悬出、可达性和重新推卡策略，而非简单增加等待步数。

[证据与全过程](../runtime/robowits-report/cases/robowits_04/process.md)

### 06 Dominos：成功案例：从目标反推可传递接触的多米诺链

agent 在原始场景发现直接触及远处目标不可靠，编写 RGB-D 颜色/高度检测，从红色目标反向寻找相连白块并选择推点。开发记录包含首推无效后撤回、重新感知剩余链条并继续推的恢复过程；还测试了另一只手和感知故障。正式五场全部成功，仅用 102–104 步。视频可见近处白块受推并传递到远端。空间关系驱动的策略与较大步数余量是有证据支持的有利因素；5/5 仍不是对所有布局的成功保证，也不能把原始场景 401 步和不同正式布局约 103 步的差异归因于后续学习。

[证据与全过程](../runtime/robowits-report/cases/robowits_06/process.md)

### 09 Hold Cup：预算调度失败与追加探索应分开评价

首轮 8 次交互探索中，前 7 次失败；最后一次通过在杯子下坡边缘布置垫片、保持杯子直立，于 786 步获得 native success。但没有给已编写的自动程序留下复测回合，未提交、未正式评测。失败是完整探索—编程—验证流程未闭合，并不等于五个正式布局实测失败。追加轮继承代码、记忆和对话，再给 8 次探索；它不是一次从零开始的 8 回合实验，也不是独立重复样本。

[证据与全过程](../runtime/robowits-report/cases/robowits_09/process.md)

### 13 Cover with Lid：失败案例：日志序列化错误打断了真实搬运

探索阶段长期受滑落、盖子定位与姿态约束影响。最后一次程序复测虽正常退出，但 native success 为 false。其 proof_lift 已成功，却在 carry 中因 numpy.bool 无法被 JSON fallback 序列化触发 TypeError，外层捕获后 retreat 并放开物体。五场正式日志全部出现同类错误：第 1/2/4/5 场均在 243、486、729 步记录 numpy.bool；第 3 场在 614、857 步记录该错误。提交门槛只要求完整、无未处理运行错误的复测，并不要求任务成功，因此这个有已知缺陷的程序仍进入正式评测，最终 0/5。这不是证明物理任务不可解；应修复日志类型处理、覆盖成功持物分支，并在新实验中重新复测。

[证据与全过程](../runtime/robowits-report/cases/robowits_13/process.md)

### 16 Stand Bulb：固定场景稳定站立没有保证新布局成功

两次最终复测均在 730 步成功，正式只有第 2 场在 729 步成功。其他四场全部到 1000 步，末段均有 settle 等待。可以确认程序未在预算内形成原生判定所需状态；仅凭末段日志不能确定是倾倒、摆放偏差、抓取丢失还是目标条件未满足。完整视频和逐步日志已附，根因仍需逐场几何/接触复核，不把“等到超时”冒充物理原因。

[证据与全过程](../runtime/robowits-report/cases/robowits_16/process.md)

### 17 Ball onto Tower：成功与失败对照：闭环对齐有效，释放后的稳定性仍脆弱

agent 从突变目标造成丢球、突然张爪造成弹飞的探索经历中，加入平滑搬运、视觉球心定位、对齐误差反馈以及连续张爪斜坡。最终版本复测 572 步成功；正式前三场在 496/514/517 步成功。第 1 场对齐日志末次三轴误差约 0.23/0.20/0.28 mm，但这是程序估计而非独立真值。第 4 场到 settle 后球不可见，576 步开始恢复且多次重找失败；录像抽帧显示球搬到塔顶后离开原位置。第 5 场起始检测失败，恢复后 783 步报告 released ball not yet successful。故成功的对齐机制并未完全解决新接触条件下释放和再获取的问题。

[证据与全过程](../runtime/robowits-report/cases/robowits_17/process.md)

### 25 Water into Mug：失败案例：原始场景的“保持不动”捷径不能迁移

最终程序只读机器人 14D 状态并重复保持姿态，没有根据水流和杯子位置重新规划。原始场景的五次冻结复测全部在 150–155 步成功；正式五场却全部保持到 1000 步失败，日志未显示程序异常。录像和代码共同支持“程序未主动调整接水位置”这一事实。源场景允许低动作策略成功，因而重复复测没有暴露该捷径；这是探索分布与正式布局差异的明确警示。至于具体流体落点、接触条件差异，需额外实验验证，不能从 0/5 单独推断仿真错误。

[证据与全过程](../runtime/robowits-report/cases/robowits_25/process.md)

### 09 续跑：完成程序验证，但泛化仍受感知与抓取限制

追加八回合首先复测已有 v1，程序运行完整但任务失败：抓持宽度正常并不代表杯身直立，RGB-D 杯口拟合发现搬运后约 12° 倾斜。agent 随后加入杯口法向反馈，将一次开发试验中的倾角降至约 1.8°，v2 在 894 步复测成功。一次人为扰动（由 agent 动作完成）耗用 90 步后失败；v3 缩短空手观察与接近，保留载物和接触动作时长，为静置留出余量。最终确切 bundle 两次复测均在 842 步成功，追加轮所有版本共 4/5 次复测成功；正式仅第 5 场在 933 步成功。第 1/3 场在第 0 步就未定位到垫片或直立杯口，第 2 场抓垫片时两次出现 3.2 cm 运动跟踪误差，第 4 场搬运时夹爪开度检查表明抓持不再可靠。这些异常触发状态检查后等待至步数上限，程序没有针对这些失效重新搜索、换视角或重抓的完整恢复流程。因此四场失败不是单纯多给几十步就能解决；应分别验证新布局下的视觉覆盖、接近可达性和持物恢复。续跑证明已有经验可以继续转化为可执行程序，但单一场景的更多探索没有消除分布差异；前后变化也不能作为纯预算消融。

| 09 正式场次 | 首次明确问题 | 最终结果 |
| --- | --- | --- |
| 1 | 第 0 步：Brown disk not localized，未定位到垫片 | 失败，1000 步 |
| 2 | 160 / 305 步：抓垫片时 Cartesian tracking error 0.032 m | 失败，1000 步 |
| 3 | 第 0 步：Upright cup rim not localized，未定位到杯口 | 失败，1000 步 |
| 4 | 325 步：夹爪开度检查不再符合可靠的平放抓持 | 失败，1000 步 |
| 5 | 820 步进入静置；933 步触发原生成功 | 成功，933 步 |

前四场异常后进入状态检查及等待，不能把最终“达到步数上限”当成最初的失败原因。

## 6. 跨任务分析

### 6.1 成功来自哪些能力

06 和 17 的程序把当前观测转换为物体空间关系，再据此决定动作。06 从远端目标反推相连白块，并在首推无效后重新观察；17 从丢球经历中加入平滑搬运、视觉对齐与渐进释放。03 的实际滑落促成抓取深度调整；09 的杯口倾斜促成姿态反馈。这些记录说明，探索经验确实转化成了程序修改，而非仅保存一段成功轨迹。

但上述机制的独立贡献没有经过消融。17、03 和 09 的正式失败也说明，存在反馈与恢复逻辑并不等于它覆盖了全部新布局。

### 6.2 失败应分层分析

| 失效层面 | 代表任务 | 已确认的证据 | 对应改进方向 |
| --- | --- | --- | --- |
| 预算与验证流程 | 09 首轮 | 第 8 回合才交互成功，未留下程序复测机会 | 为复测和修正预留回合 |
| 软件正确性 | 13 | 五个正式回合均出现 numpy.bool 序列化错误 | 修复日志类型处理并重新复测，作为新实验 |
| 探索分布与捷径 | 25 | 原始场景保持姿态复测 5/5，正式 0/5 | 加入无动作基线及与测试隔离的多样训练布局 |
| 感知覆盖 | 03、09、17 | 新布局中物体检测缺失或释放后目标不可见 | 验证换视角、再检测和物体再获取 |
| 运动与接触 | 04、09、17 | 运动跟踪停滞、推卡未悬出、持物或释放后检查失败 | 验证可达性、接触前提和针对性恢复 |
| 根因证据不足 | 部分 01、02、16 回合 | 末段搜索、重试或静置到上限 | 逐场复核视频和几何/接触信息，不用超时替代根因 |

### 6.3 固定场景复测与泛化之间的落差

25 的 5/5 复测对应 0/5 正式结果；02 的 1/1 对应 0/5；04 的 2/2 对应 2/5；09 续跑的 2/2 对应 1/5。重复相同初始场景更接近检查程序可重复运行，不能替代不同位置、遮挡和接触条件下的覆盖测试。增加相同场景的探索可帮助完成编程流程，也可能强化只在该场景有效的捷径。

### 6.4 回合预算与回合内步数预算

09 首轮的问题是探索回合不足以完成验证流程；03 部分开发回合则是滑落后重抓占用了剩余控制步，来不及完成放置。两者都可能表现为预算耗尽，但修复方法不同。对于在第 0 步感知失败后持续等待的 09 正式回合，单纯增加控制步数并不能修复感知。

## 7. 局限与结论边界

1. 每个任务只有一次探索实验和五个正式布局，没有重复探索种子；0/5 不代表永远不能成功，5/5 不代表普遍可靠。
2. 在独立二项假设下，0/5 和 5/5 的 Wilson 95% 区间约为 0–43.4% 和 56.6–100%；共享控制程序和固定布局也限制了独立性假设。
3. 没有同预算基线、算法消融或全部 50 个布局评测，不能据此声称优于其他机器人方法，也不能将结果当作论文数值复现。
4. 09 追加轮继承已有知识，同时改变预算与程序版本；不是纯预算消融，也不是独立样本。
5. Agent 笔记属于过程证据，其中部分原因判断是待验证的假设；正式成功采用原生判定，不凭视觉印象替代。
6. 运行耗时及日志中的 token 数不等于 API 费用；未统一缓存、重试和推理计量口径，因此不估算美元成本。

## 8. 建议的后续实验

以下为后续方案，本报告没有把它们描述为已经实施：

- 固定正式布局，比较 8 与 16 回合探索，并对多个探索种子重复。
- 比较重复原始场景与独立生成的多样训练布局；训练来源必须与正式 JSON 隔离。
- 为最终程序复测、失败修复及修正后复测预留预算。
- 明确区分“运行合格提交”与“任务成功验证”，保留失败候选时单独标记。
- 修复 13 的序列化缺陷后重新实验，不覆盖现有结果。
- 为 25 添加无动作基线；为感知和持物失效添加针对性的恢复试验。
- 持续记录首次失效阶段、剩余步数和恢复结果，避免只报告最终超时。

## 9. 文件与复现信息

- [机器可读结果 JSON](../runtime/robowits-report/results.json)
- [结果表 CSV](../runtime/robowits-report/results.csv)
- [最终报告 PDF](../runtime/robowits-report/report.pdf)
- [报告验证记录](../runtime/robowits-report/validation.json)
- [媒体有效性检查](../runtime/robowits-report/media-validation.json)
- [Markdown 生成脚本](../scripts/build_robowits_markdown.py)
- [原始报告生成脚本](../scripts/build_robowits_report.py)

完整证据位于 `runtime/robowits-report/cases/`。每个任务目录保存配置、成绩、逐回合录像与程序输出、代码和笔记；原始运行路径记录在 `provenance.json`。本 Markdown 使用相对路径链接这些文件。单独拷贝 Markdown 不会携带视频，迁移时须一并保留报告证据目录及相对目录结构。

重新生成 Markdown：

```bash
cd /home/junwenmiao/code/rax
python scripts/build_robowits_markdown.py
```

方法来源：[RAX 项目](https://robot-agent-exploration.com/) · [原始实现](https://github.com/robot-agent-exploration/rax)。本报告实验数值来自本地记录。


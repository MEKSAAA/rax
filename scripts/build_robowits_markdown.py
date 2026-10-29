"""Export the audited RoboWits report and evidence indexes as Markdown."""
import json
import os
from pathlib import Path
from build_robowits_report import ANALYSIS, CONTINUATION_ANALYSIS, NAMES, EXPERIMENT_OVERVIEW

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / 'runtime/robowits-report'
DEST = ROOT / 'docs/ROBOWITS_EXPERIMENT_REPORT.md'


def read(path):
    return json.loads(path.read_text())


def link(label, path, base=DEST.parent):
    return f'[{label}]({os.path.relpath(path, base).replace(os.sep, "/")})'


def table(headers, rows):
    def cell(value):
        return str(value).replace('|', '\\|').replace('\n', ' ')
    return '\n'.join(['| ' + ' | '.join(headers) + ' |',
                      '| ' + ' | '.join(['---'] * len(headers)) + ' |'] +
                     ['| ' + ' | '.join(map(cell, row)) + ' |' for row in rows])


def case_index(row):
    folder = REPORT / 'cases' / row['key']
    episodes = read(folder / 'episodes.json')
    events = read(folder / 'timeline.json')
    title = f'{row["task"][-2:]} {row["name"]}'
    if row['key'].endswith('continued'):
        title += '（追加探索）'
    s = [f'# {title}：全过程记录', '',
         '视频按运行顺序列出，保留探索失败、提前结束、程序复测与正式评测。交互日志中的 manual 指模型逐步调用机器人接口，不是人类遥操作。', '',
         '公开说明与视频时间采用不同尺度：前者是墙钟时间，后者是仿真录像时间，不能直接按秒对应。正式评测由冻结程序执行，不存在每一步的模型分析。', '',
         '## 回合、视频与程序输出', '']
    rows = []
    for e in episodes:
        assets = [link(k, folder/e[k], folder) for k in ('video', 'stdout', 'stderr', 'outcome') if e.get(k)]
        rows.append([e['mode'], e['id'], e['steps'], e['status_label'], ' · '.join(assets)])
    s += [table(['阶段', '回合 ID', '控制步数', '结果', '证据'], rows), '',
          '## Agent 公开分析说明', '',
          '以下为日志中对外输出的说明，可能包含当时尚未验证的判断；正式结果以原生评测记录为准，不包含模型内部隐藏推理。追加轮继承了原对话，因此可能包含此前的公开说明。', '']
    for e in events:
        if e['kind'] == 'public':
            s += [f'### {e["time"]}', '', '```text', str(e['detail']), '```', '']
    s += ['## 工具调用与代码修改', '',
          '[完整结构化时间线](timeline.json) 包含机器人请求、反馈和编程工具调用。下面按时间顺序展开请求与代码修改；响应详情保存在结构化时间线中，程序阶段输出见逐回合 stdout。', '']
    for e in events:
        is_request = e['kind'] == 'robot' and isinstance(e['detail'], dict) and e['detail'].get('event') == 'mcp_request'
        if e['kind'] == 'coding' or is_request:
            s += [f'### {e["time"]} · {e["label"]}', '', '```json',
                  json.dumps(e['detail'], ensure_ascii=False, indent=2), '```', '']
    s += ['## 程序和实验笔记', '']
    for part in ['code', 'memory']:
        for f in sorted((folder/part).rglob('*')):
            if f.is_file():
                s += ['- '+link(str(f.relative_to(folder)), f, folder)]
    (folder/'process.md').write_text('\n'.join(s)+'\n')
    return episodes


def main():
    data = read(REPORT/'results.json')
    baseline = data['baseline']
    continuation = data['continuation']
    all_rows = baseline + ([continuation] if continuation else [])
    episodes = {r['key']:case_index(r) for r in all_rows}
    latest = [continuation if r['task']=='robowits_09' and continuation and continuation['formal_complete'] else r for r in baseline]
    s = ['# RAX 在 RoboWits 上的实验报告', '',
         '本报告对应 2026 年 10 月 28–29 日的实验。内容来自本地配置、原生评测结果、模型公开输出、控制程序日志与录像。', '',
         '## 阅读前提：任务范围、框架与 API', '',
         '**本实验使用 RAX 的 Codex CLI 执行框架，通过 ChatGPT 登录调用 gpt-6-astra；评测对象全部是 RoboWits seed 基础任务，每任务仅测试 5 个官方布局。**', '',
         table(['项目','本次实验实际配置'],EXPERIMENT_OVERVIEW), '',
         '上述信息描述已执行实验。后续若新增或更换模型、调用服务或 API，先与用户确认后执行；报告不包含账号凭据。', '',
         '## 1. 主要结果', '',
         '**十个任务均已取得正式评测结果，纳入 09 续跑后的合计成绩为 15/50，成功率 30%。** 其中 09 使用累计 16 次探索，其余任务使用 8 次，因此这是混合预算汇总。', '',
         '原始统一 8 回合预算下，09 未完成程序提交；其余九个任务的实测成绩为 **14/45（31.1%）**。如果固定十任务共 50 场的分母、将 09 缺失场次计零，则为 **28%**，但必须同时注明缺失，不应称为 09 实测 0/5。', '',
         '这些结果仅对应所选十任务、每任务五个正式布局，不是 RoboWits 全任务全集成绩，也不是 RAX 论文原基准的数值复现。', '',
         '## 2. 实验设置', '']
    settings = [
        ('任务集','01、02、03、04、06、09、13、16、17、25，即 README 所选十任务。'),
        ('方法','冻结编程大模型通过机器人接口探索，把经验写成代码与记忆；提交后由冻结程序执行正式任务，不训练模型权重。'),
        ('模型','gpt-6-astra，xhigh；Codex CLI 0.164，ChatGPT 登录调用。'),
        ('感知','ego、右腕、左腕三视角 RGB-D，848×480，附标定和机器人状态；不使用外部 Gemini 感知。'),
        ('动作','14D 双臂绝对末端位姿与夹爪宽度；底层控制和逆运动学由环境提供。'),
        ('探索场景','collection=2，使用 RoboWits 源码定义的原始场景，初始布局重复；探索不读取正式布局。Agent 可用动作自行扰动开发场景。'),
        ('探索预算','原始每任务 8 回合；交互探索、程序开发与冻结程序复测共用。09 续跑追加 8 回合，总计 16。'),
        ('单回合限制','最多 1000 个 native 控制步，control_dt=0.0333 秒；另有 7200 秒墙钟上限。回合数和步数是两种不同预算。'),
        ('提交资格','确切版本须有一次完整、无未处理运行错误的复测；干净退出不等于任务成功，13 是典型例子。'),
        ('正式评测','官方 collection=1，layout_id=0..4，布局种子 42，每任务五场；按 native is_success 判定。'),
        ('冻结协议','正式期间不依据测试结果修改程序，也不让大模型决定每一步动作。'),
        ('环境','RoboWits-genesis131，Genesis 1.3.1；仿真 Python 3.11，host Python 3.12。'),
        ('硬件','GPU 1：RTX 6000 Ada 48 GB；后五任务最多两个任务并行。'),
        ('数据版本','RoboWits 源码提交 b3fb06568b6a76d4ee371ff25fb2318336c012b5；逐运行配置与已有哈希保存在证据目录。'),
        ('协议修正','本报告仅纳入原始场景协议与纹理加载修正后的运行；早期随机重置实验不混入本表。'),
        ('辅助指标','平均最高原生奖励反映任务内进展，不是成功率，不宜跨任务当成同一种物理尺度。')]
    s += [table(['项目','设置'],settings),'',
          '8 回合是本次实验配置，不是 RAX 方法固有限制。任务程序由实验 agent 生成，未由操作者预先手写任务解法。', '',
          '## 3. 实验结果表', '',
          '### 3.1 最新结果（09 为追加预算）', '',
          '复测列只统计最终提交的确切版本；同一原始场景的重复复测不能当作独立布局测试。', '']
    rows=[]
    for r in latest:
        folder=REPORT/'cases'/r['key']
        rows.append([link(r['task'][-2:]+' '+r['name'],folder/'process.md'),
                     '16（8+8）' if r['key'].endswith('continued') else '8',
                     f"{r['final_bundle_rehearsal_successes']}/{r['final_bundle_rehearsals']}",
                     f"{int(r['formal_successes'])}/5",f"{r['formal_successes']/5:.0%}",
                     f"{r['avg_max_reward']:.3f}" if r['avg_max_reward'] is not None else '—'])
    s += [table(['任务 / Markdown 过程记录','探索回合','最终版本复测','正式成功','成功率','平均最高奖励'],rows),'',
          '### 3.2 09 原始预算与续跑对照', '',
          table(['运行','累计探索回合','程序提交','正式结果'],[
              ['首轮','8','未提交：最后一次交互才成功，没有剩余复测回合','未执行，不能称为实测 0/5'],
              ['续跑','16','已提交：确切最终版本复测 2/2 成功','1/5，20%']]),'',
          '续跑继承首轮代码、记忆与对话；不是从零开始的新 8 回合实验，也不是一次独立重复。原始记录保留，不用续跑结果覆盖。', '',
          '### 3.3 每个正式布局的结果与步数', '',
          '表格中的编号是第 1–5 场，对应 layout_id=0..4。', '']
    detail=[]
    for r in latest:
        scores=read(REPORT/'cases'/r['key']/'formal-scores.json')
        detail.append([r['task'][-2:]+' '+r['name']]+[
            ('成功' if e['score'] else '失败')+f" / {e['step_id']} 步" for e in scores['episodes']])
    s += [table(['任务','第 1 场','第 2 场','第 3 场','第 4 场','第 5 场'],detail),'',
          '### 3.4 运行时间', '',
          '耗时含模型等待、探索、编程与仿真；并行运行和场景开销不同，因此不能当作模型速度基准。', '']
    s += [table(['任务','记录的运行时间（分钟）'],[
        [r['task'][-2:]+' '+r['name']+('（追加轮）' if r['key'].endswith('continued') else ''),
         f"{r['duration_minutes']:.1f}" if r['duration_minutes'] is not None else '原批次采用已有运行，未据此重算']
        for r in all_rows]),'',
          '## 4. 成功与失败 Demo', '',
          '以下链接直接打开视频或 Markdown 过程记录，无需使用 HTML 报告。完整过程记录包含全部已保存回合、agent 公开分析、编程工具调用、机器人请求、控制程序和笔记。工具响应详情另附 JSON 时间线。', '',
          '公开分析不包含隐藏内部推理；原文中的推测须与原生评测结果区分。录像时间是仿真播放时间，不能直接对齐模型分析的墙钟时间。', '']
    for key,label in [
        ('robowits_06','成功案例：多米诺链式推动'),
        ('robowits_17','成功与失败对照：球放塔顶'),
        ('robowits_13','软件错误案例：盖子搬运'),
        ('robowits_25','泛化失败案例：接水'),
        ('robowits_03','试错与修正案例：柠檬取出'),
        ('robowits_09_continued','追加探索案例：斜坡放杯')]:
        folder=REPORT/'cases'/key
        s += ['### '+label,'',link('完整 Markdown 过程记录',folder/'process.md'),'']
        for success,name in [(True,'成功正式录像'),(False,'失败正式录像')]:
            ep=next((e for e in episodes[key] if e['mode']=='formal' and e['success'] is success and e.get('video')),None)
            if ep:
                s += ['- '+link(name,folder/ep['video'])+f"：{ep['steps']} 步，回合 `{ep['id']}`。"]
        s += ['- '+link('工具调用与反馈时间线',folder/'timeline.json'),
              '- '+link('逐回合失败证据',folder/'failure-audit.json'),'']
    s += ['## 5. 逐任务实验分析','']
    for tid,(title,body) in ANALYSIS.items():
        s += [f'### {tid} {NAMES[tid]}：{title}','',body,'',
              link('证据与全过程',REPORT/'cases'/('robowits_'+tid)/'process.md'),'']
    s += ['### '+CONTINUATION_ANALYSIS[0],'',CONTINUATION_ANALYSIS[1],'',
          table(['09 正式场次','首次明确问题','最终结果'],[
              ['1','第 0 步：Brown disk not localized，未定位到垫片','失败，1000 步'],
              ['2','160 / 305 步：抓垫片时 Cartesian tracking error 0.032 m','失败，1000 步'],
              ['3','第 0 步：Upright cup rim not localized，未定位到杯口','失败，1000 步'],
              ['4','325 步：夹爪开度检查不再符合可靠的平放抓持','失败，1000 步'],
              ['5','820 步进入静置；933 步触发原生成功','成功，933 步']]),'',
          '前四场异常后进入状态检查及等待，不能把最终“达到步数上限”当成最初的失败原因。', '',
          '## 6. 跨任务分析', '',
          '### 6.1 成功来自哪些能力', '',
          '06 和 17 的程序把当前观测转换为物体空间关系，再据此决定动作。06 从远端目标反推相连白块，并在首推无效后重新观察；17 从丢球经历中加入平滑搬运、视觉对齐与渐进释放。03 的实际滑落促成抓取深度调整；09 的杯口倾斜促成姿态反馈。这些记录说明，探索经验确实转化成了程序修改，而非仅保存一段成功轨迹。', '',
          '但上述机制的独立贡献没有经过消融。17、03 和 09 的正式失败也说明，存在反馈与恢复逻辑并不等于它覆盖了全部新布局。', '',
          '### 6.2 失败应分层分析', '',
          table(['失效层面','代表任务','已确认的证据','对应改进方向'],[
              ['预算与验证流程','09 首轮','第 8 回合才交互成功，未留下程序复测机会','为复测和修正预留回合'],
              ['软件正确性','13','五个正式回合均出现 numpy.bool 序列化错误','修复日志类型处理并重新复测，作为新实验'],
              ['探索分布与捷径','25','原始场景保持姿态复测 5/5，正式 0/5','加入无动作基线及与测试隔离的多样训练布局'],
              ['感知覆盖','03、09、17','新布局中物体检测缺失或释放后目标不可见','验证换视角、再检测和物体再获取'],
              ['运动与接触','04、09、17','运动跟踪停滞、推卡未悬出、持物或释放后检查失败','验证可达性、接触前提和针对性恢复'],
              ['根因证据不足','部分 01、02、16 回合','末段搜索、重试或静置到上限','逐场复核视频和几何/接触信息，不用超时替代根因']]),'',
          '### 6.3 固定场景复测与泛化之间的落差', '',
          '25 的 5/5 复测对应 0/5 正式结果；02 的 1/1 对应 0/5；04 的 2/2 对应 2/5；09 续跑的 2/2 对应 1/5。重复相同初始场景更接近检查程序可重复运行，不能替代不同位置、遮挡和接触条件下的覆盖测试。增加相同场景的探索可帮助完成编程流程，也可能强化只在该场景有效的捷径。', '',
          '### 6.4 回合预算与回合内步数预算', '',
          '09 首轮的问题是探索回合不足以完成验证流程；03 部分开发回合则是滑落后重抓占用了剩余控制步，来不及完成放置。两者都可能表现为预算耗尽，但修复方法不同。对于在第 0 步感知失败后持续等待的 09 正式回合，单纯增加控制步数并不能修复感知。', '',
          '## 7. 局限与结论边界', '',
          '1. 每个任务只有一次探索实验和五个正式布局，没有重复探索种子；0/5 不代表永远不能成功，5/5 不代表普遍可靠。',
          '2. 在独立二项假设下，0/5 和 5/5 的 Wilson 95% 区间约为 0–43.4% 和 56.6–100%；共享控制程序和固定布局也限制了独立性假设。',
          '3. 没有同预算基线、算法消融或全部 50 个布局评测，不能据此声称优于其他机器人方法，也不能将结果当作论文数值复现。',
          '4. 09 追加轮继承已有知识，同时改变预算与程序版本；不是纯预算消融，也不是独立样本。',
          '5. Agent 笔记属于过程证据，其中部分原因判断是待验证的假设；正式成功采用原生判定，不凭视觉印象替代。',
          '6. 运行耗时及日志中的 token 数不等于 API 费用；未统一缓存、重试和推理计量口径，因此不估算美元成本。', '',
          '## 8. 建议的后续实验', '',
          '以下为后续方案，本报告没有把它们描述为已经实施：', '',
          '- 固定正式布局，比较 8 与 16 回合探索，并对多个探索种子重复。',
          '- 比较重复原始场景与独立生成的多样训练布局；训练来源必须与正式 JSON 隔离。',
          '- 为最终程序复测、失败修复及修正后复测预留预算。',
          '- 明确区分“运行合格提交”与“任务成功验证”，保留失败候选时单独标记。',
          '- 修复 13 的序列化缺陷后重新实验，不覆盖现有结果。',
          '- 为 25 添加无动作基线；为感知和持物失效添加针对性的恢复试验。',
          '- 持续记录首次失效阶段、剩余步数和恢复结果，避免只报告最终超时。', '',
          '## 9. 文件与复现信息', '',
          '- '+link('机器可读结果 JSON',REPORT/'results.json'),
          '- '+link('结果表 CSV',REPORT/'results.csv'),
          '- '+link('最终报告 PDF',REPORT/'report.pdf'),
          '- '+link('报告验证记录',REPORT/'validation.json'),
          '- '+link('媒体有效性检查',REPORT/'media-validation.json'),
          '- '+link('Markdown 生成脚本',ROOT/'scripts/build_robowits_markdown.py'),
          '- '+link('原始报告生成脚本',ROOT/'scripts/build_robowits_report.py'),'',
          '完整证据位于 `runtime/robowits-report/cases/`。每个任务目录保存配置、成绩、逐回合录像与程序输出、代码和笔记；原始运行路径记录在 `provenance.json`。本 Markdown 使用相对路径链接这些文件。单独拷贝 Markdown 不会携带视频，迁移时须一并保留报告证据目录及相对目录结构。', '',
          '重新生成 Markdown：', '', '```bash',
          'cd /home/junwenmiao/code/rax',
          'python scripts/build_robowits_markdown.py', '```','',
          '方法来源：[RAX 项目](https://robot-agent-exploration.com/) · [原始实现](https://github.com/robot-agent-exploration/rax)。本报告实验数值来自本地记录。', '']
    DEST.parent.mkdir(parents=True,exist_ok=True)
    DEST.write_text('\n'.join(s)+'\n')
    print(DEST)


if __name__ == '__main__':
    main()

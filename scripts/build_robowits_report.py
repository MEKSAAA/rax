"""Build a local, portable evidence report; never changes experiments or policies.

Only public assistant commentary/final messages and tool calls are exported from
CLI transcripts. Reasoning records, system/developer prompts and credentials are
not exported. Images in robot traces are represented by metadata; full videos
remain separate playable artifacts. Run again after a continuation finishes.
"""
import argparse
import csv
from datetime import datetime, timezone
import html
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
BATCHES = ['robowits10-canonical-eval', 'robowits03-canonical-eval',
           'robowits04-canonical-eval', 'robowits-remaining5-canonical-eval',
           'robowits-remaining5-lane-b-canonical-eval', 'robowits09-continued16-canonical-eval']
NAMES = dict(zip('01 02 03 04 06 09 13 16 17 25'.split(),
    ['Align Blocks', 'Retrieve Cube', 'Gap Retrieve', 'Pinch Card', 'Dominos',
     'Hold Cup', 'Cover with Lid', 'Stand Bulb', 'Ball onto Tower', 'Water into Mug']))
EXPERIMENT_OVERVIEW = [
    ('任务类型', '全部为 RoboWits seed 基础任务，mutation=False；没有评测 mutation 变体。Seed task 是任务类别，不是随机数种子。'),
    ('任务范围', 'README 的 robowits-10：01、02、03、04、06、09、13、16、17、25。'),
    ('评测规模', '官方 seed 评测为每任务 50 个布局；本实验仅测其中 5 个（layout_id=0..4），来自 eval_dataset_seed。因此是小规模子集评测，不是完整官方规模。'),
    ('探索与测试', '探索重复源码原始场景（collection=2）；正式测试使用官方独立布局（collection=1），两者仍属于同一个 seed task。'),
    ('Agent 执行框架', 'RAX 的 Codex CLI harness，运行 OpenAI Codex CLI 0.164；RAX 提供机器人 MCP 接口、回合管理、程序冻结和评测流程。'),
    ('模型与调用方式', 'gpt-6-astra，reasoning effort=xhigh；使用 profile=chatgpt，经 Codex CLI 的 ChatGPT 账号登录链路调用 OpenAI 托管模型，不是本地模型推理。'),
    ('API / 认证', '本次模型调用使用 ChatGPT 登录认证，不使用独立 OpenAI API key 或 OpenRouter 模型调用；外部 Gemini 感知关闭。仍存在在线模型请求，不能称作“不消耗模型调用”；不据此虚构按 API token 计费的美元成本。'),
    ('机器人接口', '本地 MCP 工具提供 RGB-D 观察、像素到空间定位、状态查询和双臂动作（如 robowits_observe、robowits_pixel_to_position、robowits_status、robowits_step），连接本地 Genesis 仿真。它们与云端模型调用是不同层的接口。'),
    ('何时调用模型', '探索、分析、编程与调试阶段调用模型；本次正式评测运行冻结 Python 控制程序，不再让模型逐步决定动作，也未启用外部感知模型。'),
    ('预算口径', '九个任务使用 8 次探索，09 首轮 8 次后追加 8 次。最终 15/50（30%）是混合预算汇总；原始统一预算记录另行保留。'),
]
CONTINUATION_ANALYSIS = ('09 续跑：完成程序验证，但泛化仍受感知与抓取限制',
    '追加八回合首先复测已有 v1，程序运行完整但任务失败：抓持宽度正常并不代表杯身直立，RGB-D 杯口拟合发现搬运后约 12° 倾斜。'
    'agent 随后加入杯口法向反馈，将一次开发试验中的倾角降至约 1.8°，v2 在 894 步复测成功。'
    '一次人为扰动（由 agent 动作完成）耗用 90 步后失败；v3 缩短空手观察与接近，保留载物和接触动作时长，为静置留出余量。'
    '最终确切 bundle 两次复测均在 842 步成功，追加轮所有版本共 4/5 次复测成功；正式仅第 5 场在 933 步成功。'
    '第 1/3 场在第 0 步就未定位到垫片或直立杯口，第 2 场抓垫片时两次出现 3.2 cm 运动跟踪误差，第 4 场搬运时夹爪开度检查表明抓持不再可靠。'
    '这些异常触发状态检查后等待至步数上限，程序没有针对这些失效重新搜索、换视角或重抓的完整恢复流程。'
    '因此四场失败不是单纯多给几十步就能解决；应分别验证新布局下的视觉覆盖、接近可达性和持物恢复。'
    '续跑证明已有经验可以继续转化为可执行程序，但单一场景的更多探索没有消除分布差异；前后变化也不能作为纯预算消融。')
ANALYSIS = {
'01': ('感知反馈与重试有效，但仍有布局依赖',
       '程序根据当前 RGB-D 定位蓝色方块，选择双臂或顺序搬运，并在遮挡后重新观察。最终版本原始场景复测两次成功；正式布局仅两场成功。失败日志出现多轮 approach、view_clear_check 和 recovery-limit 后等待到上限。可以确认恢复未能完成任务，尚不能把三场失败都归结为同一种视觉或接触错误。探索中的人工扰动测试是 agent 自己调用动作准备的开发场景，并非人类示范。'),
'02': ('双臂约束方案在单一布局成立，正式迁移失败',
       '探索记录显示单臂转腕时容器会在夹爪中转动；最终改为分别夹持容器口沿和下部，以两个接触点约束翻转，最后一次复测在 636 步成功。正式五场全失败：日志既有容器/方块未检出后的搜索，也有执行双臂翻转和 park_cup 后仍未终止。证据支持策略迁移与恢复不足，但不能据此断定五场都是同一种打滑。只有一次最终版本成功复测，覆盖面有限。'),
'03': ('真实滑落推动了程序修正，但恢复余量不足',
       '开发 v2/v3 的滑落触发重抓，重抓后已无足够步数完成放置；v4 加深接触位置，最终两次复测都在 845 步成功。正式仅布局 4 在 972 步成功。失败布局 1/3/5 记录 detection missing，布局 3 另有 lift check failed；布局 2 在 996 步仍处于 place，动作误差约 3.9 cm。成功证明视觉—动作闭环与程序修正能形成可执行技能，失败同时暴露感知稳定性、运动跟踪和 1000 步内恢复的限制。'),
'04': ('薄卡片获取的接触前提没有覆盖全部布局',
       '最终两次原始场景复测均在 726 步成功，正式布局 4/5 分别在 653/721 步成功。失败布局 1 在 291 步报告 pose tracking stalled；布局 2/3 分别在 314/296 步报告 card not in overhang after push，随后进入 safe_stop 直到 1000 步。因此终止时耗尽步数是结果，最早的明确失效点发生在 acquire 阶段。需要验证不同摆放下推卡悬出、可达性和重新推卡策略，而非简单增加等待步数。'),
'06': ('成功案例：从目标反推可传递接触的多米诺链',
       'agent 在原始场景发现直接触及远处目标不可靠，编写 RGB-D 颜色/高度检测，从红色目标反向寻找相连白块并选择推点。开发记录包含首推无效后撤回、重新感知剩余链条并继续推的恢复过程；还测试了另一只手和感知故障。正式五场全部成功，仅用 102–104 步。视频可见近处白块受推并传递到远端。空间关系驱动的策略与较大步数余量是有证据支持的有利因素；5/5 仍不是对所有布局的成功保证，也不能把原始场景 401 步和不同正式布局约 103 步的差异归因于后续学习。'),
'09': ('预算调度失败与追加探索应分开评价',
       '首轮 8 次交互探索中，前 7 次失败；最后一次通过在杯子下坡边缘布置垫片、保持杯子直立，于 786 步获得 native success。但没有给已编写的自动程序留下复测回合，未提交、未正式评测。失败是完整探索—编程—验证流程未闭合，并不等于五个正式布局实测失败。追加轮继承代码、记忆和对话，再给 8 次探索；它不是一次从零开始的 8 回合实验，也不是独立重复样本。'),
'13': ('失败案例：日志序列化错误打断了真实搬运',
       '探索阶段长期受滑落、盖子定位与姿态约束影响。最后一次程序复测虽正常退出，但 native success 为 false。其 proof_lift 已成功，却在 carry 中因 numpy.bool 无法被 JSON fallback 序列化触发 TypeError，外层捕获后 retreat 并放开物体。五场正式日志全部出现同类错误：第 1/2/4/5 场均在 243、486、729 步记录 numpy.bool；第 3 场在 614、857 步记录该错误。提交门槛只要求完整、无未处理运行错误的复测，并不要求任务成功，因此这个有已知缺陷的程序仍进入正式评测，最终 0/5。这不是证明物理任务不可解；应修复日志类型处理、覆盖成功持物分支，并在新实验中重新复测。'),
'16': ('固定场景稳定站立没有保证新布局成功',
       '两次最终复测均在 730 步成功，正式只有第 2 场在 729 步成功。其他四场全部到 1000 步，末段均有 settle 等待。可以确认程序未在预算内形成原生判定所需状态；仅凭末段日志不能确定是倾倒、摆放偏差、抓取丢失还是目标条件未满足。完整视频和逐步日志已附，根因仍需逐场几何/接触复核，不把“等到超时”冒充物理原因。'),
'17': ('成功与失败对照：闭环对齐有效，释放后的稳定性仍脆弱',
       'agent 从突变目标造成丢球、突然张爪造成弹飞的探索经历中，加入平滑搬运、视觉球心定位、对齐误差反馈以及连续张爪斜坡。最终版本复测 572 步成功；正式前三场在 496/514/517 步成功。第 1 场对齐日志末次三轴误差约 0.23/0.20/0.28 mm，但这是程序估计而非独立真值。第 4 场到 settle 后球不可见，576 步开始恢复且多次重找失败；录像抽帧显示球搬到塔顶后离开原位置。第 5 场起始检测失败，恢复后 783 步报告 released ball not yet successful。故成功的对齐机制并未完全解决新接触条件下释放和再获取的问题。'),
'25': ('失败案例：原始场景的“保持不动”捷径不能迁移',
       '最终程序只读机器人 14D 状态并重复保持姿态，没有根据水流和杯子位置重新规划。原始场景的五次冻结复测全部在 150–155 步成功；正式五场却全部保持到 1000 步失败，日志未显示程序异常。录像和代码共同支持“程序未主动调整接水位置”这一事实。源场景允许低动作策略成功，因而重复复测没有暴露该捷径；这是探索分布与正式布局差异的明确警示。至于具体流体落点、接触条件差异，需额外实验验证，不能从 0/5 单独推断仿真错误。'),
}

def read(p, default=None):
    return json.loads(p.read_text()) if p.exists() else default

def save(p, obj):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + '\n')

def clean(value):
    if isinstance(value, dict):
        if value.get('type') in ('image', 'input_image', 'image_url'):
            return {'type': value['type'], 'note': '图像二进制略；见对应 episode 完整视频及原始记录'}
        return {k: ('[credential omitted]' if k.lower() in ('api_key', 'access_token', 'refresh_token', 'authorization') else clean(v))
                for k, v in value.items() if k not in ('encrypted_content', 'internal_chat_message_metadata_passthrough')}
    if isinstance(value, list):
        return [clean(v) for v in value]
    if isinstance(value, str):
        value = re.sub(r'data:image/[^;]+;base64,[A-Za-z0-9+/=]+', '[image binary omitted]', value)
        return re.sub(r'\bsk-[A-Za-z0-9_-]{16,}', '[credential omitted]', value)
    return value

def lines(p):
    if p.exists():
        with p.open() as f:
            for line in f:
                try: yield json.loads(line)
                except json.JSONDecodeError: continue  # A live writer may have an unfinished last line.

def copy_file(src, dst):
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        if src.suffix == '.mp4' and dst.stat().st_size == src.stat().st_size: return
        dst.unlink()
    # Only completed videos are immutable. Notes/configuration must be snapshots.
    if src.suffix == '.mp4':
        try: os.link(src, dst); return
        except OSError: pass
    shutil.copy2(src, dst)

STYLE = '''body{font:16px/1.65 system-ui,sans-serif;background:#f6f8fb;color:#172536;margin:0}main{max-width:1150px;margin:auto;padding:32px}h1,h2,h3{line-height:1.25}h1{font-size:36px}h2{margin-top:40px}a{color:#155eab}table{border-collapse:collapse;width:100%;background:white}td,th{padding:9px 12px;border-bottom:1px solid #d8e0eb;text-align:left}th{background:#e8eef6}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#eef2f7;padding:14px;font:13px/1.6 monospace;max-height:650px;overflow:auto}.card,details{background:white;border:1px solid #dce3ed;border-radius:10px;padding:16px;margin:14px 0}.note{border-left:4px solid #d99122;padding:12px;background:#fff4dc}.good{color:#087a4e}.bad{color:#b33b32}video{width:100%;background:#111;max-height:540px}button,input,select{padding:8px;margin:5px;border:1px solid #bdcbdc;border-radius:5px}summary{cursor:pointer;font-weight:600}.muted{color:#64748b}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:14px}nav{display:flex;gap:16px;flex-wrap:wrap}@media print{video,button,input,select{display:none}details{break-inside:avoid}main{padding:0}pre{max-height:none}}'''

def page(title, body, script=''):
    return '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+html.escape(title)+'</title><style>'+STYLE+'</style><main>'+body+'</main>'+script+'</html>'

def esc(x): return html.escape(str(x))

def table(headers, rows):
    return '<table><thead><tr>'+''.join('<th>'+esc(h)+'</th>' for h in headers)+'</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+str(v)+'</td>' for v in row)+'</tr>' for row in rows)+'</tbody></table>'

def evidence_path(trial, relative):
    for base in [trial/'agent-workspace', trial]:
        if (base/relative).exists(): return base/relative

def build_trial(out, key, task, entry, batch):
    trial = Path(entry['config']).parent
    state = read(trial/'state.json', {})
    config = read(trial/'operator-input.json', {})
    scores = read(trial/'formal-scores.json', {})
    progress = read(trial/'formal-progress.json', {})
    folder = out/'cases'/key
    folder.mkdir(parents=True, exist_ok=True)
    copied = []
    for name in ['operator-input.json', 'formal-scores.json', 'formal-progress.json', 'formal.json', 'adaptation-source.json']:
        f = trial/name
        if f.exists(): copy_file(f, folder/name);copied.append(name)
    provenance=ROOT/'runtime'/batch/'implementation-provenance.json'
    if provenance.exists():copy_file(provenance,folder/'implementation-provenance.json');copied.append('implementation-provenance.json')
    for part in ['code', 'memory']:
        base = trial/'agent-workspace'/part
        if base.exists():
            for f in sorted(base.rglob('*')):
                if f.is_file() and not f.is_symlink() and f.suffix in ('.py','.md','.json','.txt'):
                    dest=folder/part/f.relative_to(base);copy_file(f,dest);copied.append(str(dest.relative_to(folder)))
    # The authoritative result list supplies episode IDs; never match video by guessed order.
    results = {r.get('episode_id'):r for r in state.get('results',[])}
    episodes=[]
    for sim in sorted((trial/'native/results').glob('*/sim')):
        summary=read(sim/'summary.json',{});reset=read(sim/'reset.json',{})
        eid=summary.get('episode_id') or reset.get('episode_id')
        if not eid: continue
        result=results.get(eid,{})
        outcome=read(sim/'evaluation_outcome.json',{})
        complete=outcome.get('complete',False)
        video=sim/'sensors.mp4'
        label=('formal' if summary.get('episode_mode')=='formal' else result.get('mode','exploration'))
        ep={'id':eid,'mode':label,'steps':summary.get('step_id'), 'success':outcome.get('native_success'),
            'complete':complete,'layout':summary.get('layout_id'),'source':str(sim.resolve())}
        ep['status_label']=('成功' if ep['success'] else '失败') if complete else ('提前结束 / 非完整原生回合' if result else '尚未完成')
        dest=folder/'episodes'/eid;dest.mkdir(parents=True,exist_ok=True)
        for name in ['summary.json','reset.json','evaluation_outcome.json','native_rewards.json']:
            f=sim/name
            if f.exists():copy_file(f,dest/name)
        if video.exists() and video.stat().st_size and eid in results:
            copy_file(video,dest/'sensors.mp4');ep['video']=str((dest/'sensors.mp4').relative_to(folder))
        for name in ['stdout','stderr']:
            relative=result.get('artifacts',{}).get(name)
            src=evidence_path(trial,relative) if relative else None
            if src:
                copy_file(src,dest/(name+'.log'));ep[name]=str((dest/(name+'.log')).relative_to(folder))
        if result:save(dest/'controller-result.json',clean(result))
        ep['failure_evidence']=[]
        if ep.get('stdout'):
            for line in (folder/ep['stdout']).read_text().splitlines():
                try: record=json.loads(line)
                except json.JSONDecodeError:continue
                if not isinstance(record,dict):continue
                marker=' '.join(str(record.get(k,'')) for k in ('event','reason','failure','phase'))
                if any(s in marker.lower() for s in ('fail','missing','lost','stalled','recoverable_error','safe_stop')):
                    ep['failure_evidence'].append(record)
        ep['outcome']=str((dest/'evaluation_outcome.json').relative_to(folder))
        episodes.append(ep)
    # Recorded robot interface operations: all events, preserving arguments and text results.
    timeline=[]
    for item in lines(trial/'mcp.jsonl'):
        if item.get('event'):
            timeline.append({'time':item.get('time'), 'kind':'robot', 'label':item.get('tool',item['event']), 'detail':clean(item)})
    # Do not export reasoning / analysis records from the underlying coding agent.
    seen=set()
    for transcript in sorted((trial/'codex-home/sessions').rglob('*.jsonl')):
        for event in lines(transcript):
            if event.get('type')!='response_item':continue
            p=event.get('payload',{});typ=p.get('type')
            identifier=p.get('id') or json.dumps(p,sort_keys=True)
            if identifier in seen:continue
            seen.add(identifier)
            if typ=='message' and p.get('role')=='assistant' and p.get('phase') in ('commentary','final'):
                text='\n'.join(c.get('text','') for c in p.get('content',[]) if c.get('type')=='output_text')
                timeline.append({'time':event.get('timestamp'),'kind':'public','label':'Agent 公开说明 / '+p['phase'],'detail':clean(text)})
            elif typ in ('function_call','custom_tool_call'):
                timeline.append({'time':event.get('timestamp'),'kind':'coding','label':p.get('name',typ),
                    'detail':clean({k:p[k] for k in ['name','arguments','input','call_id'] if k in p})})
    def epoch(v):
        if isinstance(v,(int,float)):return v
        try:return datetime.fromisoformat(v.replace('Z','+00:00')).timestamp()
        except (TypeError,ValueError,AttributeError):return 0
    timeline.sort(key=lambda v:epoch(v['time']))
    save(folder/'timeline.json',timeline)
    save(folder/'episodes.json',episodes)
    audit=[{'episode':e['id'],'mode':e['mode'],'success':e['success'],'events':e['failure_evidence']} for e in episodes]
    save(folder/'failure-audit.json',audit)
    formal=[r for r in state.get('results',[]) if r.get('mode')=='formal']
    rehearsals=[r for r in state.get('results',[]) if r.get('mode')=='rehearsal']
    formal_bundle=formal[0].get('bundle') if formal else None
    exact=[r for r in rehearsals if formal_bundle and r.get('bundle')==formal_bundle]
    success=sum(e.get('score',0) for e in scores.get('episodes',[]))
    duration=(entry.get('finished',0)-entry.get('started',0))/60 if entry.get('finished') and entry.get('started') else None
    row={'key':key,'task':task,'name':NAMES[task[-2:]],'status':entry['status'],'formal_complete':bool(scores.get('score_complete')),
         'formal_successes':success,'formal_recorded':len(scores.get('episodes',[])),'formal_expected':5,
         'exploration_used':state.get('exploration_started',0),'exploration_budget':len(config.get('exploration_seeds',[])),
         'final_bundle_rehearsals':len(exact),'final_bundle_rehearsal_successes':sum(r.get('task_complete') is True for r in exact),
         'all_rehearsals':len(rehearsals),'all_rehearsal_successes':sum(r.get('task_complete') is True for r in rehearsals),
         'formal_bundle':formal_bundle,'avg_max_reward':progress.get('avg_max_reward'),'duration_minutes':duration,
         'trial':str(trial.resolve()),'batch':batch,'started_unix':entry.get('started'),'video_count':sum('video' in e for e in episodes),
         'public_messages':sum(e['kind']=='public' for e in timeline),'timeline_events':len(timeline)}
    save(folder/'provenance.json',row)
    title=f'{task[-2:]} {NAMES[task[-2:]]}'+(' · 追加 8 回合' if key.endswith('continued') else '')
    h='<a href="../../index.html">← 返回总报告</a><h1>'+title+'</h1>'
    if key.endswith('continued'):
        h+='<p class="note">这是继承首轮经验后的追加实验。时间线保留继承的公开对话；本页录像只包括新增回合。<a href="../robowits_09/index.html">首轮八回合完整过程</a>单独保留。</p>'
    h+='<p class="note">完整过程分为探索交互、冻结程序复测、正式评测。交互中的 manual 表示模型逐步调用工具，并非人类遥操作。公开说明是 agent 原文；不包含内部推理。时间线中的 wall time 与视频的仿真播放时间不同，不作伪同步。</p>'
    analysis=CONTINUATION_ANALYSIS if key.endswith('continued') and scores.get('score_complete') else ANALYSIS[task[-2:]]
    h+='<h2>'+analysis[0]+'</h2><p>'+analysis[1]+'</p>'
    h+='<h2>逐回合视频与证据</h2><p>以下覆盖已保存的整个过程，而非仅选成功片段。视频包含 ego、右腕、左腕三个视角；可拖动、倍速播放。正式评测没有逐步 LLM 思考，只执行冻结程序，阶段信息见 stdout。</p>'
    h+='<select id="episode"></select><select id="speed"><option value="1">1×</option><option value="0.5">0.5×</option><option value="2">2×</option><option value="4">4×</option></select><p id="epinfo"></p><video id="video" controls preload="metadata"></video><p id="eplinks"></p>'
    h+='<h3>正式回合失败证据审计</h3><p>按日志原字段筛出首次显式失效；没有命中不代表没有失败。完整输出可从上方 stdout 查看。<a href="failure-audit.json">下载全部匹配事件</a></p>'
    h+=table(['回合','原生结果','首次显式问题 / 步数'],[(esc(e['id']), '成功' if e['success'] else '失败',esc(json.dumps(e['failure_evidence'][0],ensure_ascii=False)) if e['failure_evidence'] else '未匹配显式错误；需核对完整日志与视频') for e in episodes if e['mode']=='formal'])
    h+='<h2>Agent 公开分析、代码与机器人调用时间线</h2><p><a href="timeline.json">下载完整结构化时间线</a> · <a href="episodes.json">回合与视频索引</a> · <a href="provenance.json">来源及统计</a></p>'
    h+='<input id="search" placeholder="搜索：failure / rehearse / numpy / step…"><select id="kind"><option value="all">全部记录</option><option value="public" selected>Agent 公开说明</option><option value="coding">编程 / 工具调用</option><option value="robot">机器人请求与反馈</option></select><button id="prev">上一页</button><button id="next">下一页</button><span id="count"></span><div id="events"></div>'
    h+='<h2>控制程序与实验笔记</h2><p>笔记是当时的 agent 自述，可能包含未经验证的判断；正式成绩以 native_outcome 为准。文件未包含认证信息或原始模型隐藏推理。</p><ul>'
    h+=''.join('<li><a href="'+esc(n)+'">'+esc(n)+'</a></li>' for n in copied)+'</ul>'
    data=json.dumps({'episodes':episodes,'timeline':timeline},ensure_ascii=False).replace('<','\\u003c')
    js='''<script>const DATA=__DATA__;const $=s=>document.querySelector(s);let pageNo=0;
const sel=$('#episode'); DATA.episodes.forEach((e,i)=>{let o=document.createElement('option');o.value=i;o.textContent=`${e.mode} · ${e.id} · ${e.status_label} · ${e.steps}步`;sel.append(o)});
function episode(){let e=DATA.episodes[+sel.value];if(!e)return;$('#video').pause();if(e.video){$('#video').src=e.video}else{$('#video').removeAttribute('src');$('#video').load()}$('#epinfo').textContent=`${e.mode} | ${e.steps} 步 | ${e.status_label} | ${e.id}`;$('#eplinks').replaceChildren();for(let k of ['video','stdout','stderr','outcome'])if(e[k]){let a=document.createElement('a');a.href=e[k];a.textContent=k+' ';$('#eplinks').append(a)}$('#video').playbackRate=+$('#speed').value}
sel.onchange=episode;$('#speed').onchange=()=>{$('#video').playbackRate=+$('#speed').value};episode();
function draw(){let q=$('#search').value.toLowerCase(),kind=$('#kind').value;let all=DATA.timeline.filter(e=>(kind==='all'||e.kind===kind)&&(!q||JSON.stringify(e).toLowerCase().includes(q)));let max=Math.max(0,Math.ceil(all.length/40)-1);pageNo=Math.min(pageNo,max);$('#count').textContent=`共 ${all.length} 条，第 ${pageNo+1}/${max+1} 页`;$('#events').replaceChildren();all.slice(pageNo*40,(pageNo+1)*40).forEach(e=>{let d=document.createElement('details'),s=document.createElement('summary'),p=document.createElement('pre');let t=typeof e.time==='number'?new Date(e.time*1000).toISOString():e.time;s.textContent=`${t||''} · ${e.label}`;p.textContent=typeof e.detail==='string'?e.detail:JSON.stringify(e.detail,null,2);d.append(s,p);if(e.kind==='public')d.open=true;$('#events').append(d)})}
$('#search').oninput=$('#kind').onchange=()=>{pageNo=0;draw()};$('#prev').onclick=()=>{pageNo=Math.max(0,pageNo-1);draw()};$('#next').onclick=()=>{pageNo++;draw()};draw();</script>'''.replace('__DATA__',data)
    (folder/'index.html').write_text(page(title,h,js))
    return row

def build(out):
    out.mkdir(parents=True,exist_ok=True)
    rows=[]
    for batch in BATCHES:
        m=read(ROOT/'runtime'/batch/'manifest.json',{})
        for task,e in m.get('tasks',{}).items():
            if not e.get('config'):continue
            key=task+('_continued' if 'continued16' in batch else '')
            print('Building',key,flush=True)
            rows.append(build_trial(out,key,task,e,batch))
    rows.sort(key=lambda r:r['key'])
    baseline=[r for r in rows if not r['key'].endswith('continued')]
    continuation=next((r for r in rows if r['key'].endswith('continued')),None)
    finished=[r for r in baseline if r['formal_complete']]
    wins=sum(r['formal_successes'] for r in finished)
    recorded=sum(r['formal_recorded'] for r in finished)
    now=datetime.now(timezone.utc).isoformat()
    save(out/'results.json',{'generated_utc':now,'baseline':baseline,'continuation':continuation,
         'baseline_successes':wins,'baseline_recorded':recorded,'baseline_expected':50,
         'mixed_budget_successes':wins+continuation['formal_successes'] if continuation and continuation['formal_complete'] else None,
         'mixed_budget_note':'Only task09 uses 16 exploration episodes; other tasks use 8. Not an equal-budget aggregate.'})
    save(out/'report-provenance.json',{'generated_utc':now,'generator_git_head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
         'generator_path':'scripts/build_robowits_report.py','note':'Git HEAD describes the report workspace; exact experiment provenance is preserved where recorded in each case.','batches':BATCHES})
    with (out/'results.csv').open('w') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    h='<h1>RAX × RoboWits 实验报告</h1><p class="muted">生成时间（UTC）：'+now+' · 十任务 / 原始场景探索 / 独立布局评测</p>'
    h+='<h2>首先明确：任务范围、框架与 API</h2>'+table(['项目','本次实验实际配置'],[(esc(a),esc(b)) for a,b in EXPERIMENT_OVERVIEW])
    h+='<nav><a href="#setup">实验设置</a><a href="#results">结果表格</a><a href="#demos">全过程 Demo</a><a href="#analysis">实验分析</a><a href="#limits">局限与后续实验</a><a href="results.csv">CSV</a><a href="results.json">JSON</a></nav>'
    pending=continuation and continuation['status']=='running'
    h+='<p class="note">'+('这是阶段性报告：09 追加探索尚在运行，最终成绩会在结束后更新。' if pending else '本报告保留原始 8 回合预算与 09 追加预算的独立记录。')+' 原始十任务中 09 首轮未提交，不能将其称作五场实测失败。</p>'
    h+=f'<p><b>原始预算：{int(wins)}/{recorded} 个已完成正式回合成功（{wins/recorded:.1%}）；固定 50 场分母、缺失计零为 {wins/50:.1%}。</b>这不是论文原基准复现，也不是 RoboWits 全任务全集成绩。</p>'
    if continuation and continuation['formal_complete']:
        mixed=wins+continuation['formal_successes']
        h+=f'<p><b>纳入 09 续跑后的最新十任务结果：{int(mixed)}/50（{mixed/50:.0%}）。</b>此项是混合预算汇总：09 使用 16 回合，其余任务使用 8 回合；不能作为十任务等预算成绩。</p>'
    h+='<h2 id="setup">实验设置与方法边界</h2>'
    settings=[('任务集','README 十任务：01、02、03、04、06、09、13、16、17、25。每任务仅一次独立探索实验。'),
      ('RAX 模式','冻结编程大模型在探索中操作机器人、写代码和记忆；最终冻结程序独立执行正式评测。没有模型参数训练，没有人工编写任务解法。'),
      ('模型 / 接口','gpt-6-astra，xhigh，Codex CLI 0.164；ChatGPT 登录调用。未配置外部 Gemini 感知；RGB-D、机器人状态与动作接口提供给 agent。'),
      ('观测 / 控制','ego、右腕、左腕三视角 RGB-D（848×480）及标定；14D 双臂绝对末端位姿与夹爪宽度；底层控制器及 IK 由环境提供。'),
      ('探索分布','collection=2：RoboWits 源码定义的原始场景，每次初始布局重复；不读取正式测试布局。探索中 agent 可自行用动作扰动场景。'),
      ('预算','原始每任务 8 个回合，交互探索/开发/冻结程序复测共用。每回合 1000 个 native 控制步；control_dt=0.0333 秒，另有 7200 秒 wall 上限。8 回合不是 RAX 方法固有限制。'),
      ('提交规则','确切版本必须完成一次干净运行的复测；干净退出不等于任务成功。13 正是复测失败但仍合格提交的例子。'),
      ('正式测试','每任务 5 场，官方 collection=1、layout_id=0..4，布局种子 42。使用 native is_success 二值判定，冻结代码不随正式成绩修改，正式阶段不调用 LLM 决定动作。'),
      ('09 续跑','独立新运行继承首轮代码、记忆与对话，追加 8 次（累计上限 16），探索场景不变。此前没有正式成绩可泄漏给续跑。'),
      ('运行环境','RoboWits-genesis131，Genesis 1.3.1；仿真 Python 3.11，host Python 3.12；GPU 1：RTX 6000 Ada 48 GB。剩余五任务采用最多双任务并行。'),
      ('版本与修正','RoboWits 源码 b3fb06568b6a76d4ee371ff25fb2318336c012b5；原始场景协议与纹理加载修正后运行。早期 random-reset 结果不混入本表；逐运行配置/哈希见证据页。'),
      ('指标','主指标为正式 native success；avg_max_reward 仅为原生奖励进度辅助指标，不是成功率，也不能跨任务当作同一物理尺度。')]
    h+=table(['设置','内容'],[(esc(a),esc(b)) for a,b in settings])
    h+='<h2 id="results">结果表格</h2><p>复测列仅统计正式提交的确切 bundle；原始场景的重复成功不能视作独立测试布局上的成功率。运行耗时含模型等待与仿真，双任务调度下不能据此作模型速度比较。</p>'
    def result_cells(r):
        n=r['final_bundle_rehearsals'];reh=f"{r['final_bundle_rehearsal_successes']}/{n}" if n else '未提交 / 无最终版本统计'
        score=f"{int(r['formal_successes'])}/5 ({r['formal_successes']/5:.0%})" if r['formal_complete'] else '尚无完整正式成绩'
        progress=f"{r['avg_max_reward']:.3f}" if r['avg_max_reward'] is not None else '—'
        duration=f"{r['duration_minutes']:.1f}" if r['duration_minutes'] is not None else '—'
        return [f'<a href="cases/{r["key"]}/index.html">{r["task"][-2:]} {r["name"]}</a>',str(r['exploration_used']),reh,score,progress,duration]
    h+=table(['任务 / Demo','探索已用','最终版本复测成功','正式成功','平均最高奖励','耗时/分钟'],[result_cells(r) for r in baseline])
    if continuation:
        h+='<h3>09 追加预算：独立报告，不替换原始记录</h3>'+table(['任务 / Demo','本轮探索已用','最终版本复测成功','正式成功','平均最高奖励','本轮耗时/分钟'],[result_cells(continuation)])
        h+='<p>累计预算为首轮 8 + 追加轮 8；实际使用为 '+str(next(r['exploration_used'] for r in baseline if r['task']=='robowits_09')+continuation['exploration_used'])+' 回合。续跑表现不能作为额外独立随机重复，也不能与其他 8 回合任务等预算比较。</p>'
        if continuation['formal_complete']:
            h+=f'<p>续跑正式结果为 {int(continuation["formal_successes"])}/5。相比首轮未完成提交，续跑已闭合自动程序验证与正式评测流程。它同时改变了可用经验、程序版本和预算，因此不能仅用前后差异证明增加回合数的独立因果效应。</p>'
            h+='<h3>'+CONTINUATION_ANALYSIS[0]+'</h3><p>'+CONTINUATION_ANALYSIS[1]+'</p>'
            h+=table(['正式场次','首次明确失效','结局'],[
                ['1','0 步：Brown disk not localized；未找到垫片','等待至 1000 步，失败'],
                ['2','160 步和 305 步：抓垫片运动跟踪误差 0.032 m','等待至 1000 步，失败'],
                ['3','0 步：Upright cup rim not localized；未找到杯口','等待至 1000 步，失败'],
                ['4','325 步：disk grip opening no longer indicates a flat secure grasp','等待至 1000 步，失败'],
                ['5','820 步进入 settle；933 步 native success','成功']])
        h+=f'<p>本轮所有版本合计复测 {continuation["all_rehearsal_successes"]}/{continuation["all_rehearsals"]} 成功；这与最终提交版本的复测统计不同。逐次错误与修正见续跑 Demo 的 failure-audit、程序代码和 memory 文件。</p>'
        events=read(out/'cases/robowits_09_continued/timeline.json',[])
        finals=[e for e in events if e['kind']=='public' and e['label'].endswith('/ final') and isinstance(e['time'],str)
                and datetime.fromisoformat(e['time'].replace('Z','+00:00')).timestamp() >= (continuation['started_unix'] or 0)]
        if finals:
            h+='<details><summary>09 续跑最后公开工作说明（agent 原文，正式成绩另见表格）</summary><pre>'+esc(finals[-1]['detail'])+'</pre></details>'
    h+='<h2 id="demos">全过程 Demo 与阅读路径</h2><p>每个任务页都包含全部已保存探索/复测/正式视频、公开分析说明、代码工具调用、机器人输入输出、控制程序及笔记。选择回合后可直接播放完整录像；时间线支持按类型筛选和关键字检索。没有将视频播放时间伪装成模型思考的墙钟时间。</p><div class="grid">'
    for key,desc in [('robowits_06','成功：探索失败 → 识别多米诺链 → 感知与恢复程序 → 正式 5/5'),('robowits_17','同一程序成功与失败：平滑搬运、对齐、释放 → 正式前三场成功、后两场失败'),('robowits_13','软件错误：抓起盖子 → carry 日志序列化异常 → 退回释放 → 正式 0/5'),('robowits_25','场景捷径：原始场景保持不动 5/5 复测 → 正式 0/5'),('robowits_03','学习过程：滑落 → 重抓耗尽步数 → 加深抓取 → 新布局仅 1/5'),('robowits_09_continued','预算续跑：继承已有知识 → 重新复测/修正 → 追加预算结果')]:
        h+=f'<div class="card"><a href="cases/{key}/index.html">{desc}</a></div>'
    h+='</div><h2 id="analysis">逐任务成功与失败分析</h2>'
    for tid,(title,body) in ANALYSIS.items():
        h+=f'<h3>{tid} {NAMES[tid]}：{title}</h3><p>{body}</p><p><a href="cases/robowits_{tid}/index.html">查看逐回合日志、代码与视频证据</a></p>'
    h+='<h2>跨任务分析</h2><p><b>成功的共同点：</b>06 和 17 的程序把观测转成当前几何关系，而非仅重放成功动作；开发中真实失败促成重新观察、平滑轨迹与渐进释放。17 同时有正式失败，说明加入反馈与恢复是必要能力，但本实验不能证明它们足以保证泛化。</p><p><b>失败不只有一个来源：</b>09 是回合分配使验证流程未闭合；13 是可定位的软件异常；25 是单一探索场景允许的低动作捷径；03/04/17 包含感知、运动跟踪、接触和恢复预算问题。把这些都记作“模型不会操作”会掩盖可修正的工程与评测问题。</p><p><b>复测与泛化存在明显落差：</b>25 的固定场景复测 5/5 对应正式 0/5，02 的 1/1 对应 0/5，04 的 2/2 对应 2/5。重复相同初始状态更接近检查程序可重复运行，不能代替不同位置、遮挡、接触条件下的覆盖测试。增加同场景回合可能帮助 09 完成程序，也可能继续强化 25 的捷径。</p><p><b>预算要同时覆盖探索与验证：</b>8 回合还需承担编程开发、复测和修正；09 最后才成功，13 到最后才执行最终复测，均无余量修复并重新资格验证。03 则还存在回合内步数余量不足。两类预算不可混同。</p>'
    h+='<h2 id="limits">局限、证据强度与下一轮实验</h2><ol><li>每任务只有一个探索种子序列和五个正式布局，样本量很小。0/5 不是证明永远不成功，5/5 不是证明普遍可靠；在独立二项假设下，Wilson 95% 区间分别约为 0–43.4% 和 56.6–100%，实际固定布局与共享程序使独立性假设也有限。</li><li>这是适配后的 RAX 在所选 RoboWits 子集上的实验，没有同预算基线、重复种子、算法消融或全部 50 布局评测，不能声称优于其他方法或复现论文数值。</li><li>只展示记录中的公开说明；不输出隐藏内部推理。agent 笔记不等于独立验证；对因果解释采用“日志确认 / 视频观察 / 尚待验证”的层次。</li><li>下一轮应分开比较 8/16 回合预算、重复原始场景/独立训练布局，并固定正式布局和重复多个探索种子；训练布局必须单独生成或来源隔离，不能读正式 JSON。</li><li>为任务成功后的程序复测和修正保留回合；明确区分“运行合格提交”与“任务成功验证”，失败候选可保留为基线但必须标记。13 的修复应单列新实验，不覆写现有成绩。</li><li>加上无动作基线以识别 25 这类捷径；对恢复阶段记录首次失效、接触状态和剩余步数，而不把最终超时当作根因。</li><li>耗时和 token 数不是 API 费用。不同日志的缓存、重试和模型推理计量口径尚未统一，本报告不据日志虚构美元成本。</li></ol>'
    h+='<h2>复现与文件说明</h2><p>生成脚本：<code>scripts/build_robowits_report.py</code>；输出 <code>runtime/robowits-report/index.html</code>。可直接用浏览器打开；整个目录拷贝后可离线查看。所有媒体为完整原始录像，未剪掉失败动作。结果来源为逐试验 formal-scores.json 与逐回合 evaluation_outcome.json。源运行绝对路径记录在 provenance.json，导出的页面使用相对路径。</p><p>方法参考：<a href="https://robot-agent-exploration.com/">RAX 项目</a> · <a href="https://github.com/robot-agent-exploration/rax">原始实现</a>。这些链接说明方法来源；本报告数值均来自本地实验而非论文。</p>'
    (out/'index.html').write_text(page('RAX × RoboWits 实验报告',h))
    save(out/'build-status.json',{'generated_utc':now,'continuation_pending':bool(pending),'trials':len(rows),'videos':sum(r['video_count'] for r in rows)})
    chrome=shutil.which('google-chrome')
    if chrome:
        with (out/'pdf-render.log').open('w') as log:
            result=subprocess.run([chrome,'--headless','--no-sandbox','--disable-gpu','--disable-dev-shm-usage',
                '--no-first-run','--no-pdf-header-footer','--print-to-pdf='+str(out/'report.pdf'),
                (out/'index.html').as_uri()],stdout=log,stderr=subprocess.STDOUT,timeout=90)
        if result.returncode:print('PDF render failed; HTML report remains available',flush=True)
    print('Report:',out/'index.html',flush=True)

def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,default=ROOT/'runtime/robowits-report');p.add_argument('--wait-for-continuation',action='store_true');a=p.parse_args()
    os.chdir(ROOT)
    if a.wait_for_continuation:
        manifest=ROOT/'runtime/robowits09-continued16-canonical-eval/manifest.json'
        while any(e['status']=='running' for e in read(manifest,{}) .get('tasks',{}).values()):time.sleep(10)
    build(a.output.resolve())

if __name__=='__main__':main()

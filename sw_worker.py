"""SolidWorks local stage runner. Called only after evidence and plan validation."""
from pathlib import Path
import argparse, inspect, json, shutil, sys
import cadflow as flow

def verify_metrics(session, expected):
    from solidworks_mcp import binding
    model = session._model
    rebuilt = bool(model.ForceRebuild3(False))
    bodies = [binding.wrap(b,session._mod.IBody2) for b in (session._require_part().GetBodies2(0,False) or [])]
    body_codes = [int(b.Check2()) for b in bodies]
    mass = session.get_mass_properties()['mass_properties']
    volume, size = mass['volume_mm3'], mass['bounding_box_mm']['size_mm']
    features = session.list_features()
    faults = [f for f in features.get('features',[]) if f.get('error',{}).get('code') and not f['error'].get('warning')]
    errors = []
    if not rebuilt or any(body_codes) or faults: errors.append('重建、实体或特征校验失败')
    if len(bodies) != expected['solid_count']: errors.append('实体数量与计划不符')
    if not expected['volume_mm3_range'][0] <= volume <= expected['volume_mm3_range'][1]: errors.append('体积超出计划区间')
    if any(not bounds[0] <= actual <= bounds[1] for actual,bounds in zip(size,expected['size_mm_ranges'])): errors.append('三轴包络超出计划区间')
    return dict(ok=not errors,errors=errors,rebuild=rebuilt,solid_count=len(bodies),body_check_codes=body_codes,volume_mm3=volume,size_mm=size,features=features,note='实体与量化检查不代替投影/造型评审；默认密度不作为实际材料重量')

def run(case, plan):
    flow.check_plan(case,plan)  # Check before even importing/connecting COM.
    folder = case / 'stages' / plan['stage_id']
    if folder.exists(): raise ValueError('此阶段已有记录；先诊断失败或使用新阶段编号，不覆盖、不盲重试')
    folder.mkdir(parents=True)
    flow.write(folder / 'plan.json',plan)
    import pythoncom
    from solidworks_mcp.session import SolidWorksSession
    pythoncom.CoInitialize()
    session = SolidWorksSession()
    previous = None
    result = dict(stage=plan['stage_id'],review_sha256=plan['review_sha256'],started=flow.now(),steps=[],shape_review={'status':'pending','evidence':''})
    try:
        session.connect()
        docs = session.list_documents()
        previous = next((d['title'] for d in docs['documents'] if d.get('active')),None)
        # Validate command signatures before making any document.
        for step in plan['steps']: inspect.signature(getattr(session,step['method'])).bind(**step['args'])
        output = folder / 'model.SLDPRT'
        if plan.get('predecessor'):
            source = case / 'stages' / plan['predecessor'] / 'model.SLDPRT'
            shutil.copy2(source,output)
            session.open_part(str(output))
        else:
            session.new_part()
            session.save_part(str(output))
        flow.event(case,plan['stage_id'],'执行中','已建立本阶段独立文件；按计划逐条执行，无自动重试')
        flow.dashboard()
        for n, step in enumerate(plan['steps'],1):
            flow.event(case,plan['stage_id'],'执行中',f"{n}/{len(plan['steps'])} {step['method']}")
            flow.dashboard()
            answer = getattr(session,step['method'])(**step['args'])
            result['steps'].append({'index':n,'method':step['method'],'result':answer})
            flow.write(folder / 'result.json',result)
            if isinstance(answer,dict) and answer.get('ok') is False: raise RuntimeError(str(answer))
        result['metrics'] = verify_metrics(session,plan['expected'])
        if not result['metrics']['ok']: raise RuntimeError('；'.join(result['metrics']['errors']))
        session.save_part(str(output))
        for view in ('front','right','top','iso'):
            session.screenshot(str(folder / f'{view}.png'),view=view)
        result.update(state='waiting_shape_review',finished=flow.now(),model_sha256=flow.sha(output))
        flow.event(case,plan['stage_id'],'待造型评审','数值检查通过，已保存阶段模型及四视图；尚未确认与原图吻合')
    except Exception as exc:
        result.update(state='failed',error=str(exc),finished=flow.now())
        flow.event(case,plan['stage_id'],'失败已停',str(exc))
        raise
    finally:
        flow.write(folder / 'result.json',result)
        # Save diagnostic state if a new stage exists; never save the user's previous document.
        if result.get('state') == 'failed' and session._model is not None and session._model.GetPathName().lower() == str(folder / 'model.SLDPRT').lower():
            try: session.save_part(str(folder / 'model.SLDPRT'))
            except Exception: pass
        if previous:
            try: session.activate_document(previous)
            except Exception: pass
        flow.dashboard()
        pythoncom.CoUninitialize()
    return result

if __name__ == '__main__':
    p=argparse.ArgumentParser();p.add_argument('--case',required=True);p.add_argument('--plan',required=True);a=p.parse_args()
    lock = flow.ROOT / '.sw.lock'
    try:
        with lock.open('x',encoding='utf-8') as f: f.write(flow.now())
    except FileExistsError:
        raise SystemExit('已有 CAD 阶段执行中；检查进度，不并发启动')
    try:
        try:
            result=run(flow.case_dir(a.case),flow.read(a.plan))
            print(json.dumps({'state':result['state'],'stage':result['stage']},ensure_ascii=False))
        except Exception as e:
            print('阶段停止：'+str(e),file=sys.stderr);raise SystemExit(2)
    finally:
        lock.unlink()

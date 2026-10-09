"""Isolated SolidWorks Boolean smoke test, never opens/edits any product model."""
import sys
from pathlib import Path
import cadflow as flow
from sw_worker import verify_metrics

def main():
    import pythoncom
    from solidworks_mcp.session import SolidWorksSession
    pythoncom.CoInitialize();session=SolidWorksSession();session.connect()
    docs=session.list_documents();previous=next((d['title'] for d in docs['documents'] if d.get('active')),None)
    root=flow.ROOT/'verification';root.mkdir(exist_ok=True)
    report={'kind':'isolated_tool_validation_not_product_acceptance','time':flow.now(),'cases':[]}
    try:
        for operation,expected_v,expected_x in [('add',6000,30),('subtract',2000,10),('common',2000,10)]:
            target=root/f'boolean-{operation}.SLDPRT'
            if target.exists():raise ValueError('验证样本已存在；不覆盖重复运行')
            session.new_part()
            session.add_box(20,20,10,name='主体A')
            session.add_extruded_profile_on_plane(points_mm=[[10,0,0],[30,0,0],[30,20,0],[10,20,0]],plane='front',depth_mm=10,merge=False,name='工具体B')
            bodies=session.list_bodies()['bodies']
            if len(bodies)!=2:raise RuntimeError('应有两个独立工具体')
            # Resolve body names from their actual bounding positions, not guessed names.
            main_body=min(bodies,key=lambda b:b['bounding_box_mm']['min_mm'][0])['name']
            tool_body=next(b['name'] for b in bodies if b['name']!=main_body)
            session.combine_bodies(operation,main_body,[tool_body],name='布尔_'+operation)
            expected={'solid_count':1,'volume_mm3_range':[expected_v-.01,expected_v+.01],'size_mm_ranges':[[expected_x-.001,expected_x+.001],[19.999,20.001],[9.999,10.001]]}
            measured=verify_metrics(session,expected)
            if not measured['ok']:raise RuntimeError(str(measured))
            session.save_part(str(target))
            report['cases'].append(dict(operation=operation,expected=expected,measured=measured,model_sha256=flow.sha(target)))
            print(operation,measured['volume_mm3'],measured['solid_count'],measured['body_check_codes'],flush=True)
            session.close_part()
        report['ok']=True
    except Exception as e:
        report.update(ok=False,error=str(e));raise
    finally:
        flow.write(root/'boolean-report.json',report)
        if previous:
            try:session.activate_document(previous)
            except Exception:pass
        pythoncom.CoUninitialize()

if __name__=='__main__':main()

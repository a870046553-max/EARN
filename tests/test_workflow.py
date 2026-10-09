import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import cadflow as flow

def reviewed():
    r=flow.review_template('source-hash')
    r.update(status='confirmed',reviewer='Synthetic fixture',reviewed_at='2026-10-09T00:00:00Z',unit={'name':'mm','evidence':'fixture'},scale={'geometry_to_mm':1,'evidence':'fixture'},projection={'system':'explicit_mapping','evidence':'synthetic orthogonal axes'},blocking_unknowns=[])
    r['views']=[dict(region='A',horizontal='+X',vertical='+Y',look_direction='-Z',evidence='fixture'),dict(region='B',horizontal='+Z',vertical='+Y',look_direction='+X',evidence='fixture'),dict(region='C',horizontal='+X',vertical='-Z',look_direction='-Y',evidence='fixture')]
    r['correspondences']=[dict(meaning='fixture feature',refs=[dict(view='A',entity_id='a'),dict(view=v,entity_id=e)]) for v,e in [('B','b'),('C','c')]]
    r['dimensions']=[dict(name='fixture size',value_mm=20,source_type='source_geometry',evidence='fixture')]
    r['line_roles']=[dict(entity_ids=['a'],meaning='fixture edge')]
    return r

def planned(r):
    return dict(stage_id='01',review_sha256=flow.digest(r),purpose='fixture only',evidence_refs=['a'],steps=[{'method':'add_box','args':{'width_mm':20,'height_mm':20,'depth_mm':10}}],expected={'solid_count':1,'volume_mm3_range':[3999,4001],'size_mm_ranges':[[19.9,20.1],[19.9,20.1],[9.9,10.1]]},projection_check='fixture projection check')

class ReviewGates(unittest.TestCase):
    def test_complete_fixture_passes(self):
        r=reviewed(); self.assertEqual(flow.review_errors(r,'source-hash'),[])
        self.assertEqual(flow.plan_errors(planned(r),r,'source-hash'),[])

    def test_pending_review_cannot_launch_worker(self):
        with tempfile.TemporaryDirectory() as temp:
            case=Path(temp); r=flow.review_template('source-hash'); flow.write(case/'review.json',r)
            with patch.object(flow,'verify_source',return_value={'source_sha256':'source-hash'}), patch.object(flow,'check_refs',return_value=[]):
                from sw_worker import run
                with self.assertRaisesRegex(ValueError,'尚未确认'): run(case,planned(r))
            self.assertFalse((case/'stages').exists())

    def test_changed_source_invalidates_review(self):
        self.assertTrue(any('哈希' in e for e in flow.review_errors(reviewed(),'changed')))

    def test_changed_review_invalidates_plan(self):
        r=reviewed(); p=planned(r); r['dimensions'][0]['value_mm']=21
        self.assertTrue(any('版本' in e for e in flow.plan_errors(p,r,'source-hash')))

    def test_two_identical_view_directions_fail(self):
        r=reviewed(); r['views'][1].update(horizontal='+X',vertical='+Y',look_direction='-Z')
        self.assertTrue(any('X/Y/Z' in e for e in flow.review_errors(r,'source-hash')))

    def test_mirrored_view_axes_fail(self):
        r=reviewed();r['views'][0]['horizontal']='-X'
        self.assertTrue(any('镜像' in e for e in flow.review_errors(r,'source-hash')))

    def test_no_command_or_checks_is_rejected(self):
        r=reviewed(); p=planned(r); p['steps']=[];p['expected']={}
        self.assertGreaterEqual(len(flow.plan_errors(p,r,'source-hash')),3)

    def test_arbitrary_method_not_allowed(self):
        r=reviewed();p=planned(r);p['steps']=[{'method':'close_part','args':{}}]
        self.assertTrue(any('未接入' in e for e in flow.plan_errors(p,r,'source-hash')))

    def test_source_cache_detects_edit(self):
        with tempfile.TemporaryDirectory() as temp:
            case=Path(temp);(case/'source').mkdir();original=case/'original.dwg';original.write_bytes(b'original')
            (case/'source/input.dwg').write_bytes(b'original');(case/'source/input.dxf').write_bytes(b'dxf')
            flow.write(case/'manifest.json',dict(original_path=str(original),source_sha256=flow.sha(original),dxf_sha256=flow.sha(case/'source/input.dxf')))
            flow.verify_source(case)
            (case/'source/input.dxf').write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError,'变化'):flow.verify_source(case)

    def test_missing_entity_reference_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            case=Path(temp);flow.write(case/'entities.json',[{'id':'a'}]);flow.write(case/'regions.json',{'regions':[{'id':x} for x in 'ABC']})
            self.assertTrue(flow.check_refs(case,reviewed()))

    def test_predecessor_requires_shape_review(self):
        with tempfile.TemporaryDirectory() as temp:
            case=Path(temp);r=reviewed();p=planned(r);p['predecessor']='00'
            flow.write(case/'review.json',r);flow.write(case/'stages/00/result.json',dict(review_sha256=flow.digest(r),shape_review={'status':'pending','evidence':''}))
            with patch.object(flow,'verify_source',return_value={'source_sha256':'source-hash'}),patch.object(flow,'check_refs',return_value=[]):
                with self.assertRaisesRegex(ValueError,'造型未评审'):flow.check_plan(case,p)

if __name__=='__main__': unittest.main()

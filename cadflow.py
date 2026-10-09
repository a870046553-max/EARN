"""Local CAD evidence / review / staged execution. No AI or network calls."""
from __future__ import annotations
import argparse, collections, gzip, hashlib, html, json, math, re, shutil, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def read(path):
    path=Path(path); compressed=Path(str(path)+'.gz')
    if compressed.is_file(): return json.loads(gzip.decompress(compressed.read_bytes()).decode('utf-8-sig'))
    return json.loads(path.read_text(encoding="utf-8-sig"))

def write(path, data):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    if path.name == 'entities.json':
        path=Path(str(path)+'.gz');temp=path.with_suffix('.gz.tmp')
        temp.write_bytes(gzip.compress(json.dumps(data,ensure_ascii=False,separators=(',',':')).encode('utf-8'),mtime=0))
        temp.replace(path);return
    temp = path.with_suffix(path.suffix + ".tmp")
    # Keep the large source-geometry cache compact; never dump it into chat context.
    text = json.dumps(data, ensure_ascii=False, indent=2)
    temp.write_text(text, encoding="utf-8")
    temp.replace(path)

def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for b in iter(lambda: f.read(1048576), b""): h.update(b)
    return h.hexdigest()

def digest(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()

def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def config():
    p = ROOT / "config.local.json"
    return read(p if p.exists() else ROOT / "config.example.json")

def case_dir(name):
    if not re.fullmatch(r"[A-Za-z0-9_-]+", name): raise ValueError("样本编号只用字母数字和横线下划线")
    return ROOT / "cases" / name

def event(case, stage, state, detail):
    obj = dict(case=case.name, stage=stage, state=state, detail=detail, updated=now())
    write(case / "status.json", obj)
    with (case / "events.jsonl").open("a", encoding="utf-8") as f: f.write(json.dumps(obj, ensure_ascii=False) + "\n")

def load_ezdxf():
    dep = config().get("ezdxf_path")
    if dep and Path(dep).is_dir(): sys.path.insert(0, dep)
    import ezdxf
    return ezdxf

def verify_source(case):
    m = read(case / "manifest.json")
    for path, expected in [(Path(m["original_path"]), m["source_sha256"]), (case / "source/input.dwg", m["source_sha256"]), (case / "source/input.dxf", m["dxf_sha256"])]:
        if not path.is_file() or sha(path) != expected: raise ValueError("源文件或缓存变化，旧评审失效：" + str(path))
    return m

def review_template(source_hash):
    return {"source_sha256":source_hash,"status":"pending","reviewer":"","reviewed_at":"","unit":{"name":"","evidence":""},"scale":{"geometry_to_mm":None,"evidence":""},"projection":{"system":"","evidence":""},"views":[],"correspondences":[],"dimensions":[],"line_roles":[],"unknowns":[],"blocking_unknowns":["确认区域视向及投影关系","确认线条的轮廓、棱边、遮挡与细节含义","确认尺寸依据，排除上一版错误理解"],"note":"按建模流程填写。程序检查资料完整性，不代替工程判断。"}

def ingest(name, dwg, dxf=None, cache_source=None):
    case = case_dir(name); case.mkdir(parents=True, exist_ok=True)
    original = Path(dwg).resolve(); h = sha(original)
    if (case / "manifest.json").exists():
        if read(case / "manifest.json")["source_sha256"] != h: raise ValueError("原图变化，请用新样本编号，避免覆盖旧评审")
        verify_source(case)
        if not (case / "evidence.json").exists(): inspect_case(case)
        else: print("命中哈希缓存，未重复解析")
        return
    source = case / "source"; source.mkdir(exist_ok=True)
    shutil.copy2(original, source / "input.dwg")
    if dxf:
        if not cache_source or sha(cache_source) != h: raise ValueError("复用 DXF 需提供同哈希导出源副本 --cache-source")
        shutil.copy2(dxf, source / "input.dxf")
        provenance = "复用此前 AutoCAD 从同哈希 DWG 副本导出的 DXF；另登记 DXF 哈希"
    else:
        console = Path(config()["autocad_console"])
        if not console.is_file(): raise ValueError("请配置已有 AutoCAD 控制台路径")
        # AutoCAD can rewrite its input during DXFOUT; retain an untouched archive copy.
        working = source / 'export-working.dwg'
        shutil.copy2(source / 'input.dwg',working)
        script = source / "export.scr"
        script.write_text('_FILEDIA\n0\n_DXFOUT\n"' + (source / "input.dxf").as_posix() + '"\n16\n_QUIT\n_N\n', encoding="utf-8")
        with (source / "export.log").open("wb") as log:
            result = subprocess.run([str(console), "/i", str(working), "/s", str(script), "/l", "en-US"], stdout=log, stderr=subprocess.STDOUT, timeout=180)
        if result.returncode or not (source / "input.dxf").exists(): raise ValueError("导出失败，检查 source/export.log；不重试")
        provenance = "AutoCAD 副本导出"
    if sha(original) != h: raise ValueError("运行期间原件变化")
    write(case / "manifest.json", dict(original_path=str(original), source_sha256=h, dxf_sha256=sha(source / "input.dxf"), created=now(), dxf_provenance=provenance))
    inspect_case(case)

def inspect_case(case):
    manifest = verify_source(case); ezdxf = load_ezdxf()
    from ezdxf.path import make_path
    from ezdxf.disassemble import recursive_decompose
    doc = ezdxf.readfile(case / "source/input.dxf"); raw = list(doc.modelspace())
    rows, texts, dims, skipped = [], [], [], collections.Counter()
    for i, e in enumerate(recursive_decompose(raw)):
        kind = e.dxftype(); eid = str(e.dxf.get("handle", None) or f"virtual-{i}")
        if kind in ("TEXT", "MTEXT", "ATTRIB"):
            texts.append(dict(id=eid, layer=e.dxf.layer, text=e.plain_text() if hasattr(e,"plain_text") else e.dxf.text, insert=list(e.dxf.insert))); continue
        try: points = [[round(v.x,7),round(v.y,7)] for v in make_path(e).flattening(.01)]
        except (TypeError, ValueError, AttributeError, NotImplementedError): skipped[kind] += 1; continue
        if len(points) < 2: continue
        row = dict(id=eid,type=kind,layer=e.dxf.layer,linetype=e.dxf.get("linetype","BYLAYER"),points=points)
        if row["linetype"].upper() == "BYLAYER" and row["layer"] in doc.layers: row["linetype"] = doc.layers.get(row["layer"]).dxf.linetype
        if kind in ("CIRCLE","ARC"):
            row.update(center=list(e.dxf.center),radius=e.dxf.radius)
            if kind == "ARC": row.update(start_angle=e.dxf.start_angle,end_angle=e.dxf.end_angle)
        rows.append(row)
    # Original DIMENSION data remains separate from its rendered graphics.
    for e in raw:
        if e.dxftype() != "DIMENSION": continue
        try:
            measurement = e.get_measurement()
            if hasattr(measurement,"x"): measurement = list(measurement)
        except Exception: measurement = None
        dims.append(dict(id=e.dxf.handle,dimtype=e.dxf.dimtype,text_override=e.dxf.get("text",""),geometry_measurement=measurement,dimstyle=e.dxf.dimstyle))
    write(case / "entities.json", rows)
    evidence = dict(source_sha256=manifest["source_sha256"],dxf_sha256=manifest["dxf_sha256"],ezdxf_version=ezdxf.__version__,modelspace_entity_count=len(raw),geometry_count=len(rows),types=dict(collections.Counter(e.dxftype() for e in raw)),layers=dict(collections.Counter(e.dxf.layer for e in raw)),insunits=doc.header.get("$INSUNITS"),dimensions=dims,texts=texts,skipped_geometry=dict(skipped),curve_display_tolerance_drawing_units=.01,warning="SVG 是离散化显示，不判定投影或线条语义。单位与图样比例分开确认。")
    write(case / "evidence.json", evidence)
    if not (case / "review.json").exists(): write(case / "review.json", review_template(manifest["source_sha256"]))
    event(case,"读图评审","待确认",f"已提取 {len(raw)} 个原始实体、{len(dims)} 个原生尺寸实体；投影与造型尚未确认")
    print(json.dumps(dict(case=case.name,entities=len(raw),dimension_entities=len(dims),state="待读图评审"),ensure_ascii=False))

def review_errors(r, source_hash):
    errors = []
    if r.get("source_sha256") != source_hash: errors.append("评审源图哈希不一致")
    if r.get("status") != "confirmed": errors.append("三视图读图评审尚未确认")
    for k in ("reviewer","reviewed_at"):
        if not r.get(k): errors.append("缺少评审记录：" + k)
    if r.get("unit",{}).get("name") != "mm" or not r.get("unit",{}).get("evidence"): errors.append("毫米单位及依据未确认")
    scale = r.get("scale",{}).get("geometry_to_mm")
    if type(scale) not in (int,float) or not math.isfinite(scale) or scale <= 0 or not r.get("scale",{}).get("evidence"): errors.append("几何到毫米比例及依据未确认")
    projection = r.get("projection",{})
    if projection.get("system") not in ("first_angle","third_angle","explicit_mapping") or not projection.get("evidence"): errors.append("投影方式或视向映射未确认")
    views = r.get("views",[]); normals = set(); axes_set = {"+X","-X","+Y","-Y","+Z","-Z"}
    if len(views) < 3 or len({v.get("region") for v in views}) != len(views): errors.append("至少需要三个不同区域的视向记录")
    for v in views:
        axes = [v.get(k) for k in ("horizontal","vertical","look_direction")]
        if any(a not in axes_set for a in axes) or len({a[-1] for a in axes if a}) != 3 or not v.get("evidence"): errors.append("视图坐标或依据不完整：" + str(v.get("region")))
        else:
            normals.add(axes[2][-1])
            def vector(a): return [(1 if a[0]=='+' else -1) if a[1]==c else 0 for c in 'XYZ']
            u,v,look = map(vector,axes)
            cross = [u[1]*v[2]-u[2]*v[1],u[2]*v[0]-u[0]*v[2],u[0]*v[1]-u[1]*v[0]]
            if any(x != -y for x,y in zip(cross,look)): errors.append('视图轴方向与观察方向发生镜像矛盾')
    if len(normals) < 3: errors.append("正交视向未覆盖 X/Y/Z")
    pairs = r.get("correspondences",[])
    if len(pairs) < 2: errors.append("缺少跨视图同一特征的对应证据")
    for pair in pairs:
        if len(pair.get("refs",[])) < 2 or len({x.get("view") for x in pair["refs"]}) < 2 or not pair.get("meaning"): errors.append("对应证据须包含不同视图的源实体及含义")
    if not r.get("dimensions"): errors.append("没有确认任何尺寸依据")
    for d in r.get("dimensions",[]):
        v = d.get("value_mm")
        if type(v) not in (int,float) or not math.isfinite(v) or v <= 0 or not d.get("evidence") or d.get("source_type") not in ("drawing_dimension","source_geometry","measured","explicit_assumption"): errors.append("尺寸来源或数值不完整")
    if not r.get("line_roles") or any(not x.get("meaning") or not x.get("entity_ids") for x in r.get("line_roles",[])): errors.append("关键线条语义及源实体编号未记录")
    if r.get("blocking_unknowns"): errors.append("仍有阻止建模的未确认项")
    return errors

def check_refs(case, review):
    entities = {e["id"]:e for e in read(case / "entities.json")}
    ids = set(entities)
    regions = {r["id"]:r for r in read(case / "regions.json")["regions"]} if (case / "regions.json").exists() else {}
    errors = []
    for v in review.get("views",[]):
        if v.get("region") not in regions: errors.append("不存在的视图区域")
    for role in review.get("line_roles",[]):
        if any(str(i) not in ids for i in role.get("entity_ids",[])): errors.append("线条引用不存在的实体")
    for pair in review.get("correspondences",[]):
        for ref in pair.get("refs",[]):
            if ref.get("view") not in regions or str(ref.get("entity_id")) not in ids: errors.append("对应关系引用不存在的区域或实体")
            else:
                region=regions[ref['view']]; entity=entities[str(ref['entity_id'])]
                if 'bounds' in region:
                    x0,y0,x1,y1=region['bounds']
                    if not entity.get('points') or not all(x0<=p[0]<=x1 and y0<=p[1]<=y1 for p in entity['points']): errors.append('引用实体不在指定视图区域中')
    return errors

ALLOWED = {"add_box","add_cylinder","add_plane","add_sketch","extrude_sketch","cut_sketch","add_extruded_profile_on_plane","cut_profile_through_plane","combine_bodies","split_body","add_hole","set_dimension","set_equation"}

def plan_errors(plan, review, source_hash):
    errors = review_errors(review,source_hash)
    if plan.get("review_sha256") != digest(review): errors.append("计划未绑定当前读图评审版本")
    if not re.fullmatch(r"[A-Za-z0-9_-]+",str(plan.get("stage_id",""))): errors.append("无效阶段编号")
    if not plan.get("purpose") or not plan.get("evidence_refs"): errors.append("缺少几何目的和依据")
    if not plan.get("steps"): errors.append("尚无已评审执行步骤")
    for step in plan.get("steps",[]):
        if step.get("method") not in ALLOWED: errors.append("未接入命令：" + str(step.get("method")))
        if not isinstance(step.get("args"),dict): errors.append("参数须为对象")
        if step.get("method") == "combine_bodies" and step.get("args",{}).get("operation") not in ("add","subtract","common"): errors.append("布尔类型须为 add/subtract/common")
    expected = plan.get("expected",{})
    if type(expected.get("solid_count")) is not int or expected["solid_count"] < 1: errors.append("缺少预期实体数")
    volume = expected.get("volume_mm3_range"); sizes = expected.get("size_mm_ranges")
    if not volume or not sizes or len(sizes) != 3: errors.append("缺少预期体积和三轴包络区间")
    elif any(not isinstance(a,list) or len(a) != 2 or any(type(x) not in (int,float) or not math.isfinite(x) for x in a) or a[0] < 0 or a[0] > a[1] for a in [volume,*sizes]): errors.append("检查区间无效")
    if not plan.get("projection_check"): errors.append("缺少投影核对方法")
    return errors

def check_plan(case, plan):
    m = verify_source(case); r = read(case / "review.json")
    errors = plan_errors(plan,r,m["source_sha256"]) + check_refs(case,r)
    if errors: raise ValueError("；".join(errors))
    predecessor = plan.get("predecessor")
    if predecessor:
        if not re.fullmatch(r"[A-Za-z0-9_-]+",predecessor): raise ValueError("无效前一阶段编号")
        result = read(case / "stages" / predecessor / "result.json")
        if result.get("review_sha256") != digest(r): raise ValueError("前一阶段读图版本已过期")
        if result.get("shape_review",{}).get("status") != "accepted" or not result["shape_review"].get("evidence"): raise ValueError("前一阶段造型未评审，不能只凭实体有效继续")
        if not result['shape_review'].get('reviewer') or not result['shape_review'].get('reviewed_at'): raise ValueError('前一阶段造型评审缺少署名或日期')
        if not result.get('metrics',{}).get('ok'): raise ValueError('前一阶段数值检查未通过')
        if not result.get('model_sha256') or sha(case/'stages'/predecessor/'model.SLDPRT')!=result['model_sha256']: raise ValueError('前一阶段模型已变化，需重新核查')
    return dict(ok=True,stage=plan["stage_id"],review_sha256=digest(r))

STYLE = 'body{font:16px/1.6 "Microsoft YaHei",sans-serif;background:#edf2f7;color:#193047;margin:0}main{max-width:1400px;margin:auto;padding:24px}.card{background:white;border:1px solid #d7e1eb;border-radius:12px;padding:20px;margin:16px 0}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}h1{font-size:28px}h2{font-size:19px}a{color:#0768a5}small{color:#556a7b}.drawing{width:100%;height:410px}.drawing polyline{cursor:crosshair;pointer-events:stroke}.drawing .selected,.drawing polyline:hover{stroke:#e55e00;stroke-width:.35}#selected{position:sticky;top:0;background:#fff0cc;padding:10px;z-index:1}pre{white-space:pre-wrap;word-break:break-word;font-size:13px}td,th{padding:6px;border-bottom:1px solid #ddd;text-align:left}table{width:100%;border-collapse:collapse}@media(max-width:800px){.grid{grid-template-columns:1fr}}'

def svg(rows):
    pts = [p for e in rows for p in e["points"]]
    if not pts: return '<p>此区域没有几何，请核对范围与图层。</p>'
    xs,ys = zip(*pts); x0,y0=min(xs)-2,min(ys)-2; w,h=max(xs)-min(xs)+4,max(ys)-min(ys)+4
    paths = []
    for e in rows:
        pts = ' '.join(f'{x:.5f},{y:.5f}' for x,y in e['points'])
        tag = html.escape(f"{e['id']} | {e['type']} | {e['layer']} | {e['linetype']}",quote=True)
        dash = ' stroke-dasharray="1.3 .6"' if any(t in e['linetype'].upper() for t in ('HIDDEN','DASH','CENTER')) else ''
        paths.append(f'<polyline data-info="{tag}" points="{pts}"{dash}><title>{tag}</title></polyline>')
    return f'<svg class="drawing" viewBox="{x0} {-y0-h} {w} {h}" xmlns="http://www.w3.org/2000/svg"><g transform="scale(1,-1)" fill="none" stroke="#233849" stroke-width=".12">'+''.join(paths)+'</g></svg>'

JS = '''document.querySelectorAll('.drawing').forEach(s=>{const initial=s.getAttribute('viewBox');s.addEventListener('dblclick',()=>s.setAttribute('viewBox',initial));s.addEventListener('wheel',e=>{e.preventDefault();let [x,y,w,h]=s.getAttribute('viewBox').split(' ').map(Number),k=e.deltaY>0?1.15:.87;s.setAttribute('viewBox',[x+w*(1-k)/2,y+h*(1-k)/2,w*k,h*k].join(' '));},{passive:false});});document.querySelectorAll('polyline').forEach(p=>p.addEventListener('click',()=>{document.querySelectorAll('.selected').forEach(x=>x.classList.remove('selected'));p.classList.add('selected');document.getElementById('selected').textContent='已选择：'+p.dataset.info;}));'''

def render_case(case):
    evidence,manifest,review,status = [read(case / p) for p in ('evidence.json','manifest.json','review.json','status.json')]
    rows = read(case / 'entities.json')
    regions = read(case / 'regions.json') if (case / 'regions.json').exists() else {'regions':[]}
    panels = []
    for r in regions['regions']:
        x0,y0,x1,y1 = r['bounds']
        subset = [e for e in rows if (not r.get('layers') or e['layer'] in r['layers']) and all(x0<=p[0]<=x1 and y0<=p[1]<=y1 for p in e['points'])]
        figure = svg(subset); (case / f"区域{r['id']}.svg").write_text(figure,encoding='utf-8')
        panels.append(f'<section class="card"><h2>区域 {html.escape(r["id"])} · {html.escape(r["location"])}</h2><small>中性区域；{len(subset)} 条几何；点击线条查源编号</small>{figure}</section>')
    if not panels: panels = ['<section class="card">'+svg(rows)+'</section>']
    errors = review_errors(review,manifest['source_sha256'])+check_refs(case,review)
    issues = ''.join('<li>'+html.escape(e)+'</li>' for e in errors)
    recent = []
    for line in (case / 'events.jsonl').read_text(encoding='utf-8').splitlines()[-12:]:
        e = json.loads(line); recent.append('<tr>'+''.join('<td>'+html.escape(e[k])+'</td>' for k in ('updated','stage','state','detail'))+'</tr>')
    texts = ' / '.join(t['text'] for t in evidence['texts'] if t['text'].strip())
    page = f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>{case.name} 建模进度</title><style>{STYLE}</style><main><a href="../../index.html">全部样本</a><h1>{case.name} · 分步建模进度</h1><div class="card"><b>{html.escape(status['stage'])} / {html.escape(status['state'])}</b><p>{html.escape(status['detail'])}</p><small>更新 {status['updated']}（UTC）；本地工具按步骤更新，刷新页面查看。</small></div><div id="selected">点击源线条查看编号；滚轮缩放，双击复位。线条语义需要读图确认。</div><div class="grid">{''.join(panels)}</div><div class="card"><h2>原图提取事实</h2><p>原始实体 {evidence['modelspace_entity_count']}；原生尺寸实体 {len(evidence['dimensions'])}；INSUNITS={evidence['insunits']}。单位和比例仍须分别确认。</p><p>原图文字：{html.escape(texts)}</p><small>{html.escape(regions.get('note',''))}</small><p>DWG SHA256：{manifest['source_sha256']}</p><a href="evidence.json">提取清单</a> · <a href="review.json" download>读图评审模板</a> · <a href="plan.json">阶段计划</a></div><div class="card"><h2>进入建模前的检查</h2><p>{'尚未通过读图关口' if errors else '资料完整性检查通过；工程语义由评审人负责'}</p><ul>{issues}</ul><p>编辑模板后用“本地工具.cmd”导入。网页只查看资料，不会启动 SolidWorks。</p></div><div class="card"><h2>实际执行记录</h2><table><tr><th>时间 UTC</th><th>阶段</th><th>状态</th><th>记录</th></tr>{''.join(recent)}</table></div><div class="card"><h2>阶段流程</h2><p>原件登记 → 投影与尺寸评审 → 主体 → 工具体与布尔组合 → 孔槽及过渡 → 三视图/剖面复核 → 交付。</p><p>实体有效及重建通过不等于造型正确。</p></div></main><script>{JS}</script></html>'''
    (case / 'index.html').write_text(page,encoding='utf-8')

def dashboard():
    cards = []
    for case in sorted((ROOT / 'cases').glob('*')):
        if not (case / 'status.json').exists(): continue
        render_case(case); s = read(case / 'status.json')
        cards.append(f'<div class="card"><h2><a href="cases/{case.name}/index.html">样本 {case.name}</a></h2><p>{html.escape(s["stage"])} · {html.escape(s["state"])}</p><p>{html.escape(s["detail"])}</p></div>')
    page = f'<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>CAD 分步建模工作台</title><style>{STYLE}</style><main><h1>CAD 分步建模工作台</h1><p>本机工具执行 · GitHub 保存版本 · 分阶段读图与检查</p><p><a href="https://github.com/a870046553-max/EARN">GitHub 项目</a> · <a href="verification/report.json">工具验证记录</a></p>{"".join(cards)}<div class="card"><b>使用方法</b><p>点样本查看源视图、检查关口和进度。双击“本地工具.cmd”运行工具，无需反复询问 AI。</p><p>GitHub 中 HTML 显示源码；交互视图请用本机入口打开。未配置常驻远程执行器。</p></div></main></html>'
    (ROOT / 'index.html').write_text(page,encoding='utf-8'); print(str(ROOT / 'index.html'))

def doctor():
    c = config()
    try: dep = {'available':True,'version':load_ezdxf().__version__}
    except ImportError: dep = {'available':False}
    result = dict(python=sys.executable,ezdxf=dep,autocad_console=Path(c['autocad_console']).is_file(),solidworks_python=Path(c['sw_python']).is_file(),uses_ai=False)
    print(json.dumps(result,ensure_ascii=False,indent=2)); return result

def main():
    p = argparse.ArgumentParser(description=__doc__); sub = p.add_subparsers(dest='command',required=True)
    sub.add_parser('doctor'); sub.add_parser('dashboard')
    c = sub.add_parser('ingest'); c.add_argument('--case',required=True); c.add_argument('--dwg',required=True); c.add_argument('--dxf'); c.add_argument('--cache-source')
    for name in ('inspect','import-review','check-plan','run'):
        c = sub.add_parser(name); c.add_argument('--case',required=True)
        if name == 'import-review': c.add_argument('--file',required=True)
        if name in ('check-plan','run'): c.add_argument('--plan',required=True)
    a = p.parse_args()
    try:
        if a.command == 'doctor': doctor()
        elif a.command == 'dashboard': dashboard()
        elif a.command == 'ingest': ingest(a.case,a.dwg,a.dxf,a.cache_source); dashboard()
        elif a.command == 'inspect': inspect_case(case_dir(a.case)); dashboard()
        elif a.command == 'import-review':
            case = case_dir(a.case); m=verify_source(case); r=read(a.file); errors=review_errors(r,m['source_sha256'])+check_refs(case,r)
            if errors: raise ValueError('；'.join(errors))
            write(case / 'review.json',r); event(case,'分步建模计划','待计划','读图评审已导入：'+r['reviewer']); dashboard()
        else:
            case = case_dir(a.case); plan = read(a.plan); result = check_plan(case,plan)
            if a.command == 'check-plan': print(json.dumps(result,ensure_ascii=False))
            else:
                worker = Path(config()['sw_python'])
                if not worker.is_file(): raise ValueError('未找到 SolidWorks Python 环境')
                result = subprocess.run([str(worker),'-X','utf8',str(ROOT / 'sw_worker.py'),'--case',a.case,'--plan',str(Path(a.plan).resolve())])
                dashboard(); return result.returncode
    except Exception as exc:
        print('已停止：'+str(exc),file=sys.stderr); return 2
    return 0

if __name__ == '__main__': raise SystemExit(main())

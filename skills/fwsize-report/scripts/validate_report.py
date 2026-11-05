#!/usr/bin/env python3
"""Validate byte accounting and PDF identity, pagination, fonts and text bounds."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

from resource_model import MEMORIES, input_paths, require, sha256, total, validate, write_json


def run(argv,tools_dir=None):
    if tools_dir:
        argv=[str(tools_dir/argv[0]),*argv[1:]]
    result = subprocess.run(argv,check=False,capture_output=True,text=True)
    require(result.returncode==0,f'{argv[0]} failed: {result.stderr.strip()}')
    return result.stdout


def validate_charts(d,audit):
    """Reconcile chart coverage, ranking, denominators and displayed RAM classes."""
    nodes = {tuple(n['path']):n for n in d['nodes']}
    expected_scopes = {()}|{p for p in nodes if any(len(c)==len(p)+1 and c[:-1]==p for c in nodes)}
    require(len(audit['charts'])==len(expected_scopes)*2,'Duplicate or missing chart audit')
    require({(tuple(c['scope']),c['memory']) for c in audit['charts']}=={(p,m) for p in expected_scopes for m in MEMORIES},'Chart coverage differs')
    for chart in audit['charts']:
        scope,memory = tuple(chart['scope']),chart['memory']
        parent = nodes[scope] if scope else d['totals']
        rows = [n for p,n in nodes.items() if len(p)==len(scope)+1 and p[:-1]==scope and total(n[memory])]
        rows.sort(key=lambda r:(-total(r[memory]),r['path']))
        require(chart['rows']==[{'path':r['path'],'bytes':total(r[memory])} for r in rows],'Chart order or values differ')
        require(chart['denominator']==total(parent[memory]),'Chart denominator differs')
        if chart.get('layout')=='grouped-ram':
            require(memory=='ram','Grouped RAM layout used for another resource')
            expected = [{'path':r['path'],'code':r['ram']['code'],'data':r['ram']['data'],
                         'other':r['ram']['reserved']+r['ram']['padding'],'unknown':r['ram']['unknown']} for r in rows]
            require(chart.get('category_rows')==expected,'RAM category values differ from accounting')
    if audit.get('layout_version')==2:
        validate_panels(audit)


def validate_panels(audit):
    """Require balanced, nonempty panels covering every ranked row exactly once."""
    panels = defaultdict(list)
    require(len({p['id'] for p in audit['panels']})==len(audit['panels']),'Duplicate ranking panel ID')
    for panel in audit['panels']:
        panels[(tuple(panel['scope']),panel['memory'])].append(panel)
    require(set(panels)=={(tuple(c['scope']),c['memory']) for c in audit['charts']},'Ranking panel coverage differs')
    for chart in audit['charts']:
        parts = sorted(panels[(tuple(chart['scope']),chart['memory'])],key=lambda p:p['part'])
        require([p['part'] for p in parts]==list(range(len(parts))),'Duplicate or missing ranking part')
        require(all(p['parts']==len(parts) and p['denominator']==chart['denominator'] for p in parts),'Panel denominator or part count differs')
        require([row for p in parts for row in p['rows']]==[r['path'] for r in chart['rows']],'Rendered rankings omit, duplicate or reorder rows')
        sizes = [len(p['rows']) for p in parts]
        require(not chart['denominator'] or all(sizes),'An empty ranking axis would be rendered')
        require(all(n<=p['limit'] for n,p in zip(sizes,parts)),'Ranking panel exceeds its row capacity')
        require(max(sizes)-min(sizes)<=1,'Unbalanced ranking pagination leaves an isolated tail')
    if audit.get('alignment')=='shared-bands':
        blocks = defaultdict(list)
        for panel in audit['panels']:
            blocks[panel['block']].append(panel['geometry'])
        for geometries in blocks.values():
            for key in ['map_bounds','height','ranking_top','ranking_title']:
                values = [g[key] for g in geometries if g[key] is not None]
                coordinates = zip(*values) if key=='map_bounds' else [values]
                require(all(max(v)-min(v)<.01 for v in coordinates if v),f'Chart {key} boundaries are not aligned')


def validate_layout(audit):
    """Check the measured printable area and indivisible block/page plan."""
    layout = audit['layout']
    pages,expected = layout['pages'],layout['expected_pages']
    require(len(pages)==len(expected)==audit['expected_pages'],'Native pagination differs from page plan')
    seen = []
    for page,plan in zip(pages,expected):
        require(page['page_ids']==[plan['id']],'A planned page was split or combined unexpectedly')
        require([b['id'] for b in page['blocks']]==plan['blocks'],'A block was split, omitted or moved to another page')
        x0,y0,x1,y1 = page['bounds']
        last = y0
        for block in page['blocks']:
            a,b,c,e = block['bounds']
            require(x0-.5<=a<c<=x1+.5 and y0-.5<=b<e<=y1+.5,'Layout block exceeds the printable area')
            require(b>=last-.5,'Layout blocks overlap')
            last = e
            seen.append(block['id'])
    require(len(seen)==len(set(seen)),'Duplicate layout block')
    if audit.get('alignment')=='shared-bands':
        visible = {p['id']:p for p in audit['panels'] if p['block'] in seen}
        charts = [c for page in pages for c in page['charts']]
        require(len(charts)==len(visible) and {c['id'] for c in charts}==set(visible),'Measured chart coverage differs')
        rows = defaultdict(list)
        for chart in charts:
            rows[visible[chart['id']]['block']].append(chart['bounds'])
        for bounds in rows.values():
            require(all(max(values)-min(values)<.5 for values in zip(*[[b[1],b[3],b[2]-b[0]] for b in bounds])),
                    'Resource charts have different native top, bottom or width')
    if not audit['preview']:
        require({p['block'] for p in audit['panels']}<=set(seen),'A chart panel is absent from the final pages')
    heights,gap,available = layout['flow_heights'],layout['gap'],layout['available']
    groups = layout['flow_groups']
    require([b for group in groups for b in group]==list(heights),'Flow block order or coverage differs')
    used = [sum(heights[b] for b in group)+gap*(len(group)-1) for group in groups]
    require(all(0<h<=available+.5 for h in used),'Packed details exceed page capacity')
    require(all(a+b+gap>available+.5 for a,b in zip(used,used[1:])),'Avoidable sparse detail page')


def validate_word_spacing(words,number):
    """Reject substantial text intersections; small font-metric overlaps are benign."""
    rectangles = sorted((float(w.attrib['yMin']),float(w.attrib['yMax']),float(w.attrib['xMin']),float(w.attrib['xMax'])) for w in words)
    for index,a in enumerate(rectangles):
        for b in rectangles[index+1:]:
            if b[0]>=a[1]-2.5:
                break
            dy,dx = min(a[1],b[1])-max(a[0],b[0]),min(a[3],b[3])-max(a[2],b[2])
            area = min((a[1]-a[0])*(a[3]-a[2]),(b[1]-b[0])*(b[3]-b[2]))
            require(not (dy>2.5 and dx>1 and dx*dy>area*.15),f'Possible overlapping text on page {number}')


def validate_pdf(d,accounting,pdf,audit,allow_preview=False,tools_dir=None):
    require(audit['accounting_sha256']==sha256(accounting),'PDF audit belongs to different accounting')
    require(audit['pdf_sha256']==sha256(pdf),'PDF checksum differs from rendering audit')
    require(allow_preview or not audit['preview'],'Preview cannot pass as a formal report')
    require(audit['app']==d['target']['app'] and audit.get('board')==d['target'].get('board'),'PDF target identity differs')
    require(audit.get('layout_version')==2,'Legacy layout audit; regenerate with the current renderer')
    validate_charts(d,audit)
    validate_layout(audit)
    info = run(['pdfinfo',str(pdf)],tools_dir)
    match = re.search(r'^Pages:\s+(\d+)',info,re.M)
    require(match and int(match[1])==audit['expected_pages'],'PDF page count differs from planned layout')
    pages = int(match[1])
    fonts = run(['pdffonts',str(pdf)],tools_dir)
    font_rows = []
    for line in fonts.splitlines()[2:]:
        match = re.search(r'\s+(yes|no)\s+(yes|no)\s+(yes|no)\s+\d+\s+\d+\s*$',line)
        require(match is not None,'Unrecognized pdffonts row')
        require(match[1]=='yes' and match[3]=='yes','PDF has unembedded or nonsearchable font')
        font_rows.append(line.split()[0])
    require(bool(font_rows),'PDF contains no embedded fonts')
    family = re.sub('[^a-z0-9]','',audit['font'].lower())
    require(family and all(family in re.sub('[^a-z0-9]','',name.lower()) for name in font_rows),'PDF font fallback differs from requested family')
    root = ET.fromstring(run(['pdftotext','-bbox-layout',str(pdf),'-'],tools_dir))
    pdf_pages = root.findall('.//{*}page')
    require(len(pdf_pages)==pages,'Text extraction page count differs')
    for number,page in enumerate(pdf_pages,1):
        width,height = float(page.attrib['width']),float(page.attrib['height'])
        words = page.findall('.//{*}word')
        require(bool(words),f'Empty PDF page {number}')
        for word in words:
            x0,x1,y0,y1 = (float(word.attrib[k]) for k in ['xMin','xMax','yMin','yMax'])
            require(0<=x0<=x1<=width+.5 and 0<=y0<=y1<=height+.5,f'Text outside page {number}')
            require('\ufffd' not in ''.join(word.itertext()),f'Invalid text glyph on page {number}')
        validate_word_spacing(words,number)
        first_y = min(float(w.attrib['yMin']) for w in words)
        header = sorted([w for w in words if abs(float(w.attrib['yMin'])-first_y)<2],key=lambda w:float(w.attrib['xMin']))
        header_text = ' '.join(''.join(w.itertext()) for w in header)
        require(header_text==audit['app'],f'Page {number} header differs from app name: {header_text}')
        footer = sorted([w for w in words if float(w.attrib['yMin'])>height-28],key=lambda w:float(w.attrib['xMin']))
        footer_text = ''.join(''.join(w.itertext()) for w in footer).replace(' ','')
        require(footer_text==f'第{number}/{pages}页',f'Page {number} footer differs: {footer_text}')
    return {'pages':pages,'fonts':font_rows,'accounting_identity':'passed','chart_values':'passed',
            'pagination':'passed','ranking_coverage':'passed','block_bounds':'passed','text_spacing':'passed',
            'text_bounds':'passed','headers_footers':'passed','font_embedding':'passed',
            'chart_alignment':'passed' if audit.get('alignment')=='shared-bands' else 'not audited',
            'renderer':audit.get('renderer')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,required=True)
    parser.add_argument('--pdf',type=Path)
    parser.add_argument('--audit',type=Path)
    parser.add_argument('--allow-preview',action='store_true')
    parser.add_argument('--render-dir',type=Path,help='New directory for all page PNGs')
    parser.add_argument('--poppler-dir',type=Path,help='Directory containing the chosen Poppler executables')
    parser.add_argument('--visual-reviewed',action='store_true',help='Record only after inspecting the latest page renders')
    parser.add_argument('--out',type=Path)
    parser.add_argument('--overwrite',action='store_true')
    args = parser.parse_args()
    try:
        d = validate(json.loads(args.input.read_text()))
        result = {'accounting':'passed','accounting_sha256':sha256(args.input),'visual_review':'recorded' if args.visual_reviewed else 'required'}
        require(not args.audit or args.pdf,'--audit requires --pdf')
        require(not args.render_dir or args.pdf,'--render-dir requires --pdf')
        audit_path = args.audit or (args.pdf.with_suffix('.audit.json') if args.pdf else None)
        if args.pdf:
            audit = json.loads(audit_path.read_text())
            result.update(validate_pdf(d,args.input,args.pdf,audit,args.allow_preview,args.poppler_dir))
        if args.render_dir:
            require(not args.render_dir.exists(),'Choose a new render directory to avoid stale pages')
            args.render_dir.mkdir(parents=True)
            run(['pdftoppm','-r','130','-png',str(args.pdf),str(args.render_dir/'page')],args.poppler_dir)
            result['render_dir'] = str(args.render_dir)
        if args.out:
            write_json(args.out,result,args.overwrite,[p for p in [args.input,args.pdf,audit_path,*input_paths(d)] if p])
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return 0
    except (ValueError,KeyError,OSError,ET.ParseError) as exc:
        print(f'fwsize-report: {exc}',file=sys.stderr)
        return 2


if __name__=='__main__':
    sys.exit(main())

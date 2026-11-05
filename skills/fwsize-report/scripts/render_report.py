#!/usr/bin/env python3
"""Render the same accounting data as preview or formal monochrome PDF."""
from __future__ import annotations

import argparse
import html
import json
import math
import os
import re
import shlex
import sys
import tempfile
import unicodedata
import xml.etree.ElementTree as ET
from pathlib import Path

from resource_model import KINDS, MEMORIES, guard_output, input_paths, require, sha256, total, validate, write_json

WIDTH = 480
FLOW_GAP = 4*96/25.4
HATCHES = [('code','代码'),('data','数据'),('other','预留/填充'),('unknown','未知类型')]


def esc(value):
    return html.escape(str(value))


def kib(value):
    return f'{value/1024:,.3f}' if 0<value<10 else f'{value/1024:,.2f}'


def amount(value):
    return '未知' if value is None else f'{value:,}'


def rectpack(items, x, y, width, height):
    """Slice rectangles with areas proportional to byte totals."""
    if not items:
        return []
    if len(items)==1:
        return [(items[0],x,y,width,height)]
    size = sum(i['value'] for i in items)
    running,choices = 0,[]
    for cut,item in enumerate(items[:-1],1):
        running += item['value']
        choices.append((abs(size/2-running),cut,running))
    _,cut,subtotal = min(choices)
    ratio = subtotal/size
    if width>=height:
        return rectpack(items[:cut],x,y,width*ratio,height)+rectpack(items[cut:],x+width*ratio,y,width*(1-ratio),height)
    return rectpack(items[:cut],x,y,width,height*ratio)+rectpack(items[cut:],x,y+height*ratio,width,height*(1-ratio))


def ranked(rows, memory):
    return sorted((r for r in rows if total(r[memory])),key=lambda r:(-total(r[memory]),r['path']))


def text_width(label,size):
    return sum(1 if unicodedata.east_asian_width(c) in 'WF' else .56 for c in label)*size


def wrap_label(label,width=118,size=10):
    lines,current = [],''
    for character in label:
        if current and text_width(current+character,size)>width:
            lines.append(current);current=''
        current += character
    return lines+[current]


def row_limit(rows,memory='flash',bands=None):
    if memory=='ram':
        return ram_layout(rows)['limit']
    lines = max((len(wrap_label(r['path'][-1])) for r in rows),default=1)
    bands = bands or chart_bands(rows)
    available = bands['height']-bands['start']-35
    return max(1,min(22,available//(lines*12)))


def ranking_batches(rows,memory,bands=None):
    """Balance whole ranked rows across panels without an isolated tail row."""
    return groups(rows,row_limit(rows,memory,bands)) or [[]]


def text(x,y,label,size=10,anchor='start'):
    return f'<text x="{x:.3f}" y="{y:.3f}" font-size="{size}" text-anchor="{anchor}">{esc(label)}</text>'


def rectangle(x,y,w,h,fill='white',stroke=True):
    return f'<rect x="{x:.3f}" y="{y:.3f}" width="{w:.3f}" height="{h:.3f}" fill="{fill}" stroke="{"black" if stroke else "none"}" stroke-width=".65"/>'


def segments(values):
    return [('code',values['code']),('data',values['data']),('other',values['reserved']+values['padding']),('unknown',values['unknown'])]


def ram_categories(values):
    """Keep exact byte counts; combine reservation and padding only for display."""
    return dict(segments(values))


def ram_layout(rows,compact=False):
    """Allocate aligned numeric columns and whole two-line module groups."""
    fields = HATCHES if any(r['ram']['unknown'] for r in rows) else HATCHES[:3]
    widths = [34,34,44,36] if len(fields)==4 else [40,52,58]
    for index,(kind,label) in enumerate(fields):
        widths[index] = max(widths[index],text_width(label,9),
                            *(text_width(kib(ram_categories(r['ram'])[kind]),9.5)+2 for r in rows))
    gap = 10 if len(fields)==4 else 14
    left = WIDTH-2-sum(widths)-gap*(len(fields)-1)
    anchors,position = [],left
    for width in widths:
        position += width
        anchors.append(position)
        position += gap
    label_width = min(126,max(75,math.ceil(max((text_width(r['path'][-1],10) for r in rows),default=0))+12))
    axis_width = left-15-label_width
    require(axis_width>=60,'RAM numeric values leave insufficient plot width')
    lines = max((len(wrap_label(r['path'][-1],label_width-12)) for r in rows),default=1)
    pitch = max(33,lines*15+5)
    map_height = 72 if compact else 138
    legend_x = [4,70,136,218] if len(fields)==4 else [5,100,195]
    legend_y = map_height+21
    legend_end = legend_x[-1]+20+text_width(fields[-1][1],9.5)
    total_y = legend_y+(16 if legend_end+8>anchors[0]-text_width('总占比',9) else 0)
    category_y = total_y+16
    start = category_y+15
    limit = max(1,int((507-start-20)//pitch))
    return {'fields':fields,'anchors':anchors,'left':left,'label_width':label_width,
            'axis_width':axis_width,'pitch':pitch,'map_height':map_height,
            'legend_x':legend_x,'legend_y':legend_y,'total_y':total_y,
            'category_y':category_y,'start':start,'limit':limit}


def chart_bands(rows,compact=False):
    """Share map and ranking boundaries across both resource columns."""
    ram_rows = ranked(rows,'ram')
    layout = ram_layout(ram_rows,compact)
    bands = {k:layout[k] for k in ['map_height','legend_y','total_y','category_y','start']}
    height = 507
    if compact:
        flash_rows = ranked(rows,'flash')
        lines = max((len(wrap_label(r['path'][-1])) for r in flash_rows),default=1)
        height = bands['start']+max(len(flash_rows)*max(33,lines*12),len(ram_rows)*layout['pitch'])+20
    return {**bands,'height':height}


def svg_start(height,uid,font):
    return [f'<svg xmlns="http://www.w3.org/2000/svg" data-chart-id="{esc(uid)}" font-family="{esc(font)}" viewBox="0 0 {WIDTH} {height}"><defs>',
            f'<pattern id="{uid}-code" width="7" height="7" patternUnits="userSpaceOnUse"><path d="M-2 2L2-2M0 7L7 0M5 9L9 5" fill="none" stroke="black" stroke-width=".6"/></pattern>',
            f'<pattern id="{uid}-other" width="4" height="4" patternUnits="userSpaceOnUse"><circle cx="2" cy="2" r=".5" fill="black"/></pattern>',
            f'<pattern id="{uid}-unknown" width="6" height="6" patternUnits="userSpaceOnUse"><path d="M0 0L6 6M6 0L0 6" stroke="black" stroke-width=".5"/></pattern></defs>']


def ram_plot(rows,parent,uid,compact=False,part=0,font='Noto Serif CJK SC',bands=None):
    """Draw the treemap and guided bars with right-aligned two-line values."""
    layout = ram_layout(rows,compact)
    layout.update(bands or chart_bands(rows,compact))
    denominator = total(parent['ram'])
    batches = [rows] if compact else ranking_batches(rows,'ram')
    require(0<=part<len(batches),'RAM ranking part is out of range')
    selected = batches[part]
    start,pitch = layout['start'],layout['pitch']
    end = start+pitch*len(selected)
    height = layout['height']
    output = svg_start(height,uid,font)
    output.append(treemap(rows,'ram',denominator,(4,2,472,layout['map_height']),uid))
    if not denominator:
        return ''.join(output)+'</svg>'
    for x,(kind,label) in zip(layout['legend_x'],layout['fields']):
        fill = 'white' if kind=='data' else f'url(#{uid}-{kind})'
        output += [rectangle(x,layout['legend_y']-8,15,9,fill),text(x+21,layout['legend_y'],label,9.5)]
    axis_x,axis_width = layout['label_width'],layout['axis_width']
    maximum = max(total(r['ram'])/denominator for r in rows)
    step = .2 if maximum>.5 else .1 if maximum>.25 else .05
    ceiling = math.ceil(maximum/step)*step
    for tick in range(round(ceiling/step)+1):
        x = axis_x+axis_width*tick*step/ceiling
        output.append(f'<path d="M{x} {start}V{end}" stroke="#ccc" stroke-width=".35"/>')
        output.append(text(x,end+12,f'{tick*step:.0%}',8.5,'middle'))
    first = sum(map(len,batches[:part]))+1
    title = f'模块排名 {first}-{first+len(selected)-1} / {len(rows)}' if len(batches)>1 else '模块排名'
    anchors = layout['anchors']
    output += [text(4,layout['category_y'],title,10),
               text(anchors[0],layout['total_y'],'总占比',9,'end'),
               text(anchors[-1],layout['total_y'],'合计 KiB',9,'end')]
    for x,(_,label) in zip(anchors,layout['fields']):
        output.append(text(x,layout['category_y'],label,9,'end'))
    for index,row in enumerate(selected):
        values = row['ram']
        y = start+index*pitch+pitch/2
        output.append(f'<g data-ram-module="{esc(" / ".join(row["path"]))}" data-rank-path="{esc(json.dumps(row["path"],ensure_ascii=False))}">')
        labels = wrap_label(row['path'][-1],axis_x-12)
        for n,label in enumerate(labels):
            output.append(text(axis_x-8,y+3.3+(n-(len(labels)-1)/2)*15,label,10,'end'))
        offset = axis_x
        for kind,value in segments(values):
            width = axis_width*(value/denominator)/ceiling
            if width:
                output.append(rectangle(offset,y-5,width,10,'white' if kind=='data' else f'url(#{uid}-{kind})'))
            offset += width
        size = total(values)
        output += ['<g font-weight="600">',text(anchors[0],y-3,f'{size/denominator:.2%}',10,'end'),
                   text(anchors[-1],y-3,kib(size),10,'end'),'</g>']
        categories = ram_categories(values)
        for x,(kind,_) in zip(anchors,layout['fields']):
            output.append(text(x,y+11,kib(categories[kind]),9.5,'end'))
        output.append('</g>')
    return ''.join(output)+'</svg>'


def treemap(rows,memory,denominator,bounds,uid):
    if not denominator:
        x,y,w,h = bounds
        return '<g data-chart-area="treemap">'+rectangle(*bounds)+text(x+w/2,y+h/2,'0 B',14,'middle')+'</g>'
    items,tail = [],[]
    for row in rows:
        v = total(row[memory])
        item = {'label':row['path'][-1],'value':v,'values':row[memory]}
        (items if v/denominator>=.035 and len(items)<6 else tail).append(item)
    if tail:
        items.append({'label':'其余','value':sum(r['value'] for r in tail),'values':{k:sum(r['values'][k] for r in tail) for k in KINDS}})
    output = []
    for item,x,y,w,h in rectpack(items,*bounds):
        require(abs(w*h/(bounds[2]*bounds[3])-item['value']/denominator)<1e-9,'Treemap area mismatch')
        if memory=='ram':
            offset = 0
            for kind,value in segments(item['values']):
                width = w*value/item['value']
                if width:
                    output.append(rectangle(x+offset,y,width,h,'white' if kind=='data' else f'url(#{uid}-{kind})',False))
                offset += width
        output.append(rectangle(x,y,w,h,'none'))
        percent = f'{100*item["value"]/denominator:.2f}%'
        lines = [(item['label'],11),(percent,19),(kib(item['value'])+' KiB',10)] if h>=65 and w>=92 else [(item['label'],10),(percent,15)] if h>=42 and w>=58 else [(percent,10)]
        lines = [(label,size) for label,size in lines if text_width(label,size)+6<=w]
        needed = sum(size+4 for _,size in lines)
        if needed+5>h:
            lines = [(percent,10)] if len(percent)*6+5<w and h>18 else []
            needed = 14
        center = y+h/2-needed/2
        for label,size in lines:
            label_width = text_width(label,size)+4
            output.append(rectangle(x+w/2-label_width/2,center,label_width,size+4,'white',False))
            output.append(text(x+w/2,center+size+1,label,size,'middle'))
            center += size+4
    return '<g data-chart-area="treemap">'+''.join(output)+'</g>'


def plot(rows,parent,memory,uid,compact=False,part=0,font='Noto Serif CJK SC',bands=None):
    bands = bands or chart_bands(rows,compact)
    rows = ranked(rows,memory)
    if memory=='ram':
        return ram_plot(rows,parent,uid,compact,part,font,bands)
    denominator = total(parent[memory])
    height = bands['height']
    output = svg_start(height,uid,font)
    output.append(treemap(rows,memory,denominator,(4,2,472,bands['map_height']),uid))
    if not denominator:
        return ''.join(output)+'</svg>'
    y0 = bands['start']
    batches = [rows] if compact else ranking_batches(rows,memory,bands)
    require(0<=part<len(batches),'Flash ranking part is out of range')
    selected = batches[part]
    lines = max((len(wrap_label(r['path'][-1])) for r in rows),default=1)
    row_height = min(max(33,lines*12),(height-y0-20)/max(1,len(selected)))
    bar_height = row_height*len(selected)
    axis_x,axis_w = 130,210
    max_ratio = max(total(r[memory])/denominator for r in rows)
    step = .2 if max_ratio>=.5 else .1 if max_ratio>=.25 else .05
    ceiling = max(step,math.ceil(max_ratio/step-1e-12)*step)
    for tick in range(round(ceiling/step)+1):
        x = axis_x+axis_w*(tick*step)/ceiling
        output.append(f'<path d="M{x} {y0}V{y0+bar_height}" stroke="#ccc" stroke-width=".35"/>')
        output.append(text(x,y0+bar_height+12,f'{tick*step:.0%}',8,'middle'))
    percent_x,value_x = 403,478
    first = sum(map(len,batches[:part]))+1
    title = f'模块占比 {first}-{first+len(selected)-1} / {len(rows)}' if len(batches)>1 else '模块占比'
    output += [text(4,bands['category_y'],title,10),
               text(percent_x,bands['total_y'],'占比',9,'end'),text(value_x,bands['total_y'],'KiB',9,'end')]
    for index,row in enumerate(selected):
        y = y0+index*row_height+row_height/2
        output.append(f'<g data-rank-path="{esc(json.dumps(row["path"],ensure_ascii=False))}">')
        lines = wrap_label(row['path'][-1])
        for n,line in enumerate(lines):
            output.append(text(axis_x-8,y+3+(n-(len(lines)-1)/2)*12,line,10,'end'))
        v = total(row[memory])
        width = axis_w*(v/denominator)/ceiling
        h = min(18,row_height*.60)
        output.append(rectangle(axis_x,y-h/2,width,h,'black',False))
        output += [text(percent_x,y+3,f'{v/denominator:.2%}',10,'end'),text(value_x,y+3,kib(v),10,'end')]
        output.append('</g>')
    return ''.join(output)+'</svg>'


def children(nodes,scope):
    return [n for p,n in nodes.items() if len(p)==len(scope)+1 and p[:-1]==scope]


def groups(items,maximum=3):
    count = math.ceil(len(items)/maximum)
    if not count:
        return []
    size,extra = divmod(len(items),count)
    result,start = [],0
    for index in range(count):
        end = start+size+(index<extra)
        result.append(items[start:end]); start = end
    return result


def table(headers,rows):
    def cells(values,tag):
        return ''.join(f'<{tag} class="{"n" if i else ""}">{esc(v)}</{tag}>' for i,v in enumerate(values))
    return '<table><thead><tr>'+cells(headers,'th')+'</tr></thead><tbody>'+''.join('<tr>'+cells(row,'td')+'</tr>' for row in rows)+'</tbody></table>'


def compose_document(pages,app,font):
    """Use one template and running header for full pages and packed details."""
    template = (Path(__file__).resolve().parent.parent/'assets/report-template.html').read_text()
    body = f'<div class="running">{esc(app)}</div>'+''.join(pages)
    return template.replace('__TITLE__',esc(app+' Flash / RAM')).replace('__FONT__','"'+font+'", serif').replace('__FLOW_GAP__',str(FLOW_GAP)).replace('__BODY__',body)


def report_page(identifier,title,body,context='1 KiB = 1024 B',block=False):
    attr = f' data-layout-block="{identifier}"' if block else ''
    return f'<section class="page" data-layout-page="{identifier}"{attr}><div class="page-intro" data-layout-intro="1"><h1>{esc(title)}</h1><div class="context">{context}</div></div>{body}</section>'


def svg_ranking(svg,rows,memory,parent):
    """Read the emitted ranking and reject clipped or altered numeric groups."""
    root = ET.fromstring(svg)
    width,height = map(float,root.attrib['viewBox'].split()[2:])
    paths = [json.loads(g.attrib['data-rank-path']) for g in root.findall('./{*}g') if 'data-rank-path' in g.attrib]
    require(paths==[r['path'] for r in rows],'Rendered ranking omitted, duplicated or reordered a row')
    for item in root.findall('.//{*}text'):
        x,y,size = (float(item.attrib[k]) for k in ['x','y','font-size'])
        w = text_width(item.text or '',size)
        anchor = item.attrib.get('text-anchor','start')
        left = x-w if anchor=='end' else x-w/2 if anchor=='middle' else x
        require(left>=-.5 and left+w<=width+.5 and size*.6<=y<=height-.5,'SVG text exceeds chart bounds')
    row_groups = [g for g in root.findall('./{*}g') if 'data-rank-path' in g.attrib]
    if memory=='flash':
        for group,row in zip(row_groups,rows):
            values = group.findall('./{*}text')[-2:]
            require([t.text for t in values]==[f'{total(row["flash"])/total(parent["flash"]):.2%}',kib(total(row['flash']))],'Flash numeric values differ from accounting')
    elif rows:
        anchors = None
        previous = None
        count = 4 if parent['ram']['unknown'] else 3
        for group,row in zip(row_groups,rows):
            categories = ram_categories(row['ram'])
            labels = group.findall('./{*}text')
            values = labels[-count:]
            require([t.text for t in values]==[kib(categories[k]) for k in list(categories)[:count]],'RAM numeric values differ from accounting')
            positions = [float(t.attrib['x']) for t in values]
            require(anchors is None or positions==anchors,'RAM numeric columns are not aligned')
            anchors = positions
            require(all(t.attrib.get('text-anchor')=='end' for t in values),'RAM values must be right aligned')
            upper = group.find('./{*}g').findall('./{*}text')
            require([t.text for t in upper]==[f'{total(row["ram"])/total(parent["ram"]):.2%}',kib(total(row['ram']))],'RAM total or percentage differs')
            require([float(t.attrib['x']) for t in upper]==[anchors[0],anchors[-1]],'RAM totals are not aligned to category columns')
            top,bottom = float(upper[0].attrib['y']),float(values[0].attrib['y'])
            require(bottom>top and (previous is None or top-previous>bottom-top),'RAM module groups are not separated')
            previous = bottom
    return paths


def svg_geometry(svg):
    """Read the rendered map bounds and ranking baselines for alignment checks."""
    root = ET.fromstring(svg)
    rectangles = root.find('./{*}g[@data-chart-area="treemap"]').findall('./{*}rect')
    boxes = [(float(r.attrib['x']),float(r.attrib['y']),float(r.attrib['width']),float(r.attrib['height'])) for r in rectangles]
    bounds = [min(x for x,y,w,h in boxes),min(y for x,y,w,h in boxes),
              max(x+w for x,y,w,h in boxes),max(y+h for x,y,w,h in boxes)]
    guides = [re.fullmatch(r'M[\d.eE+\-]+ ([\d.eE+\-]+)V([\d.eE+\-]+)',p.attrib['d']) for p in root.findall('./{*}path')]
    require(all(guides),'Unexpected ranking guide geometry')
    tops = [float(g[1]) for g in guides]
    require(not tops or max(tops)-min(tops)<.01,'Ranking guides have different starts')
    titles = [t for t in root.findall('./{*}text') if (t.text or '').startswith(('模块排名','模块占比'))]
    return {'map_bounds':bounds,'ranking_top':tops[0] if tops else None,
            'ranking_title':float(titles[0].attrib['y']) if titles else None,
            'height':float(root.attrib['viewBox'].split()[3])}


def pack_blocks(heights,available,gap):
    """Minimize page count, then balance unused space across contiguous blocks."""
    require(all(0<h<=available+.5 for h in heights),'A detail block is taller than the printable page')
    best = {len(heights):(0,0,[])}
    for start in range(len(heights)-1,-1,-1):
        used,choices = 0,[]
        for end in range(start+1,len(heights)+1):
            used += heights[end-1]+(gap if end>start+1 else 0)
            if used>available+.5:
                break
            count,cost,tail = best[end]
            choices.append((count+1,cost+(available-used)**2,[list(range(start,end)),*tail]))
        best[start] = min(choices,key=lambda item:item[:2])
    return best[0][2] if heights else []


def measured_layout(document):
    """Read native block placement; SVG internals are checked separately."""
    pages,intros = [],[]
    for number,page in enumerate(document.pages,1):
        box = page._page_box
        x,y = box.content_box_x(),box.content_box_y()
        entry = {'number':number,'bounds':[x,y,x+box.width,y+box.height],'page_ids':[],'blocks':[],'charts':[]}
        for item in box.descendants():
            if type(item).__name__!='BlockBox' or item.element is None:
                continue
            element = item.element
            if element.get('data-layout-intro'):
                intros.append(item.border_height())
            if element.get('data-layout-page'):
                entry['page_ids'].append(element.get('data-layout-page'))
            if element.get('data-layout-block'):
                entry['blocks'].append({'id':element.get('data-layout-block'),
                    'bounds':[item.border_box_x(),item.border_box_y(),item.border_box_x()+item.border_width(),item.border_box_y()+item.border_height()]})
            if element.get('data-layout-chart'):
                entry['charts'].append({'id':element.get('data-layout-chart'),
                    'bounds':[item.border_box_x(),item.border_box_y(),item.border_box_x()+item.border_width(),item.border_box_y()+item.border_height()]})
        pages.append(entry)
    require(pages and intros,'The renderer did not expose page and block geometry')
    return pages,max(intros)


def layout_document(document,audit,font,preview=None):
    """Measure details with WeasyPrint, pack them, then verify the final layout."""
    from weasyprint import HTML
    full = audit.pop('_full_pages')
    flow = audit.pop('_flow_blocks')
    probe,intro = measured_layout(HTML(string=document).render())
    heights = {}
    for page in probe:
        for block in page['blocks']:
            require(block['id'] not in heights,'A layout block was split during measurement')
            heights[block['id']] = block['bounds'][3]-block['bounds'][1]
    gap = FLOW_GAP
    available = min(p['bounds'][3]-p['bounds'][1] for p in probe)-intro
    groups_ = pack_blocks([heights[b['id']] for b in flow],available,gap)
    plan = list(full)
    for number,indexes in enumerate(groups_):
        blocks = [flow[i] for i in indexes]
        kinds = {b['kind'] for b in blocks}
        title = '模块明细' if kinds=={'detail'} else '构建与内存布局' if kinds=={'appendix'} else '模块明细与构建信息'
        identifier = f'flow-page-{number}'
        plan.append({'id':identifier,'blocks':[b['id'] for b in blocks],
                     'html':report_page(identifier,title,''.join(b['html'] for b in blocks))})
    planned = len(plan)
    if preview is not None:
        require(preview>0,'Preview page count must be positive')
        plan = plan[:preview]
    final = HTML(string=compose_document([p['html'] for p in plan],audit['app'],font)).render()
    pages,_ = measured_layout(final)
    audit.update(planned_pages=planned,expected_pages=len(plan),preview=preview is not None,
                 layout={'pages':pages,'expected_pages':[{'id':p['id'],'blocks':p['blocks']} for p in plan],
                         'flow_groups':[[flow[i]['id'] for i in indexes] for indexes in groups_],
                         'flow_heights':{b['id']:heights[b['id']] for b in flow},'available':available,'gap':gap})
    from validate_report import validate_layout
    validate_layout(audit)
    return final


def render_document(d,font='Noto Serif CJK SC',preview=None):
    """Build measurable blocks; layout_document packs them before PDF export."""
    validate(d)
    require(repr(font).count('\n')==0 and all(c.isalnum() or c in ' -_' for c in font),'Invalid font family')
    nodes = {tuple(n['path']):n for n in d['nodes']}
    whole = d['totals']
    audits,pages,flow,panels_audit,scopes_drawn = [],[],[],[],set()
    app = d['target']['app']

    def panels(scope,selections,block_id,compact=False):
        parent = nodes[scope] if scope else whole
        rows = children(nodes,scope)
        headings,notes,figures = [],[],[]
        bands = chart_bands(rows,compact)
        for memory,part in selections:
            label = 'Flash' if memory=='flash' else 'RAM'
            v = parent[memory]
            used = total(v)
            ordered = ranked(rows,memory)
            if (scope,memory) not in scopes_drawn:
                scopes_drawn.add((scope,memory))
                require(sum(total(r[memory]) for r in ordered)==used,'Chart children do not equal parent')
                audits.append({'scope':list(scope),'memory':memory,'denominator':used,'rows':[{'path':r['path'],'bytes':total(r[memory])} for r in ordered]})
                if memory=='ram':
                    audits[-1].update(layout='grouped-ram',category_rows=[{'path':r['path'],**ram_categories(r['ram'])} for r in ordered])
            if scope:
                caption = f'占应用 {label} {used/total(whole[memory]):.2%}' if total(whole[memory]) else f'占应用 {label} 0.00%'
            else:
                capacity = d['capacities'][memory]['capacity']
                caption = '容量未知' if capacity is None else f'容量 {kib(capacity)} KiB · 使用率 {used/capacity:.2%}' if capacity else '容量 0 B'
            if memory=='ram':
                caption += f'<br>代码 {kib(v["code"])} / 数据 {kib(v["data"])} / 预留/填充 {kib(v["reserved"]+v["padding"])} KiB'
                if v['unknown']:
                    caption += f' / 未知 {kib(v["unknown"])} KiB'
            else:
                caption += f'<br>占用 {used:,} B'
            uid = f'chart-{len(panels_audit)}'
            batches = [ordered] if compact else ranking_batches(ordered,memory,bands)
            selected = batches[part]
            svg = plot(rows,parent,memory,uid,compact=compact,part=part,font=font,bands=bands)
            actual = svg_ranking(svg,selected,memory,parent)
            panels_audit.append({'id':uid,'block':block_id,'scope':list(scope),'memory':memory,'part':part,
                'parts':len(batches),'limit':row_limit(ordered,memory,bands),'denominator':used,
                'rows':actual,'compact':compact,'geometry':svg_geometry(svg)})
            headings.append(f'<div class="column"><div class="column-heading"><h2>{label}</h2><span class="amount">{kib(used)} KiB</span></div></div>')
            notes.append(f'<div class="column"><div class="note">{caption}</div></div>')
            figures.append(f'<div class="column" data-layout-chart="{uid}">{svg}</div>')
        css = 'columns'+(' solo' if len(figures)==1 else '')
        return ''.join(f'<div class="{css}">'+''.join(row)+'</div>' for row in [headings,notes,figures])

    def full(scope):
        rows = children(nodes,scope)
        bands = chart_bands(rows)
        batches = {m:ranking_batches(ranked(rows,m),m,bands) for m in MEMORIES}
        remaining = [(m,part) for m in MEMORIES for part in range(1,len(batches[m]))]
        selections = [[(m,0) for m in MEMORIES],*groups(remaining,2)]
        for part,selection in enumerate(selections):
            title = '应用静态占用' if not scope else ' / '.join(scope)
            if part:
                title += f' / 排名续页 {part}'
            context = '占比基准：'+esc(' / '.join(scope) if scope else '应用')+'已用量 · 1 KiB = 1024 B'
            if not scope:
                context += ' · '+esc(d['target'].get('board') or '')
            identifier = f'full-{len(pages)}'
            body = panels(scope,selection,identifier)
            pages.append({'id':identifier,'blocks':[identifier],
                          'html':report_page(identifier,title,body,context,True)})

    full(())
    pending = []
    def visit(scope):
        nested = children(nodes,scope)
        if len(nested)>=3 or any(len(wrap_label(n['path'][-1],90))>2 for n in nested):
            full(scope)
        else:
            pending.append(scope)
        for node in sorted(nested,key=lambda n:(-total(n['flash']),n['path'])):
            branch = tuple(node['path'])
            if children(nodes,branch):
                visit(branch)
    for root in sorted(children(nodes,()),key=lambda n:(-total(n['flash']),n['path'])):
        if children(nodes,tuple(root['path'])):
            visit(tuple(root['path']))
    def add_flow(title,body,kind):
        identifier = f'flow-{len(flow)}'
        css = 'detail' if kind=='detail' else 'appendix'
        flow.append({'id':identifier,'kind':kind,'title':title,
                     'html':f'<div class="flow-block {css}" data-layout-block="{identifier}">{body}</div>'})

    for scope in pending:
        identifier = f'flow-{len(flow)}'
        body = f'<h2 class="detail-title">{esc(" / ".join(scope))}<span>占比基准：本模块已用量</span></h2>'+panels(scope,[(m,0) for m in MEMORIES],identifier,True)
        add_flow('模块明细',body,'detail')
    require({(p,m) for p in nodes if children(nodes,p) for m in MEMORIES}<=scopes_drawn,'Some module branches have no chart')
    target,build,sdk = d['target'],d['build'],d['sdk']
    metadata = {label:value for label,value in [
        ('App',target.get('app')),('Board',target.get('board')),('Variant',target.get('variant')),
        ('Build ID',target.get('build_id')),('SDK revision',sdk.get('commit')),('SDK dirty',sdk.get('dirty')),
        ('工具链',build.get('toolchain')),('LTO',build.get('lto'))] if value is not None}
    native = build.get('native_commands',[])
    metadata_html = '<div class="meta">'+''.join(esc(k)+'：'+esc(v)+'<br>' for k,v in metadata.items() if v is not None)+'</div>'
    if native:
        metadata_html += '<div class="meta">'+ '<br>'.join(esc(shlex.join(c)) for c in native)+'</div>'
    region_rows = [[r['id'],r['kind'],amount(r['capacity']),amount(r['occupied']),amount(r['remaining'])] for r in d['regions']]
    add_flow('构建与内存区域','<h2>构建与内存区域</h2>'+metadata_html+'<h2>物理区域</h2>'+table(['区域','类型','容量 B','占用 B','布局剩余 B'],region_rows),'appendix')
    for batch in groups(d['sections'],14):
        rows = [[s['name'],amount(total(s['flash'])),amount(s['ram']['code']),amount(s['ram']['data']),amount(s['ram']['reserved']+s['ram']['padding']),amount(s['ram']['unknown']),amount(total(s['ram']))] for s in batch]
        body = table(['段','Flash B','RAM 代码 B','RAM 数据 B','RAM 预留/填充 B','RAM 未知类型 B','RAM 合计 B'],rows)
        rows = [['合计',amount(total(whole['flash'])),amount(whole['ram']['code']),amount(whole['ram']['data']),amount(whole['ram']['reserved']+whole['ram']['padding']),amount(whole['ram']['unknown']),amount(total(whole['ram']))]]
        body += '<h2>应用合计</h2>'+table(['范围','Flash B','RAM 代码 B','RAM 数据 B','RAM 预留/填充 B','RAM 未知类型 B','RAM 合计 B'],rows)
        evidence = d.get('evidence',{})
        elf_hash = evidence.get('provenance',{}).get('artifacts',{}).get('elf',{}).get('sha256')
        body += '<div class="evidence">统计范围：应用静态占用。RAM 执行内容的加载副本计入 Flash。'
        if elf_hash:
            body += '<br>ELF SHA-256：'+esc(elf_hash)
        body += '</div>'
        add_flow('段布局','<h2>段布局</h2>'+body,'appendix')
    if d['stacks'] or d['heaps']:
        rows = [[s['name'],amount(s.get('bytes')),s.get('kind',s.get('status',''))] for s in d['stacks']+d['heaps']]
        for batch in groups(rows,20):
            add_flow('栈与堆布局','<h2>栈与堆布局</h2>'+table(['名称','字节 B','类型/状态'],batch),'appendix')
    probe_pages = [p['html'] for p in pages]+[report_page('probe-'+b['id'],b['title'],b['html']) for b in flow]
    planned = len(probe_pages)
    if preview is not None:
        require(preview>0,'Preview page count must be positive')
        probe_pages = probe_pages[:preview]
    document = compose_document(probe_pages,app,font)
    return document,{'app':app,'board':target.get('board'),'planned_pages':planned,'expected_pages':len(probe_pages),
                     'preview':preview is not None,'layout_version':2,'alignment':'shared-bands','charts':audits,'panels':panels_audit,
                     '_full_pages':pages,'_flow_blocks':flow}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True,help='PDF output')
    parser.add_argument('--font',default='Noto Serif CJK SC')
    parser.add_argument('--preview',type=int,help='Render first N pages with final styling')
    parser.add_argument('--overwrite',action='store_true')
    args = parser.parse_args()
    try:
        source_hash = sha256(args.input)
        data = json.loads(args.input.read_text())
        document,audit = render_document(data,args.font)
        require(args.out.suffix.lower()=='.pdf','Report output must use the .pdf suffix')
        outputs = [args.out,args.out.with_suffix('.audit.json')]
        protected = [args.input,*input_paths(data)]
        for out in outputs:
            guard_output(out,args.overwrite,protected)
        from weasyprint import __version__ as renderer_version
        from validate_report import validate_charts
        validate_charts(data,audit)
        final = layout_document(document,audit,args.font,args.preview)
        audit['renderer'] = {'weasyprint':renderer_version,'python':sys.version.split()[0],
                             'script_sha256':sha256(Path(__file__)),
                             'template_sha256':sha256(Path(__file__).resolve().parent.parent/'assets/report-template.html')}
        args.out.parent.mkdir(parents=True,exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(suffix='.pdf',dir=args.out.parent,delete=False) as stream:
                temporary = Path(stream.name)
            final.write_pdf(temporary)
            require(sha256(args.input)==source_hash,'Accounting input changed during rendering')
            os.replace(temporary,args.out)
        finally:
            if temporary and temporary.exists():
                temporary.unlink()
        audit.update(accounting_sha256=source_hash,pdf_sha256=sha256(args.out),pdf=str(args.out.resolve()),font=args.font)
        write_json(outputs[1],audit,args.overwrite,[*protected,args.out])
        print(json.dumps({'pdf':str(args.out),'audit':str(outputs[1]),'expected_pages':audit['expected_pages']},ensure_ascii=False))
        return 0
    except (ValueError,KeyError,OSError,ImportError) as exc:
        print(f'fwsize-report: {exc}',file=sys.stderr)
        return 2


if __name__=='__main__':
    sys.exit(main())

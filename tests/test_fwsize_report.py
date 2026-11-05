from __future__ import annotations

import copy
import json
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT/'skills/fwsize-report/scripts'
sys.path.insert(0,str(SCRIPTS))
from account_resources import Inputs, elf_sections, from_fwscope, gnu_contributions
from render_report import chart_bands, kib, pack_blocks, plot, ram_categories, ram_layout, ranked, ranking_batches, rectpack, render_document, row_limit, svg_geometry, svg_ranking
from resource_model import aggregate, input_paths, sha256, validate, write_json
from validate_report import validate_charts, validate_layout, validate_word_spacing


def intervals():
    return {
        'schema_version':1,'target':{'app':'telemetry_hub','board':'prototype','build_id':'fixture-001'},
        'build':{'native_commands':[['make','clean'],['make','LTO=0']]},'sdk':{},'evidence':{'backend':'fixture','physical_totals_complete':True},
        'regions':[
            {'id':'rom-image','kind':'flash','capacity':128,'capacity_ranges':[[4096,4224]],'coverage':[[4096,4176]],'expected_occupied':80},
            {'id':'sram','kind':'ram','capacity':256,'capacity_ranges':[[0,256]],'coverage':[[0,40],[64,88]],'expected_occupied':64}],
        'contributions':[
            {'region':'rom-image','start':4096,'size':48,'kind':'code','path':['application','core'],'source':'main.o','section':'.text'},
            {'region':'rom-image','start':4144,'size':24,'kind':'data','path':['services','registry'],'source':'registry.o','section':'.registry'},
            {'region':'rom-image','start':4168,'size':8,'kind':'padding','path':['linker','alignment'],'source':'<fill>','section':'.registry'},
            {'region':'sram','start':0,'size':32,'kind':'code','path':['drivers','fast'],'source':'fast.o','section':'.ram_text'},
            {'region':'sram','start':32,'size':8,'kind':'data','path':['application','state'],'source':'state.o','section':'.bss'},
            {'region':'sram','start':64,'size':16,'kind':'reserved','path':['rtos','stacks'],'source':'<reserve>','section':'.irq_stack'},
            {'region':'sram','start':80,'size':8,'kind':'padding','path':['linker','alignment'],'source':'<fill>','section':'.irq_stack'}],
        'stacks':[],'heaps':[]}


def make_elf(wide=False,big=False):
    endian = '>' if big else '<'
    data = bytearray(512)
    data[:6] = b'\x7fELF'+bytes([2 if wide else 1,2 if big else 1])
    data[6]=1
    names=b'\0.shstrtab\0.instructions\0.table\0'
    data[80:80+len(names)]=names
    offset=192
    fmt=endian+('IIQQQQIIQQ' if wide else 'IIIIIIIIII')
    size=struct.calcsize(fmt)
    struct.pack_into(endian+('Q' if wide else 'I'),data,40 if wide else 32,offset)
    struct.pack_into(endian+'HHH',data,58 if wide else 46,size,4,1)
    for index,header in enumerate([(0,0,0,0,0,0,0,0,0,0),(1,3,0,0,80,len(names),0,0,1,0),(11,1,6,0,128,4,0,0,4,0),(25,1,2,0,132,4,0,0,4,0)]):
        struct.pack_into(fmt,data,offset+index*size,*header)
    return bytes(data)


def ranking_fixture(ram_count=19,flash_count=3,long_names=False,unknown=False):
    d=intervals();d['contributions']=[];d['regions']=[]
    for memory,count in [('flash',flash_count),('ram',ram_count)]:
        d['regions'].append({'id':memory,'kind':memory,'capacity':4096,'expected_occupied':count*10,'coverage':[[0,count*10]]} if count else
                            {'id':memory,'kind':memory,'capacity':4096,'expected_occupied':0,'coverage':[]})
        for n in range(count):
            name=('peripheral_with_a_very_long_name_' if long_names else 'peripheral_')+f'{n:02}'
            d['contributions'].append({'region':memory,'start':n*10,'size':10,'kind':'unknown' if unknown and memory=='ram' and n==0 else 'code' if memory=='flash' else 'data',
                                      'path':['drivers',name],'source':f'{n}.o','section':'.'+memory})
    return aggregate(d)


class AccountingTests(unittest.TestCase):
    def test_physical_alias_duplicate_and_shared_data(self):
        d=intervals()
        d['regions'][0]['coverage'].append([4096,4176])
        d['contributions'].append(copy.deepcopy(d['contributions'][0]))
        shared=copy.deepcopy(d['contributions'][1]);shared['source']='other.o';shared['path']=['other']
        d['contributions'].append(shared)
        out=aggregate(d)
        self.assertEqual(out['capacities']['flash']['occupied'],80)
        self.assertEqual(out['totals']['ram'],{'code':32,'data':8,'reserved':16,'padding':8,'unknown':0})
        node=next(n for n in out['leaves'] if n['path']==['linker','shared'])
        self.assertEqual(node['flash']['data'],24)

    def test_unknown_coverage_and_unknown_capacity_stay_distinct(self):
        d=intervals();d['contributions'].pop(1)
        d['regions'][0]['capacity']=None;d['regions'][0].pop('capacity_ranges')
        out=aggregate(d)
        self.assertEqual(out['totals']['flash']['unknown'],24)
        self.assertIsNone(out['capacities']['flash']['capacity'])
        self.assertIsNone(out['capacities']['flash']['remaining'])

    def test_merged_tail_with_fill_preserves_shared_attribution(self):
        d=intervals()
        d['contributions'].append({'region':'rom-image','start':4164,'size':4,'kind':'padding','path':['linker','alignment'],'source':'<fill>','section':'.registry'})
        out=aggregate(d)
        shared=next(n for n in out['leaves'] if n['path']==['linker','shared'])
        self.assertEqual(shared['flash']['data'],4)
        self.assertEqual(out['totals']['flash']['padding'],8)

    def test_conflicting_types_total_and_address_bounds_are_rejected(self):
        for mutation in ['kind','total','range']:
            with self.subTest(mutation=mutation):
                d=intervals()
                if mutation=='kind':
                    row=copy.deepcopy(d['contributions'][0]);row['kind']='data';d['contributions'].append(row)
                elif mutation=='total':d['regions'][0]['expected_occupied']=79
                else:d['regions'][0]['capacity_ranges']=[[0,128]]
                with self.assertRaises(ValueError):aggregate(d)

    def test_incomplete_or_unspecified_backend_totals_are_rejected(self):
        for value in [False,None,0,'true']:
            d=intervals();d['evidence']['physical_totals_complete']=value
            with self.assertRaises(ValueError):aggregate(d)
        d=intervals();d['evidence'].pop('physical_totals_complete')
        with self.assertRaises(ValueError):aggregate(d)

    def test_parent_direct_content_is_split_and_tampering_rejected(self):
        d=intervals();d['contributions'][0]['path']=['application']
        out=aggregate(d)
        self.assertIn(['application','self'],[n['path'] for n in out['leaves']])
        out['nodes'][0]['flash']['code']+=1
        with self.assertRaises(ValueError):validate(out)

    def test_output_cannot_overwrite_or_hardlink_an_input(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'input.json';path.write_text('evidence')
            link=Path(temp)/'linked.json';link.hardlink_to(path)
            for output in [path,link]:
                with self.assertRaises(ValueError):write_json(output,{},True,[path])
            self.assertEqual(path.read_text(),'evidence')

    def test_cli_writes_accounting_and_md_for_unrelated_target(self):
        with tempfile.TemporaryDirectory() as temp:
            source=Path(temp)/'intervals.json';source.write_text(json.dumps(intervals()))
            out=Path(temp)/'accounting.json'
            result=subprocess.run([sys.executable,str(SCRIPTS/'account_resources.py'),'--intervals',str(source),'--out',str(out)],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(json.loads(out.read_text())['target']['app'],'telemetry_hub')
            self.assertIn('telemetry_hub',out.with_suffix('.md').read_text())

    def test_cli_preserves_an_artifact_referenced_by_provenance(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            artifact=root/'application.elf';artifact.write_bytes(b'original artifact')
            d=intervals();d['evidence']['provenance']={'artifacts':{'elf':{'path':str(artifact)}}}
            source=root/'intervals.json';source.write_text(json.dumps(d))
            result=subprocess.run([sys.executable,str(SCRIPTS/'account_resources.py'),'--intervals',str(source),'--out',str(artifact),'--overwrite'],capture_output=True,text=True)
            self.assertNotEqual(result.returncode,0)
            self.assertEqual(artifact.read_bytes(),b'original artifact')
            self.assertFalse(artifact.with_suffix('.md').exists())
            d['evidence']['provenance']['artifacts']['elf']['path']=str(artifact)+'!/application.elf'
            self.assertIn(artifact,input_paths(d))


class InputTests(unittest.TestCase):
    def test_fwscope_adapter_load_copy_stack_transfer_and_map_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'x.o').write_bytes(make_elf())
            map_path=root/'application.map'
            map_path.write_text('Linker script and memory map\n .instructions 0x1000 0x4 x.o\n .table 0x2000 0x4 x.o\n')
            profile={'target':{'app':'sensor_hub'},'object_roots':[str(root)],
                     'module_rules':[{'pattern':r'x\.o$','path':['application']}],
                     'stack_symbols':['idle_stack'],'reserved_sections':{'.irq_stack':['rtos','stacks']}}
            report={'schema_version':1,'artifact_status':'CONSISTENT','physical_totals_complete':True,
                    'provenance':{'artifacts':{name:{'sha256':sha256(map_path) if name=='map' else 'fixture'} for name in ['map','elf','linker_script']}},
                    'physical_regions':[
                        {'id':'flash0','kind':'flash','capacity_bytes':64,'occupied_bytes':8,'occupied_ranges':[[0,8]],'capacity_ranges':[[0,64]]},
                        {'id':'ram0','kind':'ram','capacity_bytes':64,'occupied_bytes':12,'occupied_ranges':[[0,4],[8,16]],'capacity_ranges':[[0,64]]}],
                    'sections':[
                        {'index':1,'name':'.text','address':4096,'size':4,'runtime':{'physical':'flash0','start':0},'storage':{'physical':'flash0','start':0},'input_contributions':[]},
                        {'index':2,'name':'.data','address':8192,'size':4,'runtime':{'physical':'ram0','start':0},'storage':{'physical':'flash0','start':4},'input_contributions':[]},
                        {'index':3,'name':'.irq_stack','address':8200,'size':8,'runtime':{'physical':'ram0','start':8},'input_contributions':[]}],
                    'objects':[{'section_index':2,'name':'idle_stack','address':8192,'size':2}]}
            out=aggregate(from_fwscope(report,profile,root,map_path))
            self.assertEqual(out['totals']['flash']['code'],4)
            self.assertEqual(out['totals']['flash']['data'],4)
            self.assertEqual(out['totals']['ram']['data'],4)
            stack=next(n for n in out['leaves'] if n['path']==['rtos','stacks'])
            self.assertEqual(stack['ram']['data'],2)
            self.assertEqual(stack['ram']['reserved'],8)
            self.assertEqual(next(n for n in out['leaves'] if n['path']==['application'])['ram']['data'],2)
            map_path.write_text('different build')
            with self.assertRaises(ValueError):from_fwscope(report,profile,root,map_path)

    def test_elf_class_and_endianness(self):
        for wide in [False,True]:
            for big in [False,True]:
                with self.subTest(wide=wide,big=big):
                    sections=elf_sections(make_elf(wide,big))
                    self.assertEqual(sections['.instructions']['flags']&4,4)
                    self.assertEqual(sections['.table']['flags']&4,0)

    def test_gnu_map_non_dot_and_two_line_sections(self):
        text='Discarded input sections\n FSymTab 0x0 0x4 discarded.o\nLinker script and memory map\n FSymTab 0x1000 0x10 obj/cli.o\n .text.long_name\n                0x1010 0x20 libx.a(long.o)\n'
        rows=gnu_contributions(text)
        self.assertEqual([r['section'] for r in rows],['FSymTab','.text.long_name'])
        self.assertEqual(rows[1]['owner'],'libx.a(long.o)')

    def test_path_root_prevents_following_map_symlink_outside_root(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'objects';root.mkdir()
            outside=Path(temp)/'secret.o';outside.write_bytes(make_elf())
            (root/'bad.o').symlink_to(outside)
            inputs=Inputs({'object_roots':[str(root)]},Path(temp))
            self.assertEqual(inputs.kind('bad.o','.instructions'),'unknown')
            self.assertEqual(inputs.hashes,{})

    def test_actual_host_compiler_object_and_archive(self):
        cc=shutil.which('cc');ar=shutil.which('ar')
        if not cc or not ar:self.skipTest('Host compiler/archive tools unavailable')
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'x.c').write_text('const int table[2]={1,2}; int state; int run(int x){return x+table[0];}\n')
            subprocess.run([cc,'-fno-lto','-ffunction-sections','-fdata-sections','-c',str(root/'x.c'),'-o',str(root/'x.o')],check=True,capture_output=True)
            subprocess.run([ar,'rcs',str(root/'libx.a'),str(root/'x.o')],check=True,capture_output=True)
            inputs=Inputs({'object_roots':[str(root)]},root)
            self.assertEqual(inputs.kind('x.o','.text.run'),'code')
            self.assertEqual(inputs.kind('libx.a(x.o)','.rodata.table'),'data')
            self.assertEqual(inputs.kind('x.o','.bss.state'),'data')


class RenderingTests(unittest.TestCase):
    def test_resource_maps_and_rankings_share_boundaries_in_full_and_compact_panels(self):
        for compact in [False,True]:
            for ram_count,flash_count in [(2,1),(0,2),(2,0)]:
                with self.subTest(compact=compact,ram=ram_count,flash=flash_count):
                    d=ranking_fixture(ram_count,flash_count,unknown=True)
                    rows=[n for n in d['nodes'] if len(n['path'])==2]
                    parent=next(n for n in d['nodes'] if n['path']==['drivers'])
                    bands=chart_bands(rows,compact)
                    charts=[ET.fromstring(plot(rows,parent,m,f'paired-{m}',compact=compact,bands=bands)) for m in ['flash','ram']]
                    self.assertEqual(charts[0].attrib['viewBox'],charts[1].attrib['viewBox'])
                    geometry=[svg_geometry(ET.tostring(c,encoding='unicode')) for c in charts]
                    for key in ['map_bounds','height']:
                        self.assertEqual(geometry[0][key],geometry[1][key])
                    if ram_count and flash_count:
                        for key in ['ranking_top','ranking_title']:
                            self.assertEqual(geometry[0][key],geometry[1][key])
                    for chart in charts:
                        paths=chart.findall('./{*}path')
                        if paths:
                            start=float(paths[0].attrib['d'].split(' ')[1].split('V')[0])
                            treemap=chart.find('./{*}g[@data-chart-area="treemap"]')
                            bottom=max(float(r.attrib['y'])+float(r.attrib['height']) for r in treemap.findall('./{*}rect'))
                            self.assertGreater(start,bottom)

    def test_alignment_audit_rejects_shifted_map_or_ranking(self):
        d=ranking_fixture(2,1)
        for key in ['map_bounds','height','ranking_top','ranking_title']:
            _,audit=render_document(d)
            validate_charts(d,audit)
            panel=audit['panels'][0]
            if key=='map_bounds':panel['geometry'][key][3]+=10
            else:panel['geometry'][key]+=10
            with self.assertRaisesRegex(ValueError,'not aligned'):validate_charts(d,audit)

    def test_native_alignment_rejects_shifted_missing_or_resized_charts(self):
        audit={'expected_pages':1,'preview':False,'alignment':'shared-bands',
               'panels':[{'id':'left','block':'a'},{'id':'right','block':'a'}],
               'layout':{'pages':[{'page_ids':['p0'],'bounds':[0,0,100,100],
                                  'blocks':[{'id':'a','bounds':[0,10,100,60]}],
                                  'charts':[{'id':'left','bounds':[0,10,45,60]},{'id':'right','bounds':[55,10,100,60]}]}],
                         'expected_pages':[{'id':'p0','blocks':['a']}],
                         'flow_groups':[['a']],'flow_heights':{'a':50},'available':90,'gap':5}}
        validate_layout(audit)
        for mode in ['shift','width','missing','duplicate']:
            bad=copy.deepcopy(audit);charts=bad['layout']['pages'][0]['charts']
            if mode=='shift':charts[1]['bounds'][1]+=5
            elif mode=='width':charts[1]['bounds'][0]+=5
            elif mode=='missing':charts.pop()
            else:charts.append(copy.deepcopy(charts[0]))
            with self.assertRaises(ValueError):validate_layout(bad)

    def test_boundary_rankings_have_no_loss_duplicate_or_single_row_tail(self):
        for count in [1,8,9,10,18,19,20,27,28]:
            for long_names,unknown in [(False,False),(True,True)]:
                with self.subTest(count=count,long_names=long_names):
                    d=ranking_fixture(count,long_names=long_names,unknown=unknown)
                    parent=next(n for n in d['nodes'] if n['path']==['drivers'])
                    rows=ranked([n for n in d['nodes'] if len(n['path'])==2],'ram')
                    batches=ranking_batches(rows,'ram')
                    sizes=[len(b) for b in batches]
                    self.assertLessEqual(max(sizes)-min(sizes),1)
                    actual=[]
                    for part,batch in enumerate(batches):
                        actual+=svg_ranking(plot(rows,parent,'ram',f'p{part}',part=part),batch,'ram',parent)
                    self.assertEqual(actual,[r['path'] for r in rows])
                    with self.assertRaises(ValueError):plot(rows,parent,'ram','bad',part=len(batches))

    def test_exhausted_flash_does_not_generate_empty_continuation_axes(self):
        d=ranking_fixture();document,audit=render_document(d)
        validate_charts(d,audit)
        ram=[p for p in audit['panels'] if p['scope']==['drivers'] and p['memory']=='ram']
        self.assertEqual([len(p['rows']) for p in ram],[7,6,6])
        continuation={p['block'] for p in ram if p['part']}
        self.assertEqual(len(continuation),1)
        self.assertTrue(all(p['memory']=='ram' for p in audit['panels'] if p['block'] in continuation))
        self.assertTrue(all(p['rows'] for p in audit['panels'] if p['denominator']))

    def test_flash_wrapped_names_fit_the_short_ranking_area(self):
        d=ranking_fixture(0,10,long_names=True)
        parent=next(n for n in d['nodes'] if n['path']==['drivers'])
        rows=ranked([n for n in d['nodes'] if len(n['path'])==2],'flash')
        for part,batch in enumerate(ranking_batches(rows,'flash')):
            root=ET.fromstring(plot(rows,parent,'flash',f'flash-{part}',part=part))
            groups_=[g for g in root.findall('./{*}g') if 'data-rank-path' in g.attrib]
            spans=[]
            for group in groups_:
                labels=group.findall('./{*}text')[:-2]
                spans.append((min(float(t.attrib['y']) for t in labels)-10,max(float(t.attrib['y']) for t in labels)))
            self.assertTrue(all(a[1]<b[0] for a,b in zip(spans,spans[1:])))
            self.assertEqual(len(groups_),len(batch))

    def test_panel_validation_catches_the_previous_9_9_1_pagination(self):
        d=ranking_fixture();_,audit=render_document(d)
        parts=[p for p in audit['panels'] if p['scope']==['drivers'] and p['memory']=='ram']
        rows=[r for p in parts for r in p['rows']]
        for part,chunk in zip(parts,[rows[:9],rows[9:18],rows[18:]]):part['rows']=chunk
        with self.assertRaisesRegex(ValueError,'Unbalanced'):validate_charts(d,audit)
        for mutation in ['empty','duplicate']:
            _,audit=render_document(d)
            part=next(p for p in audit['panels'] if p['scope']==['drivers'] and p['memory']=='ram')
            part['rows']=[] if mutation=='empty' else part['rows']+[part['rows'][0]]
            with self.assertRaises(ValueError):validate_charts(d,audit)

    def test_svg_checks_drawn_rows_values_and_chart_bounds(self):
        d=ranking_fixture(3);parent=next(n for n in d['nodes'] if n['path']==['drivers'])
        rows=ranked([n for n in d['nodes'] if len(n['path'])==2],'ram')
        svg=plot(rows,parent,'ram','fixture')
        for mode in ['missing','value','bounds']:
            root=ET.fromstring(svg);group=next(g for g in root.findall('./{*}g') if 'data-rank-path' in g.attrib)
            if mode=='missing':root.remove(group)
            elif mode=='value':group.findall('./{*}text')[-1].text='999'
            else:group.findall('./{*}text')[-1].set('x','490')
            with self.assertRaises(ValueError):svg_ranking(ET.tostring(root,encoding='unicode'),rows,'ram',parent)

    def test_measured_block_packing_balances_a_short_final_appendix(self):
        heights=[50,20,20,20]
        plan=pack_blocks(heights,100,0)
        self.assertEqual([i for page in plan for i in page],list(range(4)))
        self.assertEqual(len(plan),2)
        used=[sum(heights[i] for i in page) for page in plan]
        self.assertLessEqual(max(used)-min(used),10)
        with self.assertRaises(ValueError):pack_blocks([101],100,0)
        self.assertEqual(pack_blocks([],100,0),[])

    def test_layout_validation_rejects_split_overlapping_and_outside_blocks(self):
        audit={'expected_pages':1,'preview':False,'panels':[{'block':'a'}],
               'layout':{'pages':[{'page_ids':['p0'],'bounds':[0,0,100,100],
                                  'blocks':[{'id':'a','bounds':[0,10,100,40]},{'id':'b','bounds':[0,45,100,80]}]}],
                         'expected_pages':[{'id':'p0','blocks':['a','b']}],
                         'flow_groups':[['a','b']],'flow_heights':{'a':30,'b':35},'available':90,'gap':5}}
        validate_layout(audit)
        for mode in ['split','overlap','outside','missing']:
            bad=copy.deepcopy(audit);page=bad['layout']['pages'][0]
            if mode=='split':page['page_ids'].append('p0')
            elif mode=='overlap':page['blocks'][1]['bounds'][1]=30
            elif mode=='outside':page['blocks'][1]['bounds'][3]=110
            else:page['blocks'].pop()
            with self.assertRaises(ValueError):validate_layout(bad)

    def test_pdf_spacing_distinguishes_metrics_from_large_collisions(self):
        a=ET.Element('word',{'xMin':'0','xMax':'30','yMin':'0','yMax':'12'})
        b=ET.Element('word',{'xMin':'5','xMax':'25','yMin':'9.74','yMax':'20'})
        validate_word_spacing([a,b],1)
        b.set('yMin','7')
        with self.assertRaises(ValueError):validate_word_spacing([a,b],1)

    def test_ram_classes_are_aligned_grouped_and_reconciled(self):
        out=aggregate(intervals())
        rows=ranked([n for n in out['nodes'] if len(n['path'])==1],'ram')
        layout=ram_layout(rows)
        root=ET.fromstring(plot(rows,out['totals'],'ram','fixture'))
        groups=[g for g in root.findall('./{*}g') if 'data-rank-path' in g.attrib]
        self.assertEqual(len(groups),len(rows))
        for group,row in zip(groups,rows):
            texts=group.findall('.//{*}text')
            categories=texts[-3:]
            self.assertEqual([t.text for t in categories],[kib(ram_categories(row['ram'])[kind]) for kind in ['code','data','other']])
            self.assertEqual([float(t.attrib['x']) for t in categories],layout['anchors'])
            self.assertTrue(all(t.attrib['text-anchor']=='end' for t in categories))
            upper=group.find('./{*}g').findall('./{*}text')
            inside=float(categories[0].attrib['y'])-float(upper[0].attrib['y'])
            self.assertLess(inside,layout['pitch']-inside)
        guides=root.findall('./{*}path')
        self.assertTrue(guides)
        self.assertTrue(all('V' in p.attrib['d'] and 'H' not in p.attrib['d'] for p in guides))
        _,audit=render_document(out)
        validate_charts(out,audit)
        chart=next(c for c in audit['charts'] if c['memory']=='ram' and not c['scope'])
        chart['category_rows'][0]['other']+=1
        with self.assertRaises(ValueError):validate_charts(out,audit)

    def test_ram_unknown_tiny_values_and_wrapped_names_survive_pagination(self):
        d=intervals();d['contributions']=[]
        d['regions']=[{'id':'ram','kind':'ram','capacity':1000,'expected_occupied':240,'coverage':[[0,240]]}]
        for n in range(24):
            d['contributions'].append({'region':'ram','start':n*10,'size':10,'kind':'unknown' if n==0 else 'data',
                                      'path':['drivers',f'peripheral_with_a_long_name_{n:02}'],'source':f'{n}.o','section':'.bss'})
        d['contributions'][0]['size']=1
        d['contributions'].append({'region':'ram','start':1,'size':9,'kind':'padding','path':['drivers','peripheral_with_a_long_name_00'],'source':'<fill>','section':'.bss'})
        out=aggregate(d)
        parent=next(n for n in out['nodes'] if n['path']==['drivers'])
        rows=ranked([n for n in out['nodes'] if len(n['path'])==2],'ram')
        limit=row_limit(rows,'ram')
        self.assertLess(limit,len(rows))
        observed=[]
        for part in range((len(rows)+limit-1)//limit):
            root=ET.fromstring(plot(rows,parent,'ram',f'fixture-{part}',part=part))
            for group in root.findall('./{*}g[@data-ram-module]'):
                observed.append(group.attrib['data-ram-module'])
                self.assertEqual(len(group.findall('./{*}text')[-4:]),4)
            all_text=' '.join(t.text or '' for t in root.findall('.//{*}text'))
            self.assertIn('未知类型',all_text)
        self.assertEqual(observed,[' / '.join(r['path']) for r in rows])
        self.assertIn('0.001',plot(rows,parent,'ram','tiny'))
        formal,audit=render_document(out)
        validate_charts(out,audit)
        self.assertIn('peripheral_with_a_long_name_23',formal)

    def test_treemap_proportions_and_unequal_sizes(self):
        items=[{'value':x} for x in [100,40,7,1]]
        for item,x,y,w,h in rectpack(items,0,0,500,120):
            self.assertAlmostEqual(w*h/(500*120),item['value']/148)

    def test_preview_uses_same_pages_and_long_rankings_preserve_every_child(self):
        d=intervals();d['contributions']=[]
        d['regions']=[{'id':'flash','kind':'flash','capacity':1000,'expected_occupied':300,'coverage':[[0,300]]}]
        for n in range(30):
            d['contributions'].append({'region':'flash','start':n*10,'size':10,'kind':'code','path':['drivers',f'peripheral_{n:02}'],'source':f'{n}.o','section':'.text'})
        out=aggregate(d)
        formal,audit=render_document(out)
        preview,pa=render_document(out,preview=2)
        # Compare complete first-page markup, independently of the document suffix.
        self.assertEqual(formal.split('</section>')[0],preview.split('</section>')[0])
        drivers=next(c for c in audit['charts'] if c['scope']==['drivers'] and c['memory']=='flash')
        self.assertEqual(len(drivers['rows']),30)
        self.assertGreater(audit['expected_pages'],pa['expected_pages'])
        self.assertIn('peripheral_29',formal)


if __name__=='__main__':
    unittest.main()

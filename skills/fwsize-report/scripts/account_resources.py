#!/usr/bin/env python3
"""Normalize FwScope resources or physical intervals into auditable byte totals."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shlex
import struct
import sys
from pathlib import Path

from resource_model import KINDS, MEMORIES, aggregate, guard_output, input_paths, require, sha256, total, write_json, write_text


def elf_sections(blob):
    """Read input section flags from ELF32/ELF64 in either byte order."""
    require(blob[:4] == b'\x7fELF' and blob[4] in (1,2) and blob[5] in (1,2), 'Unsupported ELF identification')
    endian = '<' if blob[5] == 1 else '>'
    wide = blob[4] == 2
    offset = struct.unpack_from(endian + ('Q' if wide else 'I'), blob, 40 if wide else 32)[0]
    entry_size, count, strings_index = struct.unpack_from(endian + 'HHH', blob, 58 if wide else 46)
    fmt = endian + ('IIQQQQIIQQ' if wide else 'IIIIIIIIII')
    minimum = struct.calcsize(fmt)
    require(entry_size >= minimum and offset+entry_size <= len(blob), 'Invalid ELF section table')
    zero = struct.unpack_from(fmt, blob, offset)
    count = count or zero[5]
    strings_index = zero[6] if strings_index == 0xffff else strings_index
    require(count > 0 and strings_index < count and offset+count*entry_size <= len(blob), 'Invalid ELF section count')
    headers = [struct.unpack_from(fmt, blob, offset+i*entry_size) for i in range(count)]
    string_header = headers[strings_index]
    require(string_header[4]+string_header[5] <= len(blob), 'Invalid ELF string table')
    names = blob[string_header[4]:string_header[4]+string_header[5]]
    result = {}
    for header in headers:
        require(header[0] < len(names), 'Invalid ELF section name')
        end = names.find(b'\0', header[0])
        require(end >= 0, 'Unterminated ELF section name')
        name = names[header[0]:end].decode('utf-8', errors='replace')
        if name in result:
            require(result[name]['flags']==header[2] and result[name]['type']==header[1], f'Ambiguous ELF input section flags: {name}')
            continue
        result[name] = {'flags': header[2], 'size': header[5], 'type': header[1], 'address': header[3]}
    return result


def ar_members(raw):
    """Read ordinary GNU/BSD archives; thin archives need materialized inputs."""
    require(raw.startswith(b'!<arch>\n'), 'Expected ordinary GNU/BSD archive; thin archives are unsupported')
    offset, names, members = 8, b'', {}
    while offset < len(raw):
        head = raw[offset:offset+60]
        require(len(head)==60 and head[58:]==b'`\n', 'Malformed archive header')
        size = int(head[48:58])
        name = head[:16].decode().strip()
        data = raw[offset+60:offset+60+size]
        require(len(data)==size, 'Truncated archive member')
        if name=='//':
            names = data
        elif name not in {'/', '/SYM64/'}:
            if name.startswith('#1/'):
                length = int(name[3:])
                name, data = data[:length].rstrip(b'\0').decode(), data[length:]
            elif name.startswith('/') and name[1:].isdigit():
                start = int(name[1:])
                end = names.find(b'/\n',start)
                require(end>=start, 'Invalid GNU long archive name')
                name = names[start:end].decode()
            else:
                name = name.rstrip('/')
            require(name not in members, f'Ambiguous duplicate archive member: {name}')
            members[name] = data
        offset += 60+size+size%2
    return members


class Inputs:
    """Resolve selected build inputs within explicitly declared roots."""
    def __init__(self, profile, base):
        self.roots = [(base/Path(p)).resolve() for p in profile.get('object_roots',[])]
        self.maps = [(m['prefix'].removeprefix('./'), (base/Path(m['root'])).resolve()) for m in profile.get('path_maps',[])]
        self.roots += [root for _,root in self.maps]
        self.maps.sort(key=lambda item:len(item[0]),reverse=True)
        self.sections, self.archives, self.hashes, self.missing = {}, {}, {}, []

    def resolve(self, name):
        name = name.removeprefix('./')
        candidates = []
        for prefix,root in self.maps:
            if name.startswith(prefix):
                candidates.append(root/name[len(prefix):])
                break
        candidates += [Path(name)] if Path(name).is_absolute() else [root/name for root in self.roots]
        for candidate in candidates:
            path = candidate.resolve()
            if any(path.is_relative_to(root) for root in self.roots) and path.is_file():
                return path
        raise FileNotFoundError(f'Object/archive not found in declared roots: {name}')

    def read(self, path):
        data = path.read_bytes()
        self.hashes[str(path)] = hashlib.sha256(data).hexdigest()
        return data

    def kind(self, owner, section):
        try:
            if owner not in self.sections:
                archive = re.fullmatch(r'(.+\.a)\(([^)]+)\)',owner)
                if archive:
                    name,member = archive.groups()
                    path = self.resolve(name)
                    if path not in self.archives:
                        self.archives[path] = ar_members(self.read(path))
                    blob = self.archives[path][member]
                else:
                    blob = self.read(self.resolve(owner))
                self.sections[owner] = elf_sections(blob)
            return 'code' if self.sections[owner][section]['flags'] & 4 else 'data'
        except (FileNotFoundError, KeyError) as exc:
            missing = {'source':owner,'section':section,'reason':str(exc)}
            if missing not in self.missing:
                self.missing.append(missing)
            return 'unknown'


def classify(owner, rules):
    normalized = re.sub('/+','/',owner.removeprefix('./'))
    for rule in rules:
        match = re.search(rule['pattern'],normalized)
        if match:
            return [part.format(*match.groups(), **match.groupdict()) for part in rule['path']]
    return ['unattributed']


def gnu_contributions(text):
    """Recover dot and non-dot input sections, including two-line names."""
    text = text.split('Linker script and memory map',1)[-1]
    result, pending = [], None
    row = re.compile(r'^\s+(?:(\S+)\s+)?(0x[0-9a-fA-F]+)\s+(0x[0-9a-fA-F]+)\s+(.+\.(?:o|obj)\)?)\s*$')
    for line in text.splitlines():
        match = row.match(line)
        if match:
            name,address,size,owner = match.groups()
            name = name or pending
            if name and int(size,16):
                result.append({'section':name,'address':int(address,16),'size':int(size,16),'owner':owner})
            pending = None
        else:
            name = re.match(r'^\s+(\S+)\s*$',line)
            pending = name[1] if name else None
    return result


def from_fwscope(report, profile, base, map_path):
    require(report.get('schema_version') == 1, 'Unsupported FwScope resource schema')
    require(report.get('artifact_status')=='CONSISTENT' and report.get('physical_totals_complete') is True, 'Resource evidence is incomplete or inconsistent')
    provenance = report.get('provenance',{})
    bundle = provenance.get('bundle',{})
    if bundle:
        require(bundle.get('integrity')=='VERIFIED', 'Bundle integrity is not verified')
        require(bundle.get('archive_checksum') in {'VERIFIED','NOT_PROVIDED'}, 'Bundle archive checksum failed')
    for key in ['map','elf','linker_script']:
        require((provenance.get('artifacts',{}).get(key) or {}).get('sha256'), f'Missing {key} evidence identity')
    require(sha256(map_path)==provenance['artifacts']['map']['sha256'], 'MAP differs from the analyzed artifact')
    target = dict(profile.get('target',{}))
    for field,bundle_key in [('app','application'),('board','board')]:
        if bundle.get(bundle_key):
            require(not target.get(field) or target[field]==bundle[bundle_key], f'Target {field} contradicts artifact identity')
            target[field] = bundle[bundle_key]
    inputs = Inputs(profile,base)
    regions = []
    for region in report['physical_regions']:
        if region['kind'].lower() not in MEMORIES:
            continue
        regions.append({'id':region['id'],'kind':region['kind'].lower(),'capacity':region['capacity_bytes'],
                        'expected_occupied':region['occupied_bytes'],'coverage':region['occupied_ranges'],
                        'capacity_ranges':region['capacity_ranges']})
    known = {r['id'] for r in regions}
    extra = gnu_contributions(map_path.read_text(encoding='utf-8'))
    contributions, stacks = [], []
    reserved = profile.get('reserved_sections',{})
    rules = profile.get('module_rules',[])
    stack_names = set(profile.get('stack_symbols',[]))
    for section in report['sections']:
        projections = { (s['physical'],s['start']) for key in ['runtime','storage'] if (s:=section.get(key)) and s['physical'] in known }
        if not projections or not section['size']:
            continue
        start,stop = section['address'],section['address']+section['size']
        raw = [dict(c) for c in section['input_contributions'] if c['size']]
        existing = {(c['section'],c['address'],c['size'],c['owner']) for c in raw}
        raw += [c for c in extra if start<=c['address'] and c['address']+c['size']<=stop and (c['section'],c['address'],c['size'],c['owner']) not in existing]
        objects = [o for o in report['objects'] if o['section_index']==section['index'] and o['name'] in stack_names and o['size']]
        if section['name'] in reserved:
            raw = [{'section':section['name'],'address':start,'size':section['size'],'owner':'<reserve>'}]
            stacks.append({'name':section['name'],'bytes':section['size'],'kind':'reserved'})
        for obj in objects:
            require(start<=obj['address'] and obj['address']+obj['size']<=stop, 'Stack symbol outside section')
            stacks.append({'name':obj['name'],'bytes':obj['size'],'kind':'data'})
        for c in raw:
            a,b = max(start,c['address']),min(stop,c['address']+c['size'])
            require(a<b, 'Input contribution outside output section')
            if c['owner']=='<reserve>':
                kind,path = 'reserved',reserved[section['name']]
            elif c['owner']=='<fill>':
                kind,path = 'padding',['linker','alignment']
            else:
                kind,path = inputs.kind(c['owner'],c['section']),classify(c['owner'],rules)
            cuts = sorted({a,b}|{point for o in objects for point in [o['address'],o['address']+o['size']] if a<point<b})
            for lo,hi in zip(cuts,cuts[1:]):
                assigned = path
                if any(o['address']<=lo and hi<=o['address']+o['size'] for o in objects):
                    require(kind in {'data','unknown'}, 'Static stack overlaps code or fill')
                    assigned = profile.get('stack_path',['rtos','stacks'])
                for region,physical_start in projections:
                    contributions.append({'region':region,'start':physical_start+lo-start,'size':hi-lo,'kind':kind,
                                          'path':assigned,'source':c['owner'],'section':section['name']})
    evidence = {'backend':'fwscope','tool_version':report.get('tool_version'),'artifact_status':report['artifact_status'],
                'physical_totals_complete':True,'provenance':provenance,'warnings':report.get('warnings',[]),
                'input_sha256':inputs.hashes,'missing_inputs':inputs.missing,'map_sha256':sha256(map_path)}
    sdk = profile.get('sdk',{})
    if bundle:
        sdk = {**sdk,'commit':bundle.get('sdk_commit'),'dirty':bundle.get('sdk_dirty')}
    return {'schema_version':1,'target':target,'build':profile.get('build',{}),'sdk':sdk,'evidence':evidence,
            'regions':regions,'contributions':contributions,'stacks':stacks,'heaps':report.get('heap_layouts',[]),
            'direct_child_name':profile.get('direct_child_name','self')}


def markdown(d):
    target = d['target']
    lines = [f'# {target["app"]} 静态资源统计','',f'- Board：`{target.get("board") or "未知"}`。',
             f'- 构建标识：`{target.get("build_id") or "未知"}`。','- 单位：B；1 KiB = 1024 B。','',
             '| 资源 | 代码 B | 数据 B | 预留 B | 填充 B | 未知类型 B | 占用 B | 容量 B | 布局剩余 B |',
             '|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    for memory in MEMORIES:
        v,c = d['totals'][memory],d['capacities'][memory]
        values = [v[k] for k in KINDS]+[total(v),c['capacity'],c['remaining']]
        lines.append('| '+memory+' | '+' | '.join(f'{v:,}' if v is not None else '未知' for v in values)+' |')
    for memory in MEMORIES:
        lines += ['',f'## {memory} 模块层级','', '| 模块 | 代码 B | 数据 B | 预留 B | 填充 B | 未知类型 B | 合计 B |', '|---|---:|---:|---:|---:|---:|---:|']
        for node in d['nodes']:
            v = node[memory]
            lines.append('| '+' / '.join(node['path'])+' | '+' | '.join(f'{v[k]:,}' for k in KINDS)+f' | {total(v):,} |')
    lines += ['', '## 段布局', '', '| 段 | Flash B | RAM 代码 B | RAM 数据 B | RAM 预留 B | RAM 填充 B | RAM 未知类型 B |', '|---|---:|---:|---:|---:|---:|---:|']
    for row in d['sections']:
        lines.append('| '+row['name']+f' | {total(row["flash"]):,} | '+' | '.join(f'{row["ram"][k]:,}' for k in KINDS)+' |')
    for label,key in [('静态栈','stacks'),('堆布局','heaps')]:
        lines += ['',f'## {label}','','| 名称 | 字节 B | 类型/状态 |','|---|---:|---|']
        for row in d[key]:
            size = row.get('bytes')
            lines.append(f'| {row["name"]} | {size:,} | {row.get("kind",row.get("status",""))} |' if size is not None else f'| {row["name"]} | 未知 | {row.get("status","")} |')
    evidence = d['evidence']
    lines += ['', '## 构建与证据', '', f'- 后端：`{evidence.get("backend","未知")}`。',
              f'- 产物状态：`{evidence.get("artifact_status","未知")}`。',
              f'- SDK revision：`{d["sdk"].get("commit","未知")}`；dirty：`{d["sdk"].get("dirty","未知")}`。']
    elf_hash = evidence.get('provenance',{}).get('artifacts',{}).get('elf',{}).get('sha256')
    if elf_hash:
        lines.append(f'- ELF SHA-256：`{elf_hash}`。')
    if d['build'].get('native_commands'):
        lines += ['', '```bash', *(shlex.join(c) for c in d['build']['native_commands']), '```']
    lines.append('')
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--intervals',type=Path,help='Normalized physical interval JSON')
    parser.add_argument('--resources',type=Path,help='FwScope full resources JSON')
    parser.add_argument('--profile',type=Path,help='Project mapping/configuration JSON')
    parser.add_argument('--map',dest='map_path',type=Path,help='Same-build GNU MAP')
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--markdown',type=Path)
    parser.add_argument('--overwrite',action='store_true')
    args = parser.parse_args()
    try:
        require(bool(args.intervals) != bool(args.resources), 'Choose --intervals or --resources')
        require(not args.resources or (args.profile and args.map_path), '--resources requires --profile and --map')
        require(not args.intervals or not (args.profile or args.map_path), 'Interval input is self-contained')
        paths = [p for p in [args.intervals,args.resources,args.profile,args.map_path] if p]
        fingerprints = {str(p.resolve()):sha256(p) for p in paths}
        if args.intervals:
            document = json.loads(args.intervals.read_text())
        else:
            profile = json.loads(args.profile.read_text())
            require(profile.get('schema_version')==1,'Unsupported project profile')
            document = from_fwscope(json.loads(args.resources.read_text()),profile,args.profile.resolve().parent,args.map_path)
        result = aggregate(document)
        result['evidence']['files_sha256'] = fingerprints
        for path,digest in {**fingerprints,**result['evidence'].get('input_sha256',{})}.items():
            require(sha256(path)==digest,f'Input changed during analysis: {path}')
        outputs = [args.out,args.markdown or args.out.with_suffix('.md')]
        require(outputs[0].resolve()!=outputs[1].resolve(),'Accounting and Markdown outputs must differ')
        protected = paths + input_paths(result)
        for out in outputs:
            guard_output(out,args.overwrite,protected)
        write_text(outputs[1],markdown(result),args.overwrite,protected)
        write_json(outputs[0],result,args.overwrite,protected)
        print(json.dumps({'accounting':str(outputs[0]),'markdown':str(outputs[1]),'totals':result['totals']},ensure_ascii=False))
        return 0
    except (ValueError,KeyError,OSError,struct.error,IndexError) as exc:
        print(f'fwsize-report: {exc}',file=sys.stderr)
        return 2


if __name__=='__main__':
    sys.exit(main())

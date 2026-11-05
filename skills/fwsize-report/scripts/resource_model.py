"""Shared byte accounting and validation; Python standard library only."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections import defaultdict
from pathlib import Path

KINDS = ('code', 'data', 'reserved', 'padding', 'unknown')
MEMORIES = ('flash', 'ram')


def require(condition, message):
    """Raise a useful input error, including when Python runs with -O."""
    if not condition:
        raise ValueError(message)


def integer(value):
    """Read a nonnegative integer or a hexadecimal address."""
    require(not isinstance(value, bool), 'Boolean is not a byte count')
    number = int(value, 0) if isinstance(value, str) else value
    require(isinstance(number, int) and number >= 0, f'Invalid byte count/address: {value!r}')
    return number


def metrics():
    return {m: {k: 0 for k in KINDS} for m in MEMORIES}


def total(values):
    return sum(values[k] for k in KINDS)


def sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def write_json(path, data, overwrite=False, inputs=()):
    write_text(path, json.dumps(data, ensure_ascii=False, indent=2) + '\n', overwrite, inputs)


def input_paths(document):
    """Include artifact containers and objects named by the evidence record."""
    evidence = document.get('evidence', {})
    paths = set(evidence.get('files_sha256', {})) | set(evidence.get('input_sha256', {}))
    provenance = evidence.get('provenance', {})
    for group in ['artifacts', 'identity_markers']:
        paths.update(item['path'] for item in provenance.get(group, {}).values() if item.get('path'))
    if provenance.get('bundle', {}).get('source'):
        paths.add(provenance['bundle']['source'])
    return [Path(path.split('!', 1)[0]) for path in paths]


def guard_output(path, overwrite=False, inputs=()):
    """Reject an input path, hard link, or an unauthorized existing output."""
    path = Path(path)
    for source in map(Path, inputs):
        require(path.resolve() != source.resolve(), f'Output is an input: {path}')
        require(not (path.exists() and source.exists() and path.samefile(source)), f'Output links to input: {path}')
    require(overwrite or not path.exists(), f'Output exists; choose a new build directory or --overwrite: {path}')


def write_text(path, text, overwrite=False, inputs=()):
    """Replace an output atomically without overwriting an input or hard link."""
    path = Path(path)
    guard_output(path, overwrite, inputs)
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=path.parent, delete=False) as stream:
            name = stream.name
            stream.write(text)
        os.replace(name, path)
    finally:
        if name and Path(name).exists():
            Path(name).unlink()


def union_size(ranges):
    end, amount = -1, 0
    for start, stop in sorted(ranges):
        if stop > end:
            amount += stop - max(start, end)
            end = stop
    return amount


def sweep(coverage, contributions):
    """Yield disjoint covered intervals; overlapping owners share bytes once."""
    events = defaultdict(list)
    for start, end in coverage:
        require(start < end, 'Coverage must have positive length')
        events[start].append(('cover', 1))
        events[end].append(('cover', -1))
    for index, row in enumerate(contributions):
        a, b = row['start'], row['start'] + row['size']
        if a == b:
            continue
        require(union_size([(max(a, x), min(b, y)) for x, y in coverage if max(a, x) < min(b, y)]) == b - a,
                f'Contribution outside declared coverage: {row}')
        events[a].append(('add', index))
        events[b].append(('remove', index))
    active, covered = set(), 0
    points = sorted(events)
    for a, b in zip(points, points[1:]):
        for op, value in events[a]:
            if op == 'cover':
                covered += value
            elif op == 'add':
                active.add(value)
            else:
                active.remove(value)
        if not covered:
            continue
        rows = [contributions[i] for i in active]
        actual = [r for r in rows if r['kind'] != 'padding'] or rows
        kinds = {r['kind'] for r in actual}
        require(len(kinds) <= 1, f'Conflicting interval types at {a:#x}: {kinds}')
        kind = next(iter(kinds), 'unknown')
        paths = {tuple(r['path']) for r in actual}
        owners = {r.get('source', '') for r in actual}
        mixed_fill = any(r['kind']=='padding' for r in rows) and any(r['kind']!='padding' for r in rows)
        path = next(iter(paths)) if len(paths) == 1 and len(owners) <= 1 and not mixed_fill else ('linker', 'shared')
        if not rows:
            path = ('linker', 'unattributed')
        sections = {r.get('section', '') for r in actual}
        section = next(iter(sections)) if len(sections) == 1 else '<shared sections>' if sections else '<unattributed>'
        yield {'start': a, 'size': b - a, 'kind': kind, 'path': list(path), 'section': section}


def aggregate(document):
    """Account physical regions, then derive every parent from disjoint leaves."""
    require(isinstance(document,dict) and document.get('schema_version') == 1, 'Unsupported interval schema')
    require(all(isinstance(document.get(k,{}),dict) for k in ['target','build','sdk','evidence']), 'Metadata fields must be objects')
    require(document.get('evidence',{}).get('physical_totals_complete') is True, 'Physical totals must be verified complete before accounting')
    regions = document['regions']
    require(len({r['id'] for r in regions}) == len(regions), 'Duplicate region id')
    known = {r['id'] for r in regions}
    require(all(c['region'] in known for c in document['contributions']), 'Unknown contribution region')
    leaves, sections = defaultdict(metrics), defaultdict(metrics)
    capacities = {m: {'capacity': 0, 'occupied': 0, 'remaining': 0} for m in MEMORIES}
    region_results = []
    for region in regions:
        memory = region['kind'].lower()
        require(memory in MEMORIES, f'Unsupported memory kind: {memory}')
        coverage = [(integer(a), integer(b)) for a, b in region['coverage']]
        rows = []
        for original in document['contributions']:
            if original['region'] != region['id']:
                continue
            row = dict(original)
            row.update(start=integer(row['start']), size=integer(row['size']))
            require(row['kind'] in KINDS, f'Unknown interval kind: {row["kind"]}')
            require(isinstance(row['path'], list) and row['path'] and all(isinstance(x, str) and x for x in row['path']), 'Empty/invalid module path')
            rows.append(row)
        occupied = 0
        for segment in sweep(coverage, rows):
            size, kind = segment['size'], segment['kind']
            leaves[tuple(segment['path'])][memory][kind] += size
            sections[segment['section']][memory][kind] += size
            occupied += size
        require(occupied == integer(region['expected_occupied']), f'Physical total mismatch for {region["id"]}')
        capacity = region.get('capacity')
        capacity = integer(capacity) if capacity is not None else None
        require(capacity is None or occupied <= capacity, f'Capacity exceeded for {region["id"]}')
        if region.get('capacity_ranges') is not None:
            limits = [(integer(a), integer(b)) for a, b in region['capacity_ranges']]
            require(all(a < b for a, b in limits), 'Invalid capacity range')
            require(capacity == union_size(limits), 'Capacity ranges disagree with capacity')
            require(all(union_size([(max(a,x), min(b,y)) for x,y in limits if max(a,x)<min(b,y)]) == b-a for a,b in coverage), 'Occupied addresses outside capacity')
        result = {'id': region['id'], 'kind': memory, 'capacity': capacity, 'occupied': occupied,
                  'remaining': None if capacity is None else capacity - occupied}
        region_results.append(result)
        c = capacities[memory]
        c['occupied'] += occupied
        c['capacity'] = None if c['capacity'] is None or capacity is None else c['capacity'] + capacity
        c['remaining'] = None if c['capacity'] is None else c['capacity'] - c['occupied']
    direct_name = document.get('direct_child_name', 'self')
    for path in list(leaves):
        if any(len(p) > len(path) and p[:len(path)] == path for p in leaves):
            destination = (*path, direct_name)
            require(destination not in leaves, f'Direct allocation child collides: {destination}')
            leaves[destination] = leaves.pop(path)
    nodes = defaultdict(metrics)
    totals = metrics()
    for path, values in leaves.items():
        for memory in MEMORIES:
            for kind in KINDS:
                amount = values[memory][kind]
                totals[memory][kind] += amount
                for depth in range(1, len(path) + 1):
                    nodes[path[:depth]][memory][kind] += amount
    result = {k: document.get(k, {}) for k in ['target', 'build', 'sdk', 'evidence']}
    result.update(schema_version=1, report_type='fwsize-accounting', totals=totals, capacities=capacities,
                  regions=region_results, stacks=document.get('stacks', []), heaps=document.get('heaps', []),
                  leaves=[{'path': list(p), **v} for p,v in sorted(leaves.items())],
                  nodes=[{'path': list(p), **v} for p,v in sorted(nodes.items())],
                  sections=[{'name': name, **v} for name,v in sorted(sections.items())])
    validate(result)
    return result


def validate(d):
    """Reject stale totals, duplicate paths, ancestor leaves and invalid metrics."""
    require(d.get('schema_version') == 1 and d.get('report_type') == 'fwsize-accounting', 'Unsupported accounting document')
    require(d.get('evidence',{}).get('physical_totals_complete') is True, 'Accounting physical totals are not verified complete')
    require(isinstance(d['target'].get('app'), str) and d['target']['app'].strip() and not any(c in d['target']['app'] for c in '\r\n'), 'Target app is required on one line')
    for group in ['leaves', 'nodes']:
        paths = [tuple(n['path']) for n in d[group]]
        require(len(paths) == len(set(paths)), f'Duplicate {group} path')
        for row in d[group]:
            require(row['path'] and all(isinstance(p,str) and p for p in row['path']), 'Invalid module path')
            for memory in MEMORIES:
                require(set(row[memory]) == set(KINDS), 'Metric kinds differ')
                for value in row[memory].values():
                    require(type(value) is int and value>=0, 'Accounting metrics must be nonnegative integers')
    nodes = {tuple(n['path']): n for n in d['nodes']}
    leaves = {tuple(n['path']): n for n in d['leaves']}
    require(not any(a != b and len(a)<len(b) and b[:len(a)]==a for a in leaves for b in leaves), 'An accounting leaf has descendants')
    expected_paths = {p[:depth] for p in leaves for depth in range(1,len(p)+1)}
    require(set(nodes) == expected_paths, 'Node/leaf path coverage differs')
    for path,node in nodes.items():
        descendants = [v for p,v in leaves.items() if p[:len(path)] == path]
        for memory in MEMORIES:
            for kind in KINDS:
                require(node[memory][kind] == sum(n[memory][kind] for n in descendants), f'Parent mismatch: {path}/{memory}/{kind}')
    for memory in MEMORIES:
        for kind in KINDS:
            require(d['totals'][memory][kind] == sum(n[memory][kind] for n in leaves.values()), f'Total mismatch: {memory}/{kind}')
            require(d['totals'][memory][kind] == sum(s[memory][kind] for s in d['sections']), f'Section mismatch: {memory}/{kind}')
        require(total(d['totals'][memory]) == d['capacities'][memory]['occupied'], 'Capacity/total mismatch')
        regions = [r for r in d['regions'] if r['kind']==memory]
        require(sum(r['occupied'] for r in regions)==d['capacities'][memory]['occupied'], 'Region total mismatch')
        for region in regions:
            integer(region['occupied'])
            if region['capacity'] is not None:
                require(integer(region['capacity']) >= region['occupied'], 'Negative remaining capacity')
                require(region['remaining'] == region['capacity']-region['occupied'], 'Remaining capacity mismatch')
        capacity = None if any(r['capacity'] is None for r in regions) else sum(r['capacity'] for r in regions)
        require(d['capacities'][memory]['capacity'] == capacity, 'Aggregate capacity mismatch')
        require(d['capacities'][memory]['remaining'] == (None if capacity is None else capacity-d['capacities'][memory]['occupied']), 'Aggregate remaining mismatch')
    return d

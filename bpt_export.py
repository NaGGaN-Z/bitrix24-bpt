#!/usr/bin/env python3
"""Export all Bitrix24 BP templates (.bpt) via REST and inventory activity types.

Usage:
    python3 bpt_export.py <webhook_file_or_url> [out_dir]

Webhook: incoming webhook (admin) with bizproc scope.
Output:
    out_dir/templates.json      — raw list response (all pages)
    out_dir/NNN_Name.bpt        — raw zlib blob, if TEMPLATE_DATA returned
    out_dir/NNN_Name.json       — decoded template tree (unserialized -> JSON)
    out_dir/summary.md          — inventory: template -> activity types, vars, params
"""
import base64
import json
import os
import re
import sys
import zlib

import urllib.request
import urllib.parse

try:
    import phpserialize  # type: ignore
except ImportError:
    phpserialize = None


# ---------- minimal PHP serialize parser (no deps) ----------

def php_unserialize(data: bytes):
    pos = 0

    def parse():
        nonlocal pos
        c = data[pos:pos + 1]
        if c == b'i':
            m = re.match(rb'i:(-?\d+);', data[pos:])
            pos += m.end()
            return int(m.group(1))
        if c == b'd':
            m = re.match(rb'd:(-?[\d.eE+-]+);', data[pos:])
            pos += m.end()
            return float(m.group(1))
        if c == b'b':
            m = re.match(rb'b:([01]);', data[pos:])
            pos += m.end()
            return m.group(1) == b'1'
        if c == b's':
            m = re.match(rb's:(\d+):"', data[pos:])
            pos += m.end()
            raw = data[pos:pos + int(m.group(1))]
            pos += int(m.group(1)) + 2  # ";
            return raw.decode('utf-8', 'replace')
        if c == b'a':
            m = re.match(rb'a:(\d+):\{', data[pos:])
            pos += m.end()
            n = int(m.group(1))
            pairs = [(parse(), parse()) for _ in range(n)]
            pos += 1  # }
            if pairs and all(isinstance(k, int) for k, _ in pairs) \
                    and [k for k, _ in pairs] == list(range(n)):
                return [v for _, v in pairs]  # plain list
            return {k if isinstance(k, str) else f'_{k}': v for k, v in pairs}
        if c == b'N':
            pos += 2
            return None
        raise ValueError(f'unparsed at {pos}: {data[pos:pos + 40]!r}')

    val = parse()
    if pos != len(data):
        raise ValueError(f'trailing bytes at {pos}/{len(data)}')
    return val


# ---------- REST ----------

def call(wh: str, method: str, params: dict) -> dict:
    url = wh.rstrip('/') + '/' + method
    payload = json.dumps(params).encode()
    req = urllib.request.Request(url, data=payload,
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=60) as r:
        resp = json.loads(r.read())
    if 'error' in resp:
        raise RuntimeError(f"{method}: {resp['error']}: {resp.get('error_description')}")
    return resp


def b64_to_zlib(b64: str) -> bytes:
    blob = base64.b64decode(b64)
    # some portals wrap twice or send raw serialized data
    if blob[:2] == b'\x78\x9c' or blob[:2] == b'\x78\xda' or blob[:2] == b'\x78\x01':
        return zlib.decompress(blob)
    return blob  # assume already raw


def walk_activities(node, acc):
    if isinstance(node, dict):
        t = node.get('Type')
        if t and isinstance(t, str) and node.get('Properties') is not None:
            acc.append({'Type': t, 'Name': node.get('Name'),
                        'props': sorted(str(k) for k in (node.get('Properties') or {}).keys())})
        for ch in (node.get('Children') or []):
            walk_activities(ch, acc)
    elif isinstance(node, list):
        for ch in node:
            walk_activities(ch, acc)


def main():
    src = sys.argv[1]
    out_dir = sys.argv[2] if len(sys.argv) > 2 else 'bpt_out'
    os.makedirs(out_dir, exist_ok=True)
    wh = src if src.startswith('http') else open(src).read().strip()

    # try to grab everything the method can give
    select = ['*', 'TEMPLATE_DATA']
    templates, start = [], 0
    while True:
        resp = call(wh, 'bizproc.workflow.template.list',
                    {'select': select, 'order': {'ID': 'ASC'}, 'start': start})
        page = resp.get('result') or []
        templates.extend(page)
        print(f'page start={start}: +{len(page)} (total={resp.get("total")})')
        if len(page) < 50:
            break
        start += 50

    json.dump(templates, open(os.path.join(out_dir, 'templates.json'), 'w'),
              ensure_ascii=False, indent=1)

    summary = []
    for t in templates:
        tid, name = t.get('ID'), t.get('NAME', 'noname')
        safe = re.sub(r'[^\w.-]+', '_', name)[:60]
        entry = {'ID': tid, 'NAME': name, 'MODULE_ID': t.get('MODULE_ID'),
                 'ENTITY': t.get('ENTITY'), 'DOCUMENT_TYPE': t.get('DOCUMENT_TYPE'),
                 'AUTO_EXECUTE': t.get('AUTO_EXECUTE')}
        td = t.get('TEMPLATE_DATA')
        if isinstance(td, dict):
            td = td.get('b64') or td.get('content')
        if isinstance(td, list) and len(td) >= 2:
            td = td[1]
        if td:
            try:
                blob = b64_to_zlib(td)
                stem = f'{tid}_{safe}'
                open(os.path.join(out_dir, stem + '.bpt'), 'wb').write(blob)
                tree = php_unserialize(blob)
                json.dump(tree, open(os.path.join(out_dir, stem + '.json'), 'w'),
                          ensure_ascii=False, indent=1)
                acts = []
                for root in (tree.get('TEMPLATE') or []):
                    walk_activities(root, acts)
                entry['activities'] = sorted({a['Type'] for a in acts})
                entry['vars'] = sorted((tree.get('VARIABLES') or {}).keys())
                entry['params'] = sorted((tree.get('PARAMETERS') or {}).keys())
                entry['decoded'] = True
            except Exception as e:
                entry['decoded'] = False
                entry['error'] = str(e)
        else:
            entry['decoded'] = None  # no TEMPLATE_DATA in list response
        summary.append(entry)

    lines = ['# BP templates inventory', '']
    for e in summary:
        lines.append(f"## [{e['ID']}] {e['NAME']} "
                     f"(module={e.get('MODULE_ID')}, auto={e.get('AUTO_EXECUTE')})")
        if e.get('decoded'):
            for a in e.get('activities', []):
                lines.append(f'- {a}')
            if e.get('vars'):
                lines.append(f"- vars: {', '.join(e['vars'])}")
        elif e.get('decoded') is None:
            lines.append('- (TEMPLATE_DATA не вернулся из list — нужен UI-экспорт)')
        else:
            lines.append(f"- DECODE ERROR: {e.get('error')}")
        lines.append('')
    open(os.path.join(out_dir, 'summary.md'), 'w').write('\n'.join(lines))
    print(f'done: {len(templates)} templates -> {out_dir}/ (see summary.md)')


if __name__ == '__main__':
    main()

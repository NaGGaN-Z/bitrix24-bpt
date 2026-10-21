#!/usr/bin/env python3
"""Export all Bitrix24 BP templates (.bpt) via REST and inventory activity types.

Usage:
    python3 bpt_export.py <webhook_file_or_url> [out_dir]
    python3 bpt_export.py            # webhook из env B24_WEBHOOK
    python3 bpt_export.py diff A.bpt B.bpt   # структурный диф (exit 0/1/2)
    python3 bpt_export.py diagram X.bpt [--diff BASE.bpt]
            [--format html|svg|mermaid|d2|puml] [--out FILE|-] [--svg]
                                     # проекция дерева БП (волна 7): html —
                                     # дефолт (интерактив, одним файлом);
                                     # svg только по запросу (--format svg
                                     # или --svg дополнительно к html);
                                     # --diff — покраска + призраки
                                     # (exit 0/1/2)
    python3 bpt_export.py probe ENTITY_TYPE ENTITY_ID [WEBHOOK_SRC]
                                     # probe-журнал дебаг-копии (etalons.md):
                                     # чтение таймлайна сущности, парсинг
                                     # [probe]-комментариев, хронология
                                     # значений переменных (exit 0/1/2)

Webhook: incoming webhook (admin) with bizproc scope. URL в argv виден в `ps`
всё время прогона — предпочтителен файл с URL или env B24_WEBHOOK.
Output:
    out_dir/templates.json      — raw list (все страницы; чекпоинт пишется
                                  после КАЖДОЙ страницы — обрыв не теряет
                                  выкачанное)
    out_dir/NNN_Name.bpt        — канон-контейнер, собранный из select[]=TEMPLATE
                                  (REST отдаёт дерево развёрнутым; template.get НЕТ)
    out_dir/NNN_Name.json       — decoded template tree (unserialized -> JSON)
    out_dir/summary.md          — inventory: template -> activity types, vars, params
Пагинация курсорная (filter >ID): мутации между страницами не сдвигают окно;
итог сверяется с total (усечение — RuntimeError, не молчаливое).
Транзиентные ошибки (QUERY_LIMIT_EXCEEDED / 5xx / обрыв) — ретрай с backoff.
"""
import base64
import copy
import difflib
import json
import os
import re
import sys
import time
import zlib

import urllib.error
import urllib.request
import urllib.parse

try:
    import phpserialize  # type: ignore
except ImportError:
    phpserialize = None


# ---------- minimal PHP serialize parser (no deps) ----------

def php_unserialize(data: bytes):
    """Lossy-парсер (int-ключи смешанных dict -> '_N'; для байтовых гейтов
    есть php_unserialize_exact в bpt_serialize). Малформ — ValueError с
    позицией; строки — strict utf-8 (U+FFFD bake-in запрещён)."""
    pos = 0

    def _unparsed():
        return ValueError(f'unparsed at {pos}: {data[pos:pos + 40]!r}')

    def parse():
        nonlocal pos
        c = data[pos:pos + 1]
        if c == b'i':
            m = re.match(rb'i:(-?\d+);', data[pos:])
            if m is None:
                raise _unparsed()
            pos += m.end()
            return int(m.group(1))
        if c == b'd':
            m = re.match(rb'd:(-?[\d.eE+-]+);', data[pos:])
            if m is None:
                raise _unparsed()
            pos += m.end()
            return float(m.group(1))
        if c == b'b':
            m = re.match(rb'b:([01]);', data[pos:])
            if m is None:
                raise _unparsed()
            pos += m.end()
            return m.group(1) == b'1'
        if c == b's':
            m = re.match(rb's:(\d+):"', data[pos:])
            if m is None:
                raise _unparsed()
            pos += m.end()
            raw = data[pos:pos + int(m.group(1))]
            pos += int(m.group(1)) + 2  # ";
            return raw.decode('utf-8')
        if c == b'a':
            m = re.match(rb'a:(\d+):\{', data[pos:])
            if m is None:
                raise _unparsed()
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
        raise _unparsed()

    val = parse()
    if pos != len(data):
        raise ValueError(f'trailing bytes at {pos}/{len(data)}')
    return val


# ---------- REST ----------

def dump_template_json(blob: bytes) -> str:
    """Верный JSON шаблона: exact-парсер (int-ключи живы) + канонический
    шов bpt_serialize.dump_json (числовой ключ JSON = int). Локальный
    импорт — разрыв цикла bpt_serialize -> bpt_export."""
    from bpt_serialize import dump_json, php_unserialize_exact
    return dump_json(php_unserialize_exact(blob))


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


def _retryable(e: Exception) -> bool:
    if isinstance(e, urllib.error.HTTPError):
        return e.code >= 500
    if isinstance(e, urllib.error.URLError):
        return True  # обрыв/таймаут транспорта
    return isinstance(e, RuntimeError) and 'QUERY_LIMIT_EXCEEDED' in str(e)


def call_retry(wh: str, method: str, params: dict, attempts: int = 5,
               base_delay: float = 1.0) -> dict:
    """call с ретраями транзиентных ошибок: QUERY_LIMIT_EXCEEDED (портал
    отвечает 503 при дренаже очереди), 5xx, обрывы URLError. Backoff
    base_delay * 2^i. Неретраебельные (WRONG_REQUEST и пр.) — сразу raise."""
    for i in range(attempts):
        try:
            return call(wh, method, params)
        except Exception as e:  # noqa: BLE001
            if i == attempts - 1 or not _retryable(e):
                raise
            delay = base_delay * (2 ** i)
            print(f'[FIX] {method}: {e} — ретрай через {delay:.0f}s '
                  f'({i + 2}/{attempts})', file=sys.stderr)
            time.sleep(delay)
    raise AssertionError('unreachable')


def fetch_all(wh: str, out_dir: str, page_size: int = 50) -> list:
    """Весь bizproc.workflow.template.list курсорной пагинацией filter >ID
    (иммунна к мутациям между страницами: delete/add не сдвигают окно, в
    отличие от offset). Чекпоинт templates.json — после КАЖДОЙ страницы:
    обрыв не теряет выкачанное. Финальная сверка с total — усечение
    инвентаря это RuntimeError, а не молчаливый короткий набор."""
    templates, last_id, total = [], 0, None
    ckpt = os.path.join(out_dir, 'templates.json')
    while True:
        resp = call_retry(wh, 'bizproc.workflow.template.list',
                          {'select': ['ID', 'NAME', 'MODULE_ID', 'ENTITY',
                                      'DOCUMENT_TYPE', 'AUTO_EXECUTE',
                                      'ACTIVE', 'MODIFIED', 'TEMPLATE',
                                      'PARAMETERS', 'VARIABLES',
                                      'CONSTANTS'],
                           'order': {'ID': 'ASC'},
                           'filter': {'>ID': last_id}})
        page = resp.get('result') or []
        templates.extend(page)
        if total is None:
            total = resp.get('total')
        json.dump(templates, open(ckpt, 'w'), ensure_ascii=False, indent=1)
        print(f'page >ID={last_id}: +{len(page)} (total={total})')
        if len(page) < page_size:
            break
        last_id = max(int(t['ID']) for t in page)
    if total is not None and len(templates) != int(total):
        raise RuntimeError(f'инвентарь неполный: собрано {len(templates)}, '
                           f'portal total={total} (мутации во время выгрузки?)')
    return templates


def b64_to_zlib(b64: str) -> bytes:
    """b64-REST-ответ → payload. b64decode с validate=True: validate=False
    молча игнорирует не-base64 символы и КАЛЕЧИТ raw-payload, поданный по
    ошибке (тихая порча, аудит); raw PHP-текст (тег a:/i:/… в голове)
    проходит как есть. Выход zlib-декомпрессии лимитирован
    (bpt_serialize.MAX_PAYLOAD, zip-bomba-гард)."""
    import binascii
    try:
        blob = base64.b64decode(b64, validate=True)
    except (binascii.Error, ValueError):
        return b64.encode('utf-8')  # уже raw payload (не base64)
    from bpt_serialize import decompress_bpt  # локально: анти-цикл
    try:
        return decompress_bpt(blob)
    except zlib.error:
        return blob  # b64 внутри был уже raw serialized (не сжат)


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


# ---------- structural diff: bpt_export.py diff A.bpt B.bpt ----------
#
# Двухуровневая схема (иммунитет к rename Name, паттерн задачи project-x):
#   identity-ключ (матчинг)  — Type + якорные пропсы БЕЗ значений;
#   content-сравнение (отчёт) — полные Properties минус летучие поля.
# Identity включает значения якорей? Нет — гоча наивной сигнатуры: ключ с
# значениями меняется при правке формулы, и узел репортится removed+added
# вместо «изменённая формула».

#: летучие Properties-ключи (Name/Node — node-level, исключаются отдельно)
DIFF_VOLATILE_PROPS = frozenset({'EditorComment', '_DesMinimized'})
#: известные kinds mixedcondition.object; прочее (A123_-имена) — ссылки на
#: активности, летучие → нормализуются в '<Act>'
COND_OBJECT_KINDS = frozenset({'Document', 'Variable', 'Constant', 'Parameter',
                               'User', 'Template'})
#: props-ключи условий для отчёта (изменения — отдельной секцией)
COND_PROPS = ('fieldcondition', 'mixedcondition', 'propertyvariablecondition',
              'truecondition')


def _load_bpt_tree(path: str):
    """Файл .bpt → дерево (exact-парсер: int-ключи живы — DOCUMENT_FIELDS
    Options без '_N'-шрамов). Файл = zlib-blob (UI-экспорт) или уже raw
    payload — decompress_bpt (тег-гейт + zip-bomba-лимит, единая точка)."""
    blob = open(path, 'rb').read()
    from bpt_serialize import decompress_bpt
    payload = decompress_bpt(blob)
    from bpt_serialize import php_unserialize_exact  # локально: анти-цикл
    return php_unserialize_exact(payload)


def _cond_anchor(props: dict) -> tuple:
    """Якорь условий if_else*-ветки / while: поля и objects БЕЗ операторов
    и значений. mixedcondition.object со ссылкой на активность (не из
    COND_OBJECT_KINDS) → '<Act>': rename активности не рвёт identity."""
    rows = []
    fc = props.get('fieldcondition')
    if isinstance(fc, list):
        rows += [('f', str(r[0])) for r in fc if isinstance(r, list) and r]
    pc = props.get('propertyvariablecondition')
    if isinstance(pc, list):
        rows += [('v', str(r[0])) for r in pc if isinstance(r, list) and r]
    mc = props.get('mixedcondition')
    if isinstance(mc, list):
        for r in mc:
            if isinstance(r, dict):
                obj = r.get('object')
                kind = obj if obj in COND_OBJECT_KINDS else '<Act>'
                rows.append(('m', kind, str(r.get('field'))))
    return tuple(sorted(rows))


def identity_key(node) -> tuple:
    """Identity-ключ узла для выравнивания (без значений и Name)."""
    if not isinstance(node, dict) or not node.get('Type'):
        return ('<bare>',)
    t, props = node['Type'], node.get('Properties') or {}
    if t == 'SetVariableActivity':
        vv = props.get('VariableValue')
        keys = tuple(sorted(str(k) for k in vv)) if isinstance(vv, dict) else ()
        return (t, 'sv', keys)
    if t in ('IfElseBranchActivity', 'WhileActivity'):
        return (t, 'cond') + _cond_anchor(props)
    return (t, 'title', str(props.get('Title') or ''))


def _content_view(node) -> dict:
    """Content узла для отчёта: node-уровень (Activated) + Properties минус
    летучие. Name → карта переименований, Node — летучий артефакт дизайнера."""
    props = {k: v for k, v in (node.get('Properties') or {}).items()
             if k not in DIFF_VOLATILE_PROPS}
    return {'Activated': node.get('Activated'), 'props': props}


def _subtree_count(node) -> int:
    n = 0
    stack = [node]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            if cur.get('Type'):
                n += 1
            stack += cur.get('Children') or []
    return n


def _similarity(a, b) -> float:
    """Tie-break подобия для хвостовых равных identity-ключей (серии
    однотипных узлов): совпадение props + жаккар типов детей."""
    ca, cb = _content_view(a), _content_view(b)
    pa, pb = ca['props'], cb['props']
    score = 0.0
    if ca['Activated'] == cb['Activated']:
        score += 0.1
    union = set(pa) | set(pb)
    if union:
        eq = sum(1 for k in set(pa) & set(pb) if pa[k] == pb[k])
        score += 0.5 * eq / len(union)
    ta = sorted(str(c.get('Type')) for c in (a.get('Children') or [])
                if isinstance(c, dict))
    tb = sorted(str(c.get('Type')) for c in (b.get('Children') or [])
                if isinstance(c, dict))
    if ta or tb:
        inter = len([t for t in ta if t in tb])
        score += 0.4 * inter / len(set(ta) | set(tb))
    return score


def align_children(a_list: list, b_list: list):
    """Выравнивание детей: difflib.SequenceMatcher по identity-ключам
    (порядок разрешает серии однотипных узлов), затем хвостовые равные
    ключи — жадно по _similarity. → (pairs[(ai, bi)], only_a[ai], only_b[bi])."""
    ka = [identity_key(n) for n in a_list]
    kb = [identity_key(n) for n in b_list]
    sm = difflib.SequenceMatcher(None, ka, kb, autojunk=False)
    pairs, used_a, used_b = set(), set(), set()
    for i, j, n in sm.get_matching_blocks():
        for off in range(n):
            pairs.add((i + off, j + off))
            used_a.add(i + off)
            used_b.add(j + off)
    # хвостые равные ключи (SequenceMatcher держит самые длинные блоки,
    # равные вне блоков остаются) — жадный tie-break по подобию
    free_b = {k: [j for j in range(len(kb))
                  if j not in used_b and kb[j] == k]
              for k in {kb[j] for j in range(len(kb)) if j not in used_b}}
    cands = [(i, j) for i in range(len(ka))
             if i not in used_a and ka[i] in free_b
             for j in free_b[ka[i]]]
    cands.sort(key=lambda ij: -_similarity(a_list[ij[0]], b_list[ij[1]]))
    for i, j in cands:
        if i in used_a or j in used_b:
            continue
        pairs.add((i, j))
        used_a.add(i)
        used_b.add(j)
    only_a = [i for i in range(len(a_list)) if i not in used_a]
    only_b = [j for j in range(len(b_list)) if j not in used_b]
    return sorted(pairs), only_a, only_b


def _clip(v, limit: int = 60) -> str:
    s = repr(v).replace('\n', '\\n')
    return s if len(s) <= limit else s[:limit - 1] + '…'


def _rename_translate(value, rename_map: dict):
    """Нормализация контента по карте переименований: A-строка с
    {=Name:...}-ссылками/mixedcondition.object после rename портала
    отличается литерально, но семантически идентична. Рекурсивно
    заменяет вхождения старых Name (по \\b-границам) на новые."""
    if not rename_map:
        return value
    if isinstance(value, str):
        for old, new in rename_map.items():
            if old and old in value:
                value = re.sub(rf'\b{re.escape(old)}\b', new, value)
        return value
    if isinstance(value, list):
        return [_rename_translate(v, rename_map) for v in value]
    if isinstance(value, dict):
        return {k: _rename_translate(v, rename_map) for k, v in value.items()}
    return value


def _pair_var_renames(a_tree: dict, b_tree: dict) -> dict:
    """Карта переименований переменных old->new по секции VARIABLES:
    пары только при ОДИНАКОВОЙ записи (Name-лейбл/Type/Default/…) —
    единственный кандидат. Запись изменилась вместе с именем = не
    rename, а пересоздание (не пара: останется key-level диффом)."""
    va = a_tree.get('VARIABLES') or {}
    vb = b_tree.get('VARIABLES') or {}
    if not isinstance(va, dict) or not isinstance(vb, dict):
        return {}
    only_a = [k for k in va if k not in vb]
    only_b = [k for k in vb if k not in va]
    pairs, used = {}, set()
    for k in only_a:
        cands = [j for j in only_b if j not in used and va[k] == vb[j]]
        if len(cands) == 1:
            pairs[k] = cands[0]
            used.add(cands[0])
    return pairs


def _normalize_var_anchors(tree: dict, var_map: dict) -> dict:
    """Копия TEMPLATE с переименованными IDENTITY-якорями переменных:
    ключи SetVariable.VariableValue, propertyvariablecondition[0],
    mixedcondition object=Variable/field, ForEach.Variable. Ссылки
    `{=Variable:old}` в формулах НЕ трогаются — иначе rename исчезнет
    из контент-диффа (sv/prop old→new остаются видимыми)."""
    if not var_map:
        return tree
    out = copy.deepcopy(tree)

    def walk(v):
        if isinstance(v, dict):
            vv = v.get('VariableValue')
            if isinstance(vv, dict):
                rows = list(vv.items())
                vv.clear()
                for k, x in rows:
                    vv[var_map.get(k, k)] = x
            if v.get('object') == 'Variable' and v.get('field') in var_map:
                v['field'] = var_map[v['field']]
            if v.get('Type') == 'ForEachActivity':
                props = v.get('Properties') or {}
                if props.get('Variable') in var_map:
                    props['Variable'] = var_map[props['Variable']]
            pvc = v.get('propertyvariablecondition')
            if isinstance(pvc, list):
                for row in pvc:
                    if isinstance(row, list) and row and row[0] in var_map:
                        row[0] = var_map[row[0]]
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)

    walk(out.get('TEMPLATE'))
    return out


def _match_tree(a, b, path: str, renames: list, events: list) -> dict:
    """Проход 1: выравнивание (align_children) + карта переименований.
    → дерево матча {'a','b','path','kids'}; ±события падают в events."""
    if a.get('Name') != b.get('Name'):
        renames.append({'path': path, 'type': a.get('Type'),
                        'title': str((a.get('Properties') or {})
                                     .get('Title') or ''),
                        'old': a.get('Name'), 'new': b.get('Name')})
    a_ch = [c for c in (a.get('Children') or []) if isinstance(c, dict)]
    b_ch = [c for c in (b.get('Children') or []) if isinstance(c, dict)]
    pairs, only_a, only_b = align_children(a_ch, b_ch)
    for i in only_a:
        events.append({'kind': 'removed', 'path': f'{path}/{i}',
                       'node': a_ch[i], 'count': _subtree_count(a_ch[i])})
    for j in only_b:
        events.append({'kind': 'added', 'path': f'{path}/{j}',
                       'node': b_ch[j], 'count': _subtree_count(b_ch[j])})
    kids = [_match_tree(a_ch[i], b_ch[j], f'{path}/{i}', renames, events)
            for i, j in pairs]
    return {'a': a, 'b': b, 'path': path, 'kids': kids}


def _content_pass(m: dict, rename_map: dict, events: list) -> None:
    """Проход 2: контент-сравнение matched-пар с полной картой
    переименований (ссылки {=Name:...} сначала транслируются — чистый
    rename не даёт ложных prop-диффов)."""
    a, b, path = m['a'], m['b'], m['path']
    ta, title = a.get('Type'), str((a.get('Properties') or {})
                                   .get('Title') or '')
    ca, cb = _content_view(a), _content_view(b)
    if ca['Activated'] != cb['Activated']:
        events.append({'kind': 'activated', 'path': path, 'type': ta,
                       'title': title, 'old': ca['Activated'],
                       'new': cb['Activated'], 'name': b.get('Name')})
    pa, pb = ca['props'], cb['props']
    for k in sorted(set(pa) | set(pb), key=str):
        va, vb = pa.get(k, '<нет>'), pb.get(k, '<нет>')
        ta_va = _rename_translate(va, rename_map)
        if ta_va == vb:
            continue  # литеральная дельта объясняется rename-ссылками
        note = ta_va != va  # дифф включает переименованные ref
        if ta == 'SetVariableActivity' and k == 'VariableValue' \
                and isinstance(va, dict) and isinstance(vb, dict):
            for vk in sorted(set(map(str, va)) | set(map(str, vb))):
                oa, ob = va.get(vk, '<нет>'), vb.get(vk, '<нет>')
                if _rename_translate(oa, rename_map) != ob:
                    events.append({'kind': 'sv', 'path': path, 'type': ta,
                                   'title': title, 'key': vk,
                                   'old': oa, 'new': ob,
                                   'name': b.get('Name')})
        elif k in COND_PROPS:
            events.append({'kind': 'cond', 'path': path, 'type': ta,
                           'title': title, 'prop': k, 'old': va, 'new': vb,
                           'ref_renamed': note, 'name': b.get('Name')})
        else:
            events.append({'kind': 'prop', 'path': path, 'type': ta,
                           'title': title, 'prop': k, 'old': va, 'new': vb,
                           'ref_renamed': note, 'name': b.get('Name')})
    for kid in m['kids']:
        _content_pass(kid, rename_map, events)


DIFF_SECTIONS = ('VARIABLES', 'PARAMETERS', 'CONSTANTS', 'DOCUMENT_FIELDS')


def diff_sections(a_tree: dict, b_tree: dict, events: list) -> None:
    """Key-level дифф секций (без него переименованная переменная невидима):
    +ключ / -ключ / ~ключ — по под-полям значения, значения не-dict —
    целиком old → new."""
    for sect in DIFF_SECTIONS:
        va = a_tree.get(sect) or {}
        vb = b_tree.get(sect) or {}
        if not isinstance(va, dict) or not isinstance(vb, dict):
            continue
        for k in sorted(set(map(str, va)) | set(map(str, vb)), key=str):
            oa, ob = va.get(k), vb.get(k)
            if oa == ob:
                continue
            if k not in va:
                events.append({'kind': 'sect', 'section': sect, 'op': '+',
                               'key': k, 'old': None, 'new': ob})
            elif k not in vb:
                events.append({'kind': 'sect', 'section': sect, 'op': '-',
                               'key': k, 'old': oa, 'new': None})
            elif isinstance(oa, dict) and isinstance(ob, dict):
                for sub in sorted(set(map(str, oa)) | set(map(str, ob)),
                                  key=str):
                    soa, sob = oa.get(sub), ob.get(sub)
                    if soa != sob:
                        events.append({'kind': 'sect', 'section': sect,
                                       'op': '~', 'key': f'{k}.{sub}',
                                       'old': soa, 'new': sob})
            else:
                events.append({'kind': 'sect', 'section': sect, 'op': '~',
                               'key': k, 'old': oa, 'new': ob})


def diff_templates(a_tree: dict, b_tree: dict):
    """Дифф двух деревьев шаблона: якорь — TEMPLATE[0] (список с одним
    корнем). Проход 1 — выравнивание + карта переименований, проход 2 —
    контент с транслацией rename-ссылок; секции — key-level.
    → (events, renames); renames — ОЖИДАЕМЫЕ находки, на вердикт не влияют."""
    events, renames = [], []
    var_map = _pair_var_renames(a_tree, b_tree)
    a_match = _normalize_var_anchors(a_tree, var_map)
    ra = (a_match.get('TEMPLATE') or [None])[0]
    rb = (b_tree.get('TEMPLATE') or [None])[0]
    if not isinstance(ra, dict) or not isinstance(rb, dict):
        events.append({'kind': 'error', 'msg': 'TEMPLATE-корень бит: '
                       f'A={type(ra).__name__} B={type(rb).__name__}'})
        return events, renames
    match_root = _match_tree(ra, rb, 'root', renames, events)
    rename_map = {r['old']: r['new'] for r in renames if r['old']}
    _content_pass(match_root, rename_map, events)
    diff_sections(a_tree, b_tree, events)
    if var_map:
        # пары rename — ожидаемые (как Name), не key-level дифф
        paired = set(var_map) | set(var_map.values())

        def _paired(e):
            if e.get('kind') != 'sect' or e.get('section') != 'VARIABLES':
                return False
            return str(e.get('key', '')).split('.', 1)[0] in paired

        events[:] = [e for e in events if not _paired(e)]
        va = a_tree.get('VARIABLES') or {}
        for old, new in var_map.items():
            rec = va.get(old) or {}
            renames.append({'path': 'VARIABLES', 'type': 'Variable',
                            'title': str(rec.get('Name') or ''),
                            'old': old, 'new': new, 'kind': 'variable'})
    return events, renames


def render_diff(events: list, renames: list) -> list:
    """Человекочитаемый grep-friendly отчёт: каждая находка — одна строка
    с префиксом [diff], секции план-порядка: ±активности → SV-формулы →
    условия → прочие props → переименования (ожидаемые) → секции шаблона."""

    def node_ctx(n):
        title = str((n.get('Properties') or {}).get('Title') or '')[:40]
        return f'{n.get("Type")} "{title}"' if title else str(n.get('Type'))

    def ctx(e):
        title = str(e.get('title') or '')[:40]
        who = f'{e["type"]} "{title}"' if title else str(e.get('type', ''))
        return f'{who} @ {e["path"]}'

    def line_removed(e):
        return f'[diff] - A only: {node_ctx(e["node"])} @ {e["path"]} ' \
               f'(поддерево {e["count"]} узл.)'

    def line_added(e):
        return f'[diff] + B only: {node_ctx(e["node"])} @ {e["path"]} ' \
               f'(поддерево {e["count"]} узл.)'

    def line_sv(e):
        return f'[diff] ~ SV {ctx(e)}: {e["key"]}: ' \
               f'{_clip(e["old"])} -> {_clip(e["new"])}'

    def line_cond(e):
        note = ' (вкл. переименованные ref)' if e.get('ref_renamed') else ''
        return f'[diff] ~ cond {ctx(e)}: {e["prop"]}: ' \
               f'{_clip(e["old"])} -> {_clip(e["new"])}{note}'

    def line_prop(e):
        note = ' (вкл. переименованные ref)' if e.get('ref_renamed') else ''
        return f'[diff] ~ prop {ctx(e)}: {e["prop"]}: ' \
               f'{_clip(e["old"])} -> {_clip(e["new"])}{note}'

    def line_activated(e):
        return f'[diff] ~ {ctx(e)}: Activated: {e["old"]} -> {e["new"]}'

    def line_sect(e):
        if e['op'] == '+':
            return f'[diff] ~ {e["section"]} +{e["key"]} ' \
                   f'(в B нет в A) = {_clip(e["new"], 80)}'
        if e['op'] == '-':
            return f'[diff] ~ {e["section"]} -{e["key"]} ' \
                   f'(в A нет в B) = {_clip(e["old"], 80)}'
        return f'[diff] ~ {e["section"]} ~{e["key"]}: ' \
               f'{_clip(e["old"])} -> {_clip(e["new"])}'

    fmt = {'removed': line_removed, 'added': line_added, 'sv': line_sv,
           'cond': line_cond, 'prop': line_prop, 'activated': line_activated,
           'sect': line_sect}
    out = [f'[diff] ERROR: {e["msg"]}' for e in events
           if e['kind'] == 'error']

    def bucket_rows(kinds):
        return [fmt[e['kind']](e) for e in events if e['kind'] in kinds]

    for kinds, header in (
            (('removed', 'added'), 'добавлено/удалено'),
            (('sv',), 'SV-формулы'),
            (('cond',), 'условия'),
            (('prop', 'activated'), 'прочие props')):
        rows = bucket_rows(kinds)
        if rows:
            out.append(f'[diff] === {header} ({len(rows)}) ===')
            out += rows
    if renames:
        var_rows = [r for r in renames if r.get('kind') == 'variable']
        act_rows = [r for r in renames if r.get('kind') != 'variable']
        label = 'переименования Name/переменных' if var_rows \
            else 'переименования Name'
        out.append(f'[diff] === {label} ({len(renames)}): '
                   'ожидаемая секция, не дифф ===')
        out += [f'[diff] = {r["type"]} "{str(r["title"])[:40]}" @ {r["path"]}:'
                f' {r["old"]} -> {r["new"]}' for r in act_rows]
        out += [f'[diff] = Variable "{str(r["title"])[:40]}" @ {r["path"]}:'
                f' {r["old"]} -> {r["new"]}' for r in var_rows]
    sect_rows = bucket_rows(('sect',))
    if sect_rows:
        out.append(f'[diff] === секции шаблона ({len(sect_rows)}) ===')
        out += sect_rows
    n_add = sum(1 for e in events if e['kind'] == 'added')
    n_rem = sum(1 for e in events if e['kind'] == 'removed')
    n_err = sum(1 for e in events if e['kind'] == 'error')
    n_chg = len(events) - n_add - n_rem - n_err
    verdict = 'IDENTICAL' if not events else 'DIFF'
    out.append(f'[diff] summary: +{n_add} -{n_rem} ~{n_chg} '
               f'renames={len(renames)} -> {verdict}')
    return out


def cmd_diff(path_a: str, path_b: str) -> int:
    """diff-режим: структурный диф двух .bpt. Exit: 0 — структурно
    идентичны (переименования допустимы) / 1 — есть дифф / 2 — usage/битый."""
    try:
        a_tree, b_tree = _load_bpt_tree(path_a), _load_bpt_tree(path_b)
    except (OSError, ValueError, zlib.error) as e:
        print(f'[diff] ERROR: {path_a} / {path_b}: {e}', file=sys.stderr)
        return 2
    events, renames = diff_templates(a_tree, b_tree)
    for line in render_diff(events, renames):
        print(line)
    return 1 if events else 0


# ---------- probe journal: bpt_export.py probe ENTITY_TYPE ENTITY_ID ----------
#
# Чтение probe-журнала дебаг-копии шаблона (контракт — etalons.md «Probe-режим»):
# генератор probe() вставляет CrmTimelineCommentAdd после каждого set_vars,
# сюда читаем таймлайн тестовой сущности и восстанавливаем хронологию
# значений рабочих переменных (шаг = исходный SetVariableActivity).

PROBE_TAG = '[probe]'
PROBE_HEAD_RE = re.compile(r'^\[probe\] after (\S+)$')


def parse_probe_comment(text: str):
    """Текст комментария таймлайна → запись probe-журнала | None (не probe).
    Контракт T1: строка 1 '[probe] after <Name SV>', далее '<var>={=Variable:X}'
    с рантайм-подстановкой → фактические данные '<var>=<значение>'. Сплит
    данных по ПЕРВОМУ '=' («=» внутри значения допустим); пустые строки и
    строки без '=' пропускаются. Тег без каноничной головы → step=None
    (репортится WARN — молча терять записи журнала нельзя)."""
    lines = text.splitlines()
    if not lines or not text.startswith(PROBE_TAG):
        return None
    m = PROBE_HEAD_RE.match(lines[0].strip())
    step = m.group(1) if m else None
    vars_ = {}
    for ln in lines[1:]:
        if not ln.strip():
            continue
        key, eq, val = ln.partition('=')
        if not eq:
            continue
        vars_[key.strip()] = val
    return {'step': step, 'vars': vars_, 'raw_head': lines[0]}


def fetch_probe_comments(wh: str, entity_type: str, entity_id: int) -> list:
    """Все комментарии таймлайна сущности: crm.timeline.comment.list, курсор
    filter >ID (мутации между страницами не сдвигают окно — тот же паттерн,
    что fetch_all), order ID ASC = хронология; сверка total — усечение это
    RuntimeError, не молчаливый короткий журнал."""
    comments, last_id, total = [], 0, None
    while True:
        resp = call_retry(wh, 'crm.timeline.comment.list',
                          {'select': ['ID', 'CREATED', 'AUTHOR_ID', 'COMMENT'],
                           'order': {'ID': 'ASC'},
                           'filter': {'ENTITY_TYPE': str(entity_type),
                                      'ENTITY_ID': int(entity_id),
                                      '>ID': last_id}})
        page = resp.get('result') or []
        comments.extend(page)
        if total is None:
            total = resp.get('total')
        if len(page) < 50:
            break
        last_id = max(int(c['ID']) for c in page)
    if total is not None and len(comments) != int(total):
        raise RuntimeError(f'журнал неполный: собрано {len(comments)}, '
                           f'portal total={total} (мутации во время чтения?)')
    return comments


def render_probe_report(entries: list) -> list:
    """Хронология по шагам: заголовок шага + строка на переменную (полные
    значения — весь смысл probe в точности); итерации циклов — серией
    однотипных шагов в естественном порядке ID."""
    out = []
    for i, e in enumerate(entries, 1):
        if e['step'] is None:
            out.append(f'[probe] #{i} WARN: тег есть, голова вне контракта: '
                       f'{e["raw_head"]!r}')
        else:
            head = f'[probe] #{i} after {e["step"]}'
            if e.get('created'):
                head += f' @ {e["created"]}'
            out.append(head)
        for k, v in e['vars'].items():
            out.append(f'[probe]     {k} = {v}')
    return out


def cmd_probe(wh: str, entity_type: str, entity_id: int) -> int:
    """probe-режим: журнал → отчёт. Exit: 0 — записи есть и отчёт построен /
    1 — журнал пуст (шаблон не гонялся / канал) / RuntimeError наружу = 1."""
    comments = fetch_probe_comments(wh, entity_type, entity_id)
    entries = []
    for c in comments:
        parsed = parse_probe_comment(c.get('COMMENT') or '')
        if parsed is None:
            continue
        parsed['id'] = c.get('ID')
        parsed['created'] = c.get('CREATED')
        entries.append(parsed)
    if not entries:
        print(f'[probe] журнал пуст: probe-комментариев на '
              f'{entity_type}#{entity_id} нет (копию не гоняли / не тот '
              f'документ / канал?)', file=sys.stderr)
        return 1
    for line in render_probe_report(entries):
        print(line)
    n_vars = len({k for e in entries for k in e['vars']})
    print(f'[probe] journal parsed: {len(entries)} entries, {n_vars} variables')
    return 0


# ---------- diagram: проекция дерева БП (волна 7 bpt-diagram) ----------

DIAGRAM_FORMATS = ('svg', 'html', 'mermaid', 'd2', 'puml')
DIAGRAM_EXT = {'html': '.diagram.html', 'mermaid': '.mmd',
               'd2': '.d2', 'puml': '.puml'}


def cmd_diagram(path_b: str, base: str = None, fmt: str = 'html',
                out: str = None, with_svg: bool = False) -> int:
    """diagram-режим (волна 7): .bpt → проекция дерева в шейпе дизайнера.
    ДЕФОЛТ — html (главный артефакт: свёртка+инспекция одним файлом);
    SVG только по запросу: --format svg или флаг --svg (дополнительно к
    html). mermaid/d2/puml — python-эмиттеры (d2/puml legacy, заморожены).
    --diff BASE — покраска diff_templates + призраки удалённого.
    Exit: 0 ок / 1 ошибка (node/парс — [diagram] ERROR) / 2 usage."""
    import bpt_diagram  # лениво: тяжёлые импорты не нужны export/probe
    paint = {}
    rename_map = {}
    stats = {}
    if base:
        try:
            paint, stats = bpt_diagram.paint_map(base, path_b)
        except (OSError, ValueError, zlib.error) as e:
            print(f'[diagram] ERROR: paint_map {base} -> {path_b}: {e}',
                  file=sys.stderr)
            return 1
        rename_map = stats.get('rename_map') or {}
    try:
        tree = bpt_diagram.load_tree(path_b, base, rename_map=rename_map)
    except (OSError, ValueError, zlib.error) as e:
        print(f'[diagram] ERROR: {path_b}: {e}', file=sys.stderr)
        return 1
    stem = os.path.basename(path_b)
    if stem.lower().endswith('.bpt'):
        stem = stem[:-4]
    if fmt == 'svg':
        return _diagram_svg(bpt_diagram, tree, paint, out, stem)
    if fmt == 'html':
        # '--out -' — stdout-контракт usage (аудит 2026-10-16 F6); файл по
        # умолчанию — только когда --out не задан вовсе
        if out != '-':
            out = out if out is not None else stem + DIAGRAM_EXT['html']
        bpt_diagram.pack_html(tree, paint, out,
                              diff_report=stats.get('diff_view'))
        if with_svg:
            # svg рядом с html, если --out указывал путь
            svg_out = None
            if out not in (None, '-'):
                svg_out = (out[:-len('.diagram.html')] + '.svg'
                           if out.endswith('.diagram.html')
                           else os.path.splitext(out)[0] + '.svg')
            rc = _diagram_svg(bpt_diagram, tree, paint, svg_out, stem)
            if rc != 0:
                return rc
        return 0
    if fmt == 'mermaid':
        text = bpt_diagram.to_mermaid(tree)
    else:
        raw = bpt_diagram.load_template(path_b)
        text = (bpt_diagram.to_d2(raw) if fmt == 'd2'
                else bpt_diagram.to_puml(raw))
    if out == '-':
        sys.stdout.write(text)
        print(f'[diagram] {fmt}: {len(text)} байт -> stdout', file=sys.stderr)
        return 0
    out = out if out is not None else stem + DIAGRAM_EXT[fmt]
    with open(out, 'w', encoding='utf-8') as f:
        f.write(text)
    print(f'[diagram] {fmt}: {len(text)} байт -> {out}', file=sys.stderr)
    return 0


def _diagram_svg(bpt_diagram, tree: dict, paint: dict, out, stem: str) -> int:
    """svg через node (внешняя зависимость единственного лейаутера):
    tree/paint во временные JSON → stdout рантайма → файл/stdout."""
    import shutil
    import subprocess
    import tempfile
    node_bin = shutil.which('node')
    if node_bin is None:
        print('[diagram] ERROR: node не найден в PATH '
              '(svg = проекция runtime.js)', file=sys.stderr)
        return 1
    with tempfile.TemporaryDirectory(prefix='bpt_diagram_') as td:
        tj = os.path.join(td, 'tree.json')
        with open(tj, 'w', encoding='utf-8') as f:
            json.dump(tree, f, ensure_ascii=False, default=str)
        argv = [node_bin, bpt_diagram.RUNTIME_JS, tj]
        if paint:
            pj = os.path.join(td, 'paint.json')
            with open(pj, 'w', encoding='utf-8') as f:
                json.dump(paint, f, ensure_ascii=False)
            argv.append(pj)
        t0 = time.time()
        try:
            r = subprocess.run(argv, capture_output=True, timeout=60)
        except subprocess.TimeoutExpired:
            print('[diagram] ERROR: node не уложился в 60 с', file=sys.stderr)
            return 1
        ms = int((time.time() - t0) * 1000)
        if r.returncode != 0:
            sys.stderr.write(r.stderr.decode('utf-8', 'replace'))
            print(f'[diagram] ERROR: node exit={r.returncode}',
                  file=sys.stderr)
            return 1
        if r.stderr:
            sys.stderr.write(r.stderr.decode('utf-8', 'replace'))
    svg = r.stdout
    if out == '-':
        sys.stdout.buffer.write(svg)
        print(f'[diagram] svg: {len(svg)} байт, node {ms} мс -> stdout',
              file=sys.stderr)
        return 0
    out = out or (stem + '.svg')
    with open(out, 'wb') as f:
        f.write(svg)
    print(f'[diagram] svg: {len(svg)} байт, node {ms} мс -> {out}',
          file=sys.stderr)
    return 0


def _template_stem(tid, safe_name: str) -> str:
    """Стем файлов экспорта. ID шаблона приходит из REST-ответа и до аудита
    2026-10-16 (№8) уходил в имя файла несанитизированным (санитизировалось
    только NAME) — чужой/скомпрометированный портал мог писать
    `_<safe>.bpt|.json` в произвольный путь (JSON-вариант = перезапись
    конфигов). Числовой ID (все живые порталы) проходит без изменений;
    сработавшая санитизация оставляет WARN-след в stderr."""
    raw = str(tid)
    safe_tid = re.sub(r'[^\w.-]+', '_', raw)[:60]
    if safe_tid != raw:
        print(f'[FIX] WARN: template ID {raw!r} -> {safe_tid!r} '
              f'(path-traversal гейт, аудит 2026-10-16 №8)', file=sys.stderr)
    return f'{safe_tid}_{safe_name}'


def main():
    args = sys.argv[1:]
    if args and args[0] == 'diff':
        if len(args) != 3:
            print('usage: bpt_export.py diff A.bpt B.bpt '
                  '(exit: 0 идентичны / 1 дифф / 2 usage)', file=sys.stderr)
            sys.exit(2)
        sys.exit(cmd_diff(args[1], args[2]))
    if args and args[0] == 'probe':
        if len(args) not in (3, 4):
            print('usage: bpt_export.py probe ENTITY_TYPE ENTITY_ID '
                  '[WEBHOOK_SRC] — probe-журнал дебаг-копии сущности; '
                  'webhook: 4-й аргумент (файл/URL) или env B24_WEBHOOK; '
                  'ENTITY_TYPE: deal/lead/… или DYNAMIC_<etid> для СП '
                  '(формат фильтра comment.list; голый числовой etid = 400); '
                  'exit: 0 журнал / 1 пусто|ошибка / 2 usage', file=sys.stderr)
            sys.exit(2)
        entity_type, entity_id = args[1], args[2]
        try:
            entity_id_i = int(entity_id)
        except ValueError:
            print(f'ENTITY_ID должен быть числом: {entity_id!r}',
                  file=sys.stderr)
            sys.exit(2)
        src = args[3] if len(args) > 3 else os.environ.get('B24_WEBHOOK')
        if not src:
            print('webhook не задан: 4-й аргумент (файл или URL) или env '
                  'B24_WEBHOOK', file=sys.stderr)
            sys.exit(2)
        if src.startswith('http'):
            print('[FIX] WARN: webhook-URL в argv виден в `ps` всё время '
                  'прогона — предпочтителен файл с URL или env B24_WEBHOOK',
                  file=sys.stderr)
            wh = src
        else:
            try:
                wh = open(src).read().strip()
            except OSError as e:
                print(f'webhook-файл не читается: {src}: {e}', file=sys.stderr)
                sys.exit(2)
        try:
            sys.exit(cmd_probe(wh, entity_type, entity_id_i))
        except RuntimeError as e:
            print(f'PROBE READ FAILED: {e}', file=sys.stderr)
            sys.exit(1)
    src = args[0] if args else os.environ.get('B24_WEBHOOK')
    if args and args[0] == 'diagram':
        rest = args[1:]
        fmt, base, out, pos = 'html', None, None, []
        with_svg = False
        bad = False
        i = 0
        while i < len(rest):
            a = rest[i]
            if a == '--svg':
                with_svg = True
                i += 1
            elif a in ('--diff', '--format', '--out'):
                if i + 1 >= len(rest):
                    bad = True
                    break
                val = rest[i + 1]
                i += 2
                if a == '--diff':
                    base = val
                elif a == '--format':
                    fmt = val
                else:
                    out = val
            elif a.startswith('--diff='):
                base = a.split('=', 1)[1]
                i += 1
            elif a.startswith('--format='):
                fmt = a.split('=', 1)[1]
                i += 1
            elif a.startswith('--out='):
                out = a.split('=', 1)[1]
                i += 1
            elif a.startswith('--'):
                bad = True
                break
            else:
                pos.append(a)
                i += 1
        if bad or len(pos) != 1 or fmt not in DIAGRAM_FORMATS:
            print('usage: bpt_export.py diagram X.bpt [--diff BASE.bpt] '
                  '[--format html|svg|mermaid|d2|puml] [--out FILE|-] '
                  '[--svg] '
                  '(default html; svg только по запросу: --format svg '
                  'или --svg дополнительно к html; exit: 0 ок / 1 ошибка '
                  '/ 2 usage)', file=sys.stderr)
            sys.exit(2)
        sys.exit(cmd_diagram(pos[0], base, fmt, out, with_svg))
    if not src:
        print(__doc__)
        print('webhook не задан: argv[1] (файл или URL) или env B24_WEBHOOK',
              file=sys.stderr)
        sys.exit(2)
    out_dir = args[1] if len(args) > 1 else 'bpt_out'
    os.makedirs(out_dir, exist_ok=True)
    if src.startswith('http'):
        print('[FIX] WARN: webhook-URL в argv виден в `ps` всё время прогона — '
              'предпочтителен файл с URL или env B24_WEBHOOK', file=sys.stderr)
        wh = src
    else:
        try:
            wh = open(src).read().strip()
        except OSError as e:
            # exit-контракт как в probe-режиме (асимметрия аудита:
            # был traceback)
            print(f'webhook-файл не читается: {src}: {e}', file=sys.stderr)
            sys.exit(2)

    try:
        templates = fetch_all(wh, out_dir)
    except RuntimeError as e:
        print(f'EXPORT FAILED: {e}', file=sys.stderr)
        sys.exit(1)

    summary = []
    for t in templates:
        tid, name = t.get('ID'), t.get('NAME', 'noname')
        safe = re.sub(r'[^\w.-]+', '_', name)[:60]
        entry = {'ID': tid, 'NAME': name, 'MODULE_ID': t.get('MODULE_ID'),
                 'ENTITY': t.get('ENTITY'), 'DOCUMENT_TYPE': t.get('DOCUMENT_TYPE'),
                 'AUTO_EXECUTE': t.get('AUTO_EXECUTE')}
        td = t.get('TEMPLATE')
        if isinstance(td, list) and td and isinstance(td[0], dict):
            td = td  # канон: список корневых активностей
        elif isinstance(td, dict):
            td = [td]  # бывает и одинокий корень-словарь
        if td:
            try:
                # канон-контейнер .bpt из полей list-ответа: REST отдаёт
                # дерево уже развёрнутым (select[]=TEMPLATE; метода
                # template.get нет) — собираем наш канон, а не блоб портала
                from bpt_serialize import (compress_bpt,
                                           php_serialize)
                tree = {'TEMPLATE': td,
                        'PARAMETERS': t.get('PARAMETERS') or {},
                        'VARIABLES': t.get('VARIABLES') or {},
                        'CONSTANTS': t.get('CONSTANTS') or {}}
                uns = php_serialize(tree)
                blob = compress_bpt(uns, 9)
                stem = _template_stem(tid, safe)
                open(os.path.join(out_dir, stem + '.bpt'), 'wb').write(blob)
                open(os.path.join(out_dir, stem + '.json'), 'w',
                     encoding='utf-8').write(dump_template_json(uns))
                acts = []
                for root in tree['TEMPLATE']:
                    walk_activities(root, acts)
                entry['activities'] = sorted({a['Type'] for a in acts})
                entry['vars'] = sorted((tree.get('VARIABLES') or {}).keys())
                entry['params'] = sorted(
                    (tree.get('PARAMETERS') or {}).keys())
                entry['decoded'] = True
            except Exception as e:
                entry['decoded'] = False
                entry['error'] = str(e)
        else:
            entry['decoded'] = None  # TEMPLATE пуст в ответе list
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
            lines.append('- (TEMPLATE пуст в ответе list)')
        else:
            lines.append(f"- DECODE ERROR: {e.get('error')}")
        lines.append('')
    open(os.path.join(out_dir, 'summary.md'), 'w').write('\n'.join(lines))
    print(f'done: {len(templates)} templates -> {out_dir}/ (see summary.md)')


if __name__ == '__main__':
    main()

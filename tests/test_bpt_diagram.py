#!/usr/bin/env python3
"""tests/test_bpt_diagram.py — жгут волны 7 (bpt-diagram, план
внутренний план волны 7).

Запуск: python3 tests/test_bpt_diagram.py  (из каталога bpt; zero-dep;
pytest-режим через tests/conftest.py — тот же список CHECKS).

Слои:
- golden через subprocess node (SKIP, если node нет в PATH — bpt это
  standalone GH-зеркало, CI может быть без node; отсутствие node — не
  FAIL): фикстуры fixtures/diagram/*.json → --json/--palette=test →
  байт-в-байт сверка с fixtures/diagram/golden_*.json. Golden гоняют
  ТЕСТОВУЮ палитру — смена дефолтной палитры runtime золото не
  переписывает. Регенерация golden (после ОСОЗНАННОЙ смены лейаута):
  node bpt_diagram_runtime.js tests/fixtures/diagram/<x>.json
  --palette=test --json > tests/fixtures/diagram/golden_<x>.json
- инварианты лейаута: нет пересечений боксов одного столбца; merge-точка
  не выше низов веток; свёртка контейнера сжимает канву и не двигает
  x-колонки сиблингов.
- unit python-слоя: paint_map (activated игнор, added поддерево целиком,
  rename-пара = RENAME без покраски — identity-якоря матчат узел по
  Type+Title, Name не участвует: документируем РЕАЛЬНОЕ поведение),
  ghost-интерколяция «по порядку A», лимит переименованного родителя
  (призраки теряются), templateData-нормализация (DOCUMENT_FIELDS вне
  блока). Синтетические .bpt строятся php_serialize+compress_bpt.
- corpus smoke: все etalons/*.bpt проходят load_tree.
- ParallelActivity: в корпусе etalons образца НЕТ (проверено grep,
  конструктора parallel в bpt_build нет) → синтетическая tree-JSON
  фикстура, эмит веером как IfElse — ЭКСТРАПОЛЯЦИЯ, корпусом не
  подтверждена (профилактика ретро №3: помечаем, не утверждаем).
"""
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
BPT = os.path.dirname(HERE)
sys.path.insert(0, BPT)

import bpt_diagram  # noqa: E402
import bpt_serialize  # noqa: E402

FIX = os.path.join(HERE, 'fixtures', 'diagram')
RUNTIME = os.path.join(BPT, 'bpt_diagram_runtime.js')
ETALONS = os.path.join(BPT, 'etalons')
PY = sys.executable
TMP = tempfile.mkdtemp(prefix='bpt_diagram_tests_')

CHECKS = []


def check(fn):
    CHECKS.append(fn)
    return fn


def node_available():
    return shutil.which('node') is not None


def run_runtime(*args):
    return subprocess.run([shutil.which('node'), RUNTIME, *args],
                          capture_output=True, timeout=60)


def read_fixture(name):
    with open(os.path.join(FIX, name), encoding='utf-8') as f:
        return json.load(f)


def read_golden(name):
    with open(os.path.join(FIX, name), encoding='utf-8') as f:
        return f.read()


# --- синтетика .bpt (raw-деревья сериализуются в файл) ----------------------

def act(name, type_, title, children=None, props=None, activated='Y'):
    return {'Type': type_, 'Name': name, 'Activated': activated,
            'Node': None, 'Properties': {'Title': title, **(props or {})},
            'Children': children or []}


def write_bpt(tpl: dict) -> str:
    path = os.path.join(TMP, f'{abs(hash(json.dumps(tpl, sort_keys=True, default=str)))}.bpt')
    with open(path, 'wb') as f:
        f.write(bpt_serialize.compress_bpt(
            bpt_serialize.php_serialize(tpl)))
    return path


# --- golden: детерминизм и снапшоты (нужен node) ----------------------------

@check
def golden_designer_snapshot():
    if not node_available():
        print('[SKIP] node отсутствует — golden-тесты пропущены', end='; ')
        return
    r = run_runtime(os.path.join(FIX, 'designer_tree.json'),
                    '--palette=test', '--json')
    assert r.returncode == 0, f'node exit={r.returncode}: {r.stderr!r}'
    assert r.stdout.decode() == read_golden('golden_designer.json'), \
        'golden designer разъехался (осознанная смена лейаута? — регенерируй)'


@check
def golden_designer_folded_snapshot():
    if not node_available():
        print('[SKIP] node отсутствует — golden-тесты пропущены', end='; ')
        return
    r = run_runtime(os.path.join(FIX, 'designer_tree.json'),
                    os.path.join(FIX, 'paint_empty.json'),
                    os.path.join(FIX, 'fold_while.json'),
                    '--palette=test', '--json')
    assert r.returncode == 0, f'node exit={r.returncode}: {r.stderr!r}'
    assert r.stdout.decode() == read_golden('golden_designer_folded.json'), \
        'golden folded разъехался'


@check
def golden_parallel_snapshot():
    if not node_available():
        print('[SKIP] node отсутствует — golden-тесты пропущены', end='; ')
        return
    r = run_runtime(os.path.join(FIX, 'parallel_tree.json'),
                    '--palette=test', '--json')
    assert r.returncode == 0, f'node exit={r.returncode}: {r.stderr!r}'
    assert r.stdout.decode() == read_golden('golden_parallel.json'), \
        'golden parallel разъехался'


@check
def golden_determinism_repeat_call():
    if not node_available():
        print('[SKIP] node отсутствует — golden-тесты пропущены', end='; ')
        return
    a = run_runtime(os.path.join(FIX, 'designer_tree.json'),
                    '--palette=test', '--json')
    b = run_runtime(os.path.join(FIX, 'designer_tree.json'),
                    '--palette=test', '--json')
    assert a.stdout == b.stdout, 'два прогона — разный байт-поток'


# --- инварианты лейаута (через node --json) ---------------------------------

@check
def layout_no_same_column_overlaps():
    if not node_available():
        print('[SKIP] node отсутствует — golden-тесты пропущены', end='; ')
        return
    r = run_runtime(os.path.join(FIX, 'designer_tree.json'),
                    '--palette=test', '--json')
    res = json.loads(r.stdout)
    boxes = {k: v for k, v in res['coords'].items()
             if not k.startswith('data:')}
    keys = sorted(boxes)
    for i, ka in enumerate(keys):
        for kb in keys[i + 1:]:
            a, b = boxes[ka], boxes[kb]
            if a['x'] == b['x']:
                assert a['y'] + a['h'] <= b['y'] or b['y'] + b['h'] <= a['y'], \
                    f'пересечение в столбце x={a["x"]}: {ka} / {kb}'


@check
def layout_merge_below_branch_bottoms():
    if not node_available():
        print('[SKIP] node отсутствует — golden-тесты пропущены', end='; ')
        return
    tree = read_fixture('designer_tree.json')
    r = run_runtime(os.path.join(FIX, 'designer_tree.json'),
                    '--palette=test', '--json')
    res = json.loads(r.stdout)

    def bottoms(node):
        acc = []
        box = res['coords'].get(node.get('name'))
        if box:
            acc.append(box['y'] + box['h'])
        for c in node.get('children') or []:
            acc += bottoms(c)
        return acc

    def walk(node):
        if node.get('type') == 'IfElseActivity':
            merge = res['nodeIndex'].get(f'@merge:{node["name"]}')
            assert merge, f'нет merge-точки для {node["name"]}'
            for br in node.get('children') or []:
                bb = max(bottoms(br))
                assert bb <= merge['y'], \
                    f'ветка {br["name"]} ниже merge: {bb} > {merge["y"]}'
        for c in node.get('children') or []:
            walk(c)

    walk(tree['root'])


@check
def fold_shrinks_canvas_and_keeps_columns():
    if not node_available():
        print('[SKIP] node отсутствует — golden-тесты пропущены', end='; ')
        return
    full = json.loads(run_runtime(os.path.join(FIX, 'designer_tree.json'),
                                  '--palette=test', '--json').stdout)
    folded = json.loads(run_runtime(
        os.path.join(FIX, 'designer_tree.json'),
        os.path.join(FIX, 'paint_empty.json'),
        os.path.join(FIX, 'fold_while.json'),
        '--palette=test', '--json').stdout)
    assert folded['stats']['h'] < full['stats']['h'], \
        'свёртка не сжала канву'
    chip = folded['nodeIndex']['n_while']
    assert chip['kind'] == 'folded' and chip['subtree'] >= 2, \
        f'чип свёртки кривой: {chip}'
    for name in ('n_start', 'n_if', 'n_term'):
        assert full['coords'][name]['x'] == folded['coords'][name]['x'], \
            f'сдвинулась x-колонка сиблинга {name}'


# --- unit python-слоя --------------------------------------------------------

@check
def paint_activated_is_ignored():
    base = {'TEMPLATE': [act('r', 'SequentialWorkflowActivity', 'root', [
        act('a', 'SetVariableActivity', 'Один', props={'VariableValue': {'x': '1'}}),
    ])], 'VARIABLES': [], 'PARAMETERS': [], 'CONSTANTS': []}
    changed = json.loads(json.dumps(base, default=str))
    changed['TEMPLATE'][0]['Children'][0]['Activated'] = 'N'
    pa, pb = write_bpt(base), write_bpt(changed)
    paint, stats = bpt_diagram.paint_map(pa, pb)
    assert paint == {}, f'activated покрасил: {paint}'
    assert stats['ignored'] >= 1, f'activated не попал в игнор: {stats}'


@check
def paint_added_subtree_whole():
    base = {'TEMPLATE': [act('r', 'SequentialWorkflowActivity', 'root', [
        act('keep', 'SetVariableActivity', 'Живой'),
    ])], 'VARIABLES': [], 'PARAMETERS': [], 'CONSTANTS': []}
    grown = json.loads(json.dumps(base, default=str))
    grown['TEMPLATE'][0]['Children'].append(
        act('newparent', 'WhileActivity', 'Новый цикл', [
            act('newkid', 'SetVariableActivity', 'Новый ребёнок'),
        ]))
    pa, pb = write_bpt(base), write_bpt(grown)
    paint, _ = bpt_diagram.paint_map(pa, pb)
    assert paint.get('newparent') == 'added', f'{paint}'
    assert paint.get('newkid') == 'added', 'added не покрасил поддерево целиком'
    assert 'keep' not in paint, 'зацепил не-added узел'


@check
def rename_pair_is_rename_not_removal():
    base = {'TEMPLATE': [act('r', 'SequentialWorkflowActivity', 'root', [
        act('old_name', 'SetVariableActivity', 'Формула выручки'),
    ])], 'VARIABLES': [], 'PARAMETERS': [], 'CONSTANTS': []}
    renamed = json.loads(json.dumps(base, default=str))
    renamed['TEMPLATE'][0]['Children'][0]['Name'] = 'new_name'
    pa, pb = write_bpt(base), write_bpt(renamed)
    paint, stats = bpt_diagram.paint_map(pa, pb)
    assert paint == {}, \
        f'rename дал покраску {paint} (identity-якоря должны матчить)'
    assert stats['renames'] == 1, f'rename не замечен: {stats}'


@check
def ghost_intercalation_keeps_a_order():
    a_tpl = {'TEMPLATE': [act('r', 'SequentialWorkflowActivity', 'root', [
        act('x', 'SetVariableActivity', 'X'),
        act('y', 'SetVariableActivity', 'Y-удалён'),
        act('z', 'SetVariableActivity', 'Z'),
    ])], 'VARIABLES': [], 'PARAMETERS': [], 'CONSTANTS': []}
    b_tpl = json.loads(json.dumps(a_tpl, default=str))
    b_tpl['TEMPLATE'][0]['Children'] = \
        [c for c in b_tpl['TEMPLATE'][0]['Children'] if c['Name'] != 'y']
    pa, pb = write_bpt(a_tpl), write_bpt(b_tpl)
    tree = bpt_diagram.load_tree(pb, base=pa)
    names = [c['name'] for c in tree['root']['children']]
    assert names == ['x', 'y', 'z'], f'порядок A сломан: {names}'
    assert tree['root']['children'][1].get('ghost') is True, \
        'y не призрак'
    assert tree['root']['children'][1]['count'] == 1, 'count поддерева кривой'


@check
def renamed_parent_pairing_via_rename_map():
    """rename-родитель + удалённый ребёнок: БЕЗ карты — деградация v1
    (призрак старого имени + added нового); С картой (stats['rename_map']
    из paint_map) — призрак ребёнка встаёт под переименованного родителя."""
    a_tpl = {'TEMPLATE': [act('r', 'SequentialWorkflowActivity', 'root', [
        act('P', 'WhileActivity', 'Цикл', [
            act('kid', 'SetVariableActivity', 'Ребёнок'),
        ]),
    ])], 'VARIABLES': [], 'PARAMETERS': [], 'CONSTANTS': []}
    b_tpl = json.loads(json.dumps(a_tpl, default=str))
    root_ch = b_tpl['TEMPLATE'][0]['Children']
    root_ch[0]['Name'] = 'Q'          # родитель переименован
    root_ch[0]['Children'] = []       # ...и ребёнок удалён
    pa, pb = write_bpt(a_tpl), write_bpt(b_tpl)

    # деградация v1: без карты P целиком призрак + Q added-новый
    tree = bpt_diagram.load_tree(pb, base=pa)
    kids = tree['root']['children']
    assert [c['name'] for c in kids] == ['P', 'Q'], \
        f'v1-деградация изменилась: {[c["name"] for c in kids]}'
    assert kids[0].get('ghost') and kids[0]['count'] == 2, \
        'призрак P потерял счёт поддерева'

    # с картой: Q жив, призрак ребёнка под ним
    _paint, stats = bpt_diagram.paint_map(pa, pb)
    assert stats['rename_map'] == {'P': 'Q'}, stats['rename_map']
    tree = bpt_diagram.load_tree(pb, base=pa,
                                 rename_map=stats['rename_map'])
    q = tree['root']['children'][0]
    assert q['name'] == 'Q' and not q.get('ghost'), 'Q должен быть живым'
    assert len(q['children']) == 1 and q['children'][0].get('ghost'), \
        f'призрак ребёнка не встал под Q: {q["children"]}'
    assert q['children'][0]['name'] == 'kid', 'призрак не тот'


@check
def template_data_normalized_document_fields_out():
    tpl = {
        'TEMPLATE': [act('r', 'SequentialWorkflowActivity', 'root')],
        'PARAMETERS': [],
        'VARIABLES': {'sum': {'Name': 'sum', 'Title': 'Сумма',
                              'Type': 'double', 'Required': '1',
                              'Multiple': '0'}},
        'CONSTANTS': {'VAT': {'Name': 'VAT', 'Title': 'НДС',
                              'Type': 'string', 'Required': '',
                              'Default': '20%'}},
        'DOCUMENT_FIELDS': {'CRM_ID': {'Name': 'CRM item ID',
                                       'Type': 'string',
                                       'BaseType': 'string'}},
    }
    td = bpt_diagram.template_data(tpl)
    assert set(td) == {'parameters', 'variables', 'constants'}, td.keys()
    assert td['variables'] == [{'name': 'sum', 'title': 'Сумма',
                                'type': 'double', 'required': True}], \
        td['variables']
    assert td['constants'] == [{'name': 'VAT', 'title': 'НДС',
                                'type': 'string', 'required': False,
                                'default': '20%'}], td['constants']
    assert td['parameters'] == []
    flat = json.dumps(td, ensure_ascii=False)
    assert 'CRM_ID' not in flat and 'CRM item ID' not in flat, \
        'DOCUMENT_FIELDS протёк в блок данных'


@check
def pack_html_escapes_script_breakout():
    """№9 аудита 2026-10-16: эскейп только </ недостаточен — HTML5-токенизатор
    в script-data-escaped: `<!--` в данных делает настоящий </script>
    незакрывающим, `<script` открывает вложенный контекст. Оракул: payload
    без raw `<` ВООБЩЕ (все вариации разом), roundtrip JSON-строк байт-в-байт,
    ровно 2 script-блока."""
    evil = '<!--</script><script><b>инъекция</b>-->'
    tree = {'root': act('r', 'SequentialWorkflowActivity', 'root', [
        act('evil', 'SetVariableActivity', evil),
    ]), 'templateData': {'parameters': [], 'variables': [], 'constants': []}}
    out = os.path.join(TMP, 'evil.diagram.html')
    bpt_diagram.pack_html(tree, {}, out)
    html = open(out, encoding='utf-8').read()
    # payload-инъекция: сырой '</script>' внутри JSON недопустим
    assert html.count('<script>') == 2, 'блоков script не два'
    assert '</script>' in html, 'runtime-блок не закрыт'
    # Data-блок — ровно одна строка 'const DATA = {...};' в bootstrap;
    # literal 'const DATA = ' внутри JS-строки runtime — не Data-блок
    m = re.search(r'^const DATA = (.+)$', html, re.M)
    assert m, 'Data-строка не найдена'
    blob = m.group(1).rstrip().rstrip(';')
    assert '<' not in blob, \
        'raw < в payload: <!-- или <script прорвались (script-data-escaped)'
    import json as _json
    restored = _json.loads(blob)
    assert restored['tree']['root']['Children'][0]['Properties']['Title'] == evil, \
        'roundtrip: строки payload искажены экранированием'
    # шебанг runtime вне позиции 0 = invalid token в браузере (грабли
    # визуальной проверки 2026-10-16)
    inline_start = html.split('<script>', 1)[1]
    assert not inline_start.lstrip('\n').startswith('#!'), \
        'шебанг уехал в inline-script'


@check
def pack_html_bootstrap_executes():
    """html исполняется: runtime+bootstrap в node vm (DOM-шим), render()
    рисует канву. Ловит синтаксический разрыв JS в питоновском шаблоне
    (_HTML_SHELL): '\\n' в питоне = реальный перенос внутри JS-строки —
    bootstrap падает, канва пустая (кейс сломанного bootstrap-мокапа)."""
    if not node_available():
        print('[SKIP] node отсутствует — golden-тесты пропущены', end='; ')
        return
    import re
    tree = {'root': act('r', 'SequentialWorkflowActivity', 'root', [
        act('a', 'SetVariableActivity', 'Один'),
    ]), 'templateData': {'parameters': [], 'variables': [],
                         'constants': []}}
    out = os.path.join(TMP, 'boot.diagram.html')
    bpt_diagram.pack_html(tree, {}, out)
    html = open(out, encoding='utf-8').read()
    scripts = re.findall(r'<script>([\s\S]*?)</script>', html)
    assert len(scripts) == 2, f'script-блоков {len(scripts)}, ждём 2'
    shim = (
        'const els = {};'
        'const fake = () => ({innerHTML: "", textContent: "",'
        ' appendChild(){}, addEventListener(){}, setAttribute(){},'
        ' getBoundingClientRect(){ return {left: 0, top: 0}; },'
        ' scrollLeft: 0, scrollTop: 0, style: {}});'
        'const document = {'
        ' getElementById(id){ return els[id] || (els[id] = fake()); },'
        ' createElement(){ return fake(); }};'
        'const window = { addEventListener() {} };')
    js = (shim + scripts[0] + '\n;' + scripts[1]
          + '\n;console.log(JSON.stringify({svg: document.getElementById'
          + '("stage").innerHTML.includes("<svg")}))')
    r = subprocess.run([shutil.which('node'), '-e', js],
                       capture_output=True, timeout=60)
    assert r.returncode == 0, \
        f'html-скрипты упали: {r.stderr[:300]!r}'
    assert b'"svg":true' in r.stdout, \
        f'канва не отрисована: {r.stdout[:200]!r}'


# --- corpus smoke ------------------------------------------------------------

@check
def corpus_load_tree_smoke():
    etas = sorted(glob.glob(os.path.join(ETALONS, '*.bpt')))
    assert etas, 'etalons пуст — corpus smoke бессмыслен'
    for src in etas:
        tree = bpt_diagram.load_tree(src)
        assert tree['root']['type'] == 'SequentialWorkflowActivity', \
            f'{src}: корень не Sequential'
        assert set(tree['templateData']) == {'parameters', 'variables',
                                             'constants'}, f'{src}: td'


@check
def mermaid_balanced_and_quoted():
    tree = read_fixture('designer_tree.json')
    text = bpt_diagram.to_mermaid(tree)
    lines = [ln for ln in text.splitlines() if ln.strip()]
    sub = sum(1 for ln in lines if ln.startswith('subgraph'))
    end = sum(1 for ln in lines if ln.strip() == 'end')
    assert sub == end, f'subgraph/end разъехались: {sub}/{end}'
    for ln in lines:
        if '["' in ln or '-->|"' in ln or '{"' in ln:
            assert ln.count('"') % 2 == 0, f'нечётные кавычки: {ln}'
    assert 'Данные шаблона' in text, 'блок данных не попал в mermaid'


@check
def parallel_fixture_renders_extropolated_fan():
    """OPEN-фикстура: ParallelActivity корпусом не подтверждён — эмит веером
    как IfElse. Тест фиксирует РЕШЕНИЕ (веер без бара), не «истину»."""
    if not node_available():
        print('[SKIP] node отсутствует — golden-тесты пропущены', end='; ')
        return
    r = run_runtime(os.path.join(FIX, 'parallel_tree.json'),
                    '--palette=test', '--json')
    res = json.loads(r.stdout)
    par = res['nodeIndex']['par1']
    assert par['kind'] == 'parallel', f'kind={par["kind"]}'
    # у Parallel нет бара-условия: сам узел — activity-стиль, ветки — колонки
    assert res['nodeIndex'].get('@merge:par1'), 'нет merge у Parallel'


# --- CLI (bpt_export.py diagram) ---------------------------------------------

@check
def cli_diagram_formats_and_exit_contract():
    if not node_available():
        print('[SKIP] node отсутствует — golden-тесты пропущены', end='; ')
        return
    src = sorted(glob.glob(os.path.join(ETALONS, '*.bpt')))[0]
    # дефолт — html; SVG не генерится без запроса (слово владельца)
    out_html = os.path.join(TMP, 'cli_default.diagram.html')
    out_svg = os.path.join(TMP, 'cli_default.svg')
    for f in (out_html, out_svg):
        if os.path.exists(f):
            os.remove(f)
    r = subprocess.run(
        [PY, os.path.join(BPT, 'bpt_export.py'), 'diagram', src,
         '--out', out_html],
        capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, f'html exit={r.returncode}: {r.stderr}'
    assert os.path.exists(out_html), 'дефолт не дал html'
    assert not os.path.exists(out_svg), 'svg сгенерился без запроса'
    # --svg дополнительно к html
    r = subprocess.run(
        [PY, os.path.join(BPT, 'bpt_export.py'), 'diagram', src,
         '--out', out_html, '--svg'],
        capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, f'--svg exit={r.returncode}: {r.stderr}'
    assert os.path.exists(out_svg), '--svg не дал svg'
    assert os.path.getsize(out_svg) > 1000, 'svg пустой'
    # явный формат + мусорный формат = usage
    r = subprocess.run(
        [PY, os.path.join(BPT, 'bpt_export.py'), 'diagram', src,
         '--format', 'svg', '--out', out_svg],
        capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, f'svg exit={r.returncode}: {r.stderr}'
    r = subprocess.run(
        [PY, os.path.join(BPT, 'bpt_export.py'), 'diagram', src,
         '--format', 'bogus'],
        capture_output=True, text=True, timeout=60)
    assert r.returncode == 2, f'bogus format exit={r.returncode}'
    assert 'usage' in r.stderr, 'нет usage на мусорный формат'


def main():
    ok, fail, skip = 0, 0, 0
    for fn in CHECKS:
        try:
            fn()
            ok += 1
            print(f'[OK] {fn.__name__}')
        except AssertionError as e:
            if '[SKIP]' in str(e):
                skip += 1
                print(f'[SKIP] {fn.__name__}: {e}')
            else:
                fail += 1
                print(f'[FAIL] {fn.__name__}: {e}')
        except Exception as e:  # noqa: BLE001 — жгут рапортует, не роняет
            fail += 1
            print(f'[FAIL] {fn.__name__}: {type(e).__name__}: {e}')
    print(f'--- {ok}/{len(CHECKS)} passed, {skip} skipped, {fail} failed')
    return 1 if fail else 0


@check
def paint_map_base_template_guards():
    """BASE без TEMPLATE / с TEMPLATE:[] — громкий ValueError (exit 1
    через cmd_diagram), не тихая деградация «+0 -0 ~0» и не IndexError
    (аудит)."""
    import bpt_serialize as bs
    def mk(payload):
        p = os.path.join(TMP, 'bad_base.bpt')
        with open(p, 'wb') as f:
            f.write(bs.compress_bpt(bs.php_serialize(payload)))
        return p
    target = os.path.join(ETALONS, 'date-window-branch.bpt')
    for label, payload in (('без TEMPLATE', {'VERSION': 2}),
                           ('TEMPLATE:[]', {'VERSION': 2, 'TEMPLATE': []})):
        try:
            bpt_diagram.paint_map(mk(payload), target)
        except ValueError:
            pass
        else:
            raise AssertionError(f'BASE {label} прошёл тихо')


@check
def d2_esc_backslash():
    """d2-эмиттер: backslash в лейблах не экранировался (legacy-зона,
    но битые строки d2 = невоспроизводимый артефакт)."""
    got = bpt_diagram.esc('a\\b')
    assert got == 'a\\\\b', 'backslash не экранирован: ' + repr(got)


if __name__ == '__main__':
    sys.exit(main())

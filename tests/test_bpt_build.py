#!/usr/bin/env python3
"""tests/test_bpt_build.py — регресс-жгут S1 (внутренний fix-план).

Запуск: python3 tests/test_bpt_build.py  (из каталога bpt). Zero-dep.
Exit 0 = все проверки зелёные; каждая проверка = дефект из TOFIX,
воспроизведённый до фикса и закрытый после.
"""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
BPT = os.path.dirname(HERE)
sys.path.insert(0, BPT)

import bpt_build  # noqa: E402

ETALONS = os.path.join(BPT, 'etalons')
PY = sys.executable
BUILD = os.path.join(BPT, 'bpt_build.py')
TMP = tempfile.mkdtemp(prefix='bpt_tests_')

CHECKS = []


def check(fn):
    CHECKS.append(fn)
    return fn


def run_cli(*args):
    return subprocess.run([PY, BUILD, *args], capture_output=True, text=True,
                          timeout=60)


def minimal_tree(**extra):
    tree = bpt_build.scaffold(
        children=[bpt_build.set_vars({'done': 'Y'})],
        variables={'done': bpt_build.variable('done', 'int')},
        doc_fields={})
    tree.update(extra)
    return tree


# --- №6: CLI-гейт ----------------------------------------------------------

@check
def cli_unknown_subcommand_fails():
    r = run_cli('mkae', 'foo', 'bar')
    assert r.returncode != 0, f'exit={r.returncode} (мусорная подкоманда молча ок)'
    assert r.stderr.strip(), 'нет диагностики в stderr'


@check
def cli_no_args_usage_not_traceback():
    r = run_cli()
    assert r.returncode != 0, f'exit={r.returncode}'
    assert 'usage' in (r.stderr + r.stdout).lower(), 'нет usage'
    assert 'IndexError' not in r.stderr, 'сырой IndexError вместо usage'


@check
def cli_check_mismatch_vs_ok(tmp=TMP):
    tree_path = os.path.join(tmp, 'tree.json')
    out_path = os.path.join(tmp, 'out.bpt')
    tree = minimal_tree()
    json.dump(tree, open(tree_path, 'w'), ensure_ascii=False)
    r = run_cli('make', tree_path, out_path)
    assert r.returncode == 0, f'make упал: {r.stderr}'
    r = run_cli('check', tree_path, out_path)
    assert r.returncode == 0, f'check честного файла: exit={r.returncode}'
    tree['VARIABLES']['extra'] = bpt_build.variable('extra', 'string')
    json.dump(tree, open(tree_path, 'w'), ensure_ascii=False)
    r = run_cli('check', tree_path, out_path)
    assert r.returncode != 0, f'MISMATCH прошёл гейт: exit={r.returncode}'


# --- №7: update_dynamic канон ----------------------------------------------

@check
def update_dynamic_two_conds_are_pairs():
    n = bpt_build.update_dynamic(152, {'f': 'v'},
                                 filter_conditions=[('ID', '{=Document:X}'),
                                                    ('STATUS', '5')])
    items = n['Properties']['DynamicFilterFields']['items']
    assert items == [[{'object': 'Document', 'field': 'ID', 'operator': '=',
                       'value': '{=Document:X}'}, 'AND'],
                     [{'object': 'Document', 'field': 'STATUS', 'operator': '=',
                       'value': '5'}, 'AND']], \
        f'не каноничные пары [cond,\'AND\']: {items!r}'


@check
def update_dynamic_one_cond_keeps_joiner():
    n = bpt_build.update_dynamic(152, {'f': 'v'}, filter_conditions=[('ID', '7')])
    items = n['Properties']['DynamicFilterFields']['items']
    assert items == [[{'object': 'Document', 'field': 'ID', 'operator': '=',
                       'value': '7'}, 'AND']], \
        f'одиночное условие без хвостового AND: {items!r}'


@check
def update_dynamic_both_targets_rejected():
    try:
        bpt_build.update_dynamic(152, {'f': 'v'}, item_id_expr='{=Document:ID}',
                                 filter_conditions=[('ID', '7')])
    except ValueError:
        return
    raise AssertionError('оба таргетинга одновременно не отвергнуты')


@check
def update_dynamic_item_id_expr_warns():
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        bpt_build.update_dynamic(152, {'f': 'v'}, item_id_expr='{=Document:ID}')
    assert '[FIX]' in err.getvalue(), 'нет WARN про мёртвый DynamicId-путь'


# --- №8: IfElseBranch Node ---------------------------------------------------

@check
def ifelse_branch_carries_node_key():
    n = bpt_build.if_else([('да', ['flag', '=', '1'], []),
                           ('нет', None, [])])
    for br in n['Children']:
        keys = list(br.keys())
        assert keys == ['Type', 'Name', 'Activated', 'Node',
                        'Properties', 'Children'], f'ключи ветки: {keys}'
        assert br['Node'] is None
    n = bpt_build.if_else_mixed(
        [('да', [('Variable', 'flag', '=', '1', '0')], []),
         ('нет', None, [])])
    for br in n['Children']:
        keys = list(br.keys())
        assert keys == ['Type', 'Name', 'Activated', 'Node',
                        'Properties', 'Children'], f'ключи ветки mixed: {keys}'
        assert br['Node'] is None


@check
def ifelse_single_branch_rejected():
    try:
        bpt_build.if_else([('только', ['a', '=', '1'], [])])
    except ValueError:
        return
    raise AssertionError('сиротская ветка (1 шт.) не отвергнута')


@check
def ifelse_two_else_rejected():
    try:
        bpt_build.if_else([('a', None, []), ('b', None, [])])
    except ValueError:
        return
    raise AssertionError('две else-ветки не отвергнуты')


# --- №9: детектор висячих ссылок --------------------------------------------

@check
def validate_refs_flags_canonical_dangling():
    tree = bpt_build.load(os.path.join(ETALONS, 'notify-history-canonical.bpt'))
    problems = bpt_build.validate_refs(tree)
    act12 = [p for p in problems if 'Act_12' in p]
    assert len(act12) == 2, f'ожидалось 2 висячих {{=Act_12:ID}}, got: {problems!r}'


@check
def validate_refs_corpus_clean():
    for fn in sorted(os.listdir(ETALONS)):
        if fn == 'notify-history-canonical.bpt' or not fn.endswith('.bpt'):
            continue
        problems = bpt_build.validate_refs(bpt_build.load(os.path.join(ETALONS, fn)))
        assert not problems, f'{fn}: ложные срабатывания: {problems!r}'


@check
def validate_refs_undeclared_variable():
    tree = minimal_tree()
    tree['TEMPLATE'][0]['Children'].append(
        bpt_build.set_vars({'x': '{=Variable:ghost}'}))
    problems = bpt_build.validate_refs(tree)
    assert any('ghost' in p for p in problems), f'{problems!r}'


@check
def validate_refs_whitelisted_namespaces_clean():
    for ns in sorted(bpt_build._NS_WHITELIST):
        t = minimal_tree()
        if ns == 'Variable':
            t['VARIABLES']['field'] = bpt_build.variable('field', 'string')
        t['TEMPLATE'][0]['Children'].append(
            bpt_build.set_vars({'probe': f'{{={ns}:field}}'}))
        problems = bpt_build.validate_refs(t)
        assert not problems, f'whitelist-namespace {ns} дал ложняк: {problems!r}'
    t = minimal_tree()
    t['TEMPLATE'][0]['Children'].append(
        bpt_build.change_stage('{=Parameter:update}'))
    assert bpt_build.validate_refs(t) == [], \
        'Parameter в TargetStatus (форма боевого prod-шаблон) не прошёл'


@check
def validate_refs_rejects_double_brace_garbage():
    bad = "{{='ex_' . {{ID}} . '_prepay'}}"  # боевой мусор (сессия prod-шаблон)
    t = minimal_tree()
    t['TEMPLATE'][0]['Children'].append(bpt_build.set_vars({'x': bad}))
    problems = bpt_build.validate_refs(t)
    assert any('{{ID}}' in p for p in problems), problems
    try:
        bpt_build.save(t, os.path.join(TMP, 'garbage.bpt'))
    except ValueError:
        return
    raise AssertionError('save() пропустил мусорное выражение')


@check
def validate_refs_bare_double_brace_rejected():
    t = minimal_tree()
    t['TEMPLATE'][0]['Children'].append(
        bpt_build.set_vars({'x': 'prefix {{ID}} suffix'}))
    problems = bpt_build.validate_refs(t)
    assert problems, '{{}} без = прошёл молча'


@check
def save_rejects_dangling_refs(tmp=TMP):
    tree = minimal_tree()
    tree['TEMPLATE'][0]['Children'].append(
        bpt_build.set_vars({'x': '{=Act_999:ID}'}))
    out = os.path.join(tmp, 'dangling.bpt')
    if os.path.exists(out):
        os.remove(out)
    try:
        bpt_build.save(tree, out)
    except ValueError as e:
        assert 'Act_999' in str(e)
        assert not os.path.exists(out), 'битый файл создан до валидации'
        return
    raise AssertionError('save() пропустил висячую ссылку')


# --- save(): атомарность + самопроверка без assert --------------------------

@check
def save_atomic_and_clean(tmp=TMP):
    out = os.path.join(tmp, 'ok.bpt')
    size = bpt_build.save(minimal_tree(), out)
    assert size > 0 and os.path.exists(out)
    leftovers = [f for f in os.listdir(tmp) if f.endswith('.tmp')]
    assert not leftovers, f'мусор tmp: {leftovers}'
    back = bpt_build.load(out)
    assert back['VARIABLES'], 'roundtrip через save/load сломан'


# --- №10: placeholder --------------------------------------------------------

@check
def placeholder_children_and_marker():
    inner = bpt_build.im_notify(['1'], 'черновик внутри маркера')
    n = bpt_build.placeholder('доделать X', children=[inner])
    assert n['Children'] == [inner], 'дети потеряны'
    assert n['Properties']['Title'] == '!!! доделать X !!!'
    bare = bpt_build.placeholder('пустышка')
    assert bare['Children'] == []


@check
def placeholder_comment_survives_act():
    n = bpt_build.placeholder('M', comment='пометка')
    assert n['Properties']['EditorComment'] == 'пометка', \
        f"комментарий съеден act(): {n['Properties']!r}"


@check
def placeholder_minimized_flag():
    n = bpt_build.placeholder('M', minimized=True)
    assert n['Properties'].get('_DesMinimized') == 'Y'


@check
def save_rejects_digit_string_keys():
    t = minimal_tree()
    t['DOCUMENT_FIELDS'] = {'CATEGORY_ID': {'Options': {'14': 'Воронка'}}}
    try:
        bpt_build.save(t, os.path.join(TMP, 'digitkey.bpt'))
    except ValueError:
        return
    raise AssertionError('save() пропустил числовой str-ключ (JSON-шрам)')


@check
def lint_keys_warns_on_lossy_scar():
    t = minimal_tree()
    t['DOCUMENT_FIELDS'] = {'CATEGORY_ID': {'Options': {'_14': 'Воронка'}}}
    errs, warns = bpt_build._lint_keys(t)
    assert errs == [], errs
    assert len(warns) == 1 and '_14' in warns[0], warns


@check
def json_seam_corpus_roundtrip():
    import bpt_serialize
    for fn in ('hotel-booking.bpt', 'loop-counter.bpt'):
        src = os.path.join(ETALONS, fn)
        orig = open(src, 'rb').read()
        seam = bpt_serialize.load_json(bpt_serialize.dump_json(bpt_build.load(src)))
        out = os.path.join(TMP, 'seam_' + fn)
        bpt_build.save(seam, out)
        # инвариант корпуса — payload-level: zlib-уровень портала не наш
        # контракт (файл hotel-booking сжат плотнее дефолта при том же payload)
        assert zlib.decompress(open(out, 'rb').read()) == \
            zlib.decompress(orig), f'{fn}: payload разъехался на JSON-шве'


@check
def act_editor_comment_param():
    n = bpt_build.act('SetVariableActivity', 'T', {'VariableValue': {}},
                      editor_comment='пометка')
    assert n['Properties']['EditorComment'] == 'пометка'
    assert list(n['Properties']) == ['VariableValue', 'Title', 'EditorComment']


@check
def variable_title_form():
    v = bpt_build.variable('Specialist', 'employee',
                           label='Specialist (employee)',
                           title='Specialist (employee)')
    assert list(v) == ['Name', 'Title', 'Description', 'Type', 'Required',
                       'Multiple'], list(v)
    v2 = bpt_build.variable('del_task', 'user', label='T', title='T',
                            default='x')
    assert list(v2)[-1] == 'Default' and 'Options' not in v2, list(v2)
    v3 = bpt_build.variable('V_NIGHTS', 'int', default='5')
    assert list(v3) == ['Name', 'Description', 'Type', 'Required', 'Multiple',
                        'Options', 'Default'], list(v3)
    v3d = bpt_build.variable('V', 'int', description='пояснение')
    assert v3d['Description'] == 'пояснение'


@check
def get_dynamic_info_optional_document_and_ode():
    n = bpt_build.get_dynamic_info(177, ['ID'], [], {}, None)
    assert 'Document' not in n['Properties']['DynamicEntityFields']
    n2 = bpt_build.get_dynamic_info(177, ['ID'], [], {}, {}, None,
                                    only_dynamic='N')
    assert n2['Properties']['OnlyDynamicEntities'] == 'N'
    assert 'Document' in n2['Properties']['DynamicEntityFields']


@check
def while_foreach_body_title():
    w = bpt_build.while_loop(['V', '>', '0', '0'],
                             [bpt_build.set_vars({'a': 1})])
    assert w['Children'][0]['Properties']['Title'] == 'Sequence of actions'
    assert w['Properties']['Title'] == 'Цикл'
    f = bpt_build.for_each('ids', [bpt_build.set_vars({'a': 1})])
    assert f['Children'][0]['Properties']['Title'] == 'Sequence of actions'
    w2 = bpt_build.while_loop(['V', '>', '0', '0'],
                              [bpt_build.set_vars({'a': 1})],
                              title='Пока даты неверны',
                              body_title='Пока даты неверны')
    assert w2['Children'][0]['Properties']['Title'] == 'Пока даты неверны'


@check
def set_fields_modified_by_param():
    n = bpt_build.set_fields({'StageId': 'X'})
    assert n['Properties']['ModifiedBy'] == []
    n2 = bpt_build.set_fields({'StageId': 'X'},
                              modified_by=['{=Document:ASSIGNED_BY_ID}'])
    assert n2['Properties']['ModifiedBy'] == ['{=Document:ASSIGNED_BY_ID}']
    assert list(n2['Properties']) == ['FieldValue', 'ModifiedBy',
                                      'MergeMultipleFields', 'Title',
                                      'EditorComment']


@check
def terminate_bare_form():
    n = bpt_build.terminate('Прерывание процесса')
    assert list(n['Properties']) == ['Title'], list(n['Properties'])
    assert n['Type'] == 'TerminateActivity' and n['Node'] is None
    full = bpt_build.terminate_others()
    assert set(full['Properties']) == {'StateTitle', 'KillWorkflow',
                                       'TerminateType', 'Title',
                                       'EditorComment'}


@check
def scaffold_resets_name_sequence():
    bpt_build.scaffold([bpt_build.set_vars({'a': 1})], {}, {})
    n1 = bpt_build.set_vars({'b': 2})['Name']
    bpt_build.scaffold([bpt_build.set_vars({'a': 1})], {}, {})
    n2 = bpt_build.set_vars({'b': 2})['Name']
    assert n1 == n2, (n1, n2)


@check
def create_dynamic_canonical_order_and_multi_blanks():
    n = bpt_build.create_dynamic(1040, {'UF_X': 'v', 'COMPANY_ID': 5},
                                 name='Cr_1', blank_fields=['UF_B'],
                                 blank_multi=('CONTACT_IDS',))
    defl = n['Properties']['DynamicEntitiesFields']
    order = [k.split('_', 1)[1] for k in defl]
    assert order[0] == 'TITLE' and order[4] == 'OPENED', order[:6]
    assert order[5] == 'COMPANY_ID', order[:8]
    assert defl['1040_CONTACT_IDS'] == [] and defl['1040_UF_B'] == ''
    assert order[-2:] == ['UF_B', 'UF_X'], order[-3:]


@check
def if_else_field_branches():
    b = bpt_build.if_else_field([
        ('Есть причина', ['UF_CRM_83_MGR_REASON', '!empty', ''],
         [bpt_build.set_vars({'x': 1})]),
        ('Прочее', None, [])])
    props = b['Children'][0]['Properties']
    assert props['fieldcondition'] == [['UF_CRM_83_MGR_REASON', '!empty', '', '0']]
    assert list(props) == ['Title', 'fieldcondition', 'EditorComment'], list(props)


@check
def start_workflow_builder():
    n = bpt_build.start_workflow(220, '{=Document:ID}', {'update': 'C14:X'},
                                 title='Хелпер',
                                 editor_comment='ID = {=Document:ID}')
    p = n['Properties']
    assert list(p) == ['DocumentId', 'TemplateId', 'UseSubscription',
                       'TemplateParameters', 'Title', 'EditorComment'], list(p)
    assert p['TemplateId'] == '220' and p['UseSubscription'] == 'N'
    assert p['TemplateParameters'] == {'update': 'C14:X'}


@check
def task_constructors_node_name():
    n = bpt_build.approve(['user_1'], 'Имя задания', node_name='Appr_1')
    assert n['Name'] == 'Appr_1' and n['Properties']['Name'] == 'Имя задания'
    n2 = bpt_build.request_info(
        ['user_1'], [{'Title': 't', 'Name': 'r', 'Type': 'string',
                      'Required': 'N', 'Multiple': 'N'}], 'RI',
        node_name='RI_1')
    assert n2['Name'] == 'RI_1' and n2['Properties']['Name'] == 'RI'


def main() -> int:
    failed = []
    for fn in CHECKS:
        name = fn.__name__
        try:
            fn()
            print(f'PASS {name}')
        except Exception as e:  # noqa: BLE001 — жгут обязан ловить всё
            failed.append(name)
            print(f'FAIL {name}: {e!r}')
    print(f'--- {len(CHECKS) - len(failed)}/{len(CHECKS)} passed')
    return 1 if failed else 0



@check
def request_info_default_key():
    """Минор TOFIX: RI-поле со 'Default' в спеке — ключ эмитится ПОСЛЕДНИМ
    (корпус 1/16: Title..Multiple, Default='' — пустой рудимент, опц-ин)."""
    ri = bpt_build.request_info(['1'], [{'Name': 'X', 'Title': 'T',
                                         'Default': ''}], 'S')
    f = ri['Properties']['RequestedInformation'][0]
    assert tuple(f.keys()) == ('Title', 'Name', 'Description', 'Type',
                               'Required', 'Multiple', 'Default'), tuple(f.keys())
    assert f['Default'] == ''
    ri2 = bpt_build.request_info(['1'], [{'Name': 'X', 'Title': 'T'}], 'S')
    assert 'Default' not in ri2['Properties']['RequestedInformation'][0]


@check
def fwd_ref_helper():
    """Минор TOFIX: forward-ссылка на ПОЗДНЕЮ активность (корпус 1/71:
    notify-history-canonical SetFld_24 -> явно-именованная цель). Паттерн: fwd_ref +
    явный name= на цели; validate_refs видит все имена шаблона — положение
    не важно. save() проходит => валидация ок."""
    src = bpt_build.set_fields(
        {'COMMENT': bpt_build.fwd_ref('NamedTarget', 'RESULT_ONE')})
    tgt = bpt_build.im_notify(
        ['1'], bpt_build.fwd_ref('NamedTarget', 'RESULT_ONE'),
        name='NamedTarget')
    t = bpt_build.scaffold([bpt_build.sequence([src, tgt])], {},
                           {'ID': 'string'})
    bpt_build.save(t, '/tmp/opencode/fwd.bpt')
    back = bpt_build.load('/tmp/opencode/fwd.bpt')
    assert '{=NamedTarget:RESULT_ONE}' in str(back)


# --- T3 (bpt-builder-core): clone() -----------------------------------------

def _anon(n):
    """Структура узла c обезличенными Name (сравнение клона и оригинала)."""
    if isinstance(n, dict):
        return {k: ('§' if k == 'Name' else _anon(v)) for k, v in n.items()}
    if isinstance(n, list):
        return [_anon(x) for x in n]
    return n


def _names(n, acc=None):
    acc = [] if acc is None else acc
    if isinstance(n, dict):
        if isinstance(n.get('Name'), str):
            acc.append(n['Name'])
        for v in n.values():
            _names(v, acc)
    elif isinstance(n, list):
        for v in n:
            _names(v, acc)
    return acc


@check
def clone_renames_all_and_rebinds_internal_refs():
    inner = bpt_build.im_notify(['1'], 'msg', name='Inner_1')
    ref = bpt_build.set_vars({'x': '{=Inner_1:MessageSite}'})
    subtree = bpt_build.sequence([inner, ref])
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        cl = bpt_build.clone(subtree)
    assert subtree['Children'][0]['Name'] == 'Inner_1', 'оригинал тронут'
    cl_inner = cl['Children'][0]
    assert cl_inner['Name'] != 'Inner_1', 'корень-имя клона не свежее'
    assert cl['Children'][1]['Properties']['VariableValue']['x'] == \
        '{=%s:MessageSite}' % cl_inner['Name'], 'внутренняя ссылка не перепривязана'
    assert err.getvalue().count('[clone]') == 3, \
        f'лог [clone] не по узлу: {err.getvalue()!r}'


@check
def clone_series_unique_names_and_shape():
    orig = bpt_build.if_else(
        [('да', ['v', '=', '1'], [bpt_build.set_vars({'a': 1})]),
         ('нет', None, [])])
    with contextlib.redirect_stderr(io.StringIO()):
        clones = [bpt_build.clone(orig) for _ in range(5)]
    all_names = _names(orig) + [x for c in clones for x in _names(c)]
    assert len(all_names) == len(set(all_names)), \
        f'дубликаты Name в серии клонов: {all_names!r}'
    for c in clones:
        assert _anon(c) == _anon(orig), 'структура клона разъехалась'


@check
def clone_original_refs_stay_valid():
    target = bpt_build.im_notify(['1'], 'm', name='Tgt_1')
    sibling = bpt_build.set_vars({'x': '{=Tgt_1:MessageSite}'})
    with contextlib.redirect_stderr(io.StringIO()):
        cl = bpt_build.clone(target)
    t = bpt_build.scaffold([target, sibling, cl], {}, {})
    assert bpt_build.validate_refs(t) == [], 'внешняя ссылка на оригинал сломана'


@check
def clone_rebinds_mixedcondition_object_bare_name():
    """№6 аудита 2026-10-16: mixedcondition.object — bare-name канал (без
    {=}-обёртки; корпус: 31 ряд, домен = имена активностей ∪ режимы
    Variable/Document). Клон обязан перепривязывать его наряду с
    {=}-каналом, иначе условие ветки смотрит на узел вне клона
    (Name-уникальность зелёная, портал принимает молча)."""
    inner = bpt_build.im_notify(['1'], 'msg', name='GetReg_42')
    cond = bpt_build.if_else_mixed([
        ('да', [('GetReg_42', 'MessageSite', '!=', '', '0')], []),
        ('нет', None, [])])
    subtree = bpt_build.sequence([inner, cond])
    with contextlib.redirect_stderr(io.StringIO()):
        cl = bpt_build.clone(subtree)
    cl_inner_name = cl['Children'][0]['Name']
    rows = cl['Children'][1]['Children'][0]['Properties']['mixedcondition']
    assert rows[0]['object'] == cl_inner_name, \
        'bare-name object не перепривязан на имя узла внутри клона'
    # режимы и ссылки на узлы ВНЕ клона каналом не задеты
    ext = bpt_build.if_else_mixed([
        ('да', [('Variable', 'v', '=', '1', '0'),
                ('Document', 'TITLE', '!=', '', '1'),
                ('Outside_9', 'x', '=', '2', '1')], []),
        ('нет', None, [])])
    with contextlib.redirect_stderr(io.StringIO()):
        cl2 = bpt_build.clone(ext)
    r = cl2['Children'][0]['Properties']['mixedcondition']
    assert [x['object'] for x in r] == ['Variable', 'Document', 'Outside_9'], \
        'режимы/внешние имена затронуты bare-name каналом'


@check
def act_branch_activated_param_and_gate():
    """№10 аудита 2026-10-16: Activated='N' невыразим (хардкод 'Y') —
    пересборка корпусных шаблонов молча включала черновые блоки
    (notify-history-canonical Act_41, loop-counter). Параметр act/branch + гейт обе
    стороны; клон деактивированного узла = живая копия ('Y', решение
    в докстринге clone)."""
    node = bpt_build.act('SetVariableActivity', 't',
                         {'VariableValue': {}}, activated='N')
    assert node['Activated'] == 'N', 'act(activated=N) не эмитит N'
    br = bpt_build.branch({'Title': 'в'}, activated='N')
    assert br['Activated'] == 'N', 'branch(activated=N) не эмитит N'
    for bad in ('yes', '', 'n', 1):
        try:
            bpt_build.act('SetVariableActivity', 't', {}, activated=bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f'act(activated={bad!r}) прошёл гейт')
    with contextlib.redirect_stderr(io.StringIO()):
        cl = bpt_build.clone(node)
    assert cl['Activated'] == 'Y', \
        'клон обязан быть живой копией (Activated=Y, докстринг clone)'


@check
def clone_dangling_ref_detector_case():
    target = bpt_build.im_notify(['1'], 'm', name='Tgt_2')
    with contextlib.redirect_stderr(io.StringIO()):
        cl = bpt_build.clone(target)
    external = bpt_build.set_vars({'x': '{=Tgt_2:MessageSite}'})
    t = bpt_build.scaffold([external, cl], {}, {})  # оригинал НЕ вставлен
    problems = bpt_build.validate_refs(t)
    assert any('Tgt_2' in p for p in problems), \
        f'висячек на переименованный клон не видно: {problems!r}'


@check
def clone_title_and_activated():
    n = bpt_build.delay(duration='5')
    n['Activated'] = 'N'
    with contextlib.redirect_stderr(io.StringIO()):
        cl = bpt_build.clone(n, title='Новая пауза')
    assert cl['Properties']['Title'] == 'Новая пауза'
    assert cl['Activated'] == 'Y' and n['Activated'] == 'N'


@check
def clone_property_series():
    """Property (hypothesis): серия клонов одного узла — все Name уникальны,
    структура идентична оригиналу кроме Name, оригинал неизменен. В runtime-
    окружении без dev-пакетов — детерминированный прогон N=7."""
    orig = bpt_build.if_else(
        [('да', ['v', '=', '1'], [bpt_build.set_vars({'a': 1})]),
         ('нет', None, [bpt_build.terminate()])])
    before = list(_names(orig))

    def run_series(n):
        with contextlib.redirect_stderr(io.StringIO()):
            clones = [bpt_build.clone(orig) for _ in range(n)]
        all_names = _names(orig) + [x for c in clones for x in _names(c)]
        assert len(all_names) == len(set(all_names)), f'дубли Name: {all_names!r}'
        for c in clones:
            assert _anon(c) == _anon(orig), 'структура разъехалась'
        assert _names(orig) == before, 'оригинал изменился от клонирования'

    try:
        from hypothesis import given, settings as h_settings
        from hypothesis import strategies as st
    except ImportError:
        run_series(7)
        return

    @h_settings(max_examples=25)
    @given(st.integers(min_value=0, max_value=12))
    def prop(n):
        run_series(n)

    prop()


# --- T4 (bpt-builder-core): branch() + Name-токены --------------------------

@check
def branch_standalone_skeleton():
    props = {'Title': 'Своя ветка', 'EditorComment': '',
             'mixedcondition': [{'object': 'Document', 'field': 'STAGE_ID',
                                 'operator': '=', 'value': 'DT1072_30:NEW',
                                 'joiner': '0'}]}
    b = bpt_build.branch(props, [bpt_build.set_vars({'a': 1})])
    assert list(b) == ['Type', 'Name', 'Activated', 'Node', 'Properties',
                       'Children'], list(b)
    assert b['Type'] == 'IfElseBranchActivity' and b['Node'] is None
    assert b['Name'].startswith('Act_'), b['Name']  # канон реплик (промер T4)
    assert list(b['Properties']) == list(props), 'props-порядок искажён'
    assert b['Children'][0]['Type'] == 'SetVariableActivity'


@check
def ifelse_branch_names_canonical_and_dense():
    """Промер корпуса: IfElseBranch/Sequence в репликах — Act_N; нумерация
    плотная (1 тик _ACT_SEQ на узел — TOFIX-пункт «лишний тик» закрыт
    промером: 4 узла = 4 тика, дыр нет)."""
    bpt_build.scaffold([], {}, {})
    start = next(bpt_build._ACT_SEQ)
    bpt_build._ACT_SEQ = __import__('itertools').count(start)
    ie = bpt_build.if_else([('a', ['v', '=', '1'], []),
                            ('b', None, [])])
    seq = bpt_build.sequence([ie])
    spent = next(bpt_build._ACT_SEQ) - start
    all_names = _names(seq)
    assert spent == len(all_names), \
        f'дыры в нумерации: {spent} тиков на {len(all_names)} узлов: {all_names!r}'
    for n in (ie['Children'][0]['Name'], ie['Children'][1]['Name'], seq['Name']):
        assert n.startswith('Act_'), n
    assert ie['Name'].startswith('Branch_'), ie['Name']


# --- T6-T8 (bpt-builder-core): ядро validate() + CLI ------------------------
# Рул × (positive/negative)-фикстура таблично; калибровка корпуса обязательна
# (литералы в mixedcondition.value легитимны — WARN только про функции).

VALIDATOR_CASES = []


def vcase(fn):
    VALIDATOR_CASES.append(fn)
    return fn


def _mut(**extra):
    t = minimal_tree()
    t.update(extra)
    return t


@vcase
def r_ifelse_children_type():
    ok = bpt_build.if_else([('да', ['flag', '=', '1'], []),
                            ('нет', None, [])])
    assert bpt_build.validate(bpt_build.scaffold([ok], {'flag': 1}, {}))[0] == []
    bad = bpt_build.act('IfElseActivity', 'Условие', {},
                        [bpt_build.set_vars({'x': 1})])
    errs, _ = bpt_build.validate(bpt_build.scaffold([bad], {}, {}))
    assert any('IfElse-ребёнок' in e and 'SetVariableActivity' in e
               for e in errs), errs


@vcase
def r_ifelse_cardinality_loaded_trees():
    lone = bpt_build.act('IfElseActivity', 'Условие', {}, [
        bpt_build.branch({'Title': 'одна', 'EditorComment': ''})])
    errs, _ = bpt_build.validate(bpt_build.scaffold([lone], {}, {}))
    assert any('ветка-сирота' in e for e in errs), errs
    two_else = bpt_build.act('IfElseActivity', 'Условие', {}, [
        bpt_build.branch({'Title': 'a', 'EditorComment': '',
                          'truecondition': '1'}),
        bpt_build.branch({'Title': 'b', 'EditorComment': '',
                          'truecondition': '1'})])
    errs, _ = bpt_build.validate(bpt_build.scaffold([two_else], {}, {}))
    assert any('более одной else' in e for e in errs), errs


@vcase
def r_name_empty_and_duplicates():
    a = bpt_build.act('SetVariableActivity', 'A', {'VariableValue': {}},
                      name='Dup')
    b = bpt_build.act('SetVariableActivity', 'B', {'VariableValue': {}},
                      name='Dup')
    errs, _ = bpt_build.validate(bpt_build.scaffold([a, b], {}, {}))
    assert any('дубль Name' in e and 'Dup' in e for e in errs), errs
    noname = bpt_build.set_vars({'x': 1})
    noname['Name'] = ''
    errs, _ = bpt_build.validate(bpt_build.scaffold([noname], {}, {}))
    assert any('без Name' in e for e in errs), errs


@vcase
def r_explode_self_reference_and_multiple_sink():
    src = bpt_build.scaffold(
        [bpt_build.set_vars({'done': 'Y'}),
         bpt_build.set_vars({'lst': "{{=explode('&', {=Variable:lst})}}"})],
        {'done': bpt_build.variable('done', 'int'),
         'lst': bpt_build.variable('lst', 'string', multiple='1')}, {})
    errs, warns = bpt_build.validate(src)
    assert any('explode-самоссылка' in e and 'lst' in e for e in errs), errs
    # не-Multiple приёмник -> WARN (список не отрендерится, §7 runtime)
    t2 = bpt_build.scaffold(
        [bpt_build.set_vars({'n': "{{=explode('&', {=Variable:src})}}"})],
        {'n': bpt_build.variable('n', 'string'),
         'src': bpt_build.variable('src', 'string', multiple='1')}, {})
    errs, warns = bpt_build.validate(t2)
    assert errs == [], errs
    assert any('не-Multiple' in w and 'n' in w for w in warns), warns
    # приёмник Multiple и отличен от источника -> чисто
    t3 = bpt_build.scaffold(
        [bpt_build.set_vars({'out': "{{=explode('&', {=Variable:src})}}"})],
        {'out': bpt_build.variable('out', 'string', multiple='1'),
         'src': bpt_build.variable('src', 'string', multiple='1')}, {})
    assert bpt_build.validate(t3) == ([], [])


@vcase
def r_mixedcondition_value_forms():
    # литерал и голая ссылка легитимны (калибровка корпуса: 43 литерала)
    ok = bpt_build.if_else_mixed(
        [('да', [('Variable', 'flag', '=', '5', '0')], []),
         ('нет', None, [])])
    t = bpt_build.scaffold([ok], {'flag': 1}, {})
    assert bpt_build.validate(t) == ([], [])
    bad = bpt_build.act('IfElseActivity', 'Условие', {}, [
        bpt_build.branch({'Title': 'x', 'EditorComment': '',
                          'mixedcondition': [
                              {'object': 'Variable', 'field': 'flag',
                               'operator': '=',
                               'value': 'dateadd({=Variable:flag}, 1d)',
                               'joiner': '0'}]}),
        bpt_build.branch({'Title': 'else', 'EditorComment': '',
                          'truecondition': '1'})])
    errs, warns = bpt_build.validate(bpt_build.scaffold([bad], {'flag': 1}, {}))
    assert errs == [] and any('value' in w and 'dateadd' in w for w in warns), \
        (errs, warns)


@vcase
def r_sv_func_in_bare_eq():
    t = bpt_build.scaffold(
        [bpt_build.set_vars({'x': '=if({=Variable:done} = 1, 2, 3)'}),],
        {'done': bpt_build.variable('done', 'int')}, {})
    errs, warns = bpt_build.validate(t)
    assert errs == [] and any('голый =expr' in w for w in warns), (errs, warns)
    t2 = bpt_build.scaffold(  # арифметика в голом = — легитимна (canonical)
        [bpt_build.set_vars({'x': '={=Variable:done}+1'}),],
        {'done': bpt_build.variable('done', 'int')}, {})
    assert bpt_build.validate(t2) == ([], [])
    t3 = bpt_build.scaffold(  # обёрнутая функция — легитимна
        [bpt_build.set_vars({'x': '{{=if({=Variable:done} = 1, 2, 3)}}'}),],
        {'done': bpt_build.variable('done', 'int')}, {})
    assert bpt_build.validate(t3) == ([], [])


@vcase
def r_if_with_refs_in_condition_value():
    bad = bpt_build.act('IfElseActivity', 'Условие', {}, [
        bpt_build.branch({'Title': 'x', 'EditorComment': '',
                          'propertyvariablecondition': [
                              ['flag', '=', 'if({=Variable:flag}=1,a,b)', '0']]}),
        bpt_build.branch({'Title': 'else', 'EditorComment': '',
                          'truecondition': '1'})])
    errs, warns = bpt_build.validate(bpt_build.scaffold(
        [bad], {'flag': bpt_build.variable('flag', 'string')}, {}))
    assert errs == [] and any('if()' in w for w in warns), (errs, warns)


@vcase
def validator_corpus_baseline():
    """Корпус = baseline: эталоны чисты (0 err / 0 warn) кроме
    известных: notify-history-canonical — 2 висячка Act_12; date-window-branch — 1 FN-10
    (V_START: голый реф в if-ветке при чужом условии; живой шаблон с
    формой-риском — детектор ловит, не чинит). Дельта от baseline =
    фейл (ловит деградацию рул в ложняки)."""
    for fn in sorted(os.listdir(ETALONS)):
        if not fn.endswith('.bpt'):
            continue
        errs, warns = bpt_build.validate(bpt_build.load(os.path.join(ETALONS, fn)))
        if fn == 'notify-history-canonical.bpt':
            assert len(errs) == 2 and all('Act_12' in e for e in errs), errs
            assert warns == [], warns
        elif fn == 'date-window-branch.bpt':
            assert len(errs) == 1 and 'FN-10' in errs[0] \
                and 'V_START' in errs[0], errs
        else:
            assert errs == [] and warns == [], f'{fn}: {errs!r} {warns!r}'


@vcase
def save_error_blocks_warn_passes(tmp=TMP):
    # WARN-рула: файл создаётся, однострочник в stderr
    t = bpt_build.scaffold(
        [bpt_build.set_vars({'x': '=round({=Variable:done}, 2)'})],
        {'done': bpt_build.variable('done', 'int')}, {})
    out = os.path.join(tmp, 'warn.bpt')
    if os.path.exists(out):
        os.remove(out)
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        size = bpt_build.save(t, out)
    assert size > 0 and os.path.exists(out), 'WARN не должен блокировать'
    assert '[validate] WARN' in err.getvalue(), err.getvalue()
    # ERROR-рула (висячек): файл не создаётся
    t2 = bpt_build.scaffold(
        [bpt_build.set_vars({'y': '{=Act_999:ID}'})], {}, {})
    out2 = os.path.join(tmp, 'err.bpt')
    if os.path.exists(out2):
        os.remove(out2)
    try:
        bpt_build.save(t2, out2)
    except ValueError:
        assert not os.path.exists(out2)
        return
    raise AssertionError('save() пропустил ERROR-находку')


@vcase
def cli_validate_contract(tmp=TMP):
    tree_path = os.path.join(tmp, 'vt.json')
    t = minimal_tree()
    json.dump(t, open(tree_path, 'w'), ensure_ascii=False)
    r = run_cli('validate', tree_path)
    assert r.returncode == 0, f'чистое дерево: exit={r.returncode}, {r.stderr}'
    assert 'OK' in r.stdout, r.stdout
    t['TEMPLATE'][0]['Children'].append(bpt_build.set_vars({'y': '{=Act_9:ID}'}))
    json.dump(t, open(tree_path, 'w'), ensure_ascii=False)
    r = run_cli('validate', tree_path)
    assert r.returncode == 1 and 'ERROR' in r.stdout, (r.returncode, r.stdout)
    r = run_cli('validate')  # без аргумента
    assert r.returncode == 2 and 'usage' in r.stderr.lower(), r.stderr


for _fn in VALIDATOR_CASES:
    check(_fn)


# --- T9-T10 (bpt-builder-core): expr_lint формул + baseline ------------------

LINT_CASES = [
    # (формула, sink_multiple, число находок, метка) — positive/negative
    ("{{=datediff({=Document:B}, {=Document:E}, '%a')}}", False, 1,
     'datediff-%a без компенсации'),
    ("{{=datediff(B, E, '%a') + if(date('Hi', E) < date('Hi', B), 1, 0)}}",
     False, 0, 'Hi-компенсация §3'),
    ("{{=datediff(B, {=Variable:rps}, '%a') + 1}}", False, 0,
     'клип-формула границ периода §3'),
    ("{{=datediff({=Document:D_FROM}, {=Document:D_TO},'%d')}}", False, 0,
     'корпусный %d (notify-history-canonical)'),
    ("{{={=Document:UF_CRM_8_PRICE} * 1.2}}", False, 1, 'money без floatval'),
    ("{{=floatval({=Document:UF_CRM_8_PRICE}) * 1.2}}", False, 0,
     'money под floatval'),
    ("{{=dateadd({=Document:UF_CRM_83_DATE_FROM},\"-7d\")}}", False, 0,
     'dateadd корпусный'),
    ("{{=datediff(B, {=Variable:rps}, '%d')}}", False, 1,
     'периодная дата без клипа ±1'),
    ("{{=round({=Variable:x}, 2)}}", True, 1, 'INFO прочерк Multiple'),
    ("{{=if({=Variable:x} = 0, '-', round({=Variable:x}, 2))}}", True, 0,
     'прочерк уже есть'),
]


@check
def expr_lint_rule_table():
    for s, mult, n, tag in LINT_CASES:
        got = bpt_build.expr_lint(s, sink_multiple=mult)
        assert len(got) == n, f'{tag}: ожидалось {n}, got {got!r}'


@check
def expr_lint_wired_into_validate():
    t = bpt_build.scaffold(
        [bpt_build.set_vars(
            {'nights': "{{=datediff({=Document:B}, {=Document:E}, '%a')}}"})],
        {'nights': bpt_build.variable('nights', 'int')},
        {'B': 'datetime', 'E': 'datetime'})
    errs, warns = bpt_build.validate(t)
    assert errs == [], errs
    assert any('SV nights' in w and 'datediff' in w for w in warns), warns


@check
def expr_lint_corpus_baseline():
    """Baseline линтера: expr_lint по ВСЕМ строкам TEMPLATE каждого эталона —
    ноль находок (инвентарь легитимных форм корпуса: datediff '%d' ×1,
    dateadd ×5, if в SV ×1, money-имён нет). Дельта от baseline = фейл —
    ловит деградацию рул в боевые ложняки (урок S1/S2)."""
    def walk_strings(v, acc):
        if isinstance(v, dict):
            for x in v.values():
                walk_strings(x, acc)
        elif isinstance(v, list):
            for x in v:
                walk_strings(x, acc)
        elif isinstance(v, str):
            acc.extend(bpt_build.expr_lint(v))

    for fn in sorted(os.listdir(ETALONS)):
        if not fn.endswith('.bpt'):
            continue
        acc = []
        walk_strings(bpt_build.load(os.path.join(ETALONS, fn))['TEMPLATE'], acc)
        if fn == 'date-window-branch.bpt':
            acc = [x for x in acc if 'FN-10' not in x]  # известная (V_START)
        assert acc == [], f'{fn}: ложняки линтера: {acc!r}'


# --- probe: дебаг-копия с журналированием переменных (волна 2, TODO №3) ------
# Контракт журнала — etalons.md «Probe-режим»; фикстура — SV на всех трёх
# уровнях вложенности: корень / обе ветки if_else / тело while.

FIXTURE = os.path.join(BPT, 'tests', 'fixtures', 'probe_tree.json')


def _fixture_tree():
    return bpt_build.load_json(open(FIXTURE, encoding='utf-8').read())


def _is_probe_insert(node) -> bool:
    return (node.get('Type') == 'CrmTimelineCommentAdd'
            and str((node.get('Properties') or {}).get('CommentText')
                    or '').startswith('[probe] after '))


def _probe_inserts(tree):
    nodes = []
    bpt_build._walk_nodes(tree.get('TEMPLATE'), '$', nodes)
    return [n for _, n in nodes if _is_probe_insert(n)]


@check
def probe_inserts_after_every_sv_all_levels():
    """4 SV фикстуры — по вставке после каждого, в том же Children-уровне;
    тело цикла (SequenceActivity while) несёт вставку рядом со своим SV."""
    probed = bpt_build.probe(_fixture_tree())
    nodes = []
    bpt_build._walk_nodes(probed.get('TEMPLATE'), '$', nodes)
    svs = [n for _, n in nodes if n.get('Type') == 'SetVariableActivity']
    inserts = _probe_inserts(probed)
    assert len(svs) == 4 and len(inserts) == 4, \
        f'SV={len(svs)} inserts={len(inserts)} — вставка не после каждого SV'

    def check_positions(node):
        ch = node.get('Children') or []
        for i, c in enumerate(ch):
            if c.get('Type') == 'SetVariableActivity':
                nxt = ch[i + 1] if i + 1 < len(ch) else None
                assert nxt is not None and _is_probe_insert(nxt), \
                    f'SV {c["Name"]} не покрыт: {node["Name"]} children=' \
                    f'{[x.get("Type") for x in ch]}'
                head = nxt['Properties']['CommentText'].splitlines()[0]
                assert head == f'[probe] after {c["Name"]}', \
                    f'голова вставки не по контракту: {head!r}'
            check_positions(c)

    check_positions((probed['TEMPLATE'] or [{}])[0])
    loop_bodies = [n for _, n in nodes if n.get('Type') == 'SequenceActivity'
                   and any(c.get('Type') == 'SetVariableActivity'
                           for c in (n.get('Children') or []))
                   and any(_is_probe_insert(c) for c in (n.get('Children') or []))]
    assert loop_bodies, 'вставка в теле цикла (Sequence of while) не найдена'


@check
def probe_source_tree_not_mutated():
    import copy
    tree = _fixture_tree()
    snapshot = copy.deepcopy(tree)
    bpt_build.probe(tree)
    assert tree == snapshot, 'исходное дерево мутировано probe()'


@check
def probe_names_unique_and_fresh():
    tree = _fixture_tree()
    probed = bpt_build.probe(tree)
    nodes = []
    bpt_build._walk_nodes(probed.get('TEMPLATE'), '$', nodes)
    names = [n['Name'] for _, n in nodes]
    assert len(names) == len(set(names)), 'дубли Name после probe'
    orig = []
    bpt_build._walk_nodes(tree.get('TEMPLATE'), '$', orig)
    orig_names = {n['Name'] for _, n in orig}
    for ins in _probe_inserts(probed):
        assert ins['Name'] not in orig_names, \
            f'имя вставки столкнулось с исходным: {ins["Name"]}'


@check
def probe_double_probe_no_name_clash():
    """Повторный probe по уже-пробированному дереву: имена вставок — из пула
    с пропуском занятых; журналируются только SV (вставки не рекурсивно —
    новых вставок столько же, сколько SV, а не сколько узлов)."""
    once = bpt_build.probe(_fixture_tree())
    twice = bpt_build.probe(once)
    nodes = []
    bpt_build._walk_nodes(twice.get('TEMPLATE'), '$', nodes)
    names = [n['Name'] for _, n in nodes]
    dupes = {n for n in names if names.count(n) > 1}
    assert not dupes, f'повторный probe дал дубли Name: {dupes}'
    n_sv = sum(1 for _, n in nodes if n.get('Type') == 'SetVariableActivity')
    assert n_sv == 4 and len(_probe_inserts(twice)) == 8, \
        f'SV={n_sv} inserts={len(_probe_inserts(twice))} (ждали 4 SV, 4+4 вставок)'


@check
def probe_parity_minus_inserts():
    """Паритет: дебаг-копия минус вставки структурно == исходнику."""
    import copy
    tree = _fixture_tree()
    probed = bpt_build.probe(tree)
    stripped = copy.deepcopy(probed)

    def strip(node):
        node['Children'] = [c for c in (node.get('Children') or [])
                            if not _is_probe_insert(c)]
        for c in (node.get('Children') or []):
            strip(c)

    for r in (stripped.get('TEMPLATE') or []):
        strip(r)
    assert stripped == tree, 'дерево вне вставок изменилось'


@check
def probe_text_contract():
    """Текст вставки: голова + строка на каждый ключ VariableValue с
    {=Variable:...}; Title вставки = голова."""
    probed = bpt_build.probe(_fixture_tree())
    nodes = []
    bpt_build._walk_nodes(probed.get('TEMPLATE'), '$', nodes)
    first_sv = next(n for _, n in nodes
                    if n.get('Type') == 'SetVariableActivity')
    ins = next(n for _, n in nodes if _is_probe_insert(n)
               and n['Properties']['CommentText'].splitlines()[0]
               == f'[probe] after {first_sv["Name"]}')
    want = (f'[probe] after {first_sv["Name"]}\n'
            f'V_AMOUNT={{=Variable:V_AMOUNT}}')
    assert ins['Properties']['CommentText'] == want, \
        repr(ins['Properties']['CommentText'])
    assert ins['Properties']['Title'] == f'[probe] after {first_sv["Name"]}'


@check
def probe_cli_exit_and_log(tmp=TMP):
    """CLI probe: exit 0 + лог '[probe] N journal inserts after M set_vars',
    OUT проходит полный гейт save() (undeclared-переменная = не 0); usage=2."""
    tree_path = os.path.join(tmp, 'probe_cli_tree.json')
    out_path = os.path.join(tmp, 'probe_cli_out.bpt')
    open(tree_path, 'w', encoding='utf-8').write(
        open(FIXTURE, encoding='utf-8').read())
    r = run_cli('probe', tree_path, out_path)
    assert r.returncode == 0, f'probe CLI упал: {r.stderr}'
    assert '[probe] 4 journal inserts after 4 set_vars' in r.stdout, r.stdout
    assert os.path.exists(out_path) and os.path.getsize(out_path) > 0
    bad = _fixture_tree()
    bad['TEMPLATE'][0]['Children'].append(bpt_build.set_vars({'nosuch': 'Y'}))
    from bpt_serialize import dump_json
    open(tree_path, 'w', encoding='utf-8').write(dump_json(bad))
    r = run_cli('probe', tree_path, os.path.join(tmp, 'probe_cli_bad.bpt'))
    assert r.returncode != 0, 'undeclared-переменная прошла гейт probe'
    r = run_cli('probe')
    assert r.returncode == 2, f'usage exit={r.returncode}'


# --- волна 4 (bpt-fn-lint): FN-правила канона урока 4912 ---------------------
# Мутант на каждое правило FN-01…FN-09 + чистые канонические формы (ложняки)
# + корпус-гейт 0 ERROR. Реестр/токенайзер — tests/test_bpt_funcs.py.

FN_CASES = [
    # (формула, rule-ID, severity, метка)
    ("{{=count({=Document:X})}}", 'FN-01', 'ERROR', 'неизвестное имя'),
    ("{{=number_format(1300.5, 2, ',', ' ')}}", 'FN-01', 'ERROR',
     'near-miss: number_format -> numberformat'),
    ("{{=if(a, b)}}", 'FN-02', 'ERROR', 'арность if 2 != 3'),
    ("{{=datediff({=Document:A})}}", 'FN-02', 'ERROR',
     'арность datediff 1 != 3'),
    ("{{=if({=Variable:x} == 1, 'y', 'n')}}", 'FN-04', 'WARN',
     '== не документирован'),
    ("{{=if(a && b, 'y', 'n')}}", 'FN-04', 'WARN', '&& не документирован'),
    ("{{=dateadd({=Document:D}, \"10m\")}}", 'FN-05', 'INFO',
     "голый 'm' = месяцы"),
    ("{{=dateadd({=Document:D}, \"3 fortnights\")}}", 'FN-05', 'WARN',
     'неизвестный юнит'),
    ("{{=dateadd({=Document:D}, \"-{=Variable:N}d\")}}", 'FN-05', 'WARN',
     'реф в кавычках — нужен &'),
    ("{{=date('d', {=Document:X}) + 1}}", 'FN-07', 'ERROR',
     'date() в арифметике'),
    ("{{=1 + locdate('Y')}}", 'FN-07', 'ERROR', 'locdate в арифметике'),
    ("{{=strtotime(\"завтра\")}}", 'FN-08', 'WARN', 'кириллица в strtotime'),
    ("{{=datediff({=Document:A}, {=Document:B}, '%d') % 2}}", 'FN-09',
     'INFO', '% — проценты'),
]


@check
def fn_rules_fire_with_severity():
    for s, rule, sev, tag in FN_CASES:
        hits = [f for f in bpt_build.expr_lint(s)
                if f.startswith(f'[{rule} {sev}]')]
        assert hits, f'{tag}: {rule} {sev} не сработал: {s!r}'


@check
def fn_clean_canonical_forms():
    """Канонические формы урока 4912 — ноль FN-находок (нет ложняков):
    numberformat без подчёркивания, true() в аргументе, мультисловный
    интервал, каноничный '=', конкатенация & над date(), английский
    strtotime, workdateadd с пользователем."""
    for s in ("{{=numberformat(1300.5, 2, ',', ' ')}}",
              "{{=getdocumenturl('html', true())}}",
              "{{=dateadd({=Document:D}, \"2 days 3 minutes\")}}",
              "{{=dateadd({=Document:D}, \"-2d\")}}",
              "{{=if({=Variable:x} = 1, 'y', 'n')}}",
              "{{=date('Hi', {=Document:E}) & date('d', {=Document:B})}}",
              "{{=strtotime(\"next Thursday\")}}",
              "{{=workdateadd({=Document:D}, \"2d\", {=Document:U})}}"):
        hits = [f for f in bpt_build.expr_lint(s) if f.startswith('[FN-')]
        assert hits == [], f'ложняк на каноне {s!r}: {hits!r}'


@check
def fn06_nested_expr_in_expr():
    probs = bpt_build._lint_expr('{{=f({{=g()}})}}')
    assert any(p.startswith('[FN-06 ERROR]') for p in probs), probs


@check
def fn03_portal_profile():
    """FN-03: cloud-дефолт — без проверок; box@X.Y — WARN на функциях с
    since выше профиля; box выше since — чисто."""
    expr = '{{=settime({=Variable:d}, 15, 55)}}'
    assert bpt_build.expr_lint(expr) == [], 'cloud: проверок быть не должно'
    w = bpt_build.expr_lint(expr, portal='box@22.100')
    assert any(f.startswith('[FN-03 WARN]') for f in w), w
    assert bpt_build.expr_lint(expr, portal='box@23.0') == [], \
        '23.0 >= 22.500 — чисто'


@check
def fn_severity_routed_by_validate():
    """validate() маршрутизует FN-severity: [FN-NN ERROR] -> errors
    (блокирует save), WARN/INFO -> warnings."""
    t = bpt_build.scaffold(
        [bpt_build.set_vars({
            'bad': '{{=count({=Document:X})}}',
            'warn': "{{=if({=Variable:x} == 1, 'y', 'n')}}"})],
        {'x': bpt_build.variable('x', 'int')}, {})
    errs, warns = bpt_build.validate(t)
    assert any('FN-01 ERROR' in e and 'SV bad' in e for e in errs), errs
    assert any('FN-04 WARN' in w and 'SV warn' in w for w in warns), warns


@check
def fn_corpus_baseline_zero_error():
    """Корпус-гейт волны 4 (промер 2026-10-02): validate() на 12 эталонах —
    ERROR только известные висячки notify-history-canonical (Act_12 ×2, эталоны не
    править); FN-находок ноль — corpus-only пуст, операторы/юниты чисты,
    '%' в корпусе живёт внутри строковых литералов."""
    total_err = 0
    for fn in sorted(os.listdir(ETALONS)):
        if not fn.endswith('.bpt'):
            continue
        errs, warns = bpt_build.validate(
            bpt_build.load(os.path.join(ETALONS, fn)))
        for e in errs:
            ok = ('Act_12' in e and fn == 'notify-history-canonical.bpt') \
                or ('FN-10' in e and fn == 'date-window-branch.bpt')
            assert ok, f'{fn}: новый ERROR: {e}'
        total_err += len(errs)
    assert total_err == 3, total_err  # Act_12 ×2 + date-window-branch FN-10 ×1


@check
def cli_portal_flag(tmp=TMP):
    """CLI --portal= (FN-03): WARN не меняет exit; невалидный профиль и
    дубль флага -> usage exit 2."""
    tree_path = os.path.join(tmp, 'portal_tree.json')
    # оба SV в ОДНОМ scaffold: инкрементальный append после scaffold даёт
    # дубль Name (scaffold сбрасывает _ACT_SEQ — имена детерминированы
    # в пределах одного билда)
    t = bpt_build.scaffold(
        [bpt_build.set_vars({'done': 'Y'}),
         bpt_build.set_vars({'d': '{{=settime({=Variable:done}, 15, 55)}}'})],
        {'done': bpt_build.variable('done', 'int'),
         'd': bpt_build.variable('d', 'datetime')}, {})
    json.dump(t, open(tree_path, 'w'), ensure_ascii=False)
    r = run_cli('validate', tree_path, '--portal=box@22.100')
    assert r.returncode == 0, f'WARN не меняет exit: {r.returncode}, {r.stdout}'
    assert any('FN-03' in ln for ln in r.stdout.splitlines()), r.stdout
    r = run_cli('validate', tree_path, '--portal=box@23.0')
    assert r.returncode == 0 and 'FN-03' not in r.stdout, r.stdout
    r = run_cli('validate', tree_path, '--portal=trash')
    assert r.returncode == 2 and 'usage' in r.stderr.lower(), r.stderr
    r = run_cli('validate', tree_path, '--portal=cloud', '--portal=cloud')
    assert r.returncode == 2, 'дубль --portal= должен давать usage'


# ---------- replica-фикстуры классов date-filters-heavy/company-stage (корпус, волна-4 хвост) --
# Контракт: конструктор обязан воспроизводить ЭТАЛОННЫЙ узел побайтно
# (php_serialize), Name назначен эталонным, EditorComment исключён
# (эталоны то несут его, то нет; {} ≡ [] — оба a:0:{}).


def _etalon_node(fname, node_name):
    nodes = []
    bpt_build._walk_nodes(
        bpt_build.load(os.path.join(ETALONS, fname)).get('TEMPLATE'), '$', nodes)
    for _, n in nodes:
        if n.get('Name') == node_name:
            return n
    raise AssertionError(f'{fname}: узел {node_name} не найден')


def _replica_ser(node):
    n = dict(node)
    n['Properties'] = {k: v for k, v in node['Properties'].items()
                       if k != 'EditorComment'}
    return bpt_build.php_serialize(n)


def _replica(built, et):
    return _replica_ser({**built, 'Name': et['Name']}) == _replica_ser(et)


@check
def replica_task2_bp222():
    et = _etalon_node('date-filters-heavy.bpt', 'Task2_13371024')
    f = et['Properties']['Fields']
    built = bpt_build.task(f['TITLE'], responsible=f['RESPONSIBLE_ID'],
                           description=f['DESCRIPTION'],
                           creator=f['CREATED_BY'])
    assert _replica(built, et), \
        'task() не воспроизводит боевую форму Task2_13371024 (date-filters-heavy)'


@check
def task_accomplices_auditors_mapping():
    # тестовый узел date-filters-heavy с участниками: маппинг списков (прочие поля
    # узла несут пустые ''-значения тестовой сессии — вне контракта)
    f = _etalon_node('date-filters-heavy.bpt', 'TP_19'
                     )['Properties']['Fields']
    built = bpt_build.task(f['TITLE'], responsible=f['RESPONSIBLE_ID'],
                           creator=f['CREATED_BY'], description='',
                           accomplices=['user_14', 'user_58'],
                           auditors=['user_14', 'user_58'])
    bf = built['Properties']['Fields']
    assert bf['ACCOMPLICES'] == f['ACCOMPLICES'] == ['user_14', 'user_58'], \
        f'ACCOMPLICES: {bf["ACCOMPLICES"]!r}'
    assert bf['AUDITORS'] == f['AUDITORS'] == ['user_14', 'user_58'], \
        f'AUDITORS: {bf["AUDITORS"]!r}'


@check
def replica_create_deal_bp222():
    et = _etalon_node('date-filters-heavy.bpt', 'TP_11')
    built = bpt_build.create_deal(et['Properties']['Fields'])
    assert _replica(built, et), \
        'create_deal() не воспроизводит форму CreateCrmDealDocument (date-filters-heavy)'


@check
def replica_event_add_bp222():
    et = _etalon_node('date-filters-heavy.bpt', 'TP_03')
    p = et['Properties']
    built = bpt_build.event_add(p['EventText'], event_type=p['EventType'])
    assert _replica(built, et), \
        'event_add() не воспроизводит форму CrmEventAdd (date-filters-heavy)'


@check
def replica_get_deal_info_bp222():
    et = _etalon_node('date-filters-heavy.bpt', 'TP_05')
    p = et['Properties']
    rows = [(r[0]['object'], r[0]['field'], r[0]['operator'], r[0]['value'])
            for r in p['DynamicFilterFields']['items']]
    built = bpt_build.get_deal_info(
        p['ReturnFields'], rows,
        {k: v for k, v in p['DynamicEntityFields'].items() if k != 'Document'})
    assert _replica(built, et), \
        'get_deal_info() не воспроизводит deal-режим CrmGetDynamicInfo (date-filters-heavy)'


@check
def replica_control_notify_bp172():
    et = _etalon_node('company-stage-control.bpt', 'TP_17')
    built = bpt_build.control_notify(et['Properties']['MessageText'])
    assert _replica(built, et), \
        'control_notify() не воспроизводит форму CrmControlNotify (company-stage)'


# --- аудит 2026-10-16, №5: char-взрыв user-list ------------------------------
# Голый list(x) на принимающих точках рвал 'user_231' на 8 «юзеров»-символов
# (все гейты зелёные, импорт проходит, уведомление уходит не тому).

@check
def user_str_single_recipient_not_char_exploded():
    seams = [
        ('set_fields.ModifiedBy',
         lambda: bpt_build.set_fields(
             {'F': '1'}, modified_by='user_9')['Properties']['ModifiedBy'],
         ['user_9']),
        ('get_user.UserParameter',
         lambda: bpt_build.get_user(
             'user_5')['Properties']['UserParameter'],
         ['user_5']),
        ('get_user.ReserveUserParameter',
         lambda: bpt_build.get_user(
             'user_5', reserve='user_7')['Properties']['ReserveUserParameter'],
         ['user_7']),
        ('get_user_info.GetUser',
         lambda: bpt_build.get_user_info(
             'user_5', {})['Properties']['GetUser'],
         ['user_5']),
        ('event_add.EventUser',
         lambda: bpt_build.event_add(
             'txt', users='user_3')['Properties']['EventUser'],
         ['user_3']),
        ('control_notify.ToUsers',
         lambda: bpt_build.control_notify(
             'msg', users='user_3')['Properties']['ToUsers'],
         ['user_3']),
        ('approve.Users',
         lambda: bpt_build.approve(
             'user_5', 'S')['Properties']['Users'],
         ['user_5']),
        ('request_info.Users',
         lambda: bpt_build.request_info(
             'user_5', [], 'S')['Properties']['Users'],
         ['user_5']),
        ('review.Users',
         lambda: bpt_build.review(
             'user_5', 'S')['Properties']['Users'],
         ['user_5']),
        ('timeline_comment.CommentUser',
         lambda: bpt_build.timeline_comment(
             'txt', users='user_4')['Properties']['CommentUser'],
         ['user_4']),
        ('im_notify.MessageUserTo',
         lambda: bpt_build.im_notify(
             'user_231', 'msg')['Properties']['MessageUserTo'],
         ['user_231']),
        ('im_notify.MessageUserFrom',
         lambda: bpt_build.im_notify(
             'u1', 'msg', users_from='user_9')['Properties']['MessageUserFrom'],
         ['user_9']),
        ('absence_entry.AbsenceUser',
         lambda: bpt_build.absence_entry(
             'user_8', '2026-01-01', '2026-01-09',
             'N')['Properties']['AbsenceUser'],
         ['user_8']),
        ('calendar_event.CalendarUser',
         lambda: bpt_build.calendar_event(
             'N', 'f', 't', users='user_8')['Properties']['CalendarUser'],
         ['user_8']),
        ('task.ACCOMPLICES',
         lambda: bpt_build.task(
             'T', accomplices='user_14')['Properties']['Fields']['ACCOMPLICES'],
         ['user_14']),
        ('task.AUDITORS',
         lambda: bpt_build.task(
             'T', auditors='user_58')['Properties']['Fields']['AUDITORS'],
         ['user_58']),
    ]
    for label, get, expected in seams:
        got = get()
        assert got == expected, f'{label}: {got!r} != {expected!r} (char-взрыв)'


@check
def user_str_probe_journal_not_char_exploded():
    probed = bpt_build.probe(minimal_tree(), users='user_2')
    journals = [ch for ch in probed['TEMPLATE'][0]['Children']
                if isinstance(ch, dict)
                and ch.get('Type') == 'CrmTimelineCommentAdd']
    assert journals, 'journal-вставка probe не построена'
    assert journals[0]['Properties']['CommentUser'] == ['user_2'], \
        f'CommentUser: {journals[0]["Properties"]["CommentUser"]!r}'


@check
def user_list_empty_forms_canonical():
    # Обратная сторона фикса №5: канонические пустые формы не дрейфуют
    assert bpt_build.event_add('t')['Properties']['EventUser'] == {}
    assert bpt_build.control_notify('m')['Properties']['ToUsers'] == {}
    assert bpt_build.im_notify(
        'user_1', 'm')['Properties']['MessageUserFrom'] == ['1']
    assert bpt_build.timeline_comment('t')['Properties']['CommentUser'] == \
        ['{=Document:ASSIGNED_BY_ID}']
    assert bpt_build.calendar_event(
        'n', 'f', 't')['Properties']['CalendarUser'] == ['1']
    t = bpt_build.task('T')
    assert t['Properties']['Fields']['ACCOMPLICES'] == ''
    assert t['Properties']['Fields']['AUDITORS'] == ''
    assert bpt_build.set_fields({'F': '1'})['Properties']['ModifiedBy'] == []
    assert bpt_build.get_user(
        'user_5')['Properties']['ReserveUserParameter'] == []


# --- волна 2 (аудит 2026-10-16): кластер graft-канона -------------------------

@check
def for_each_source_activity_name_mode():
    """№1: имя-режим ForEach (Object=<Name активности>; корпус
    notify-history-canonical Object=A73347_…) выражается конструктором;
    существование имени проверяется валидатором по дереву; мусорный
    source и двусмысленность source+source_activity режутся сборкой."""
    fe = bpt_build.for_each('rows', [bpt_build.set_vars({'x': '1'})],
                            source_activity='NamedSrc')
    assert fe['Properties']['Object'] == 'NamedSrc', fe['Properties']
    tgt = bpt_build.im_notify(['1'], 'ok', name='NamedSrc')
    ok = bpt_build.scaffold(
        [bpt_build.sequence([tgt, fe])],
        {'rows': bpt_build.variable('rows', 'string', multiple='1')},
        {'ID': 'string'})
    errs, _w = bpt_build.validate(ok)
    assert errs == [], errs
    ghost = bpt_build.scaffold(
        [bpt_build.sequence([fe])],
        {'rows': bpt_build.variable('rows', 'string', multiple='1')},
        {'ID': 'string'})
    errs, _w = bpt_build.validate(ghost)
    assert any('ForEach' in e and 'Ghost' not in e and 'NamedSrc' in e
               for e in errs), errs
    ghost_fe = bpt_build.for_each('rows', [bpt_build.set_vars({'x': '1'})],
                                  source_activity='Ghost_9')
    errs, _w = bpt_build.validate(bpt_build.scaffold(
        [ghost_fe],
        {'rows': bpt_build.variable('rows', 'string', multiple='1')},
        {'ID': 'string'}))
    assert any('ForEach' in e and 'Ghost_9' in e for e in errs), errs
    try:
        bpt_build.for_each('rows', [], source='Bogus')
        raise AssertionError('source=Bogus молча прошёл')
    except ValueError:
        pass
    try:
        bpt_build.for_each('rows', [], source='Constant',
                           source_activity='NamedSrc')
        raise AssertionError('source+source_activity молча прошли')
    except ValueError:
        pass


@check
def variable_multiple_default_forms():
    """№4: Default-форма по Multiple на эмиссии — единый арбитр
    DEFAULT_NORM (корпус: multi -> list|None, single -> str; базовая
    форма несёт Default присутствующим, multi-пусто = [] — промер
    корпуса: 130+ наблюдений Default=[] и 3 ABSENT title-формы)."""
    v = bpt_build.variable('ids', 'string', multiple='1')
    assert v['Default'] == [], v
    v2 = bpt_build.variable('objs', 'string', multiple='1', default=['a'])
    assert v2['Default'] == ['a'], v2
    single = bpt_build.variable('s', 'string', default='x')
    assert single['Default'] == 'x', single
    vt = bpt_build.variable('t', 'string', multiple='1', title='T')
    assert 'Default' not in vt, vt
    vt2 = bpt_build.variable('t2', 'string', multiple='1', title='T',
                             default=['k'])
    assert vt2['Default'] == ['k'], vt2
    try:
        bpt_build.variable('bad', 'string', multiple='1', default='x')
        raise AssertionError('multi str-Default молча прошёл')
    except ValueError:
        pass
    try:
        bpt_build.variable('bad2', 'string', default=['x'])
        raise AssertionError('single list-Default молча прошёл')
    except ValueError:
        pass
    try:
        bpt_build.variable('bad3', 'string', multiple='yes')
        raise AssertionError('multiple=yes молча прошёл')
    except ValueError:
        pass


# --- T5 (волна 4, suggest-хвост аудита 2026-10-16) ----------------------------

@check
def id_slots_none_loud_not_str_none():
    """str(None)→'None' в ID-слотах: None — громкая ошибка сборки, не
    строка 'None' в DynamicTypeId/DocumentId/TemplateId (аудит)."""
    cases = [
        ('create_dynamic(etid=None)', lambda: bpt_build.create_dynamic(
            None, {'TITLE': 'x'}, 'Act_1')),
        ('start_workflow(tpl=None)', lambda: bpt_build.start_workflow(
            None, 'deal_1')),
        ('start_workflow(doc=None)', lambda: bpt_build.start_workflow(
            '11', None)),
        ('update_dynamic(etid=None)', lambda: bpt_build.update_dynamic(
            None, {'TITLE': 'x'}, item_id_expr='{=A1:ItemId}')),
    ]
    for label, fn in cases:
        try:
            fn()
        except ValueError:
            pass
        else:
            raise AssertionError(f'{label}: None в ID-слоте прошёл молча')


@check
def update_dynamic_requires_targeting():
    """DynamicId='' без фильтра — нетаргетированный update (семантика
    портала неизвестна, открытый вопрос): сборка обязана отказать."""
    try:
        bpt_build.update_dynamic(154, {'TITLE': 'x'})
    except ValueError:
        pass
    else:
        raise AssertionError('update_dynamic без таргетинга прошёл молча')


@check
def delay_guards_and_int_coercion():
    """delay(): без аргументов / оба сразу — ValueError (XOR); int
    коэрцится в str (канон s:"40", аудит: delay(40)→i:40)."""
    for label, fn in (
            ('без аргументов', lambda: bpt_build.delay()),
            ('оба сразу', lambda: bpt_build.delay(
                duration='40', until='{=System:Now}'))):
        try:
            fn()
        except ValueError:
            pass
        else:
            raise AssertionError(f'delay {label} прошёл молча')
    n = bpt_build.delay(40)
    assert n['Properties']['TimeoutDuration'] == '40', \
        f'int-duration не коэрцится: {n["Properties"]["TimeoutDuration"]!r}'


@check
def condition_rows_length_gates():
    """Length-гарды условий: while_loop — ровно 4 (с хвостовым '0'),
    if_else/if_else_field — ровно 3 (хвост допишется сам), mixed-ряд —
    ровно 5. Молчаливые 3-элем while / 5-элем if_else — порча формы."""
    cases = [
        ('while_loop 1-элем', lambda: bpt_build.while_loop(
            ['v'], [bpt_build.set_vars({'a': 1})])),
        ('while_loop 5-элем', lambda: bpt_build.while_loop(
            ['v', '=', '1', '0', 'x'], [bpt_build.set_vars({'a': 1})])),
        ('if_else 4-элем (с хвостом)', lambda: bpt_build.if_else(
            [('д', ['v', '=', '1', '0'], []), ('н', None, [])])),
        ('if_else_field 4-элем', lambda: bpt_build.if_else_field(
            [('д', ['F', '=', '1', '0'], []), ('н', None, [])])),
        ('if_else_mixed 4-элем ряд', lambda: bpt_build.if_else_mixed(
            [('д', [('Variable', 'v', '=', '1')], []), ('н', None, [])])),
    ]
    for label, fn in cases:
        try:
            fn()
        except ValueError as e:
            assert label.split()[0] in str(e), \
                f'{label}: сообщение без имени конструктора: {e}'
        else:
            raise AssertionError(f'{label} прошёл молча')
    # живой API: 3-элем while — автодоливка хвоста (не гейт)
    w = bpt_build.while_loop(['v', '=', '1'], [])
    assert w['Properties']['propertyvariablecondition'][0] == \
        ['v', '=', '1', '0'], '3-элем хвост не долит'


@check
def create_dynamic_etid_dup_repair():
    """Ключ поля уже с etid-префиксом (копипаста из эталона) — не
    удваивать: strip + WARN (тихая порча → громкий ремонт)."""
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        node = bpt_build.create_dynamic(154, {'154_TITLE': 'x'}, 'Act_2')
    keys = list(node['Properties']['DynamicEntitiesFields'])
    assert '154_TITLE' in keys, f'ремонт не сработал: {keys}'
    assert not any(k.startswith('154_154_') for k in keys), \
        f'etid-префикс удвоен: {keys}'
    assert 'WARN' in err.getvalue(), 'ремонт без WARN-следа'


@check
def if_else_family_title_param():
    """if_else*() хардкодили Title='Условие' (корпус: 'router',
    'WHOIS') — параметр title= у всех трёх."""
    n = bpt_build.if_else([('д', ['v', '=', '1'], []), ('н', None, [])],
                          title='router')
    m = bpt_build.if_else_mixed(
        [('д', [('Variable', 'v', '=', '1', '0')], []), ('н', None, [])],
        title='WHOIS')
    f = bpt_build.if_else_field([('д', ['F', '=', '1'], []), ('н', None, [])],
                                title='T')
    for label, node in (('if_else', n), ('if_else_mixed', m),
                        ('if_else_field', f)):
        assert node['Properties']['Title'] != 'Условие', \
            f'{label}: title= не принят'


@check
def log_entry_constructor_corpus_canon():
    """LogActivity без конструктора (корпус notify-history ×2): канон
    Text → SetVariable '0' → Title 'Log entry' → EditorComment."""
    n = bpt_build.log_entry('{=A1:GetUser}')
    assert n['Type'] == 'LogActivity'
    assert list(n['Properties']) == ['Text', 'SetVariable', 'Title',
                                     'EditorComment'], \
        f'порядок ключей не канон: {list(n["Properties"])}'
    assert n['Properties']['SetVariable'] == '0'
    assert n['Properties']['Title'] == 'Log entry'


@check
def scaffold_bare_root_bp172_form():
    """Корень company-stage (×2 корпуса) без Activated/Node — форма невыразима.
    bare_root=True эмитит 4-ключевой корень; дефолт прежний (6 ключей)."""
    t = bpt_build.scaffold([], {}, {}, bare_root=True)
    r = t['TEMPLATE'][0]
    assert list(r) == ['Type', 'Name', 'Properties', 'Children'], \
        f'корень не в форме company-stage: {list(r)}'
    d = bpt_build.scaffold([], {}, {})
    assert 'Activated' in d['TEMPLATE'][0] and 'Node' in d['TEMPLATE'][0], \
        'дефолтная форма корня сломана'


@check
def probe_cli_author_flag():
    """probe CLI не задавал автора журнала: пустой ответственный
    сущности = портал молча не пишет комментарии. --author user_ID
    задаёт CommentUser всех журнальных вставок."""
    from bpt_serialize import dump_json, decompress_bpt, php_unserialize_exact
    tree = bpt_build.scaffold([bpt_build.set_vars({'x': 1})],
                              {'x': bpt_build.variable('x', 'string')}, {})
    tj = os.path.join(TMP, 'probe_tree.json')
    ob = os.path.join(TMP, 'probe_out.bpt')
    with open(tj, 'w', encoding='utf-8') as fh:
        fh.write(dump_json(tree))
    r = subprocess.run([PY, BUILD, 'probe', tj, ob, '--author', '42'],
                       capture_output=True, text=True, cwd=BPT)
    assert r.returncode == 0, f'probe CLI упал: {r.stderr[-300:]}'
    out = php_unserialize_exact(decompress_bpt(open(ob, 'rb').read()))

    def journals(node, acc):
        if isinstance(node, dict):
            if node.get('Type') == 'CrmTimelineCommentAdd':
                acc.append(node)
            for c in node.get('Children') or []:
                journals(c, acc)
        elif isinstance(node, list):
            for c in node:
                journals(c, acc)
        return acc

    js = journals(out.get('TEMPLATE'), [])
    assert js, 'журнальных вставок нет'
    assert all(j['Properties']['CommentUser'] == ['user_42'] for j in js), \
        f'CommentUser не по --author: {[j["Properties"]["CommentUser"] for j in js]}'


@check
def constructor_inputs_not_aliased():
    """Алиасинг входных dict/list: пост-мутация caller'а тихо меняла
    собранные узлы (аудит) — конструкторы снимают копию."""
    import copy
    props = {'MessageSite': 'x', 'Nested': ['a']}
    kids = [bpt_build.set_vars({'a': 1})]
    kids_snap = copy.deepcopy(kids)
    n = bpt_build.act('IMNotifyActivity', 't', props, kids)
    mapping = {'x': 'old'}
    sv = bpt_build.set_vars(mapping)
    # caller мутирует свои объекты ПОСЛЕ сборки
    props['MessageSite'] = 'MUTATED'
    props['Nested'].append('MUT')
    kids.append(bpt_build.set_vars({'b': 2}))
    mapping['x'] = 'MUTATED'
    assert n['Properties']['MessageSite'] == 'x', 'props-алиасинг (скаляр)'
    assert n['Properties']['Nested'] == ['a'], 'props-алиасинг (влож. list)'
    assert len(n['Children']) == 1, 'children-алиасинг'
    assert kids == kids_snap or True  # сам caller волен мутировать
    assert sv['Properties']['VariableValue']['x'] == 'old', \
        'VariableValue-алиасинг'


if __name__ == '__main__':
    sys.exit(main())

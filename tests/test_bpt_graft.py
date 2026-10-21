#!/usr/bin/env python3
"""tests/test_bpt_graft.py — регресс-жгут bpt_graft (волна 6): API-контракты,
гейты (path-diff/delta-validate/form-lint/резолвер), кейс-переходы booking-chain
байт-в-байт, корпус-инвариант эталонов. Запуск: python3 tests/
test_bpt_graft.py (из каталога bpt). Zero-dep."""
import copy
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
BPT = os.path.dirname(HERE)
sys.path.insert(0, BPT)

os.environ.setdefault('GRAFT_LOG_LEVEL', 'QUIET')

import bpt_graft  # noqa: E402
from bpt_graft import GraftError, GraftTree, NodeRef  # noqa: E402
import bpt_build  # noqa: E402
from bpt_build import set_vars  # noqa: E402
from bpt_serialize import php_serialize, compress_bpt  # noqa: E402

FIX_BOOKING_CHAIN = os.path.join(HERE, 'fixtures', 'booking-chain')
V17 = os.path.join(FIX_BOOKING_CHAIN, 'booking-chain_v17.bpt')
V18 = os.path.join(FIX_BOOKING_CHAIN, 'booking-chain_v18.bpt')
V19 = os.path.join(FIX_BOOKING_CHAIN, 'booking-chain_v19.bpt')
ETALONS = os.path.join(BPT, 'etalons')
PY = sys.executable
TMP = tempfile.mkdtemp(prefix='bpt_t_graft_')

CHECKS = []


def check(fn):
    CHECKS.append(fn)
    return fn


CASE_SRC = {
    'patch_v17_to_v18': V17,
    'patch_v18_to_v19': V18,
    'patch_v20p_to_v21': os.path.join(FIX_BOOKING_CHAIN, 'booking-chain-v20-portal.bpt'),
    'patch_v21_to_v22': os.path.join(FIX_BOOKING_CHAIN, 'booking-chain_v21.bpt'),
    'patch_v21_to_v23': os.path.join(FIX_BOOKING_CHAIN, 'booking-chain_v21.bpt'),
}


def run_case(name):
    """Прогон скрипта-перехода cases_booking_chain; возвращает путь rebuilt."""
    script = os.path.join(HERE, 'cases_booking_chain', name + '.py')
    dst = os.path.join(TMP, name + '_rebuilt.bpt')
    r = subprocess.run([PY, script, CASE_SRC[name], dst],
                       capture_output=True, text=True, timeout=120)
    return r, dst


# --- T1: каркас ------------------------------------------------------------

@check
def load_snapshot_zlib_level():
    gt = GraftTree.load(V17)
    assert gt.zlib_level == 9, f'v17 zlib {gt.zlib_level} != 9'
    gt6 = GraftTree.load(os.path.join(FIX_BOOKING_CHAIN, '..', '..', 'fixtures',
                                      'booking-chain', 'booking-chain_v17.bpt'))
    blob = open(V17, 'rb').read()
    import zlib
    assert zlib.compress(php_serialize(gt6.tree), 9) == blob, \
        'payload не воспроизводит блоб уровня 9'
    # портальный эталон: python-zlib не воспроизводим (None); сан-копия
    # в публичном контуре рекомпрессована каноном уровня 9 (план
    # bpt-public-release: fallback-9 для портального deflate)
    gtp = GraftTree.load(os.path.join(ETALONS, 'company-stage.bpt'))
    assert gtp.zlib_level in (None, 9), \
        f'портальный блоб: None (оригинал) или 9 (сан-канон), не {gtp.zlib_level}'


@check
def alloc_name_safe_unique():
    gt = GraftTree.load(V17)
    a, b = gt.alloc_name('SetVariableActivity'), gt.alloc_name(
        'SetVariableActivity')
    assert a != b and a.startswith('SetVar_'), f'{a} / {b}'
    taken = gt._taken  # noqa: SLF001 — тест-контракт safe-alloc
    assert a in taken and b in taken


@check
def path_of_identity_stable():
    gt = GraftTree.load(V17)
    ref = gt.locate(kind='CrmGetDynamicInfoActivity',
                    name='TP_09', expect=1)
    before = ref.path
    gt.sv_set(gt.locate_all(kind='SetVariableActivity')[0], 'zz', '1')
    assert ref.path == before, 'sv_set сместил путь стороннего узла'


# --- T2: locate-контракты ---------------------------------------------------

@check
def locate_expect_enumerates():
    gt = GraftTree.load(V17)
    # имя первого IfElse — динамически (в сан-корпусе узлы TP-имена)
    probe = str(gt.locate_all(kind='IfElseActivity')[0]
                .props.get('Name') or '')
    try:
        gt.locate_all(kind='IfElseActivity', expect=1)
        raise AssertionError('34 IfElse должны отказаться')
    except GraftError as e:
        assert 'ожидалось 1' in str(e) and probe in str(e), \
            'отказ обязан перечислять найденные (Name+Title)'


@check
def locate_sv_keys_and_pred_and_title():
    gt = GraftTree.load(V19)
    assert len(gt.locate_all(sv_keys=['eg_total', 'eg_count'])) >= 1
    ctx = gt.locate_all(
        kind='SetVariableActivity',
        pred=lambda p: 'oe_percent' in str(
            (p.get('VariableValue') or {}).get('oe_subtotal', '')),
        expect=1)
    assert 'oe_percent' in str(ctx[0].props.get('VariableValue', {})
                               .get('oe_subtotal', ''))
    g21 = GraftTree.load(os.path.join(FIX_BOOKING_CHAIN, 'booking-chain-v20-portal.bpt'))
    sums = g21.locate(kind='SetVariableActivity', title='unit sums',
                      expect=1)
    assert 'obj_titles' in sums.props.get('VariableValue', {})


@check
def noderef_path_raises_when_detached():
    gt = GraftTree.load(V17)
    ref = gt.locate_all(kind='TerminateActivity', expect=None)[0] \
        if gt.locate_all(kind='TerminateActivity', expect=None) \
        else gt.locate_all(kind='CrmChangeStatusActivity', expect=None)[0]
    gt.delete(ref)
    try:
        _ = ref.path
        raise AssertionError('путь удалённого узла должен отказать')
    except GraftError:
        pass


# --- T3: edit-операции ------------------------------------------------------

@check
def sv_replace_nested_occurrences_guarded():
    """v17-баг: new, содержащий old, не переваривается повторно — один
    проход str.replace."""
    gt = GraftTree.load(V17)
    ctx = gt.locate_all(
        kind='SetVariableActivity',
        pred=lambda p: 'oe_percent' in str(
            (p.get('VariableValue') or {}).get('oe_subtotal', '')),
        expect=1)
    n = gt.sv_replace('oe_hst', 'X*0.13', '(X*P)*0.13', expect=0,
                      refs=ctx)
    assert n == 0, 'несуществующий шаблон не даёт замен'
    try:
        gt.sv_replace('oe_*', '', 'NOPE')
        raise AssertionError('пустой old должен отказать')
    except GraftError:
        pass


@check
def sv_replace_context_scoping():
    """'{=A:UF}*0.13' в двух ветках; контекст-refs правит одну (кейс v18)."""
    gt = GraftTree.load(V17)
    both = gt.sv_replace(
        'oe_hst',
        '{=TP_04:UF_CRM_45_1783485570}*0.13', 'ZZZ',
        expect=2)
    assert both == 2, 'без контекста — обе ветки'
    gt2 = GraftTree.load(V17)
    ctx = gt2.locate_all(
        kind='SetVariableActivity',
        pred=lambda p: 'oe_percent' in str(
            (p.get('VariableValue') or {}).get('oe_subtotal', '')),
        expect=1)
    one = gt2.sv_replace(
        'oe_hst',
        '{=TP_04:UF_CRM_45_1783485570}*0.13', 'ZZZ',
        expect=1, refs=ctx)
    assert one == 1, 'контекст-сужение = только целевая ветка'


@check
def sv_set_two_forms_and_list_values():
    gt = GraftTree.load(V19)
    ref = gt.locate_all(sv_keys=['eg_total'])[0]
    gt.sv_set(ref, 'a', '1')
    gt.sv_set(ref, {'b': ['x', 'y'], 'c': '{{=1}}'})
    vv = ref.props['VariableValue']
    assert vv['a'] == '1' and vv['b'] == ['x', 'y']


@check
def props_ops_guards():
    gt = GraftTree.load(V18)
    deal = gt.locate(kind='CrmGetDynamicInfoActivity',
                     name='TP_09', expect=1)
    rf = deal.props['ReturnFields'][0]
    try:
        gt.props_append(deal, 'ReturnFields', rf)
        raise AssertionError('дубль ReturnFields должен отказать')
    except GraftError:
        pass
    gt.props_append(deal, 'ReturnFields', 'UF_TEST_1', 'UF_TEST_2')
    assert deal.props['ReturnFields'][-2:] == ['UF_TEST_1', 'UF_TEST_2']
    # merge: позиция существующего ключа сохраняется
    pos0 = list(deal.props).index('DynamicTypeId')
    gt.props_merge(deal, {'DynamicTypeId': '2', 'Fresh': 1})
    assert list(deal.props).index('DynamicTypeId') == pos0
    assert deal.props['DynamicTypeId'] == '2' and deal.props['Fresh'] == 1
    try:
        gt.props_merge(deal, {'DynamicFilterFields': 'скаляр'})
        raise AssertionError('merge скаляром в dict-ключ должен отказать')
    except GraftError:
        pass
    # скаляр-в-скаляр — легитимная замена (props_merge без dict-сторон)
    gt.props_merge(deal, {'DynamicTypeId': '1036'})
    assert deal.props['DynamicTypeId'] == '1036'


@check
def insert_anchor_positions_and_name_guards():
    gt = GraftTree.load(V17)
    seqs = [s for s in gt.locate_all(kind='SequenceActivity')
            if s.node.get('Children')]
    tgt = seqs[0]
    a = gt.insert_child(tgt, set_vars({'i1': '1'}), position='first')
    b = gt.insert_child(tgt, set_vars({'i2': '2'}), position='last')
    assert tgt.node['Children'][0] is a.node
    assert tgt.node['Children'][-1] is b.node
    c = gt.insert_child(a, set_vars({'i3': '3'}), position='after')
    idx_a = tgt.node['Children'].index(a.node)
    assert tgt.node['Children'][idx_a + 1] is c.node
    # коллизия имени
    clash = set_vars({'x': '1'})
    clash['Name'] = a.name
    try:
        gt.insert_child(tgt, clash, position='last')
        raise AssertionError('коллизия Name должна отказать')
    except GraftError:
        pass
    # Children:None — отказ; SV с [] — легитимный пустой контейнер
    leaf = copy.deepcopy(a.node)
    leaf['Children'] = None
    leaf['Name'] = None
    try:
        gt.insert_child(NodeRef(leaf, gt), set_vars({'y': '1'}),
                        position='last')
        raise AssertionError('Children:None должен отказать')
    except GraftError:
        pass


@check
def rename_rebind_rewrites_refs():
    gt = GraftTree.load(V17)
    deal = gt.locate(kind='CrmGetDynamicInfoActivity',
                     name='TP_09', expect=1)
    old = deal.name
    users_before = gt._count_ref_usage(old)  # noqa: SLF001
    new = gt.rename_rebind(deal)
    assert new != old and deal.node['Name'] == new
    assert gt._count_ref_usage(old) == 0  # noqa: SLF001
    assert gt._count_ref_usage(new) == users_before  # noqa: SLF001


@check
def declare_shapes_and_gotchas():
    gt = GraftTree.load(V18)
    gt.declare('VARIABLES', {'s1': {'Type': 'string'}})
    rec = gt.tree['VARIABLES']['s1']
    assert rec['Name'] == 's1', 'VARIABLES: Name == ключ принудительно'
    assert list(rec) == ['Name', 'Description', 'Type', 'Required',
                         'Multiple', 'Options', 'Default'], \
        f'каркас: {list(rec)}'
    gt.declare('VARIABLES', {'m1': {'Multiple': '1'}})
    assert gt.tree['VARIABLES']['m1']['Default'] == [], \
        'multi Default -> [] (кейс mgr)'
    try:
        gt.declare('VARIABLES', {'s1': {}})
        raise AssertionError('повторный declare должен отказать')
    except GraftError:
        pass
    try:
        gt.declare('PARAMETERS', {'p_no_label': {'Type': 'string'}})
        raise AssertionError('PARAMETERS без Name-лейбла — грабля booking-chain')
    except GraftError:
        pass
    gt.declare('PARAMETERS', {'p_ok': {'Type': 'string',
                                       'Name': 'Лейбл'}})
    assert gt.tree['PARAMETERS']['p_ok']['Name'] == 'Лейбл'


@check
def payload_set_forms():
    gt = GraftTree.load(V18)
    gt.payload_set('VARIABLES', 's1', {'Type': 'double'})
    assert gt.tree['VARIABLES']['s1']['Type'] == 'double'
    pos = list(gt.tree['VARIABLES']).index('s1')
    gt.payload_set('VARIABLES', 's1', {'Required': '1'})
    assert list(gt.tree['VARIABLES']).index('s1') == pos, \
        'merge сохраняет позицию ключа'
    gt.payload_set('VARIABLES', 's1', 'abc')
    assert gt.tree['VARIABLES']['s1']['Default'] == 'abc', \
        'скаляр -> Default (смена значения)'


# --- T4: path-diff / delta-validate ------------------------------------------

@check
def path_diff_granularity_added_subtree_vs_parent():
    gt = GraftTree.load(V17)
    seqs = [s for s in gt.locate_all(kind='SequenceActivity')
            if s.node.get('Children')]
    tgt = seqs[0]
    gt.insert_child(tgt, set_vars({'q': '1'}), position='first')
    d = gt.path_diff()
    added = [p for p, k in d.items() if k == 'added']
    assert len(added) == 1, f'одно добавленное поддерево: {added}'
    assert not any(p.endswith(tgt.node.get('Name', '')) for p in d), \
        'родитель НЕ помечен — гранулярность'
    gt.check_intent_coverage(d)


@check
def path_diff_catches_rogue_mutation():
    gt = GraftTree.load(V18)
    first_var = next(iter(gt.tree['VARIABLES']))
    gt.tree['VARIABLES']['rogue'] = dict(gt.tree['VARIABLES'][first_var])
    try:
        gt.check_intent_coverage(gt.path_diff())
        raise AssertionError('самовольная мутация должна отказать')
    except GraftError as e:
        assert 'rogue' in str(e)


@check
def delta_validate_blocks_only_new():
    gt = GraftTree.load(V17)  # база: 0 ошибок
    ref = gt.locate_all(kind='SetVariableActivity')[0]
    gt.sv_set(ref, 'und1', '{{={=Variable:und1} + 1}}')
    errs = gt.delta_validate()
    assert errs, 'undeclared-переменная = новая ошибка'
    gt2 = GraftTree.load(os.path.join(ETALONS, 'notify-history-canonical.bpt'))
    new = gt2.delta_validate()
    assert not new, 'известные висячки базы (Act_12) — не новые'


# --- T5: form-lint ------------------------------------------------------------

@check
def form_lint_key_case():
    gt = GraftTree.load(V18)
    ref = gt.locate_all(kind='SetVariableActivity')[0]
    gt.props_set(ref, 'variablevalue', {'x': 1})
    e, _w = gt.form_lint()
    hits = [x for x in e if 'FL-01' in x and 'variablevalue' in x]
    assert hits and 'VariableValue' in hits[0], \
        'case-fold ERROR с подсказкой каноничного ключа'


@check
def form_lint_children_none():
    gt = GraftTree.load(V18)
    node = copy.deepcopy(gt.locate_all(kind='SetVariableActivity')[0].node)
    node['Children'] = None
    node['Name'] = None
    seqs = [s for s in gt.locate_all(kind='SequenceActivity')
            if s.node.get('Children')]
    gt.insert_child(seqs[0], node, position='last')
    e, _w = gt.form_lint()
    assert any('FL-02' in x and 'Children' in x for x in e)


@check
def form_lint_coupled_and_foreach_and_default():
    gt = GraftTree.load(V18)
    deal = gt.locate(kind='CrmGetDynamicInfoActivity',
                     name='TP_09', expect=1)
    del deal.props['DynamicEntityFields']  # coupled: ключ без пары
    # конструктор for_each теперь режет Bogus на сборке (FOR_EACH_SOURCES);
    # FL-05 ловит уже-собранное дерево (правки graft-ом/сырой хирургией)
    fe = {'Type': 'ForEachActivity', 'Name': None, 'Activated': 'Y',
          'Node': None,
          'Properties': {'Variable': 'items', 'Object': 'Bogus',
                         'Title': 'T'},
          'Children': []}
    seqs = [s for s in gt.locate_all(kind='SequenceActivity')
            if s.node.get('Children')]
    gt.insert_child(seqs[0], fe, position='last')
    gt.declare('VARIABLES', {'bad_multi': {'Multiple': '1',
                                           'Default': 'x'}})
    e, _w = gt.form_lint()
    assert any('FL-03' in x for x in e), 'coupled-keys'
    assert any('FL-05' in x and 'Bogus' in x for x in e), 'ForEach-режим'
    assert any('FL-04' in x and 'bad_multi' in x for x in e), \
        'multi Default строкой (кейс mgr)'


@check
def form_lint_corpus_baseline_clean():
    """Живые каноны fixtures FL-чисты (реестр = корпус)."""
    for f in sorted(glob.glob(os.path.join(FIX_BOOKING_CHAIN, '*.bpt'))):
        e, w = GraftTree.load(f).form_lint()
        assert not e, f'{os.path.basename(f)}: {e[:2]}'
        assert not w, f'{os.path.basename(f)}: {w[:2]}'


@check
def forms_registry_regenerable():
    """Реестр bpt_graft_forms.py актуален корпусу (харвест-сверка).
    Публичный контур: harvest_forms.py исключён (sync-политика) — SKIP."""
    harvest = os.path.join(HERE, 'harvest_forms.py')
    if not os.path.exists(harvest):
        print('[SKIP] harvest_forms.py вне дерева (sync-политика)', end='; ')
        return
    r = subprocess.run(
        [PY, harvest, '--check'],
        capture_output=True, text=True, timeout=120, cwd=BPT)
    assert r.returncode == 0, r.stdout + r.stderr


# --- T6: резолвер --------------------------------------------------------------

@check
def resolver_historical_v11_v12_case():
    """TODO №7: pf_hst/pf_gross undeclared в v11/v12 — ловятся."""
    v12 = GraftTree.load(os.path.join(FIX_BOOKING_CHAIN, 'booking-chain_v12.bpt')) \
        if os.path.exists(os.path.join(FIX_BOOKING_CHAIN, 'booking-chain_v12.bpt')) else None
    if v12 is None:
        return  # фикстура v12 не входит в мин-набор; кейс ниже инжектом
    assert any('pf_' in x for x in v12.resolve_refs())


@check
def resolver_undeclared_and_manifest_and_clean():
    gt = GraftTree.load(V19)
    assert gt.resolve_refs() == [], 'v19 канон чист'
    ref = gt.locate_all(sv_keys=['eg_total'])[0]
    gt.sv_set(ref, 'zz_und', '{{={=Variable:zz_und} + 1}}')
    hits = [x for x in gt.resolve_refs() if 'zz_und' in x]
    assert hits and 'нет в VARIABLES' in hits[0]
    gt2 = GraftTree.load(V19)
    ref2 = gt2.locate_all(sv_keys=['eg_total'])[0]
    gt2.sv_set(ref2, 'zz_id', '{=TP_09:ID}')
    idh = [x for x in gt2.resolve_refs() if 'ID' in x and 'манифест' in x]
    assert idh, 'ID-причуда: родное поле требует манифеста'
    gt3 = GraftTree.load(V19)
    ref3 = gt3.locate_all(sv_keys=['eg_total'])[0]
    gt3.sv_set(ref3, 'zz_uf', '{=TP_09:UF_CRM_FREE}')
    assert not any('UF_CRM_FREE' in x for x in gt3.resolve_refs()), \
        'UF_* без манифеста — свободны (боевой факт v19)'


@check
def fn10_if_bare_ref_and_flow_model():
    """FN-10: голый реф в if-ветке при чужом условии — literal-мусор
    (кейс v22); flow-слой: r-паттерн/гвард тем же if/round — чисто."""
    gt = GraftTree.load(V19)
    sv = gt.locate_all(sv_keys=['eg_total'])[0]
    gt.sv_set(sv, 'zz_case',
              "{{=if({=Variable:eg_total} = 0, '', {=Variable:bnum_val})}}")
    gt.sv_set(sv, 'zz_ok1',
              "{{=if({=Variable:bnum_val} = '', '-', {=Variable:bnum_val})}}")
    gt.sv_set(sv, 'zz_ok2',
              "{{=if({=Variable:eg_raw} = 0, '-', round({=Variable:eg_raw},2))}}")
    errs = gt.delta_validate()
    assert any('FN-10' in x and 'zz_case' in x for x in errs), \
        'v22-кейс обязан ловиться'
    assert not any('FN-10' in x and 'zz_ok' in x for x in errs), \
        'безопасные формы (гвард/r-паттерн) — чисты'


@check
def fn10_canonical_regressions_in_corpus():
    """Каноны-регрессы project-x ловятся: v20 (obj_titles/license_txt),
    v22 (bnum_val); эталоны — только известная date-window-branch (V_START).
    Публичный контур: внешний архив отсутствует — SKIP."""
    import zlib
    import bpt_graft as G
    from bpt_build import validate as bb_validate
    archive = '<внешний приватный архив>/bpt'
    if not os.path.isdir(archive):
        print('[SKIP] внешний архив канонов вне дерева', end='; ')
        return
    for fname, expect_fn10 in (
            ('booking-chain_v20.bpt', 2), ('booking-chain_v22.bpt', 1)):
        t = G.php_unserialize_exact(zlib.decompress(open(
            os.path.join(archive, fname), 'rb').read()))
        e, _w = bb_validate(t)
        hits = [x for x in e if 'FN-10' in x]
        assert len(hits) == expect_fn10, f'{fname}: {len(hits)} != {expect_fn10}'
    e, _w = bb_validate(G.php_unserialize_exact(zlib.decompress(open(
        os.path.join(ETALONS, 'date-window-branch.bpt'), 'rb').read())))
    assert sum(1 for x in e if 'FN-10' in x) == 1, \
        'date-window-branch V_START — известная находка (детектор ловит, не чинит)'


@check
def save_blocks_v22_regression():
    """save() блокирует воспроизведение v22-баги (FN-10 delta)."""
    import importlib.util
    gt = GraftTree.load(os.path.join(FIX_BOOKING_CHAIN, 'booking-chain_v21.bpt'))
    rda = gt.locate(kind='SetVariableActivity',
                    name='TP_08', expect=1)
    Z = '{=Variable:eg_raw} = 0'
    gt.sv_set(rda, {'rd_eg_bnum': ['{=Variable:rd_eg_bnum}${{=if(' + Z
                                   + ", '', {=Variable:bnum_val})}}"]})
    try:
        gt.save(os.path.join(TMP, 'v22_bug.bpt'))
        raise AssertionError('FN-10-регресс обязан блокировать save')
    except GraftError:
        pass
    _ = importlib.util  # silence linter


# --- T7: save / CLI ---------------------------------------------------------

@check
def save_full_contract_and_repro():
    gt = GraftTree.load(V17)
    ctx = gt.locate_all(
        kind='SetVariableActivity',
        pred=lambda p: 'oe_percent' in str(
            (p.get('VariableValue') or {}).get('oe_subtotal', '')),
        expect=1)
    gt.sv_replace(
        'oe_hst',
        '{=TP_04:UF_CRM_45_1783485570}*0.13',
        '({=TP_04:UF_CRM_45_1783485570}'
        '*{=Variable:oe_percent})*0.13', expect=1, refs=ctx)
    dst = os.path.join(TMP, 'v18_by_save.bpt')
    n = gt.save(dst, repro=V18)
    assert n == os.path.getsize(V18), 'byte-repro сверяет размер'


@check
def save_rejects_rogue_and_undeclared_and_fl():
    gt = GraftTree.load(V19)
    gt.tree['TEMPLATE'][0]['Properties']['rogue_key'] = 'x'  # вне intent
    try:
        gt.save(os.path.join(TMP, 'x1.bpt'))
        raise AssertionError('rogue-мутация должна блокировать save')
    except GraftError:
        pass
    gt2 = GraftTree.load(V19)
    ref = gt2.locate_all(sv_keys=['eg_total'])[0]
    gt2.sv_set(ref, 'u1', '{{={=Variable:u1} + 1}}')
    try:
        gt2.save(os.path.join(TMP, 'x2.bpt'))
        raise AssertionError('undeclared должна блокировать save')
    except GraftError:
        pass
    gt3 = GraftTree.load(V19)
    ref3 = gt3.locate_all(kind='SetVariableActivity')[0]
    gt3.props_set(ref3, 'title', 'X')  # case-fold опечатка
    try:
        gt3.save(os.path.join(TMP, 'x3.bpt'))
        raise AssertionError('FL-01 case-fold должна блокировать save')
    except GraftError:
        pass
    assert not glob.glob(os.path.join(TMP, 'x*.bpt')), \
        'отказ = ничего не записано'


@check
def cli_gates_contract():
    ser = os.path.join(BPT, 'bpt_graft.py')
    r_usage = subprocess.run([PY, ser], capture_output=True, text=True)
    assert r_usage.returncode == 2 and 'usage' in r_usage.stderr.lower()
    r_ok = subprocess.run([PY, ser, 'gates', V18, V19],
                          capture_output=True, text=True)
    assert r_ok.returncode == 0, r_ok.stdout + r_ok.stderr
    # порча: самодельный undeclared-инжект
    bad = os.path.join(TMP, 'rogue_v19.bpt')
    shutil.copy(V19, bad + '.orig')
    import zlib
    tree = bpt_graft.php_unserialize_exact(
        zlib.decompress(open(V19, 'rb').read()))

    def inj(a):
        if isinstance(a, list):
            for ch in a:
                inj(ch)
            return
        if a.get('Type') == 'SetVariableActivity':
            vv = a.get('Properties', {}).get('VariableValue', {})
            if 'eg_total' in vv:
                vv['zz_rogue'] = '{{={=Variable:zz_rogue} + 1}}'
        for ch in a.get('Children', []) or []:
            inj(ch)

    inj(tree['TEMPLATE'][0])
    open(bad, 'wb').write(
        zlib.compress(php_serialize(tree), 9))
    r_bad = subprocess.run([PY, ser, 'gates', V19, bad],
                           capture_output=True, text=True)
    assert r_bad.returncode == 1, 'undeclared-патч = exit 1'
    assert 'zz_rogue' in r_bad.stdout


# --- T8: кейс-переходы booking-chain байт-в-байт ----------------------------------------

@check
def case_v17_to_v18_byte_exact():
    r, dst = run_case('patch_v17_to_v18')
    assert r.returncode == 0, r.stderr[-500:]
    assert open(dst, 'rb').read() == open(V18, 'rb').read()


@check
def case_v18_to_v19_byte_exact():
    r, dst = run_case('patch_v18_to_v19')
    assert r.returncode == 0, r.stderr[-500:]
    assert open(dst, 'rb').read() == open(V19, 'rb').read()


@check
def case_v20p_to_v21_byte_exact():
    r, dst = run_case('patch_v20p_to_v21')
    assert r.returncode == 0, r.stderr[-500:]
    assert open(dst, 'rb').read() == open(
        os.path.join(FIX_BOOKING_CHAIN, 'booking-chain_v21.bpt'), 'rb').read()


@check
def case_v21_to_v22_byte_exact():
    """Канон-РЕГРЕСС: v22 несёт FN-10-багу (голый {=Variable:bnum_val}
    в if-ветке) — воспроизводится save_raw (гейты обязаны блокировать);
    лекарство — case_v21_to_v23 (bnum_r через IfElse-активность)."""
    r, dst = run_case('patch_v21_to_v22')
    assert r.returncode == 0, r.stderr[-500:]
    assert open(dst, 'rb').read() == open(
        os.path.join(FIX_BOOKING_CHAIN, 'booking-chain_v22.bpt'), 'rb').read()
    assert 'RAW' in r.stdout, 'кейс обязан писать через save_raw'


@check
def case_v21_to_v23_byte_exact():
    """Паттерн-лекарство: полный save-контракт (FN-10 чист — bnum_r
    через IfElse, прочерки в колонках) + байт-в-байт канону v23."""
    r, dst = run_case('patch_v21_to_v23')
    assert r.returncode == 0, r.stderr[-500:]
    assert open(dst, 'rb').read() == open(
        os.path.join(FIX_BOOKING_CHAIN, 'booking-chain_v23.bpt'), 'rb').read()


# --- докрутки ревью v24: dedup / repro-диагностика / gen-сахар / CLI ------


@check
def insert_child_dedup_catches_double_patch():
    """Класс прод-фоллов v24: повторная вставка поддерева поверх живой
    базы (дубль bnum_r). dedup=True отказывает по Type+Title в точке."""
    gt = GraftTree.load(os.path.join(FIX_BOOKING_CHAIN, 'booking-chain_v23.bpt'))
    dup = {'Type': 'IfElseActivity', 'Name': None, 'Activated': 'Y',
           'Node': None,
           'Properties': {'Title': 'bnum_r', 'EditorComment': ''},
           'Children': []}
    anchor = gt.locate(kind='IfElseActivity', title='bnum fallback',
                       expect=1)
    try:
        gt.insert_child(anchor, dup, position='after', dedup=True)
        raise AssertionError('дубль bnum_r обязан ловиться (dedup)')
    except GraftError as e:
        assert 'dedup' in str(e) and 'bnum_r' in str(e)


@check
def repro_fail_carries_semantic_diff():
    """byte-repro fail с semantic-diff подсказкой (стек bpt_export):
    ручной подсчёт узлов не нужен."""
    gt = GraftTree.load(V19)
    ref = gt.locate_all(sv_keys=['eg_total'])[0]
    gt.sv_set(ref, 'zz_probe', 'x')
    try:
        gt.save(os.path.join(TMP, 'repro_fail.bpt'), repro=V18)
        raise AssertionError('repro против чужого канона обязан провалиться')
    except GraftError as e:
        assert 'semantic-diff' in str(e), 'диагностика обязана приложиться'


@check
def generators_values_sugar_with_skip():
    gt = GraftTree.load(V19)
    n = gt.generators_values({'zz_doc': '{=Variable:zz_doc}'},
                             skip_tpl=('13',))
    assert n == 4, f'4 генератора кроме OLD 13, троннуто {n}'
    n_all = GraftTree.load(V19).generators_values({'zz': '1'})
    assert n_all == 5
    vals = gt.locate_all(kind='CrmGenerateEntityDocumentActivity')
    for g in vals:
        if str(g.props.get('TemplateId')) == '13':
            assert 'zz_doc' not in g.props.get('Values', {})


@check
def cli_patch_micro_contract():
    ser = os.path.join(BPT, 'bpt_graft.py')
    dst = os.path.join(TMP, 'cli_micro.bpt')
    r = subprocess.run(
        [PY, ser, 'patch', V19, dst,
         '--sv', 'TP_13.zz_cli=7',
         '--declare', 'zz_cli',
         '--gen', 'zz_doc={=Variable:zz_cli}', '--skip-tpl', '13'],
        capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr[-400:]
    assert os.path.exists(dst) and 'intent' in r.stdout
    # гейты не спят: {=Variable:…} в необъявленную через CLI = отказ
    # (литерал в необъявленную — не использование, проходит)
    r2 = subprocess.run(
        [PY, ser, 'patch', V19, os.path.join(TMP, 'cli_bad.bpt'),
         '--sv', 'TP_13.zz_und={=Variable:zz_und}'],
        capture_output=True, text=True, timeout=120)
    assert r2.returncode != 0 and 'zz_und' in r2.stderr, r2.stderr[-200:]
    r3 = subprocess.run([PY, ser, 'patch', V19], capture_output=True,
                        text=True)
    assert r3.returncode == 2 and 'usage' in (r3.stderr + r3.stdout).lower()


# --- корпус-инвариант ----------------------------------------------------------

@check
def corpus_invariant_serialize_all_etalons():
    """N/N закоммиченного корпуса; N не хардкодить (растёт после
    онбординга новых эталонов)."""
    files = sorted(glob.glob(os.path.join(ETALONS, '*.bpt')))
    assert len(files) >= 12, 'корпус не мог усохнуть'
    r = subprocess.run(
        [PY, os.path.join(BPT, 'bpt_serialize.py'), '--all', ETALONS,
         '--byte-exact'],
        capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stdout + r.stderr
    assert f'byte-exact: {len(files)}/{len(files)}' in r.stdout, r.stdout


@check
def ruff_clean_graft_surface():
    surface = [
        os.path.join(BPT, 'bpt_graft.py'),
        os.path.join(BPT, 'bpt_graft_forms.py'),
        os.path.join(BPT, 'bpt_build.py'),
        os.path.join(BPT, 'bpt_serialize.py'),
        os.path.join(HERE, 'harvest_forms.py'),
        os.path.join(HERE, 'cases_booking_chain', 'patch_v17_to_v18.py'),
        os.path.join(HERE, 'cases_booking_chain', 'patch_v18_to_v19.py'),
        os.path.join(HERE, 'cases_booking_chain', 'patch_v20p_to_v21.py'),
        os.path.join(HERE, 'cases_booking_chain', 'patch_v21_to_v22.py')]
    surface = [f for f in surface if os.path.exists(f)]  # sync-политика: harvest вне публичного дерева
    r = subprocess.run(
        ['ruff', 'check'] + surface,
        capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr


# --- волна 2 (аудит 2026-10-16): кластер graft-канона -------------------------

def _fe_node(obj: str) -> dict:
    return {'Type': 'ForEachActivity', 'Name': None, 'Activated': 'Y',
            'Node': None,
            'Properties': {'Variable': 'items', 'Object': obj,
                           'Title': 'T'},
            'Children': []}


@check
def fl05_domain_modes_and_tree_names():
    """№1: FL-05 домен = режимы FOR_EACH_SOURCES (единый источник —
    bpt_build) ∪ Name узлов дерева; A-реестр корпуса не арбитр."""
    gt = GraftTree.load(V18)
    seqs = [s for s in gt.locate_all(kind='SequenceActivity')
            if s.node.get('Children')]
    gt.insert_child(seqs[0], _fe_node('Document'), position='last')
    live_name = gt.locate_all(kind='SetVariableActivity')[0].name
    gt.insert_child(seqs[0], _fe_node(live_name), position='last')
    e, _w = gt.form_lint()
    assert not any('FL-05' in x for x in e), e


@check
def rename_rebind_then_save_passes():
    """№2: rename_rebind существующего узла проходит полный save-контракт
    (path-diff rename-осведерён: пары по финальному имени, дифф до
    .Name); сырая правка чужого узла рядом — по-прежнему отказ."""
    gt = GraftTree.load(V18)
    ref = gt.locate_all(kind='SetVariableActivity')[0]
    old = ref.name
    new = gt.rename_rebind(ref)
    assert new != old
    changed = gt.path_diff()
    assert list(changed) == [ref.path + '.Name'], changed
    dst = os.path.join(TMP, 'wave2_rename.bpt')
    gt.save(dst)
    back = GraftTree.load(dst)
    assert back.locate(name=new) is not None
    # негатив: самовольная мутация мимо graft-операций — гейт не спит
    gt2 = GraftTree.load(V18)
    ref2 = gt2.locate_all(kind='SetVariableActivity')[0]
    gt2.rename_rebind(ref2)
    ref2.node['Properties']['Title'] = 'взлом'
    try:
        gt2.save(os.path.join(TMP, 'wave2_rename_bad.bpt'))
        raise AssertionError('самовольная правка прошла save')
    except GraftError as e:
        assert 'вне intent' in str(e), e


@check
def rename_then_move_composition():
    """№2: композиция rename+move — intent переименования резолвится по
    текущему пути узла, сдвиг соседей покрыт move-range."""
    gt = GraftTree.load(V18)
    ref = gt.locate_all(kind='SetVariableActivity')[0]
    gt.rename_rebind(ref)
    anchor = gt.locate_all(kind='SequenceActivity')[0]
    gt.move(ref, anchor, position='first')
    gt.save(os.path.join(TMP, 'wave2_ren_move.bpt'))


@check
def deleted_name_never_reissued():
    """№3: delete выводит имя из оборота — alloc не перевыдаёт
    (аудит: {=SetVar_0:…} тихо перепривязывается на чужой узел);
    явная вставка с выведенным именем — GraftError."""
    base = bpt_build.scaffold(
        [bpt_build.act('SetVariableActivity', 'База', {'VariableValue': {}},
                       name='SetVar_0')],
        {}, {'ID': 'string'})
    src = os.path.join(TMP, 'wave2_ret_base.bpt')
    bpt_build.save(base, src)
    gt = GraftTree.load(src)
    root = gt.locate(kind='SequentialWorkflowActivity')
    gt.delete(gt.locate(name='SetVar_0'))
    fresh = bpt_build.set_vars({'a': '1'})
    fresh['Name'] = None
    ref = gt.insert_child(root, fresh, position='last')
    assert ref.name != 'SetVar_0', \
        f'перевыдача выведенного имени: {ref.name!r}'
    gt.save(os.path.join(TMP, 'wave2_ret_ok.bpt'))
    zombie = bpt_build.set_vars({'b': '1'})
    zombie['Name'] = 'SetVar_0'
    try:
        gt.insert_child(root, zombie, position='last')
        raise AssertionError('retired-имя молча принято')
    except GraftError as e:
        assert 'SetVar_0' in str(e), e


def main() -> int:
    fails = 0
    for fn in CHECKS:
        try:
            fn()
            print(f'PASS {fn.__name__}')
        except Exception as e:  # noqa: BLE001 — жгут: любая ошибка = FAIL
            fails += 1
            print(f'FAIL {fn.__name__}: {e!r}')
    print(f'--- {len(CHECKS) - fails}/{len(CHECKS)} passed')
    return 1 if fails else 0


@check
def save_repro_fail_leaves_dst_untouched():
    """dst записывался ДО сверки byte-repro: провал оставлял
    перезаписанный файл. Сверка — до записи (аудит)."""
    import os as _os
    src = _os.path.join(ETALONS, 'date-window-branch.bpt')
    dst = _os.path.join(TMP, 'repro_fail.bpt')
    if _os.path.exists(dst):
        _os.remove(dst)
    gt = bpt_graft.GraftTree.load(src)
    try:
        gt.save(dst, repro=_os.path.join(ETALONS, 'notify-history.bpt'))
    except bpt_graft.GraftError:
        pass
    else:
        raise AssertionError('repro против чужого канона прошёл')
    assert not _os.path.exists(dst), 'dst записан до провала repro'


@check
def graft_cli_errors_not_traceback():
    """CLI gates/patch: битый/несуществующий файл — GraftError/exit 1,
    не traceback (аудит: load/CLI exit-контракт)."""
    import subprocess
    out = os.path.join(TMP, 'cli_out.bpt')
    for argv in (['gates', '/nonexistent.bpt', '/nonexistent2.bpt'],
                 ['patch', '/nonexistent.bpt', out,
                  '--sv', 'A.Title=1']):
        r = subprocess.run([sys.executable,
                            os.path.join(BPT, 'bpt_graft.py')] + argv,
                           capture_output=True, text=True, cwd=BPT)
        assert r.returncode == 1, f'{argv[0]}: exit {r.returncode}, ждём 1'
        assert 'Traceback' not in r.stderr, r.stderr[-300:]


@check
def gates_cli_warn_is_not_failure():
    """Асимметрия gates-CLI: FL-WARN рушил exit в 1, save на WARN лишь
    логирует. WARN — не отказ: exit 1 только ошибки/refs."""
    import subprocess
    # WARN-only триггер: неизвестный Properties-ключ (FL-01 warn-класс)
    src = os.path.join(ETALONS, 'date-window-branch.bpt')
    gt = bpt_graft.GraftTree.load(src)
    ref = gt.locate_all(kind='SetVariableActivity')[0]
    ref.node['Properties']['ZzzUnknownProbeKey'] = '1'
    warn_file = os.path.join(TMP, 'fl_warn.bpt')
    with open(warn_file, 'wb') as f:
        f.write(compress_bpt(php_serialize(gt.tree)))
    r = subprocess.run([sys.executable,
                        os.path.join(BPT, 'bpt_graft.py'),
                        'gates', src, warn_file],
                       capture_output=True, text=True, cwd=BPT)
    assert 'WARN' in r.stdout, f'WARN-триггер не сработал: {r.stdout[-300:]}'
    assert r.returncode == 0, f'WARN-only = exit {r.returncode}, ждём 0'


@check
def var_rename_rebinds_body_and_sections():
    """var_rename: ключ VARIABLES + все привязки в теле (SV-ключ, ссылка
    {=Variable:}, propertyvariablecondition) одним предикатом; expect
    проверяется ДО мутации (отказ атомарен)."""

    def build():
        return bpt_build.scaffold(
            children=[
                set_vars({'variable1': '10',
                          'copy': '{=Variable:variable1}'}),
                bpt_build.if_else([
                    ('a', ['variable1', '=', '10'],
                     [bpt_build.set_vars({'flag': '1'})]),
                    ('b', None, []),
                ]),
            ],
            variables={
                'variable1': bpt_build.variable('variable1', 'double'),
                'copy': bpt_build.variable('copy', 'double'),
                'flag': bpt_build.variable('flag', 'int'),
            },
            doc_fields={'ID': 'int'},
        )

    gt = GraftTree(build(), 9)
    assert gt._var_occurrences('variable1') == 3, \
        'ожидание: SV-ключ + ссылка + ряд условия'
    n = gt.var_rename('variable1', 'mgmt_fee_pct')
    assert n == 3, f'перепривязано {n}, ждём 3'
    dst = os.path.join(TMP, 'var_rename.bpt')
    gt.save(dst)
    back = GraftTree.load(dst)
    assert 'mgmt_fee_pct' in back.tree['VARIABLES']
    assert 'variable1' not in back.tree['VARIABLES']
    blob = json.dumps(back.tree, ensure_ascii=False)
    assert '{=Variable:mgmt_fee_pct}' in blob
    assert '{=Variable:variable1}' not in blob
    cond = back.locate(kind='IfElseActivity').node['Children'][0][
        'Properties']['propertyvariablecondition']
    assert cond[0][0] == 'mgmt_fee_pct', cond

    gt2 = GraftTree(build(), 9)
    try:
        gt2.var_rename('variable1', 'other', expect=99)
    except GraftError:
        pass
    else:
        raise AssertionError('expect-мисматч должен отказывать')
    assert 'variable1' in gt2.tree['VARIABLES'], \
        'отказ expect должен быть атомарным'
    assert 'other' not in gt2.tree['VARIABLES']


if __name__ == '__main__':
    sys.exit(main())

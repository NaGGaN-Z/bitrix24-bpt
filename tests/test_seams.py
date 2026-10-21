#!/usr/bin/env python3
"""tests/test_seams.py — seam-жгут (аудит 2026-10-16, волна 2, Шаг 0).

Связки модулей, не покрытые per-module-тестами (10/10 критикалов
аудита — на стыках). Инварианты сквозные, каждый чек независим:
  1) kitchen-sink срез конструкторов build → GraftTree.load().save()
     без FL-ERROR (поймал бы FL-04/FL-05 волны 2);
  2) канон-домены: каждый режим FOR_EACH_SOURCES проходит graft
     form-lint (инвариант единого источника канона ForEach) + живой
     корпус-эталон с Object=A-именем чист;
  3) graft-output → serialize byte-roundtrip + идемпотентность save;
  4) build-tree → diagram без дропов узлов (подсчёт по типам рендера).

Запуск: python3 tests/test_seams.py (из каталога bpt). Zero-dep.
Полнота ФОРМ конструкторов — зона test_bpt_build.py; здесь — связка
модулей (build → graft → serialize → diagram).
"""
import os
import re
import sys
import tempfile
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
BPT = os.path.dirname(HERE)
sys.path.insert(0, BPT)

os.environ.setdefault('GRAFT_LOG_LEVEL', 'QUIET')

import bpt_build  # noqa: E402
import bpt_diagram  # noqa: E402
from bpt_graft import GraftTree  # noqa: E402
from bpt_serialize import php_serialize, php_unserialize_exact  # noqa: E402

ETALONS = os.path.join(BPT, 'etalons')
TMP = tempfile.mkdtemp(prefix='bpt_t_seams_')

CHECKS = []


def check(fn):
    CHECKS.append(fn)
    return fn


def kitchen_sink() -> dict:
    """Широкий срез конструкторов build: flow, ветвления, циклы,
    итераторы (оба спорных режима Object), задания, уведомления,
    секции с мульти-переменной (форма Default по Multiple)."""
    return bpt_build.scaffold(
        children=[
            bpt_build.set_vars({'done': 'Y'}),
            bpt_build.get_user(['1']),
            bpt_build.if_else([
                ('да', ['flag', '=', '5'], [bpt_build.set_vars({'x': '1'})]),
                ('нет', None, [bpt_build.terminate()]),
            ]),
            bpt_build.while_loop(
                ['i', '<', '3'],
                [bpt_build.set_vars({'i': '{{={=Variable:i}+1}}'})]),
            bpt_build.for_each(
                'ids', [bpt_build.set_vars({'seen': '{=Variable:ids}'})],
                source='Variable'),
            bpt_build.for_each(
                'docs', [bpt_build.timeline_comment('итерация')],
                source='Document'),
            bpt_build.approve(['user_1'], 'Согласование'),
            bpt_build.request_info(
                ['user_1'],
                [{'Title': 'Комментарий', 'Name': 'c', 'Type': 'string'}],
                'Уточнение'),
            bpt_build.review(['user_1'], 'Ознакомление'),
            bpt_build.delay(30),
            bpt_build.change_stage('NEW'),
            bpt_build.event_add('Событие'),
            bpt_build.control_notify('Контроль'),
            bpt_build.im_notify(['1'], 'Готово'),
        ],
        variables={
            'done': bpt_build.variable('done', 'string'),
            'flag': bpt_build.variable('flag', 'int'),
            'i': bpt_build.variable('i', 'int'),
            'x': bpt_build.variable('x', 'string'),
            'seen': bpt_build.variable('seen', 'string'),
            'ids': bpt_build.variable('ids', 'string', multiple='1'),
        },
        doc_fields={'ID': 'string'})


# --- 1: build → graft save -------------------------------------------------

@check
def kitchen_sink_survives_graft_surgery():
    """Типовой выход билдера доступен для graft-хирургии: build save →
    GraftTree.load → save (полный гейт-контракт) без FL-ERROR."""
    src = os.path.join(TMP, 'ks.bpt')
    dst = os.path.join(TMP, 'ks_graft.bpt')
    bpt_build.save(kitchen_sink(), src)
    GraftTree.load(src).save(dst)
    e, _w = GraftTree.load(dst).form_lint()
    assert e == [], e


# --- 2: канон-домены ForEach ----------------------------------------------

@check
def for_each_domain_single_source():
    """Каждый режим FOR_EACH_SOURCES (build) проходит FL-05 graft —
    инвариант единого источника канона (аудит №1)."""
    for mode in sorted(bpt_build.FOR_EACH_SOURCES):
        t = bpt_build.scaffold(
            [bpt_build.for_each(
                'ids', [bpt_build.set_vars({'x': '1'})], source=mode)],
            {'ids': bpt_build.variable('ids', 'string', multiple='1')},
            {'ID': 'string'})
        p = os.path.join(TMP, f'fe_{mode}.bpt')
        bpt_build.save(t, p)
        e, _w = GraftTree.load(p).form_lint()
        assert not any('FL-05' in x for x in e), (mode, e)


@check
def for_each_name_mode_corpus_etalon():
    """Корпус-эталон с Object=<имя активности> (notify-history-canonical,
    A73347_…) FL-чист — имя-режим легитимен на живом каноне."""
    e, _w = GraftTree.load(
        os.path.join(ETALONS, 'notify-history-canonical.bpt')).form_lint()
    assert not any('FL-05' in x for x in e), e


# --- 3: graft-output → serialize ------------------------------------------

@check
def graft_output_serialize_roundtrip():
    """Выход graft save: zlib+php roundtrip байт-в-байт; повторный
    проход того же дерева — те же байты (детерминизм)."""
    t = bpt_build.scaffold(
        [bpt_build.set_vars({'done': 'Y'})],
        {'done': bpt_build.variable('done', 'string')}, {'ID': 'string'})
    src = os.path.join(TMP, 'rt_src.bpt')
    dst = os.path.join(TMP, 'rt_graft.bpt')
    bpt_build.save(t, src)
    GraftTree.load(src).save(dst)
    blob = open(dst, 'rb').read()
    payload = zlib.decompress(blob)
    tree = php_unserialize_exact(payload)
    assert php_serialize(tree) == payload, 'roundtrip байт-в-байт'
    dst2 = os.path.join(TMP, 'rt_graft2.bpt')
    GraftTree.load(dst).save(dst2)
    assert open(dst2, 'rb').read() == blob, 'идемпотентность save'


# --- 4: build-tree → diagram ----------------------------------------------

def _walk_contract(node, out):
    if isinstance(node, dict):
        if node.get('ghost'):
            return
        out.append(node.get('type'))
        for c in node.get('children') or []:
            _walk_contract(c, out)
    elif isinstance(node, list):
        for x in node:
            _walk_contract(x, out)


@check
def build_tree_diagram_no_drops():
    """Kitchen-sink дерево рендерится диаграммой без потерь: простые
    узлы → N-ноды, IfElse → ромбы D, контейнеры While/ForEach →
    subgraph L (подсчёт по типам рендера to_mermaid)."""
    src = os.path.join(TMP, 'ks_diag.bpt')
    bpt_build.save(kitchen_sink(), src)
    tree = bpt_diagram.load_tree(src)
    types = []
    _walk_contract(tree['root'], types)
    simple = sum(1 for t in types if t not in (
        'SequentialWorkflowActivity', 'IfElseActivity',
        'IfElseBranchActivity', 'WhileActivity', 'ForEachActivity',
        'ParallelActivity'))
    diamonds = sum(1 for t in types if t == 'IfElseActivity')
    loops = sum(1 for t in types if t in ('WhileActivity',
                                          'ForEachActivity'))
    mm = bpt_diagram.to_mermaid(tree)
    n_nodes = len(re.findall(r'^N\d+\[', mm, re.M))
    d_nodes = len(re.findall(r'^D\d+\{', mm, re.M))
    l_subs = len(re.findall(r'^subgraph L\d+\[', mm, re.M))
    assert n_nodes == simple, f'простые узлы: дерево {simple}, mmd {n_nodes}'
    assert d_nodes == diamonds, f'IfElse: дерево {diamonds}, mmd {d_nodes}'
    assert l_subs == loops, f'контейнеры: дерево {loops}, mmd {l_subs}'


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


if __name__ == '__main__':
    sys.exit(main())

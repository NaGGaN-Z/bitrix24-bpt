#!/usr/bin/env python3
"""tests/test_bpt_export.py — регресс-жгут S2 (bpt_export: малформ-парсинг,
strict-decode, call_retry, курсорная пагинация fetch_all с чекпоинтом и
сверкой total) + probe-журнал (волна 2, T5/T6: контракт T1, курсор,
отчёт-хронология, exit-контракт). Запуск: python3 tests/test_bpt_export.py.
Zero-dep."""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import urllib.error
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
BPT = os.path.dirname(HERE)
sys.path.insert(0, BPT)

import bpt_export  # noqa: E402

TMP = tempfile.mkdtemp(prefix='bpt_t_exp_')

CHECKS = []


def check(fn):
    CHECKS.append(fn)
    return fn


def fake_call(pages, log):
    def call(wh, method, params):
        log.append(params)
        item = pages.pop(0)
        if isinstance(item, Exception):
            raise item
        return item
    return call


def page(ids, total):
    return {'result': [{'ID': str(i), 'NAME': f't{i}'} for i in ids],
            'total': total}


@check
def malformed_tokens_valueerror():
    for bad in (b'i:zz;', b'd:NAN;', b'b:2;'):
        try:
            bpt_export.php_unserialize(bad)
        except ValueError:
            continue
        except Exception as e:  # noqa: BLE001
            raise AssertionError(f'{bad!r} -> {type(e).__name__}, ждал ValueError') from e
        raise AssertionError(f'{bad!r} распарсен молча')


@check
def invalid_utf8_raises():
    try:
        bpt_export.php_unserialize(b's:2:"\xc3\x28";')
    except ValueError:
        return
    raise AssertionError('невалидный UTF-8 прошёл (U+FFFD bake-in)')


@check
def call_retry_survives_query_limit(monkey=bpt_export):
    log = []
    err = RuntimeError('bizproc.workflow.template.list: QUERY_LIMIT_EXCEEDED: '
                       'too many requests')
    ok = page([1], 1)
    original_call, original_sleep = monkey.call, monkey.time.sleep
    delays = []
    monkey.time.sleep = delays.append
    monkey.call = fake_call([err, err, ok], log)
    try:
        resp = monkey.call_retry('http://x', 'm', {})
        assert resp == ok, 'ретрай не вернул ответ'
        assert len(delays) >= 2, f'без backoff: {delays!r}'
        assert delays[1] > delays[0] > 0, f'backoff не растёт: {delays!r}'
    finally:
        monkey.call, monkey.time.sleep = original_call, original_sleep


@check
def call_retry_exhausts_and_skips_non_retryable(monkey=bpt_export):
    original_call, original_sleep = monkey.call, monkey.time.sleep
    monkey.time.sleep = lambda s: None
    calls = []

    def always(wh, method, params):
        calls.append(1)
        raise RuntimeError('x: QUERY_LIMIT_EXCEEDED: slow down')

    monkey.call = always
    try:
        try:
            monkey.call_retry('http://x', 'm', {}, attempts=3)
        except RuntimeError:
            pass
        else:
            raise AssertionError('исчерпание ретраев не подняло ошибку')
        assert len(calls) == 3, f'попыток {len(calls)}, ждал 3'

        calls.clear()

        def fatal(wh, method, params):
            calls.append(1)
            raise RuntimeError('x: WRONG_REQUEST: bad scope')

        monkey.call = fatal
        try:
            monkey.call_retry('http://x', 'm', {}, attempts=3)
        except RuntimeError as e:
            assert 'WRONG_REQUEST' in str(e)
        else:
            raise AssertionError('неретраебельная ошибка проглочена')
        assert len(calls) == 1, f'было {len(calls)} вызовов, ждал 1 (без ретраев)'
    finally:
        monkey.call, monkey.time.sleep = original_call, original_sleep


@check
def call_retry_retries_http_503(monkey=bpt_export):
    original_call, original_sleep = monkey.call, monkey.time.sleep
    monkey.time.sleep = lambda s: None
    ok = page([1], 1)
    monkey.call = fake_call([urllib.error.HTTPError(
        'u', 503, 'Service Unavailable', None, None), ok], [])
    try:
        assert monkey.call_retry('http://x', 'm', {}) == ok
    finally:
        monkey.call, monkey.time.sleep = original_call, original_sleep


@check
def fetch_all_cursor_pagination_and_checkpoint(monkey=bpt_export):
    original_call = monkey.call
    log = []
    monkey.call = fake_call(
        [page(range(1, 51), 60), page(range(51, 61), 60)], log)
    try:
        out = os.path.join(TMP, 'fetch1')
        os.makedirs(out, exist_ok=True)
        templates = monkey.fetch_all('http://x', out)
    finally:
        monkey.call = original_call
    assert len(templates) == 60, f'собрано {len(templates)}'
    assert len(log) == 2, f'запросов {len(log)} (хвостовой не нужен при <50)'
    assert log[1].get('filter') == {'>ID': 50}, \
        f'второй запрос не курсорный: {log[1]!r}'
    assert 'start' not in log[0] and 'start' not in log[1], 'offset-пагинация'
    ckpt = json.load(open(os.path.join(out, 'templates.json')))
    assert len(ckpt) == 60, f'чекпоинт неполон: {len(ckpt)}'


@check
def fetch_all_single_short_page_stops(monkey=bpt_export):
    original_call = monkey.call
    log = []
    monkey.call = fake_call([page(range(1, 11), 10)], log)
    try:
        out = os.path.join(TMP, 'fetch2')
        os.makedirs(out, exist_ok=True)
        templates = monkey.fetch_all('http://x', out)
    finally:
        monkey.call = original_call
    assert len(templates) == 10
    assert len(log) == 1, 'лишний запрос после короткой страницы'


@check
def fetch_all_total_mismatch_is_loud(monkey=bpt_export):
    original_call = monkey.call
    monkey.call = fake_call([page(range(1, 51), 99), page(range(51, 61), 99)], [])
    try:
        try:
            out = os.path.join(TMP, 'fetch3')
            os.makedirs(out, exist_ok=True)
            monkey.fetch_all('http://x', out)
        except RuntimeError as e:
            assert '99' in str(e) or 'total' in str(e).lower(), str(e)
            return
        raise AssertionError('усечение инвентаря против total прошло молча')
    finally:
        monkey.call = original_call


@check
def export_json_faithful_int_keys():
    import bpt_serialize
    blob = zlib.decompress(open(os.path.join(BPT, 'etalons', 'hotel-booking.bpt'), 'rb').read())
    js = bpt_export.dump_template_json(blob)
    tree = bpt_serialize.load_json(js)
    assert tree['DOCUMENT_FIELDS']['CATEGORY_ID']['Options'] == {14: 'Общая воронка'}, \
        tree['DOCUMENT_FIELDS']['CATEGORY_ID']['Options']


@check
def b64_to_zlib_all_levels_and_raw():
    import base64
    payload = b'a:1:{s:3:"foo";s:3:"bar";}'
    for lvl in range(10):
        blob = zlib.compress(payload, lvl)
        got = bpt_export.b64_to_zlib(base64.b64encode(blob).decode())
        assert got == payload, f'level {lvl}: {blob[:2]!r}'
    assert bpt_export.b64_to_zlib(base64.b64encode(payload).decode()) == payload


# ---------- diff-режим (волна 3: структурный диф, иммунный к rename Name) ----------

def _nd(typ, name, props=None, children=None):
    n = {'Type': typ, 'Name': name, 'Activated': 'Y', 'Node': 'n'}
    n['Properties'] = props if props is not None else {}
    n['Children'] = children if children is not None else []
    return n


def _tpl(root, variables=None):
    return {'VERSION': 2, 'TEMPLATE': [root], 'VARIABLES': variables or {},
            'PARAMETERS': {}, 'CONSTANTS': {}, 'DOCUMENT_FIELDS': {}}


def _pair(a, b):
    return bpt_export.diff_templates(_tpl(a), _tpl(b))


ETALONS = os.path.join(BPT, 'etalons')


@check
def diff_identity_key_ignores_values():
    """Гоча наивной сигнатуры: identity БЕЗ значений — правка формулы
    даёт 'изменённая формула', а не removed+added."""
    a = _nd('SetVariableActivity', 'A1', {'Title': 'sv',
                                          'VariableValue': {'x': '1'}})
    b = _nd('SetVariableActivity', 'B1', {'Title': 'sv',
                                          'VariableValue': {'x': '2'}})
    ev, rn = _pair(a, b)
    assert len(ev) == 1 and ev[0]['kind'] == 'sv' and ev[0]['key'] == 'x', ev
    assert not [e for e in ev if e['kind'] in ('added', 'removed')], ev
    assert len(rn) == 1 and rn[0]['old'] == 'A1' and rn[0]['new'] == 'B1'


@check
def diff_variable_rename_anchor_aware():
    """Переименование переменной: identity-якорь SV-ключей нормализуется
    (нет ложных ±узлов), секция VARIABLES не шумит key-level диффом,
    rename репортится ожидаемой секцией, а ссылка в формуле видна как
    sv old→new."""
    a = _nd('SetVariableActivity', 'A',
            {'Title': 'sv',
             'VariableValue': {'variable1': '10',
                               'copy': '{=Variable:variable1}'}})
    b = _nd('SetVariableActivity', 'B',
            {'Title': 'sv',
             'VariableValue': {'mgmt_fee_pct': '10',
                               'copy': '{=Variable:mgmt_fee_pct}'}})
    va = {'variable1': {'Name': 'Management fee (%)', 'Type': 'double',
                        'Default': ''},
          'copy': {'Name': 'copy', 'Type': 'double', 'Default': ''}}
    vb = {'mgmt_fee_pct': {'Name': 'Management fee (%)', 'Type': 'double',
                           'Default': ''},
          'copy': {'Name': 'copy', 'Type': 'double', 'Default': ''}}
    ev, rn = bpt_export.diff_templates(_tpl(a, va), _tpl(b, vb))
    assert not [e for e in ev if e['kind'] in ('added', 'removed')], ev
    assert [e for e in ev if e['kind'] == 'sv' and e['key'] == 'copy'], ev
    assert not [e for e in ev if e['kind'] == 'sect' and
                e['section'] == 'VARIABLES'], ev
    var_rn = [r for r in rn if r.get('kind') == 'variable']
    assert len(var_rn) == 1 and var_rn[0]['old'] == 'variable1' \
        and var_rn[0]['new'] == 'mgmt_fee_pct', var_rn


@check
def diff_identity_anchor_per_type():
    """Якоря по типу (на детях — корни TEMPLATE[0] матчатся безусловно):
    set_vars — ключи маппинга; условия — поля/objects (activity-ref →
    '<Act>'); прочие — Type+Title. Смена якоря = ±узел."""
    def seq(*ch):
        return _tpl(_nd('SequentialWorkflowActivity', 'R', children=list(ch)))
    a = _nd('SetVariableActivity', 'A', {'VariableValue': {'v': '1'},
                                         'Title': 't'})
    b = _nd('SetVariableActivity', 'B',
            {'VariableValue': {'v': '1', 'w': '2'}, 'Title': 't'})
    ev, _ = bpt_export.diff_templates(seq(a), seq(b))
    assert sorted(e['kind'] for e in ev) == ['added', 'removed'], ev
    # условие: mixedcondition с object-ссылкой на активность — rename
    # объекта не рвёт identity (<Act>-нормализация), репортится cond-диффом
    br1 = _nd('IfElseBranchActivity', 'B1',
              {'mixedcondition': [{'object': 'A63498_19089', 'field': 'ID',
                                   'operator': 'empty', 'value': '',
                                   'joiner': '0'}], 'Title': 'br'})
    br2 = _nd('IfElseBranchActivity', 'B2',
              {'mixedcondition': [{'object': 'R999_1', 'field': 'ID',
                                   'operator': 'empty', 'value': '',
                                   'joiner': '0'}], 'Title': 'br'})
    ev, rn = bpt_export.diff_templates(seq(br1), seq(br2))
    cond = [e for e in ev if e['kind'] == 'cond']
    assert not [e for e in ev if e['kind'] in ('added', 'removed')], ev
    assert len(cond) == 1 and cond[0]['prop'] == 'mixedcondition', ev
    # прочие: Title в identity → смена Title = ±узел
    ev, _ = bpt_export.diff_templates(
        seq(_nd('IMNotifyActivity', 'N', {'Title': 'a'})),
        seq(_nd('IMNotifyActivity', 'N', {'Title': 'b'})))
    assert sorted(e['kind'] for e in ev) == ['added', 'removed'], ev


@check
def diff_volatile_fields_are_not_content():
    """Name (→карта переименований), Node, EditorComment, _DesMinimized —
    летучие: их смена не даёт событий. Activated — содержательный."""
    a = _nd('DelayActivity', 'A', {'Title': 'w', 'EditorComment': 'c',
                                   '_DesMinimized': 'Y'})
    b = _nd('DelayActivity', 'B', {'Title': 'w', 'EditorComment': 'other',
                                   '_DesMinimized': 'N'})
    b['Node'] = 'other-node'
    ev, rn = _pair(a, b)
    assert not ev, ev
    assert len(rn) == 1, rn
    b2 = dict(b)
    b2 = _nd('DelayActivity', 'B', {'Title': 'w'})
    b2['Activated'] = 'N'
    ev, _ = _pair(a, b2)
    assert len(ev) == 1 and ev[0]['kind'] == 'activated', ev


@check
def diff_series_order_and_insert():
    """Серии однотипных узлов: вставка в середину не роняет матчинг;
    порядок (а не контент) разводит равные identity-ключи."""
    def sv(n, k, v):
        return _nd('SetVariableActivity', n, {'VariableValue': {k: v},
                                              'Title': ''})
    a = _tpl(_nd('SequentialWorkflowActivity', 'R', children=[sv('A', 'a', '1'), sv('B', 'b', '2')]))
    b = _tpl(_nd('SequentialWorkflowActivity', 'R', children=[sv('A', 'a', '1'), sv('C', 'c', '9'), sv('B', 'b', '2')]))
    ev, _ = bpt_export.diff_templates(a, b)
    assert [e['kind'] for e in ev] == ['added'] and \
        ev[0]['node']['Name'] == 'C', ev
    # равные ключи: матчинг по порядку, значения сведутся попарно
    a2 = _tpl(_nd('SequentialWorkflowActivity', 'R', children=[sv('A', 'a', '1'), sv('B', 'a', '2')]))
    b2 = _tpl(_nd('SequentialWorkflowActivity', 'R', children=[sv('A2', 'a', '2'), sv('B2', 'a', '1')]))
    ev, _ = bpt_export.diff_templates(a2, b2)
    assert sorted(e['kind'] for e in ev) == ['sv', 'sv'], ev
    # пустые дети
    ev, _ = _pair(_nd('SequenceActivity', 'S'), _nd('SequenceActivity', 'S'))
    assert not ev, ev


@check
def diff_sections_key_level():
    """Секции: +ключ / -ключ / ~ключ.под-поле — переименованная
    переменная видна как пара -old/+new."""
    root_a = _nd('SequentialWorkflowActivity', 'R')
    root_b = _nd('SequentialWorkflowActivity', 'R')
    a = _tpl(root_a, {'a': {'Type': 'string'}, 'b': {'Default': ''}})
    b = _tpl(root_b, {'b': {'Default': 'x'}, 'c': {'Type': 'int'}})
    ev, _ = bpt_export.diff_templates(a, b)
    sects = {(e['op'], e['key']) for e in ev if e['kind'] == 'sect'}
    assert ('-', 'a') in sects and ('+', 'c') in sects, sects
    assert ('~', 'b.Default') in sects, sects
    assert len(ev) == 3, ev


@check
def diff_rename_translate_boundaries():
    """{=Name:...}-ссылки транслируются по карте (\\b-границы: Who_18
    не задевает Who_189); рекурсия по dict/list."""
    m = {'Who_18': 'Z_1'}
    got = bpt_export._rename_translate('{=Who_18:GetUser} Who_189', m)
    assert got == '{=Z_1:GetUser} Who_189', got
    assert bpt_export._rename_translate({'k': ['Who_18'], 'n': 5}, m) \
        == {'k': ['Z_1'], 'n': 5}
    assert bpt_export._rename_translate('no refs', m) == 'no refs'


@check
def diff_pure_rename_is_identical():
    """Главный инвариант: rename всех Name эталона (146 узлов) с
    портало-верной перепривязкой ссылок → дифф пуст, exit 0."""
    import random
    import bpt_serialize
    blob = zlib.decompress(open(os.path.join(ETALONS, 'notify-history-canonical.bpt'),
                                'rb').read())
    tree = bpt_serialize.php_unserialize_exact(blob)
    rng, renames = random.Random(42), {}

    def ren(n):
        if not isinstance(n, dict):
            return
        if n.get('Type'):
            renames[n['Name']] = 'R%d_%d' % (rng.randrange(10 ** 5),
                                             rng.randrange(10 ** 5))
            n['Name'] = renames[n['Name']]
        for ch in (n.get('Children') or []):
            ren(ch)

    ren(tree['TEMPLATE'][0])
    tree = bpt_export._rename_translate(tree, renames)  # портал-стиль
    out_bpt = os.path.join(TMP, 'pure_rename.bpt')
    open(out_bpt, 'wb').write(zlib.compress(bpt_serialize.php_serialize(tree)))
    rc = bpt_export.cmd_diff(os.path.join(ETALONS, 'notify-history-canonical.bpt'),
                             out_bpt)
    assert rc == 0, f'чистый rename дал exit {rc}'
    assert len(renames) == 146, f'узлов в эталоне {len(renames)}, ждали 146'


@check
def diff_corpus_pairs_measured():
    """Ожидания зафиксированы промером пар (не выдуманы): 1197 —
    идентичны; 24_2 — ровно +1 активность; 1187 — 2+/8-; 1195-canonical —
    SV-сценарий + переименования + секции."""
    def load(n):
        return bpt_export._load_bpt_tree(os.path.join(ETALONS, n + '.bpt'))

    def kinds(ev, k):
        return [e for e in ev if e['kind'] == k]

    ev, rn = bpt_export.diff_templates(load('loop-idle'), load('loop-idle-work'))
    assert not ev and not rn, (ev, rn)

    ev, rn = bpt_export.diff_templates(load('hotel-booking'), load('hotel-booking-v2'))
    added = kinds(ev, 'added')
    assert len(added) == 1 and \
        added[0]['node']['Type'] == 'CrmUpdateDynamicActivity', ev
    assert not kinds(ev, 'removed'), ev

    ev, rn = bpt_export.diff_templates(load('stage-flow-extended'), load('approval-stages'))
    assert len(kinds(ev, 'added')) == 2 and len(kinds(ev, 'removed')) == 8, \
        (len(kinds(ev, 'added')), len(kinds(ev, 'removed')))

    ev, rn = bpt_export.diff_templates(load('notify-history'),
                                       load('notify-history-canonical'))
    assert any(e['kind'] == 'sv' and e['key'] == 'Stage' for e in ev), \
        'нет SV-диффа по Stage (главный сценарий)'
    assert len(rn) >= 50, len(rn)
    assert any(e['kind'] == 'sect' and e['section'] == 'VARIABLES'
               for e in ev), 'нет key-level диффа VARIABLES'
    assert len(kinds(ev, 'added')) >= 20 and len(kinds(ev, 'removed')) >= 5


@check
def diff_cli_exit_contract():
    """CLI: usage=2, битый файл=2, идентичные=0, дифф=1."""
    orig_argv = sys.argv
    try:
        for argv, want in (
                (['bpt_export.py', 'diff'], 2),
                (['bpt_export.py', 'diff', 'x'], 2),
                (['bpt_export.py', 'diff', 'nope.bpt', 'nope2.bpt'], 2),
                (['bpt_export.py', 'diff', os.path.join(ETALONS, 'loop-idle.bpt'),
                  os.path.join(ETALONS, 'loop-idle-work.bpt')], 0),
                (['bpt_export.py', 'diff', os.path.join(ETALONS, 'hotel-booking.bpt'),
                  os.path.join(ETALONS, 'hotel-booking-v2.bpt')], 1)):
            sys.argv = argv
            try:
                bpt_export.main()
            except SystemExit as e:
                assert e.code == want, f'{argv[2:]}: exit {e.code}, ждали {want}'
            else:
                raise AssertionError(f'{argv[2:]}: main() без SystemExit')
    finally:
        sys.argv = orig_argv


# --- probe-журнал (волна 2, TODO №3; контракт — etalons.md «Probe-режим») ---

def probe_comment(cid, text='[probe] after X\nV=1',
                  created='2026-10-02T14:00:00+03:00'):
    """Фикстура ответа crm.timeline.comment.list — форма по доке метода:
    total/next — top-level, поля строки (ID/ENTITY_ID/…), FILES dict."""
    return {'ID': str(cid), 'ENTITY_ID': '7', 'ENTITY_TYPE': 'deal',
            'CREATED': created, 'AUTHOR_ID': '1', 'COMMENT': text,
            'FILES': {}}


@check
def probe_parse_contract():
    """Контракт T1: голова '[probe] after <Name>' + строки var=value;
    сплит по первому '='; тег без каноничной головы -> step=None."""
    p = bpt_export.parse_probe_comment(
        '[probe] after SetVar_3\nV_AMOUNT=12500.5\nV_FLAG=Y')
    assert p['step'] == 'SetVar_3' and p['vars'] == {
        'V_AMOUNT': '12500.5', 'V_FLAG': 'Y'}, p
    assert bpt_export.parse_probe_comment('соседний комментарий') is None
    assert bpt_export.parse_probe_comment('') is None
    eq = bpt_export.parse_probe_comment('[probe] after X\nV_S=auth=key')
    assert eq['vars']['V_S'] == 'auth=key', f'значение с "=" обрезано: {eq}'
    bad = bpt_export.parse_probe_comment('[probe] кривая голова\nV=1')
    assert bad['step'] is None and bad['vars'] == {'V': '1'}, \
        f'тег без головы потерян молча: {bad}'
    empty_val = bpt_export.parse_probe_comment('[probe] after X\nV=')
    assert empty_val['vars']['V'] == '', 'пустое значение потеряно'


@check
def probe_fetch_cursor_and_total():
    """Курсор filter >ID + сверка total: страницы 50/10; усечение —
    RuntimeError, не молчаливый короткий журнал."""
    pages = [
        {'result': [probe_comment(i) for i in range(1, 51)], 'total': 60},
        {'result': [probe_comment(i) for i in range(51, 61)], 'total': 60},
    ]
    log = []
    orig = bpt_export.call_retry
    bpt_export.call_retry = fake_call(pages, log)
    try:
        got = bpt_export.fetch_probe_comments('wh', 'deal', 7)
    finally:
        bpt_export.call_retry = orig
    assert len(got) == 60, f'собрано {len(got)} вместо 60'
    assert log[0]['filter']['>ID'] == 0 and log[1]['filter']['>ID'] == 50, \
        f'курсор не двинулся: {[p["filter"] for p in log]}'
    pages = [{'result': [probe_comment(i) for i in range(1, 3)],
              'total': 99}]
    log = []
    bpt_export.call_retry = fake_call(pages, log)
    try:
        try:
            bpt_export.fetch_probe_comments('wh', 'deal', 7)
        except RuntimeError:
            pass
        else:
            raise AssertionError('усечение журнала не поймано (total != собрано)')
    finally:
        bpt_export.call_retry = orig


@check
def probe_cmd_report_series_and_empty():
    """cmd_probe: записи -> 0 + хронология (итерации цикла серией однотипных
    шагов), summary-строка; пустой журнал -> 1 + stderr-диагностика."""
    orig = bpt_export.fetch_probe_comments
    bpt_export.fetch_probe_comments = lambda wh, t, i: [
        probe_comment(1, 'просто комментарий юзера'),
        probe_comment(2, '[probe] after SetVar_0\nV_AMOUNT=0'),
        probe_comment(3, '[probe] after SetVar_6\nV_COUNT=1'),
        probe_comment(4, '[probe] after SetVar_6\nV_COUNT=2'),  # итерация №2
    ]
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            rc = bpt_export.cmd_probe('wh', 'deal', 7)
    finally:
        bpt_export.fetch_probe_comments = orig
    assert rc == 0, f'rc={rc} на непустом журнале'
    lines = out.getvalue().splitlines()
    assert any('#3 after SetVar_6' in ln for ln in lines), \
        f'серия итераций (нумерация только probe-записей): {lines}'
    assert any('V_COUNT = 2' in ln for ln in lines), \
        f'значение итерации: {lines}'
    assert lines[-1] == '[probe] journal parsed: 3 entries, 2 variables', \
        f'summary: {lines[-1]!r}'
    bpt_export.fetch_probe_comments = lambda wh, t, i: [probe_comment(1, 'нет')]
    err = io.StringIO()
    try:
        with contextlib.redirect_stderr(err):
            rc = bpt_export.cmd_probe('wh', 'deal', 7)
    finally:
        bpt_export.fetch_probe_comments = orig
    assert rc == 1 and 'журнал пуст' in err.getvalue(), (rc, err.getvalue())


@check
def probe_render_warn_on_malformed_head():
    entries = [
        {'step': None, 'vars': {'V': '1'}, 'raw_head': '[probe] ???'},
        {'step': 'SetVar_6', 'vars': {'V_COUNT': '1'}, 'created': ''},
    ]
    lines = bpt_export.render_probe_report(entries)
    assert any('WARN' in ln for ln in lines), \
        f'кривая голова не репортится: {lines}'
    assert any('after SetVar_6' in ln for ln in lines), lines


@check
def probe_cli_exit_contract():
    """CLI: usage-варианты = 2, нечисловой ENTITY_ID = 2, битый
    webhook-файл = 2."""
    orig_argv = sys.argv
    bad_wh = os.path.join(TMP, 'no_such_webhook.txt')
    try:
        for argv, want in (
                (['bpt_export.py', 'probe'], 2),
                (['bpt_export.py', 'probe', 'deal'], 2),
                (['bpt_export.py', 'probe', 'deal', '1', 'a', 'b'], 2),
                (['bpt_export.py', 'probe', 'deal', 'abc'], 2),
                (['bpt_export.py', 'probe', 'deal', '1', bad_wh], 2)):
            sys.argv = argv
            try:
                bpt_export.main()
            except SystemExit as e:
                assert e.code == want, f'{argv[2:]}: exit {e.code}, ждали {want}'
            else:
                raise AssertionError(f'{argv[2:]}: main() без SystemExit')
    finally:
        sys.argv = orig_argv


@check
def probe_live_journal_fixture():
    """Санитизированная живая фиксtура (T6, E2E 2026-10-02): 8 комментариев
    портала → 7 probe-записей (юзер-комментарий отфильтрован), сырая
    {{=}}-формула в значении V_M_FULL не рвёт парсер (сплит по первому '=')."""
    doc = json.load(open(os.path.join(BPT, 'tests', 'fixtures',
                                      'probe_journal_live.json'),
                         encoding='utf-8'))
    assert doc['total'] == len(doc['result']), \
        f"форма ответа: total={doc['total']} != result={len(doc['result'])}"
    entries = [p for c in doc['result']
               if (p := bpt_export.parse_probe_comment(c.get('COMMENT') or ''))]
    assert len(entries) == 7, f'probe-записей {len(entries)}, ждали 7'
    assert entries[0]['vars'] == {'V_DL': ''}, 'пустое значение V_DL потеряно'
    marker = next(e for e in entries if 'V_M_FULL' in e['vars'])
    assert marker['vars']['V_M_FULL'] == "{{='ex_' . 4 . '_full'}}", \
        f'сырая формула в значении порезана: {marker["vars"]!r}'
    steps = [e['step'] for e in entries]
    assert steps.count('SetVar_99') == 1 and 'SetVar_103' in steps, steps


# --- аудит 2026-10-16, №8/F6: traversal-ID стем, --out - stdout --------------

PY = sys.executable
EXPORT = os.path.join(BPT, 'bpt_export.py')


def run_cli(*args):
    return subprocess.run([PY, EXPORT, *args], capture_output=True, text=True,
                          timeout=60)


@check
def template_stem_sanitizes_traversal_id():
    for evil in ('../../evil', '..\\..\\evil', '/etc/x', 'a/b/c'):
        stem = bpt_export._template_stem(evil, 'name')
        assert '/' not in stem and '\\' not in stem, (evil, stem)
        assert os.path.dirname(
            os.path.join(TMP, stem + '.json')) == TMP, (evil, stem)
    assert bpt_export._template_stem(
        123, 'n') == '123_n', 'числовой ID обязан проходить без изменений'
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        bpt_export._template_stem('../evil', 'n')
    assert 'WARN' in err.getvalue(), 'санитизация без WARN-следа'


@check
def diagram_out_dash_mermaid_to_stdout(tmp=TMP):
    src = os.path.join(tmp, 'f6_mermaid.bpt')
    open(src, 'wb').write(
        open(os.path.join(ETALONS, 'hotel-booking.bpt'), 'rb').read())
    r = run_cli('diagram', src, '--format', 'mermaid', '--out', '-')
    assert r.returncode == 0, f'exit={r.returncode}: {r.stderr!r}'
    assert r.stdout.startswith('flowchart'), \
        f'stdout не mermaid: {r.stdout[:60]!r}'
    assert not os.path.exists(src[:-4] + '.mmd'), \
        'контракт --out -: файл .mmd создан мимо stdout'


@check
def diagram_out_dash_html_to_stdout(tmp=TMP):
    src = os.path.join(tmp, 'f6_html.bpt')
    open(src, 'wb').write(
        open(os.path.join(ETALONS, 'hotel-booking.bpt'), 'rb').read())
    r = run_cli('diagram', src, '--format', 'html', '--out', '-')
    assert r.returncode == 0, f'exit={r.returncode}: {r.stderr!r}'
    assert r.stdout.lstrip().startswith('<!doctype html'), 'stdout не html'
    assert '[diagram] html:' not in r.stdout, 'служебный лог замусорил stdout'
    assert '[diagram] html:' in r.stderr, 'нет лога записи в stdout'
    assert not os.path.exists(src[:-4] + '.diagram.html'), \
        'контракт --out -: файл .diagram.html создан мимо stdout'


def main() -> int:
    failed = []
    for fn in CHECKS:
        name = fn.__name__
        try:
            fn()
            print(f'PASS {name}')
        except Exception as e:  # noqa: BLE001
            failed.append(name)
            print(f'FAIL {name}: {e!r}')
    print(f'--- {len(CHECKS) - len(failed)}/{len(CHECKS)} passed')
    return 1 if failed else 0


@check
def b64_to_zlib_raw_payload_passthrough():
    """b64decode до try калечил raw-payload (validate=False игнорирует
    не-base64 символы → мусор вместо данных). Raw PHP-payload проходит
    как есть; легитимный b64+zlib работает."""
    import base64
    raw = b'a:1:{s:3:"foo";s:3:"bar";}'
    assert bpt_export.b64_to_zlib(raw.decode()) == raw, \
        'raw-payload искажен b64decode'
    blob = zlib.compress(raw)
    b64 = base64.b64encode(blob).decode()
    assert bpt_export.b64_to_zlib(b64) == raw, 'легитимный b64 сломан'


@check
def export_cli_missing_webhook_file_exit2():
    """export-режим: нечитаемый webhook-файл — exit 2 с диагностикой
    (как probe), не traceback (асимметрия аудита)."""
    r = subprocess.run([PY, os.path.join(BPT, 'bpt_export.py'),
                        '/nonexistent/wh.txt'],
                       capture_output=True, text=True, cwd=BPT)
    assert r.returncode == 2, f'exit {r.returncode}, ждём 2'
    assert 'Traceback' not in r.stderr, r.stderr[-300:]
    assert 'webhook' in (r.stderr + r.stdout).lower()


if __name__ == '__main__':
    sys.exit(main())

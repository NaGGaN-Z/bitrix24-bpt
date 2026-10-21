#!/usr/bin/env python3
"""tests/test_bpt_funcs.py — реестр FN-канона + токенайзер (волна 4, T7).

Запуск: python3 tests/test_bpt_funcs.py  (zero-dep, из каталога bpt);
pytest подхватит @check-функции через conftest-адаптер (контракт двойного
запуска — tests/conftest.py). Канон реестра — урок 4912 (bpt_funcs.py).
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BPT = os.path.dirname(HERE)
sys.path.insert(0, BPT)

import bpt_funcs  # noqa: E402

CHECKS = []


def check(fn):
    CHECKS.append(fn)
    return fn


# --- реестр -----------------------------------------------------------------

@check
def registry_shape():
    """43 записи: 40 функций урока 4912 + операторы or/and/not (вызываются
    как функции). Corpus-only записей нет (промер 2026-10-02) — все doc=True;
    NEAR_MISS-имена не пересекаются с реестром (политика: ERROR, не whitelist)."""
    assert len(bpt_funcs.FUNCS) == 43, len(bpt_funcs.FUNCS)
    for name, rec in bpt_funcs.FUNCS.items():
        assert rec['doc'] is True, f'{name}: corpus-only записей нет'
        lo, hi = rec['arity']
        assert lo >= 0 and (hi is None or hi >= lo), (name, rec['arity'])
    assert not set(bpt_funcs.NEAR_MISS) & set(bpt_funcs.FUNCS)
    assert bpt_funcs.NEAR_MISS['number_format'] == 'numberformat'


@check
def registry_operators():
    """Канон-операторы урока 4912; двойные — только в NONCANON (fix-hint)."""
    for op in '+ - * / = <> < > <= >= & ^ %'.split():
        assert op in bpt_funcs.CANON_OPERATORS, op
    for op in ('==', '!=', '&&', '||'):
        assert op not in bpt_funcs.CANON_OPERATORS
        assert op in bpt_funcs.NONCANON_OPERATORS


@check
def registry_interval_units():
    """Юниты dateadd — надмножество workdateadd; 'm' = месяцы есть только
    у dateadd (главная ловушка FN-05)."""
    da, wda = bpt_funcs.INTERVAL_UNITS['dateadd'], \
        bpt_funcs.INTERVAL_UNITS['workdateadd']
    assert wda <= da
    assert 'm' in da and 'm' not in wda
    assert {'i', 'min', 'minute', 'minutes'} <= da
    assert {'y', 'year', 'years', 'month', 'months', 's', 'second',
            'seconds'} <= da


# --- токенайзер --------------------------------------------------------------

@check
def tokenize_kinds_strings_refs():
    """Строковые литералы — одним токеном (запятые/скобки внутри не токены),
    экранирование одноимённой кавычкой, {=Ns:Field} — реф целиком."""
    toks = bpt_funcs.tokenize("if(a, \"x,y\", 'z\\'w') & {=Variable:n}")
    kinds = [(k, t) for k, t, _ in toks]
    assert ('ident', 'if') in kinds
    assert ('str', "\"x,y\"") in kinds
    assert ("str", "'z\\'w'") in kinds
    assert ('ref', '{=Variable:n}') in kinds
    assert ('op', '&') in kinds
    # запятая-токен только межу аргументами: a , "x,y" , 'z\'w' -> 2 шт.
    assert [t for k, t, _ in toks if (k, t) == ('op', ',')] == [',', ',']


@check
def tokenize_operator_longest_match():
    toks = bpt_funcs.tokenize('a==b != c && d || e <> f <= g >= h')
    ops = [t for k, t, _ in toks if k == 'op']
    assert ops == ['==', '!=', '&&', '||', '<>', '<=', '>='], ops


@check
def tokenize_numbers_and_bad_literal():
    toks = bpt_funcs.tokenize('round(15.576, 2)')
    nums = [t for k, t, _ in toks if k == 'num']
    assert nums == ['15.576', '2'], nums
    toks = bpt_funcs.tokenize("+ 'abc")
    assert toks[-1][:2] == ('bad', "'abc"), toks  # незакрытый литерал


@check
def calls_arity_and_nesting():
    """argc — верхний уровень вызова: вложенные вызовы = один аргумент;
    пустые скобки = 0; незакрытая скобка -> i_close None (арность не
    проверяется)."""
    toks = bpt_funcs.tokenize(
        "if(min(1, 2), implode(\",\", x), \"a,b\")")
    cls = {n: a for n, a, _, _ in bpt_funcs.calls(toks)}
    assert cls == {'if': 3, 'min': 2, 'implode': 2}, cls
    toks = bpt_funcs.tokenize('rand()')
    assert bpt_funcs.calls(toks) == [('rand', 0, 1, 2)]
    toks = bpt_funcs.tokenize('if(a')
    assert bpt_funcs.calls(toks)[0][:2] == ('if', 1) \
        and bpt_funcs.calls(toks)[0][3] is None


@check
def calls_true_false_literal_calls():
    """true()/false() из примера урока 4912 (getdocumenturl) — вызовы
    распознаются, но FN-правилами пропускаются (литералы, не функции)."""
    toks = bpt_funcs.tokenize("getdocumenturl('html', true())")
    cls = {n: a for n, a, _, _ in bpt_funcs.calls(toks)}
    assert cls == {'getdocumenturl': 2, 'true': 0}, cls


def main() -> int:
    failed = []
    for fn in CHECKS:
        try:
            fn()
            print(f'PASS {fn.__name__}')
        except Exception as e:  # noqa: BLE001 — жгут обязан ловить всё
            failed.append(fn.__name__)
            print(f'FAIL {fn.__name__}: {e!r}')
    print(f'--- {len(CHECKS) - len(failed)}/{len(CHECKS)} passed')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())

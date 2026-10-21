#!/usr/bin/env python3
"""tests/test_sanitize_corpus.py — регресс-кейсы санитайзера (план
bpt-public-release, задача 1.2).

Запуск: python3 tests/test_sanitize_corpus.py  (zero-dep, из каталога bpt);
pytest подхватит @check-функции через conftest-адаптер (контракт двойного
запуска — tests/conftest.py).

Мини-пары: синтетическое дерево с клиентскими строками (ключи/значения/
формулы) — замена, пересчёт s:N-длин, zlib-уровень оригинала сохранён,
нетронутые байты идентичны. Реальный корпус не трогаем (оригиналы —
инвариант №1 плана).
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BPT = os.path.dirname(HERE)
sys.path.insert(0, BPT)
sys.path.insert(0, os.path.join(BPT, 'scripts', 'release'))

import bpt_serialize  # noqa: E402
try:
    from sanitize_corpus import Engine, load_map  # noqa: E402
    _HAVE_ENGINE = True
except ImportError:
    # Публичный контур: scripts/release (приватный тулинг) исключён из
    # sync — жгут деградирует в SKIP, не в FAIL (идиома zero-dep).
    _HAVE_ENGINE = False

if _HAVE_ENGINE:
    CHECKS = []
else:
    CHECKS = []


def check(fn):
    if _HAVE_ENGINE:
        CHECKS.append(fn)
    return fn


MINI_TREE = {
    'VERSION': 2,
    'TEMPLATE': [{
        'Type': 'SequentialWorkflowActivity',
        'Properties': {
            'Title': 'Сводка Менеджер',
            'mgr_ok': '{=Constant:mgr_contact}',
        },
        'Children': [
            {'Type': 'IfElseActivity',
             'Properties': {'Title': 'Call Region-1 1', 'Keep': 'untouched'}},
        ],
    }],
    'VARIABLES': {'mgr_units': {'Name': 'Юниты Менеджера', 'Type': 'string'}},
}


def _mini_engine():
    m = {
        'string_replacements': [
            {'old': 'mgr', 'new': 'mgr'},
            {'old': 'Менеджер', 'new': 'Менеджер'},
            {'old': 'Region-1', 'new': 'Region-1'},
        ],
        'text_replacements': [
            {'old': 'hotel-booking-v2', 'new': 'hotel-booking-v2'},
            {'old': 'hotel-booking', 'new': 'hotel-booking'},
            {'old': 'booking-chain', 'new': 'booking-chain', 'boundary': True},
        ],
        'file_renames': {},
    }
    return Engine(m)


@check
def detect_level_roundtrip():
    """payload, сжатый уровнем 9, детектится как 9 (боевой уровень корпуса);
    дефолт compress_bpt не путается с явным уровнем."""
    payload = bpt_serialize.php_serialize(MINI_TREE)
    blob9 = bpt_serialize.compress_bpt(payload, 9)
    assert bpt_serialize.detect_level(blob9, payload) == 9, 'уровень 9 не детектится'
    blob_def = bpt_serialize.compress_bpt(payload)
    lvl = bpt_serialize.detect_level(blob_def, payload)
    assert lvl in (1, 2, 3, 4, 5, 6, 7, 8, 9), f'дефолт-уровень не детектится: {lvl}'


@check
def blob_surgery_replaces_and_recomputes():
    """Замены в ключах/значениях/формулах; s:N пересчитаны (структура
    парсится exact-парсером); нетронутая строка идентична; уровень 9."""
    payload = bpt_serialize.php_serialize(MINI_TREE)
    blob = bpt_serialize.compress_bpt(payload, 9)
    eng = _mini_engine()
    new_blob, events = eng.sanitize_blob(blob)
    tree = bpt_serialize.php_unserialize_exact(bpt_serialize.decompress_bpt(new_blob))
    props = tree['TEMPLATE'][0]['Properties']
    assert props['Title'] == 'Сводка Менеджер', f'значение не заменено: {props["Title"]!r}'
    assert props['mgr_ok'] == '{=Constant:mgr_contact}', \
        f'формула не заменена каскадно: {props.get("mgr_ok")!r}'
    assert 'mgr_ok' in props, 'ключ не заменён'
    child = tree['TEMPLATE'][0]['Children'][0]['Properties']
    assert child['Keep'] == 'untouched', 'нетронутая строка изменилась'
    assert child['Title'] == 'Call Region-1 1', 'подстрока в значении не заменена'
    assert 'mgr_units' in tree['VARIABLES'], 'ключ пространства имён не заменён'
    assert bpt_serialize.detect_level(new_blob,
                                      bpt_serialize.decompress_bpt(new_blob)) == 9, \
        'уровень оригинала не сохранён'
    assert len(events) >= 6, f'событий меньше ожидаемого: {len(events)}'


@check
def blob_surgery_noop_is_byte_exact():
    """Нет матчей — выход байт-в-байт равен входу (инвариант: сан без
    замен не меняет файл; recompress тем же уровнем)."""
    tree = {'VERSION': 2, 'TEMPLATE': [{
        'Type': 'SequentialWorkflowActivity',
        'Properties': {'Title': 'Neutral title', 'Note': 'plain'},
    }]}
    payload = bpt_serialize.php_serialize(tree)
    blob = bpt_serialize.compress_bpt(payload, 9)
    eng = _mini_engine()
    new_blob, events = eng.sanitize_blob(blob)
    assert new_blob == blob, 'no-op санитизация изменила байты'
    assert events == [], 'no-op дал события'


@check
def blob_surgery_length_recompute_cyrillic():
    """Замена с ИЗМЕНЕНИЕМ байтовой длины (Менеджер 18Б → Менеджер 16Б)
    не ломает поток: exact-парсер проходит, значение корректно."""
    payload = bpt_serialize.php_serialize(MINI_TREE)
    blob = bpt_serialize.compress_bpt(payload, 6)
    eng = _mini_engine()
    new_blob, _ = eng.sanitize_blob(blob)
    tree = bpt_serialize.php_unserialize_exact(bpt_serialize.decompress_bpt(new_blob))
    name = tree['VARIABLES']['mgr_units']['Name']
    assert name == 'Юниты Менеджера', f'кириллическая замена с новой длиной битая: {name!r}'


@check
def text_engine_boundary_and_ordering():
    """boundary: booking-chain заменяется, 9530 — нет; порядок: hotel-booking-v2 раньше hotel-booking
    (иначе hotel-booking отрежет хвост _2)."""
    eng = _mini_engine()
    text = 'база booking-chain и число 9530; фикстуры hotel-booking-v2.bpt и hotel-booking.bpt'
    out, _ = eng.apply_text(text)
    assert 'база booking-chain и' in out, f'boundary-замена не сработала: {out!r}'
    assert '9530' in out, '9530 задета boundary-заменой'
    assert 'hotel-booking-v2.bpt' in out, 'hotel-booking-v2 не заменён (порядок?)'
    assert 'hotel-booking.bpt' in out, 'hotel-booking не заменён'
    assert 'bp-' not in out.replace('hotel-booking-v2', ''), f'остались bp-литералы: {out!r}'


@check
def live_map_validates():
    """Боевая карта санитайзера (приватный тулинг) проходит инварианты
    (validate_map) и её blob-замены применимы к мини-дереву без ошибок."""
    eng = Engine(load_map())
    tree = {'VERSION': 2, 'TEMPLATE': [{
        'Type': 'SequentialWorkflowActivity',
        'Properties': {'Title': 'BookingAPI номер брони Elm',
                       'list': 'mgr_list'},
    }]}
    payload = bpt_serialize.php_serialize(tree)
    blob = bpt_serialize.compress_bpt(payload, 9)
    new_blob, events = eng.sanitize_blob(blob)
    parsed = bpt_serialize.php_unserialize_exact(bpt_serialize.decompress_bpt(new_blob))
    props = parsed['TEMPLATE'][0]['Properties']
    assert props['Title'] == 'BookingAPI номер брони Elm', props['Title']
    assert props['list'] == 'mgr_list', props['list']
    assert events, 'боевая карта не сматчила мини-дерево'


def main() -> int:
    if not _HAVE_ENGINE:
        print('[SKIP] sanitize_corpus недоступен (приватный тулинг '
              'исключён из дерева) — 0 checks')
        return 0
    print(f'--- {len(CHECKS)} checks')
    failed = 0
    for fn in CHECKS:
        try:
            fn()
            print(f'  ok    {fn.__name__}')
        except AssertionError as exc:
            failed += 1
            print(f'  FAIL  {fn.__name__}: {exc}')
    print(f'--- {len(CHECKS) - failed}/{len(CHECKS)} passed')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())

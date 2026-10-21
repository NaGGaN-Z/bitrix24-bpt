#!/usr/bin/env python3
"""tests/test_bpt_serialize.py — регресс-жгут S2 (bpt_serialize: гейты,
изоляция, малформ-парсинг, strict-decode, type-strict compare, --byte-exact).
Запуск: python3 tests/test_bpt_serialize.py (из каталога bpt). Zero-dep."""
import os
import shutil
import subprocess
import sys
import tempfile
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
BPT = os.path.dirname(HERE)
sys.path.insert(0, BPT)

import bpt_serialize  # noqa: E402
from bpt_export import php_unserialize as lossy_parse  # noqa: E402

ETALONS = os.path.join(BPT, 'etalons')
PY = sys.executable
SER = os.path.join(BPT, 'bpt_serialize.py')
TMP = tempfile.mkdtemp(prefix='bpt_t_ser_')

CHECKS = []


def check(fn):
    CHECKS.append(fn)
    return fn


def run_cli(*args):
    return subprocess.run([PY, SER, *args], capture_output=True, text=True,
                          timeout=60)


def make_bpt(payload: bytes, name: str) -> str:
    path = os.path.join(TMP, name)
    open(path, 'wb').write(zlib.compress(payload))
    return path


# --- гейт: false-green -------------------------------------------------------

@check
def all_nonexistent_dir_fails():
    r = run_cli('--all', os.path.join(TMP, 'no-such-dir'))
    assert r.returncode != 0, f'несуществующий каталог: exit={r.returncode} (0/0 = always-green)'


@check
def all_empty_dir_fails():
    empty = os.path.join(TMP, 'empty_dir')
    os.makedirs(empty, exist_ok=True)
    r = run_cli('--all', empty)
    assert r.returncode != 0, f'пустой каталог: exit={r.returncode}'


@check
def no_args_usage_exits_2():
    r = run_cli()
    assert r.returncode == 2, f'без аргументов exit={r.returncode} (ждал 2)'
    assert 'usage' in (r.stderr + r.stdout).lower(), 'нет usage'


# --- пер-файловая изоляция ---------------------------------------------------

@check
def broken_file_does_not_abort_sweep():
    d = os.path.join(TMP, 'mixed')
    os.makedirs(d, exist_ok=True)
    good = os.path.join(ETALONS, 'hotel-booking.bpt')
    shutil.copy(good, os.path.join(d, 'zzz-good.bpt'))
    blob = open(good, 'rb').read()
    open(os.path.join(d, 'aaa-broken.bpt'), 'wb').write(blob[:100])  # битый идёт первым
    r = run_cli('--all', d)
    assert 'zzz-good.bpt' in r.stdout, f'валидный файл не проверен: {r.stdout!r}'
    assert 'aaa-broken' in r.stderr, f'битый файл не отрепорчен: {r.stderr!r}'
    assert r.returncode != 0, 'прогон с битым файлом вышел зелёным'


# --- малформ-парсинг: ValueError, не AttributeError --------------------------

@check
def malformed_tokens_valueerror():
    for parser, name in ((bpt_serialize.php_unserialize_exact, 'exact'),
                         (lossy_parse, 'lossy')):
        for bad in (b'i:zz;', b'd:NAN;', b'd:INF;', b'b:2;'):
            try:
                parser(bad)
            except ValueError:
                continue
            except Exception as e:  # noqa: BLE001
                raise AssertionError(f'{name}: {bad!r} -> {type(e).__name__} '
                                     f'(ждал ValueError)') from e
            raise AssertionError(f'{name}: {bad!r} распарсен молча')


# --- strict utf-8 decode -----------------------------------------------------

@check
def invalid_utf8_raises_instead_of_fffd():
    for parser, name in ((bpt_serialize.php_unserialize_exact, 'exact'),
                         (lossy_parse, 'lossy')):
        try:
            parser(b's:2:"\xc3\x28";')
        except ValueError:  # UnicodeDecodeError — подкласс
            continue
        raise AssertionError(f'{name}: невалидный UTF-8 прошёл (U+FFFD bake-in)')


# --- type-strict семантическое сравнение -------------------------------------

@check
def sem_equal_is_type_strict():
    eq = bpt_serialize._sem_equal
    assert not eq(0, False), 'i:0; == b:0;'
    assert not eq(False, 0), 'b:0; == i:0;'
    assert not eq(0, 0.0), 'i:0; == d:0;'
    assert not eq('1', 1)
    assert not eq({14: 'a'}, {'_14': 'a'}), 'int-ключ == str-ключ'
    assert eq(0, 0) and eq(0.0, 0.0) and eq('1', '1')
    assert eq([1, {'a': [True]}], [1, {'a': [True]}])
    assert not eq([1], [1, 1])


# --- гейт --byte-exact -------------------------------------------------------

@check
def byte_exact_flag_gates():
    f = make_bpt(b'a:1:{i:0;d:0.0;}', 'floatnorm.bpt')  # наш writer эмитит d:0;
    r = run_cli(f)
    assert r.returncode == 0, f'semantic-гейт упал на semantic-ok: {r.stdout}'
    assert 'semantic-ok' in r.stdout
    r = run_cli('--byte-exact', f)
    assert r.returncode != 0, '--byte-exact пропустил байтовый дифф'
    r = run_cli('--byte-exact', os.path.join(ETALONS, 'hotel-booking.bpt'))
    assert r.returncode == 0, f'эталон byte-exact упал: {r.stdout}'


# --- корпус остаётся зелёным (in-process) ------------------------------------

@check
def corpus_semantic_ok_inprocess():
    files = sorted(f for f in os.listdir(ETALONS) if f.endswith('.bpt'))
    for fn in files:
        sem, _byte = bpt_serialize.roundtrip(os.path.join(ETALONS, fn))
        assert sem, f'{fn}: semantic сломан'


@check
def dump_load_json_int_keys_roundtrip():
    t = {14: 'Общая воронка', -3: [1, 2.5, None, True], 0: {'a': 'b'},
         'plain': 'x', '_14': 'str выживает как str'}
    js = bpt_serialize.dump_json(t)
    assert '"14"' in js and '"-3"' in js, js
    back = bpt_serialize.load_json(js)
    assert back == t, (back, t)
    first = list(back)[0]
    assert first == 14 and isinstance(first, int)


@check
def load_json_digit_keys_become_int():
    back = bpt_serialize.load_json('{"14": "x", "-7": 1, "Title": "y"}')
    assert list(back)[0] == 14 and isinstance(list(back)[0], int)
    assert back[-7] == 1 and back['Title'] == 'y'


@check
def negative_zero_preserved():
    assert bpt_serialize.php_serialize(-0.0) == b'd:-0;'
    assert bpt_serialize.php_serialize(0.0) == b'd:0;'


# --- аудит 2026-10-16, №7: позиция --byte-exact ------------------------------
# Документированная форма `--all DIR --byte-exact` молча игнорировала флаг
# (парсился только argv[0]) — байтовый гейт деградировал до semantic, exit 0
# при расхождении; `roundtrip FILE --byte-exact` трактовал флаг как имя файла.

@check
def byte_exact_after_dir_enforces_byte_gate(tmp=TMP):
    d = os.path.join(TMP, 'be_pos_dir')
    os.makedirs(d, exist_ok=True)
    # semantic-ok, но НЕ byte-exact: d:1.0; парсится во float 1.0, writer
    # канонизирует в d:1; (roundtrip-стабильный порядок ключей для этой
    # цели не годится — нужен неканонический байтовый исходник)
    payload = b'd:1.0;'
    open(os.path.join(d, 'd10.bpt'), 'wb').write(zlib.compress(payload))
    r = run_cli('--all', d, '--byte-exact')
    assert r.returncode == 1, (f'--all DIR --byte-exact: exit={r.returncode} '
                               '— флаг проигнорирован, гейт деградировал '
                               'до semantic')
    r_sem = run_cli('--all', d)
    assert r_sem.returncode == 0, \
        f'без флага semantic-гейт должен быть зелёным: {r_sem.returncode}'


@check
def byte_exact_after_file_not_treated_as_filename(tmp=TMP):
    p = make_bpt(b'd:1.0;', 'be_pos_file.bpt')
    r = run_cli('roundtrip', p, '--byte-exact')
    assert r.returncode == 1, f'exit={r.returncode}'
    assert 'ERROR' not in r.stderr, f'флаг стал именем файла: {r.stderr!r}'
    assert 'byte-exact: 0/1' in r.stdout, r.stdout


@check
def byte_exact_flag_tail_corpus_green():
    r = run_cli('--all', ETALONS, '--byte-exact')
    assert r.returncode == 0, \
        f'корпус 16/16 byte-exact и с флагом в хвосте: {r.stdout}'


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
def decompress_raw_fallback_php_tag_gate():
    """raw-.bpt симметрия (аудит): decompress_bpt принимает raw
    PHP-payload (тег-гейт a:/i:/s:/…), мусор без тега — zlib.error."""
    raw = b'a:1:{s:1:"a";i:1;}'
    assert bpt_serialize.decompress_bpt(raw) == raw, 'raw PHP-payload не прошёл'
    try:
        bpt_serialize.decompress_bpt(b'garbage-not-php!')
    except zlib.error:
        pass
    else:
        raise AssertionError('мусор без PHP-тега прошёл как raw')


@check
def decompress_bomb_limit():
    """zip-bomba-гард: payload > 64 MB — ValueError, не OOM."""
    bomb = zlib.compress(b'\x00' * (80 * 1024 * 1024))
    try:
        bpt_serialize.decompress_bpt(bomb)
    except ValueError:
        pass
    else:
        raise AssertionError('декомпрессия без лимита прожевала 80 MB')


if __name__ == '__main__':
    sys.exit(main())

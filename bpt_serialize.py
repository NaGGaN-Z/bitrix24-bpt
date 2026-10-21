#!/usr/bin/env python3
"""bpt_serialize.py — PHP-serialize writer для .bpt (обратный bpt_export.php_unserialize).

PHP-семантика:
  str  -> s:<BYTELEN>:"...";          (длина в байтах UTF-8!)
  bool -> b:0; / b:1;                 (проверять ДО int: bool подкласс int)
  int  -> i:N;
  float-> d:<num>;                    (PHP serialize_precision=-1: 1.0 -> "1")
  None -> N;
  list -> a:N:{i:0;v;i:1;v;...}
  dict -> a:N:{k;v;...}               (порядок ключей = вставка, как в PHP assoc)

CLI:
  python3 bpt_serialize.py roundtrip FILE.bpt [FILE.bpt ...]
      parse -> reserialize -> byte-compare (жёсткий тест writer'а)
  python3 bpt_serialize.py --all DIR
      рекурсивно по *.bpt
  python3 bpt_serialize.py --byte-exact ...   # гейт по байтам, не semantic

Exit-контракт: 0 — гейт зелёный; 1 — MISMATCH/битый файл/пустой набор
(0/0 = always-green запрещён); 2 — usage. Битый файл не валит прогон:
пер-файловая изоляция + сводка. Семантическое сравнение type-strict
(b:0; не равно i:0;, int-ключ не равен str-ключу).
"""
import json
import os
import re
import sys
import zlib

from bpt_export import php_unserialize  # noqa: E402


def _php_float(f: float) -> str:
    if f != f or f in (float('inf'), float('-inf')):
        raise ValueError(f'PHP не сериализует {f!r}')
    if f.is_integer() and abs(f) < 1e15:
        return '-0' if str(f)[0] == '-' else str(int(f))
    r = repr(f)
    if 'e' in r or 'E' in r:
        mant, _, exp = r.replace('E', 'e').partition('e')
        if '.' not in mant:
            mant += '.0'
        sign = '+' if not exp.startswith('-') else ''
        exp = exp.lstrip('+')
        return f'{mant}E{sign}{exp}'
    return r


def php_serialize(value) -> bytes:
    out = bytearray()

    def w(v):
        if v is None:
            out.extend(b'N;')
        elif isinstance(v, bool):
            out.extend(b'b:1;' if v else b'b:0;')
        elif isinstance(v, int):
            out.extend(f'i:{v};'.encode())
        elif isinstance(v, float):
            out.extend(f'd:{_php_float(v)};'.encode())
        elif isinstance(v, str):
            raw = v.encode('utf-8')
            out.extend(b's:%d:"' % len(raw) + raw + b'";')
        elif isinstance(v, list):
            out.extend(b'a:%d:{' % len(v))
            for i, item in enumerate(v):
                w(i)
                w(item)
            out.extend(b'}')
        elif isinstance(v, dict):
            out.extend(b'a:%d:{' % len(v))
            for k, item in v.items():
                if isinstance(k, bool) or not isinstance(k, (int, str)):
                    raise TypeError(f'bad key {k!r}')
                w(k)
                w(item)
            out.extend(b'}')
        else:
            raise TypeError(f'unsupported {type(v)}')

    w(value)
    return bytes(out)


def php_unserialize_exact(data: bytes):
    """Lossless-парсер: dict остаётся dict с исходными типами ключей
    (int не превращается в '_N'), list — только для последовательных 0..n-1.
    Нужен для байт-точного roundtrip-теста. Малформ (i:zz;, d:NAN;) —
    ValueError с позицией; строки — strict utf-8 (U+FFFD bake-in запрещён)."""
    pos = 0
    import re as _re

    def _unparsed():
        return ValueError(f'unparsed at {pos}: {data[pos:pos + 40]!r}')

    def parse():
        nonlocal pos
        c = data[pos:pos + 1]
        if c == b'i':
            m = _re.match(rb'i:(-?\d+);', data[pos:])
            if m is None:
                raise _unparsed()
            pos += m.end()
            return int(m.group(1))
        if c == b'd':
            m = _re.match(rb'd:(-?[\d.eE+\-]+);', data[pos:])
            if m is None:
                raise _unparsed()
            pos += m.end()
            return float(m.group(1))
        if c == b'b':
            m = _re.match(rb'b:([01]);', data[pos:])
            if m is None:
                raise _unparsed()
            pos += m.end()
            return m.group(1) == b'1'
        if c == b's':
            m = _re.match(rb's:(\d+):"', data[pos:])
            if m is None:
                raise _unparsed()
            pos += m.end()
            raw = data[pos:pos + int(m.group(1))]
            pos += int(m.group(1)) + 2
            return raw.decode('utf-8')
        if c == b'a':
            m = _re.match(rb'a:(\d+):\{', data[pos:])
            if m is None:
                raise _unparsed()
            pos += m.end()
            n = int(m.group(1))
            pairs = [(parse(), parse()) for _ in range(n)]
            pos += 1
            if [k for k, _ in pairs] == list(range(n)):
                return [v for _, v in pairs]
            d = {}
            for k, v in pairs:
                d[k] = v
            return d
        if c == b'N':
            pos += 2
            return None
        raise _unparsed()

    val = parse()
    if pos != len(data):
        raise ValueError(f'trailing bytes at {pos}/{len(data)}')
    return val


def _sem_equal(a, b) -> bool:
    """Семантическое сравнение, строгое по типам: PHP-значения b:0;/i:0;/d:0;
    различимы, Python == — нет (bool подкласс int, 0 == 0.0). Строгость
    обязана быть на стороне гейта, иначе типо-смещения невидимы."""
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        return (len(a) == len(b)
                and all(k in b and _sem_equal(v, b[k]) for k, v in a.items()))
    if isinstance(a, list):
        return (len(a) == len(b)
                and all(_sem_equal(x, y) for x, y in zip(a, b)))
    return a == b


_INT_KEY_RE = re.compile(r'^-?\d+$')


def dump_json(tree) -> str:
    """Канонический JSON-шов .bpt-дерева: int-ключи PHP -> числовые
    str-ключи JSON ({"14": ...}); load_json возвращает их int-ами.
    Числовой str-ключ в валидном дереве невозможен (PHP автокастует
    числовые ключи массивов в int) — потому правило однозначно; его
    нарушение ловит _lint_keys в bpt_build.save()."""
    def prep(v):
        if isinstance(v, dict):
            return {(str(k) if isinstance(k, int) and not isinstance(k, bool)
                     else k): prep(x) for k, x in v.items()}
        if isinstance(v, list):
            return [prep(x) for x in v]
        return v
    return json.dumps(prep(tree), ensure_ascii=False, indent=1)


def load_json(text: str):
    """Обратная сторона шва: str-ключ из чистых цифр -> int. Входной JSON
    содержит только str-ключи — коллизия при конверсии невозможна.
    Неканонические формы нормализуются ('007' -> 7), bool-значения и всё
    остальное проходят как есть."""
    def prep(v):
        if isinstance(v, dict):
            return {(int(k) if isinstance(k, str) and _INT_KEY_RE.match(k)
                     else k): prep(x) for k, x in v.items()}
        if isinstance(v, list):
            return [prep(x) for x in v]
        return v
    return prep(json.loads(text))


def compress_bpt(payload: bytes, level: int = -1) -> bytes:
    """Сжатие payload; level=-1 — дефолт zlib. Явный уровень нужен
    санитайзеру (scripts/release): боевой корпус сжат уровнем 9 —
    рекомпрессия дефолтом даёт другие байты (план bpt-public-release 1.2)."""
    if level < 0:
        return zlib.compress(payload)
    return zlib.compress(payload, level)


def detect_level(blob: bytes, payload: bytes) -> int:
    """Определить zlib-уровень оригинала перебором 9..1 по точному байтовому
    совпадению. Не найден (нестандартный словарь/стратегия) → -1 — вызывающий
    решает (санитайзер при -1 падает: неизвестная recompress-политика)."""
    for lvl in range(9, 0, -1):
        if zlib.compress(payload, lvl) == blob:
            return lvl
    return -1


#: zip-bomba-гард (security-аудит 2026-10-16): потолок распакованного
#: payload; боевые .bpt < 100 KB, 64 MB = явная аномалия
MAX_PAYLOAD = 64 * 1024 * 1024

#: голова raw PHP-payload (serialize-теги) — тег-гейт raw-фолбэка
_PHP_TAGS = (b'a:', b'i:', b's:', b'd:', b'b:', b'O:', b'N;', b'r:', b'R:')


def _decompress_limited(data: bytes) -> bytes:
    """zlib-декомпрессия с потолком выхода: > MAX_PAYLOAD — ValueError,
    не OOM (decompress(data, max_length) не аллоцирует сверх лимита)."""
    d = zlib.decompressobj()
    out = d.decompress(data, MAX_PAYLOAD + 1)
    if len(out) <= MAX_PAYLOAD:
        out += d.flush(MAX_PAYLOAD + 1)
    if len(out) > MAX_PAYLOAD:
        raise ValueError(f'payload > {MAX_PAYLOAD // (1024 * 1024)} MB — '
                         'zip-bomba-гард (bpt_serialize.MAX_PAYLOAD)')
    return out


def decompress_bpt(blob: bytes) -> bytes:
    """zlib-blob ИЛИ уже raw PHP-payload (симметрия чтения .bpt: UI-экспорт
    жмёт, ручные правки/патчи могут быть raw). Тег-гейт: raw опознаётся по
    serialize-тегу в голове; мусор без тега — zlib.error (порчу не
    маскируем). Выход лимитирован (см. _decompress_limited)."""
    try:
        return _decompress_limited(blob)
    except zlib.error:
        if blob[:2] in _PHP_TAGS:
            return blob  # уже raw payload
        raise


def roundtrip(path: str) -> tuple[bool, bool]:
    blob = open(path, 'rb').read()
    payload = decompress_bpt(blob)
    tree = php_unserialize_exact(payload)
    payload2 = php_serialize(tree)
    ok_bytes = payload2 == payload
    ok_semantic = _sem_equal(php_unserialize(payload2), php_unserialize(payload))
    status = 'BYTE-EXACT' if ok_bytes else ('semantic-ok' if ok_semantic else 'MISMATCH')
    print(f'{path}: {status} (len_ours={len(payload2)}, len_orig={len(payload)})')
    if not ok_bytes and ok_semantic:
        n = min(len(payload2), len(payload))
        for i in range(n):
            if payload2[i] != payload[i]:
                print(f'  first byte diff at {i}: ours={payload2[i-15:i+20]!r} '
                      f'orig={payload[i-15:i+20]!r}')
                break
    return ok_semantic, ok_bytes


USAGE = ('usage: bpt_serialize.py roundtrip FILE.bpt [FILE.bpt ...]\n'
         '       bpt_serialize.py --all DIR [--byte-exact]\n'
         'exit: 0 ok | 1 mismatch/битый файл/пустой набор | 2 usage')


def main() -> None:
    args = sys.argv[1:]
    if not args:
        print(USAGE, file=sys.stderr)
        sys.exit(2)
    # Флаг позиционно-независим (аудит 2026-10-16 №7): документированная
    # форма `--all DIR --byte-exact` молча игнорировалась при парсинге
    # только-as-argv[0] — байтовый гейт деградировал до semantic (exit 0
    # при расхождении); в roundtrip-форме флаг становился «именем файла».
    byte_exact = '--byte-exact' in args
    args = [a for a in args if a != '--byte-exact']
    if args and args[0] == 'roundtrip':
        args = args[1:]
    files = []
    root = None
    if args and args[0] == '--all':
        root = args[1] if len(args) > 1 else '.'
        for dirpath, _dirs, names in os.walk(root):
            files += [os.path.join(dirpath, n) for n in names if n.endswith('.bpt')]
    elif args:
        files = args
    else:
        print(USAGE, file=sys.stderr)
        sys.exit(2)
    if not files:
        print(f'[FIX] .bpt не найдено в {root!r} — пустой гейт не бывает '
              f'зелёным (опечатка пути = always-green)', file=sys.stderr)
        sys.exit(1)
    ok_sem = ok_byte = errors = 0
    for f in files:
        try:
            sem, byt = roundtrip(f)
            ok_sem += sem
            ok_byte += byt
        except Exception as e:  # noqa: BLE001 — изоляция: битый файл не валит прогон
            errors += 1
            print(f'{f}: ERROR {type(e).__name__}: {e}', file=sys.stderr)
    summary = (f'--- semantic-ok: {ok_sem}/{len(files)}, '
               f'byte-exact: {ok_byte}/{len(files)}')
    if errors:
        summary += f', errors: {errors}'
    print(summary)
    gate = ok_byte if byte_exact else ok_sem
    sys.exit(0 if errors == 0 and gate == len(files) else 1)


if __name__ == '__main__':
    main()

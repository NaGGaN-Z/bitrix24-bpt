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
"""
import os
import sys
import zlib

from bpt_export import php_unserialize  # noqa: E402


def _php_float(f: float) -> str:
    if f != f or f in (float('inf'), float('-inf')):
        raise ValueError(f'PHP не сериализует {f!r}')
    if f.is_integer() and abs(f) < 1e15:
        return str(int(f))
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
    Нужен для байт-точного roundtrip-теста."""
    pos = 0
    import re as _re

    def parse():
        nonlocal pos
        c = data[pos:pos + 1]
        if c == b'i':
            m = _re.match(rb'i:(-?\d+);', data[pos:])
            pos += m.end()
            return int(m.group(1))
        if c == b'd':
            m = _re.match(rb'd:(-?[\d.eE+\-]+);', data[pos:])
            pos += m.end()
            return float(m.group(1))
        if c == b'b':
            m = _re.match(rb'b:([01]);', data[pos:])
            pos += m.end()
            return m.group(1) == b'1'
        if c == b's':
            m = _re.match(rb's:(\d+):"', data[pos:])
            pos += m.end()
            raw = data[pos:pos + int(m.group(1))]
            pos += int(m.group(1)) + 2
            return raw.decode('utf-8', 'replace')
        if c == b'a':
            m = _re.match(rb'a:(\d+):\{', data[pos:])
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
        raise ValueError(f'unparsed at {pos}: {data[pos:pos + 40]!r}')

    val = parse()
    if pos != len(data):
        raise ValueError(f'trailing bytes at {pos}/{len(data)}')
    return val


def compress_bpt(payload: bytes) -> bytes:
    return zlib.compress(payload)


def decompress_bpt(blob: bytes) -> bytes:
    return zlib.decompress(blob)


def roundtrip(path: str) -> tuple[bool, bool]:
    blob = open(path, 'rb').read()
    payload = zlib.decompress(blob)
    tree = php_unserialize_exact(payload)
    payload2 = php_serialize(tree)
    ok_bytes = payload2 == payload
    ok_semantic = php_unserialize(payload2) == php_unserialize(payload)
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


def main() -> None:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return
    if args[0] == 'roundtrip':
        args = args[1:]
    files = []
    if args[0] == '--all':
        root = args[1] if len(args) > 1 else '.'
        for dirpath, _dirs, names in os.walk(root):
            files += [os.path.join(dirpath, n) for n in names if n.endswith('.bpt')]
    else:
        files = args
    results = [roundtrip(f) for f in files]
    print(f'--- semantic-ok: {sum(r[0] for r in results)}/{len(results)}, '
          f'byte-exact: {sum(r[1] for r in results)}/{len(results)}')
    sys.exit(0 if all(r[0] for r in results) else 1)


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""tests/test_repo_lint.py — репо-линт сырой хирургии .bpt (волна 6, T10).

Обход bpt_graft (сырой collect()/zlib.compress/php_unserialize_exact в
патч-скриптах) = возврат к до-гейтевой эпохе (прод-фоллы project-x: орфан-артефакт
с дублями Name отклонён порталом — гейты не вызывались). Severity WARN —
НЕ блокирует (решение владельца): находки печатаются, exit всегда 0,
жгут остаётся зелёным.

Запуск: python3 tests/test_repo_lint.py (из каталога bpt).
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BPT = os.path.dirname(HERE)

# Канонные места сериализации: сам модуль хирургии, сериализатор,
# билдер (save/load), экспортёр. Патч-скрипты (patch_*.py, рабочие
# скрипты bpt_out/, cases_booking_chain) — НЕ в списке: правка .bpt только graft.
# scripts/release — релиз-тулинг (план bpt-public-release): санитайзер
# работает с zlib только через импорт bpt_serialize; каталог исключён из
# скана, чтобы WARN-шум не рос при развитии sync-инструментария.
ALLOW = {
    'bpt_graft.py', 'bpt_serialize.py', 'bpt_build.py', 'bpt_export.py',
}
# Признак сырой хирургии = ЗАПИСЬ .bpt мимо graft: zlib.compress
# (сжатие payload в блоб — без него .bpt не собрать). Чистое чтение
# (php_unserialize_exact/decompress — bpt_diagram-эмиттер) НЕ флагается:
# хирургия = запись. Сообщение упоминает exact-читалку как подсказку
# где обычно живёт патч-цикл.
WRITE_MARK = re.compile(r'zlib\.compress\s*\(')


def raw_surgery(text: str) -> list:
    return ['zlib.compress'] if WRITE_MARK.search(text) else []


def scan():
    warns = []
    for dirpath, dirnames, filenames in os.walk(BPT):
        dirnames[:] = [d for d in dirnames
                       if d not in ('.git', '__pycache__', '.hypothesis',
                                    '.pytest_cache', '.ruff_cache', 'etalons',
                                    'tests', 'bpt_out', 'build', 'release')]
        for fn in filenames:
            if not fn.endswith('.py') or fn in ALLOW:
                continue
            path = os.path.join(dirpath, fn)
            try:
                text = open(path, encoding='utf-8').read()
            except (OSError, UnicodeDecodeError):
                continue
            hits = raw_surgery(text)
            if hits:
                rel = os.path.relpath(path, BPT)
                warns.append(f'{rel}: сырая хирургия {", ".join(hits)} — '
                             f'правки .bpt через bpt_graft '
                             f'(load/locate/edit/save с гейтами)')
    return warns


def main() -> int:
    warns = scan()
    if warns:
        print(f'[repo-lint] WARN ×{len(warns)} (severity WARN, не блок):')
        for w in warns:
            print(f'  {w}')
    else:
        print('[repo-lint] чисто: сырой хирургии вне канона нет')
    return 0  # WARN никогда не блокирует (решение владельца)


if __name__ == '__main__':
    sys.exit(main())

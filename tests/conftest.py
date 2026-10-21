"""tests/conftest.py — pytest-адаптер над zero-dep @check-жгутом.

Контракт двойного запуска (план bpt-builder-core T1):
  pytest tests/                     — этот адаптер собирает в тесты все
                                      функции, зарегистрированные @check;
  python3 tests/test_<mod>.py       — прямой прогон того же списка CHECKS
                                      через main() модуля (zero-dep, без
                                      dev-пакетов).

Источник истины один — список CHECKS модуля: тесты не переписываются и не
дублируются, добавление @check-функции видно обоими режимами сразу.
"""
import types

import pytest


def pytest_pycollect_makeitem(collector, name, obj):
    """Собирать @check-функции тест-модулей как pytest-тесты.

    Имена проверок не следуют test_*-конвенции (жгут старше pytest-гейта),
    поэтому дефолтный коллектор их не видит — матрим по членству в CHECKS.
    Возврат None отдаёт имя дефолтной обработке (main/run_cli/helpers не
    коллбектятся: их нет в CHECKS и они не матчат test_*).
    """
    checks = getattr(collector.obj, 'CHECKS', None)
    if checks and isinstance(obj, types.FunctionType) and obj in checks:
        return pytest.Function.from_parent(collector, name=name, callobj=obj)
    return None

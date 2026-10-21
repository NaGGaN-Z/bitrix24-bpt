# bpt — тулчейн шаблонов бизнес-процессов Битрикс24 (.bpt)

[![CI](https://github.com/NaGGAN-Z/bitrix24-bpt/actions/workflows/ci.yml/badge.svg)](https://github.com/NaGGAN-Z/bitrix24-bpt/actions/workflows/ci.yml)

> Parse → build → validate → probe → diff: полный цикл работы с шаблонами БП
> без дизайнера, zero-dep (чистый Python 3).

Три модуля: сериализация (`bpt_serialize.py`), REST-экспорт и дифф
(`bpt_export.py`), генератор с гейтами (`bpt_build.py`); хирургия
существующих шаблонов — `bpt_graft.py` (load → locate → edit → save с
гейтами; правка живых .bpt — ТОЛЬКО через него). Канон форм
активностей и грабли сборок — **`etalons.md`** (книга, рядом). Рантайм-уроки
калькулятора выражений — скилл `bitrix24-bpt`
(references/expression-runtime.md).

## Quick Start

```bash
cd bpt

# инвариант корпуса: 12 эталонов обязаны быть byte-exact
python3 bpt_serialize.py --all etalons        # 12/12

# минимальный шаблон: json-дерево -> .bpt (через полный гейт validate)
python3 bpt_build.py make TREE.json OUT.bpt

# валидация дерева без записи (ERROR-рулы = exit 1)
python3 bpt_build.py validate TREE.json [--portal=cloud|box@X.Y]

# тесты (оба режима обязаны быть зелёными)
python3 tests/test_bpt_build.py               # zero-dep жгут
pytest tests/                                 # адаптер conftest.py
```

## Возможности

- **Byte-exact roundtrip** — `php_serialize`/`php_unserialize_exact`
  (int-ключи не портятся), канонический JSON-шов, atomic save.
- **Генератор DSL** — 37 конструкторов активностей: от `set_vars` до
  CRM-классов date-filters-heavy (`task`, `create_deal`, `event_add`); детерминированные
  имена, `clone`/`branch`/`fwd_ref` для рефакторинга деревьев.
- **Гейты validate()** — висячие ссылки, undeclared-переменные, дубль-Name,
  IfElse-кардинальность, JSON-шрамы; ERROR блокируют запись.
- **FN-линт формул** — канон функций калькулятора (урок 4912): реестр
  `bpt_funcs.py`, правила FN-01..10, профиль портала `--portal=`.
- **Probe-режим** — дебаг-копия с журналированием переменных + отчёт по
  таймлайну сущности (рантайм-проверка формул без дизайнера).
- **Diff-режим** — структурный диф двух .bpt, иммунный к переименованию
  Name; exit-контракт для CI.
- **Диаграмма БП** — проекция дерева в шейпе дизайнера Б24: интерактивный
  HTML одним файлом (свёртка, инспекция, дифф-отчёт, ночная тема) +
  статика SVG/mermaid; покраска дельт и призраки удалённого по `--diff`.

## Модули и CLI

| Модуль | Роль | CLI |
|---|---|---|
| `bpt_serialize.py` | PHP-serialize/zlib/JSON-шов, roundtrip | `--all <dir>`, `--byte-exact`, per-file |
| `bpt_export.py` | REST-выгрузка шаблонов (webhook, ретраи, чекпоинт), diff, probe-отчёт, диаграмма | `fetch`/`diff A.bpt B.bpt`/`probe <etype> <eid>`/`diagram X.bpt [--diff BASE]` |
| `bpt_diagram.py` | проекция дерева (парс/краска/HTML-упаковка) + legacy D2/puml | вызывается через `bpt_export.py diagram`, напрямую — library-API |
| `bpt_build.py` | DSL-генератор + validate-гейты + probe-копия | `make`/`check`/`probe`/`validate` (+`--portal=`) |
| `bpt_graft.py` | хирургия существующих .bpt: locate-предикаты, edit-операции, гейты path-diff/form-lint/резолвер, byte-repro | `gates BASE.bpt PATCHED.bpt` |

## Documentation

| Guide | О чём |
|-------|-------|
| [Getting Started](docs/getting-started.md) | Установка, первый прогон, минимальное дерево |
| [Builder DSL](docs/builder.md) | Полный инвентарь конструкторов (из кода), паттерны, миграция старых скриптов |
| [Validate & FN-линт](docs/validate.md) | ERROR/WARN-рулы, FN-01..10, `--portal=`, корпус-инвариант |
| [Probe-режим](docs/probe.md) | Runbook рантайм-прогона и чтения журнала |
| [Graft](docs/graft.md) | Хирургия существующих .bpt: API, гейты, runbook патча, дебаг-копия как деплой-дисциплина |
| [Diagram](docs/diagram.md) | Проекция дерева в шейпе дизайнера: HTML-обёртка, краска дельт, призраки, вьюер, грабли |
| `etalons.md` | Книга форм: контейнер, активности, каноны, грабли |

## Гейты качества

- `python3 bpt_serialize.py --all etalons` — **N/N byte-exact** (N =
  размер корпуса, не хардкодится).
- Тесты — 150+ чеков в 5 жгутах, zero-dep (`python3 tests/test_*.py`) и
  pytest-адаптер (`pytest tests/`, контракт двойного запуска в
  `tests/conftest.py`).
- Регресс-база booking-chain (`tests/cases_booking_chain/`): 4 перехода живой цепочки —
  байт-в-байт канонам (`tests/test_bpt_graft.py`).
- `ruff check .` — чисто (селект в `ruff.toml`).
- `python3 tests/test_repo_lint.py` — WARN на сырую хирургию
  (`zlib.compress` вне канонных модулей; правки .bpt — только graft).
- `validate` на эталонах: 0 ERROR (кроме 2 известных висячков
  notify-history-canonical — детектор обязан их ловить).

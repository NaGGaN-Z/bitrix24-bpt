[Back to README](../README.md) · [Builder DSL →](builder.md)

# Getting Started

## Требования

- Python 3.10+ (используются `str | None`-аннотации). Зависимостей нет —
  zero-dep; для dev-гейтов: `pip install -r requirements-dev.txt`
  (pytest, ruff; hypothesis — guarded, опционален).

## Установка

Клонировать репо — этого достаточно. Все команды выполняются из корня
репозитория.

## Первый прогон (проверка окружения)

```bash
python3 bpt_serialize.py --all etalons
# ожидание: semantic-ok: 16/16

python3 tests/test_bpt_build.py        # --- 95/95 passed
pytest tests/ -q                       # 227 passed
```

Если semantic-ok < 16/16 — окружение трогать нельзя, разбирайтесь
до любой другой работы (инвариант корпуса, см. [Validate](validate.md)).

## Минимальный шаблон

```python
import bpt_build as B

tree = B.scaffold(
    children=[
        B.set_vars({'done': 'Y'}),
        B.if_else([
            ('готово', ['flag', '=', '1'], [B.change_stage('DONE')]),
            ('иначе', None, [B.im_notify(['user_1'], 'не готово')]),
        ]),
    ],
    variables={'done': B.variable('done', 'int'),
               'flag': B.variable('flag', 'int', default='0')},
    doc_fields={'ID': 'int', 'TITLE': 'string'},
)
B.save(tree, 'out.bpt')      # полный гейт: validate ERROR блокируют запись
```

Веточный кортеж `if_else`: `(title, condition|None, activities)`;
`condition = [переменная, оператор, значение]` (хвост-джойнер добавится
сам), `None` = else-ветка (не более одной). Полный набор конструкторов —
[Builder DSL](builder.md).

## Валидация без записи

```bash
python3 bpt_build.py validate tree.json --portal=box@22.100
# exit: 0 чисто / 1 ERROR / 2 usage; WARN не меняют exit
```

`--portal=` включает FN-03 (since-версии функций урока 4912 против
версии коробки; cloud-дефолт = без проверок) —
см. [Validate & FN-линт](validate.md).

## Дальше

- Формы активностей и каноны — `etalons.md` (книга, в корне репо).
- Рантайм формул: [Probe-режим](probe.md).
- REST-выгрузка шаблонов с портала: `python3 bpt_export.py fetch`
  (env `B24_WEBHOOK`; argv-URL светится в `ps` — используйте env).

## See Also

- [Builder DSL](builder.md) — полный инвентарь конструкторов.
- [Validate & FN-линт](validate.md) — гейты и правила формул.
- [Probe-режим](probe.md) — рантайм-проверка значений переменных.

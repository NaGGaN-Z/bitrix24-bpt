# Формат контейнера .bpt

Файл `.bpt` — шаблон бизнес-процесса Битрикс24 в транспортном формате:

```
.bpt = zlib(PHP-serialize(дерево шаблона))
```

## Дерево верхнего уровня

| Ключ | Содержимое |
|---|---|
| `VERSION` | int, у шаблонов нового дизайнера — `2` |
| `TEMPLATE` | список из одного корневого узла `SequentialWorkflowActivity` |
| `PARAMETERS` | параметры шаблона; пусто — `[]` (не `{}`) |
| `VARIABLES` | переменные: `{ИМЯ: {Name, Description, Type, Required, Multiple, Options, Default}}` |
| `CONSTANTS` | константы; пусто — `[]` |
| `DOCUMENT_FIELDS` | поля типа документа; копируются verbatim из эталонного экспорта того же типа |

## Узел активности

Каждый узел — ассоциативный массив с ключами в каноничном порядке:

```
Type        — строковый класс активности (см. ACTIVITIES.md)
Name        — уникальный идентификатор узла, латиница (движок, выражения)
Activated   — 'Y'
Node        — None (всегда)
Properties  — свойства класса; порядок ключей байт-критичен
Children    — дочерние узлы; у листовых — []
```

`Title` и `EditorComment` живут внутри `Properties` (человеческая метка
и комментарий дизайнера). `Name` уникален в пределах шаблона: импорт
шаблона с двумя одинаковыми `Name` отклоняется («has a duplicate»).

## Правила сериализации (PHP-семантика)

- строки: `s:<BYTELEN>:"...";` — длина в **байтах UTF-8**, не в символах
- bool: `b:0;` / `b:1;` — проверять ДО int (bool — подкласс int в Python)
- int: `i:N;`; float: `d:<num>;` с PHP `serialize_precision=-1` (`1.0` → `d:1;`)
- `None` → `N;`
- list → `a:N:{i:0;...;i:N-1;...}` — только для последовательных ключей `0..n-1`
- dict → `a:N:{k;v;...}`, порядок ключей = порядок вставки (как в PHP assoc)
- числовидные значения в Properties нового дизайнера — **строки**:
  `MessageType '4'`, `TimeoutDuration '40'`, `ApproveMinPercent '50'`

## Порядок ключей и байт-точность

PHP-массив упорядочен, поэтому порядок ключей `Properties` байт-критичен:
собирайте dict в эталонном порядке вставки (например, у `ReviewActivity`
`TaskButton*` идёт ДО `Timeout*`). Канонические порядки всех классов —
в `docs/ACTIVITIES.md` и в коде билдеров `bpt_build.py`.

## Гейт качества: roundtrip

Любой сгенерированный `.bpt` обязан проходить цикл
`parse → re-serialize → byte-compare` с исходным payload:

```
python3 bpt_serialize.py roundtrip FILE.bpt
python3 bpt_serialize.py --all DIR
```

Статусы: `BYTE-EXACT` (идеал), `semantic-ok` (минимум для приёмки),
`MISMATCH` (брак). Утилита печатает первый расходящийся байт с контекстом.

Для байт-точного roundtrip-теста используйте `php_unserialize_exact`
(`bpt_serialize.py`): в отличие от парсера `bpt_export.php_unserialize`
он сохраняет типы ключей dict (int остаётся int) и не сворачивает
списки — утилитарный парсер превращает `i:14;` в `'_14'`.

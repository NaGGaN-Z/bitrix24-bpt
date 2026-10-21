[← Builder DSL](builder.md) · [Back to README](../README.md) · [Probe-режим →](probe.md)

# Validate & FN-линт

`validate(tree, portal='cloud')` → `(errors, warnings)`. ERROR блокируют
`save()`/`make` (exit 1 у CLI), WARN — однострочный вывод, запись
продолжается. Автозапуск: make/check/save/probe. CLI:
`bpt_build.py validate TREE.json [--portal=…]` — exit 0/1/2 (WARN не
меняют exit).

## Ядро: ERROR

- IfElse-дети — только IfElseBranchActivity; кардинальность ≥ 2 ветки,
  ≤ 1 else (reject импорта).
- Name узлов пуст/дубль (reject импорта; см. `scaffold`-граблю в
  [builder.md](builder.md)).
- Висячая ссылка `{=Ns:Field}` (не namespace, не Name активности) и
  undeclared `{=Variable:X}`.
- Мусорные `{{...}}`-формы, незакрытые `{{=`/`{=`.
- explode-самоссылка (зацикливание doc-side, §7 runtime).
- Числовой str-ключ JSON (`'14'` — в PHP он int; грузи через `load_json`).
- FN-01/02/06/07 (таблица ниже).

## Ядро: WARN

- Функция/выражение в `value` условия (mixedcondition — не вычисляются,
  §5 runtime); `if()` с `{=...}`-рефами в value (пустой реф ломает парсер,
  §2).
- SV-функция в голом `=expr` (без `{{=}}` не вычислится, §1).
- explode в не-Multiple-приёмник (список не отрендерится, §7).
- Доменные рулы `expr_lint` (§3/§4/§7 runtime): datediff-`'%a'` без
  Hi-компенсации; money-поле в арифметике без `floatval()`; дата периода
  в datediff без клипа ±1; `[INFO]` round() в Multiple без прочерка.

## FN-линт: канон функций калькулятора (урок 4912)

Источник — урок «Функции калькулятора выражений» (dev.1c-bitrix.ru,
COURSE_ID=57, LESSON_ID=4912; обновлён 22.02.2026, выборка 2026-10-02):
40 функций с арностями и since-версиями модуля bizproc, операторы,
интервал-юниты. Реестр — `bpt_funcs.py` (zero-dep: данные + токенайзер
`{{=}}`-тел, устойчивый к вложенным вызовам и запятым в литералах);
правила — `expr_lint`/`validate` в `bpt_build.py`, rule-ID в сообщениях
`[FN-NN SEVERITY]`; ERROR блокируют save()/make, WARN/INFO — вывод.

Политика severity (владелец): функция вне документации И вне корпуса
12 эталонов = ERROR; corpus-only (работает, но не документирована) =
WARN. Промер корпуса 2026-10-02: corpus-only пуст — неизвестные имена
сегодня ERROR. Противоречие корпусу калибровкой = понижение правила,
не whitelist-налево.

| ID | Правило | Severity |
|---|---|---|
| FN-01 | имя функции вне doc∪corpus; corpus-only → WARN; `number_format` → fix-hint `numberformat` | ERROR |
| FN-02 | арность вне (min,max) реестра | ERROR |
| FN-03 | since-версия выше профиля портала: `validate(portal='box@X.Y')`, CLI `--portal=` (дефолт cloud = без проверок; save() всегда cloud) | WARN |
| FN-04 | `==`/`!=`/`&&`/`||` не документированы — fix-hint `=`, `<>`, `and`, `or` | WARN |
| FN-05 | юниты dateadd: неизвестный юнит — WARN; голый `m` = месяцы (минуты `i`/`min`) — INFO; `{=…}` внутри кавычек интервала — WARN (нужен `&`) | WARN/INFO |
| FN-06 | вложенное `{{=` внутри `{{=…}}` — внутренние выражения без обёртки | ERROR |
| FN-07 | `date()`/`locdate()` в арифметике `+ - * /` — возвращает СТРОКУ (конкатенация `&` — легитимна) | ERROR |
| FN-08 | `strtotime` с кириллицей в литерале — парсит только английские даты | WARN |
| FN-09 | оператор `%` — вычисление процентов, не modulo | INFO |
| FN-10 | голый реф в value-ветке `if()` без гварда в условии ЭТОГО if — пустая подстановка вырезается -> literal-мусор в документе (кейсы v20/v22; безопасны: гвард тем же if, r-паттерн, flow-непустые round/date) | ERROR |

Тесты: мутант на каждое правило + юнит-токенайзер
(`tests/test_bpt_funcs.py`) + корпус-гейт «0 ERROR»
(`fn_corpus_baseline_zero_error`).

## Корпус-инвариант

Прогон линтера на 12 эталонах — ноль находок (кроме 2 известных висячков
notify-history-canonical `Act_12` ×2 — детектор обязан их ловить, эталоны не
править). Корпус-промер зафиксирован в докстринге `bpt_funcs.py`;
`bpt_serialize.py --all etalons` — 12/12 byte-exact. Любая дельта от
baseline = фейл теста (ловит деградацию рул в боевые ложняки).

## Diff-режим (bpt_export)

`python3 bpt_export.py diff A.bpt B.bpt` — структурный диф двух шаблонов,
иммунный к переименованию Name: identity-ключ узла = Type + якоря без
значений (ключи SV-маппингов / поля-objects условий / Title); контент —
Properties минус летучие `Name/Node/EditorComment/_DesMinimized`;
`{=Name:...}`-ссылки транслируются по карте переименований; секции
VARIABLES/PARAMETERS/CONSTANTS/DOCUMENT_FIELDS — key-level. Выравнивание
детей — difflib по identity-ключам. Переименования переменных
(`VARIABLES` old→new с идентичной записью) нормализуются в identity-якорях
(SV-ключи, `propertyvariablecondition`, `ForEach.Variable`,
`mixedcondition` Variable/field) и репортятся ожидаемой секцией, а не
ложными ±узлами; ссылки `{=Variable:old}` в формулах остаются видимыми как
sv/prop old→new. Exit: 0 структурно идентичны /
1 есть дифф / 2 usage (годится для CI-гейта).

## See Also

- [Builder DSL](builder.md) — конструкторы и `scaffold`-грабля имён.
- [Probe-режим](probe.md) — фактические значения формул на живом прогоне.
- Скилл `bitrix24-bpt` — references/expression-runtime.md (уроки §1–§7).


## Прочерк-гард explode-групп (грабля #4 — WARN)

`$`-аппенды exploded-переменных (проходят `explode()`-SV) в одном узле с
РАЗНОРОДНЫМ гвардом — колонки без `if(x=0,'-',…)` при гвардёных соседях:
потенциал рассинхрона строк таблицы (mseparator=2 выбрасывает нули;
кейс v22->v23 — EG/Keys/Cleaning пустыми при pet=650). Severity WARN:
разнородность — норма корпуса (пустотность часто снята семантикой отбора
итератора, статически невидимой). Рефы на flow-непустые перемененные
(r-паттерн `if(x=0,'-',round(x,2))`) не считаются голыми.

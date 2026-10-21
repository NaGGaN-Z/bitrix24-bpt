# Graft — хирургия существующих .bpt

> `bpt_graft.py` — правка живых шаблонов портала: `load → locate → edit →
> save` с гейтами. Конструкторы (`bpt_build.py`) — новому; существующее
> правится ТОЛЬКО здесь: реэмиссия чужого дерева конструктором = риск
> перепривязки Name-ссылок и расползания байтов.

Спецификация модуля оплачена практикой задачи project-x (две ретро авторов
патчей): сырые патч-скрипты с самодельными `collect()` и `zlib.compress`
в обход гейтов — прод-фоллы (орфан-артефакт с дублями `A51294_…`/
`A62907_…` отклонён порталом при импорте). Гейты строились руками 18
версий — потребность реальная.

## Runbook патча

```bash
cd bpt

# 1. база: свежий экспорт шаблона с портала (или регресс-фикстура)
gt = GraftTree.load('BASE.bpt')       # exact-парс + snapshot + zlib-детект

# 2. правки — каждая операция = intent-строка в логе (stderr)
deal = gt.locate(kind='CrmGetDynamicInfoActivity',
                 name='TP_09', expect=1)
gt.expose_field(deal, 'UF_CRM_1774870980')          # ReturnFields+манифест
gt.sv_set(gt.locate(sv_keys=['eg_total'], expect=1), 'eg_raw', '…')

# 3. save с каноном (если есть) — побайтовая сверка = лучший дым-тест
gt.save('OUT.bpt', repro='CANON.bpt')

# отдельно: гейты на чужом артефакте (самодельный патч, дебаг-копия)
python3 bpt_graft.py gates BASE.bpt PATCHED.bpt    # exit 0/1/2

# микро-патч без файла-скрипта (1–3 операции, полный save-гейт):
python3 bpt_graft.py patch SRC.bpt DST.bpt \
    --sv NODE.KEY='значение' \            # sv_set по Name узла
    --sv-replace KEY='OLD::NEW:EXPECT' \  # контекстный replace (expect=замен)
    --declare VAR[:TYPE[:multi]] \        # объявить переменную
    --expose NODE.FIELD \                 # ReturnFields+манифест парой
    --gen K=V --skip-tpl 13 \             # Values во все генераторы кроме OLD
    --repro CANON.bpt                      # byte-repro + semantic-diff при фейле
```

**Деплой-дисциплина (unknown-unknowns):** портал — оракул, но проверять
на ДЕБАГ-КОПИИ шаблона (upsert в копию дёшев, ретрай мгновенный), не в
боевой. Форма, не доказанная корпусом дословно, на критичном пути ⇒
обязательный fail-loud гвард — иначе отказ ветки неотличим от отказа
логики.

## API

### load / snapshot / zlib-детект

`GraftTree.load(src)` — exact-парс (`php_unserialize_exact`), deepcopy-
снапшот (основа path-diff), снапшот находок валидатора и резолвера
(delta-гейты), детект zlib-уровня: заголовок уровень не кодирует —
перекомпрессия payload уровнями 1–9 с побайтовой сверкой против
исходного блоба. Уровень исходника критичен для byte-repro: патчи project-x
жмут уровнем 9, `save()` билдера — дефолтом 6. Портальный экспорт
(zlib чужой версии) даёт `None` → save пишет уровнем 9 (конвенция
патчей project-x) + WARN: байты канона в любом случае не сойдутся, портал
ресжатое принимает.

### locate / locate_all — первичные предикаты

```python
gt.locate_all(kind=…, title=…, sv_keys=…, pred=…, name=…, expect=N)
```

`kind` — точный Type; `title` — подстрока в Properties.Title;
`sv_keys` — узел содержит ВСЕ ключи VariableValue (подмножество);
`pred(props)` — произвольный предикат по Properties (контекстные правки:
«узел, где oe_percent в ЗНАЧЕНИИ oe_subtotal» — ключом не ловится);
`name` — вторичный хэндл. `expect=N` — обязательный контракт: нашли не
ровно N → отказ с перечислением найденных (Name+Title). Наследие
`assert patched == 1/==4` патчей project-x.

### edit-операции

| Операция | Семантика |
|---|---|
| `sv_set(ref, mapping/k, v)` | запись в VariableValue; списковые значения — легитимны (`rd_*`) |
| `sv_replace(key_glob, old, new, expect, refs)` | контекстный replace в формулах; glob по ключам; `expect` считает ЗАМЕНЫ-вхождения; `refs` — сужение контекста (кейс v18: шаблон в двух ветках, правится одна); однопроходность = вложенные вхождения не перевариваются повторно (v17-баг) |
| `props_append(ref, key, *vals)` | append в список (ReturnFields); дубль — отказ |
| `props_set / props_merge` | установка / dict-merge (позиция ключа сохраняется; Values генераторов docx) |
| `insert_child(anchor, node, after/before/first/last)` | позиция ЯКОРЕМ, не idx (патч выше сдвигает смысл); after/before — сосед якоря, first/last — дитё якоря; Name: нет → safe-alloc, коллизия → отказ |
| `expose_field(ref, field, shape_from=, shape=, manifest=)` | ReturnFields + DynamicEntityFields ПАРОЙ; шейп: манифест > готовый > сиблинг; UF-поля работают без манифеста (v19-факт), ID — требует (причуда); `manifest=False` — форма патча v19 |
| `declare(section, entries, strict=True)` | НОВЫЕ записи VARIABLES/CONSTANTS/PARAMETERS; Name: VARIABLES/CONSTANTS = ключ, PARAMETERS = лейбл (грабля booking-chain); multi-Default → `[]` (кейс mgr) |
| `var_rename(old, new, expect=)` | Переименование переменной: ключ VARIABLES (позиция сохраняется) + все привязки в теле (`VariableValue`-ключи, `{=Variable:old}`, `propertyvariablecondition[0]`, `ForEachActivity.Variable`, `mixedcondition` object=Variable/field). Name-лейбл записи не меняется (display-title). `expect` проверяется ДО мутации (отказ атомарен) |
| `payload_set(section, key, value)` | точечная правка секции: dict → merge, скаляр → Default записи; гранулярность path-diff сохраняется |
| `rename_rebind(ref)` | переименование + перепривязка `{=old:…}` по всему дереву; новое имя ТОЛЬКО safe-alloc (защита от орфан-дублей) |
| `insert_child(…, dedup=True)` | идемпотентность: Type+Title уже в точке вставки -> отказ (класс прод-фоллов «повторный патч поверх живой базы» — дубль bnum_r в ручном v24) |
| `generators_values(mapping, skip_tpl=)` | Values-merge во все генераторы docx кроме skip_tpl (идиома каждого патча project-x; канон: '13' = OLD) |
| `delete(ref) / move(ref, anchor, pos)` | удаление (пред-WARN о висячих ссылках) / перенос (цикл-гард, покрытие сдвига соседей) |

### save(dst, repro=) — полный контракт

1. **path-diff ⊆ intent** — изменилось только задекларированное;
   гранулярность: добавление поддерева ≠ правка родителя;
2. **delta-validate** — блокируют только НОВЫЕ ошибки (база с
   висячками правится не за один приём); FN-10 (голый реф в if-ветке —
   кейсы v20/v22) с flow-моделью непустоты (r-паттерн / гвард тем же
   if / round-обёртка — чисто); `save_raw` — запись мимо гейтов для
   канонов-регрессов и дебага;
3. **form-lint** (FL-01..05, ERROR всегда — база корпуса FL-чиста);
4. **резолвер ссылок** — новые нерезолвленные `{=Ns:Field}` блокируют;
5. self-check re-serialize + числовые str-ключи (JSON-шрам);
6. атомарная запись (tmp+fsync+rename); zlib-уровень исходника;
7. `repro=` — побайтовая сверка с каноном; при провале в отказ
   вкладывается semantic-diff (стек bpt_export, identity-матчинг) —
   ручной подсчёт расхождений не нужен.

## Гейты

### path-diff

Сравнение snapshot↔текущего с гранулярностью «добавление поддерева ≠
правка родителя»: элементы списка — added/removed ЦЕЛИКОМ; равные
середины серий — попарный спуск (разрозненные правки соседних узлов).
`check_intent_coverage`: `paths_changed ⊆ intent_paths` — самовольная
мутация мимо graft-операций = отказ с перечнем путей. Intent-пути
разрешаются лениво (id-обход) — вставка выше точки правки не ломает
сопоставление.

### form-lint (FL-01..05) — реестры растут ДАННЫМИ

Реестр `bpt_graft_forms.py` генерируется харвестом из
живого корпуса (etalons + booking-chain + bp_external, 46 .bpt, 41 класс); ручная
правка запрещена, сверка `--check`. Ран-тайм внешних путей не требует.

- **FL-01** key-case: case-fold совпадение с известным ключом класса =
  ERROR с подсказкой (конвенция в реальных БП СМЕШАННАЯ — условия
  lowercase); ключ чужого класса / неизвестный = WARN;
- **FL-02** Children: `None` = ERROR «портал ждёт массив» (прумер:
  0/46 корпуса; serialize-НЕловимо, портал падает count() — прод-фолл
  parking-sync);
- **FL-03** coupled-keys: ReturnFields ↔ DynamicEntityFields (обе
  стороны, корпус 104/104);
- **FL-04** записи секций: Default-форма по Multiple (multi → list|None,
  single → str; multi-Default значением не пережил импорт — кейс
  mgr) = ERROR; ключи вне каркаса = WARN;
- **FL-05** ForEach Object ∈ {Variable, Parameter, Constant, Document}
  (портал-канон v20; анти-паттерн — экстраполяция из одного режима).

### Резолвер ссылок

Availability-модель одним проходом: `{=Variable:X}` ⊆ VARIABLES и
`{=Parameter:X}` ⊆ PARAMETERS — STRICT (исторические undeclared v11/v12
`pf_hst`/`pf_gross` ловятся); `{=Act:Field}` — имя активности
+ для активностей чтения поле ∈ манифест ∪ ReturnFields ∪ `UF_*` (ID —
строго манифест); `{=Document:X}`/`{=Constant:X}` — вне модели
(манифесты экспортов недозаполнены: живые ссылки вне секций —
approval-stages, bp_external); System/GlobalConst/Template/User — free.

## Регресс-база booking-chain

`tests/cases_booking_chain/` — 4 перехода живой цепочки задачи project-x, каждый
скрипт = intent-декларация, выход **байт-в-байт** канону:

| Переход | Что закрывает |
|---|---|
| v17 → v18 | sv_replace с контекст-сужением (OE-процент) |
| v18 → v19 | expose_field (ReturnFields-only), sv_set пачками, declare пачками, props_merge Values, порядок вставки ключей |
| v20-portal → v21 | база с ручными правками владельца (не трогаем), multi-миграция payload_set |
| v21 → v22 | **канон-РЕГРЕСС** (save_raw): v22 несёт FN-10-багу — голый `{=Variable:bnum_val}` в if-ветке; гейты обязаны блокировать, кейс = байт-в-байт документация баги; expose_field с готовым шейпом (манифест ПАРОЙ), согласованные аппенды |
| v21 → v23 | паттерн-ЛЕКАРСТВО (полный save): bnum_r через IfElse-активность (!empty-ветки), прочерки в колонках, 4 источника extra |

Фикстуры в `tests/fixtures/booking-chain/` — тесты не зависят от внешних путей
(bpt зеркалится standalone). Синтетический корпус проверок —
`tests/test_bpt_graft.py` (33 @check, оба режима запуска).

## Репо-линт сырой хирургии

`python3 tests/test_repo_lint.py` — WARN (не блок, решение владельца) на
`zlib.compress` в .py вне канонных модулей: обход graft = возврат к
до-гейтевой эпохе. Чистое чтение (diagram-эмиттер) не флагается —
хирургия = запись.

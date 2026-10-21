[← Getting Started](getting-started.md) · [Back to README](../README.md) · [Validate & FN-линт →](validate.md)

# Builder DSL — инвентарь конструкторов

Полный публичный API `bpt_build.py` (43 публичные функции, синхронизирован с кодом
волной 4; из них 37 — конструкторы). Канонические примеры использования — тесты
(`tests/test_bpt_build.py`: каждый чек — рабочий сниппет); докстринги
функций — первоисточник деталей (канон-эталоны указаны в них). Формы
активностей и порядок ключей — [etalons.md](etalons.md).

## Каркас и узлы

| Функция | Суть |
|---|---|
| `scaffold(children, variables, doc_fields, ...)` | Каркас шаблона; сбрасывает счётчик имён — байты детерминированы; пустые секции обязаны быть `[]` |
| `act(a_type, title, props, children, name, ...)` | Универсальный узел активности; Name = `<токен>_<счётчик>` (уникален, латиница) |
| `sequence(children, title)` | Последовательный блок — эталонная обёртка веток заданий |
| `branch(props, children, a_type)` | Standalone-ветка IfElse (if_else-семейство построено на ней) |
| `clone(node, title)` | Deep-copy с БЕЗУСЛОВНЫМ переименованием Name + перепривязка внутренних `{=Name:...}`-ссылок |
| `fwd_ref(target_name, field)` | Ссылка на результат ПОЗДНЕЙ в билде активности (цель — явный `name=`) |
| `placeholder(marker, ...)` | Заглушка незакрытой функциональности (EmptyBlockActivity) |

⚠ `scaffold` сбрасывает счётчик имён: инкрементальный `Children.append()`
после него даёт **дубль Name** (reject импорта) — дерево собирается одним
`scaffold`.

## Переменные и поля

| Функция | Суть |
|---|---|
| `variable(name, v_type, ...)` | Объявление переменной (базовая и title-форма) |
| `set_vars(mapping, title)` | `{ИМЯ: значение|выражение}`; вычисляемое — `{{=...}}`, подстановка — `{=...}` |
| `set_fields(mapping, ...)` | Запись полей ТЕКУЩЕГО документа (props-порядок эталонный) |

## Ветвления и циклы

| Функция | Суть |
|---|---|
| `if_else(branches)` | `(title, [перем, оператор, значение] \| None, acts)`; `None` = else (≤1) |
| `if_else_field(branches)` | По полю документа: `[поле, оператор, значение]`; операторы `=`, `empty`, `!empty` |
| `if_else_mixed(branches)` | mixedcondition: `[(object, field, operator, value, joiner)]`; object — имя активности / `Variable` / `Document` |
| `while_loop(condition, children, ...)` | `[перем, оператор, значение, '0']` — свойство выхода из цикла |
| `for_each(variable_name, children, source, ...)` | Итератор по мульти-значению (source=`Variable`/`Document`) |

## Задания (интерактив)

| Функция | Суть |
|---|---|
| `approve(users, name, ...)` | Согласование, две кнопки; `name` = СТАТУСНОЕ имя (node-Name — `node_name=`) |
| `request_info(users, fields, ...)` | Запрос доп.информации (заполнение полей; optional-форма есть) |
| `review(users, ...)` | Ознакомление, одна кнопка |
| `task(title, responsible, ...)` | Задача (Task2Activity, полная форма: accomplices/auditors/deadline/crm_link; эталон date-filters-heavy) |

`name`/`node_name=`: в заданиях `name` — статусное имя (Properties.Name);
node-Name под `{=Name:...}`-ссылку — только `node_name=`. В `act()`
наоборот: `name=` — сразу node-Name (ловушка молчаливой порчи).

## CRM

| Функция | Суть |
|---|---|
| `create_dynamic(target_etid, fields, ...)` | Создание элемента СП (+`blank_fields`/`blank_multi`/`system_blanks`) |
| `create_dynamic_item_id_expr(node)` | `{=Name:ITEM_ID}`-ссылка на ID созданного элемента |
| `update_dynamic(target_etid, fields, ...)` | Обновление ЧУЖИХ элементов СП; таргетинг — строго ИЛИ `item_id_expr` ИЛИ `filter_conditions` |
| `get_dynamic_info(etid, return_fields, filter_rows, ...)` | Чтение элементов СП по фильтру (эталон loop-idle); результаты — доп. поля активности |
| `get_deal_info(return_fields, filter_rows, ...)` | Чтение полей сделки из БП на любом документе (date-filters-heavy) |
| `create_deal(fields, ...)` | Создание сделки из БП любого типа документа (date-filters-heavy) |
| `change_stage(target_status)` | Смена стадии ТЕКУЩЕГО документа — ставить ПОСЛЕДНЕЙ активностью ветки |
| `event_add(text, event_type, users, ...)` | Событие таймлайна CRM (date-filters-heavy) |

## Сотрудники

| Функция | Суть |
|---|---|
| `get_user(user_parameter, ...)` | Нативный резолвер (`boss`/резерв/пропуск отсутствующих) |
| `get_user_info(user, fields)` | Чтение полей профиля (`['user_231']` или выражение) |

## Коммуникации и прочие действия

| Функция | Суть |
|---|---|
| `im_notify(users_to, message, ...)` | Уведомление; ТЕКСТ в Message (message_type — ключ `MESSAGE_TYPE`) |
| `timeline_comment(text, users)` | Комментарий в таймлайн (неизменяемая история) |
| `absence_entry(users, date_from, date_to, ...)` | График отсутствий (legacy-класс: НЕТ delete/update) |
| `calendar_event(name, date_from, ...)` | Событие календаря (Calendar2Activity) |
| `delay(duration, unit, until, ...)` | Пауза: interval (`unit` s/m/h/d) ИЛИ дата `until` |
| `terminate(title)` | Мягкий стоп текущего запуска (bare-форма) |
| `terminate_others(title)` | Убить все процессы ЭТОГО шаблона для документа, кроме текущего |
| `start_workflow(template_id, ...)` | Запуск подчинённого шаблона (TemplateId — ID НА ПОРТАЛЕ; канон loop-counter) |

## Гейты и IO

| Функция | Суть |
|---|---|
| `validate(tree, portal='cloud')` | Ядро валидатора → `(errors, warnings)`; правила — [validate.md](validate.md) |
| `expr_lint(s, sink_multiple, portal)` | Линтер формул: доменные рулы + FN-01..09 |
| `validate_refs(tree)` | Ссылочная целостность `{=Ns:Field}` + синтаксис `{{=...}}` |
| `save(tree, path)` | Сериализация с гейтами (ERROR блокируют), атомарная запись |
| `load(path)` | Чтение .bpt |
| `probe(tree, tag, users)` | Дебаг-копия с журналированием — [probe.md](probe.md) |

## Миграция старых build-скриптов

Карта точек замены при следующей регенерации клиентских билд-скриптов.
Код вне репо НЕ трогается — только перечень:

| Скрипт | Локальный паттерн | Замена в `bpt_build` |
|---|---|---|
| legacy-скрипты с VARS-диктами (голые `{Name, Description, Type, ...}`) | `variable()` (базовая и title-форма) |
| 〃 | `D = '{=Document:%s}'` / `V = '{=Variable:%s}'` шорткаты | прямые f-строки; forward-ссылки — `fwd_ref()` + явный `name=` на цели |
| 〃 | клиентский `ENUM` (hotel/flight/…) | остаётся ЛОКАЛЬНЫМ: клиентский слой поверх платформенных `MESSAGE_TYPE`/`ABSENCE_TYPE` |
| `docs/work/2026-10-02-acme-tur-tz/build-dispatcher.py` | `uid()`/`task()` — deepcopy + ручной Name | `clone(node, title=…)` |
| 〃 | `par()`/`branch()` — инлайн-дикты IfElseBranch | `branch(props, children)` |
| 〃 | `ENUM_PREPAY`/`ENUM_FULL` | локальный слой над словарями-константами |
| 〃 | `reg_create` объявлен ДВАЖДЫ (затеняется) | к сведению: при миграции схлопнуть в одну |

Типичный legacy-скрипт (**паттерны ранних версий**):
читать только вместе с этой таблицей; канонические примеры — тесты.

## See Also

- [Validate & FN-линт](validate.md) — гейты записи и правила формул.
- [Getting Started](getting-started.md) — установка и первый прогон.
- [etalons.md](etalons.md) — формы активностей и каноны порядка ключей.

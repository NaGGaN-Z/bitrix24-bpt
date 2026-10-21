# Эталоны активностей дизайнера: формы, каноны, грабли

Справочник по внутренним формам шаблонов бизнес-процессов Битрикс24
(.bpt), восстановленным реверс-инжинирингом реальных шаблонов рабочих
порталов. Официальной документации по формату нет: всё ниже снято с
экспортов дизайнера и подтверждено побайтовым roundtrip-инвариантом
(эталонный корпус собирается и разбирается без единого отличия —
`python3 bpt_serialize.py --all etalons`).

Примеры ссылаются на эталонный корпус репозитория (`etalons/*.bpt`) —
файлы с нейтральными именами, содержимое снято с живых шаблонов.

## Контейнер и сериализация

- Формат файла: `zlib(PHP-serialize)`. Payload верхнего уровня —
  словарь с ключами VERSION / TEMPLATE / PARAMETERS / VARIABLES /
  CONSTANTS / DOCUMENT_FIELDS.
- Числовидные значения — СТРОКИ: MessageType '4', TimeoutDuration '40',
  ApproveMinPercent '50'.
- Float: PHP `serialize_precision=-1` (1.0 → `d:1;`) — writer
  воспроизводит.
- Порядок ключей Properties байт-критичен: собирать dict в эталонном
  порядке вставки (пример: review — TaskButton* ДО Timeout*).
- Каждый узел активностей несёт `'Node': None` (порядок ключей узла:
  Type, Name, Activated, Node, Properties, Children — измерено на 332
  узлах корпуса). Исключения существуют — см. CrmControlNotify ниже.
- Пустые PARAMETERS/VARIABLES/CONSTANTS — `[]`, не `{}`.
- Точный парсер: `bpt_serialize.php_unserialize_exact` (int-ключи
  dict'ов сохраняются; lossy-парсер `bpt_export.php_unserialize` для
  байтовых тестов не годится).

## Смарт-процессы (CRM SP)

### CrmCreateDynamicActivity — «Создать элемент смарт-процесса»
```
DynamicTypeId: "1038"              # ЦЕЛЕВОЙ entityTypeId, СТРОКОЙ
OnlyDynamicEntities: "Y"
DynamicEntitiesFields: {           # ВСЕ поля цели списком, пустое = не ставить
  "1038_TITLE": "…",               # ключ = <etid>_<ПОЛЕ>  (с префиксом etid!)
  "1038_UF_CRM_6_NIGHTS": "",      # значение = литерал ИЛИ выражение
  "1038_UF_CRM_6_TOURISTS": {},    # мульти-crm = {}
  ...
}
Title: "Создать элемент смарт-процесса"
```

### CrmUpdateDynamicActivity — «Изменить элемент смарт-процесса»
```
DynamicTypeId: "1040"
DynamicId: ""                      # прямой ID цели (пусто → фильтр)
DynamicFilterFields: {             # фильтр целей обновления
  "items": [[
    {"object":"Document","field":"ID","operator":"=",
     "value":"{=Document:TIME_CREATE}"},
    "AND"
  ]]
}
DynamicEntitiesFields: {           # БЕЗ префикса etid (отличие от Create!)
  "UF_CRM_8_PRICE_NETTO": ""
}
```

**Каноничный таргетинг обновления.** `DynamicId` выражения НЕ
вычисляет (строка уходит как литерал → фоллбэк «первый элемент типа»).
Рабочая форма — фильтр по родному полю ID цели:

    DynamicFilterFields.items = [[
      {"object":"Document", "field":"ID", "operator":"=",
       "value":"{=Document:UF_CRM_6_SVC_HOTEL_ID}"}, "AND"
    ]]

Условие на `ID`, значение — выражение из связующего поля документа.

**Разгадка энумов.** CrmCreateDynamicActivity пишет enumeration-поле
ТОЛЬКО в хэш-форме дизайнера (внутренние md5-подобные идентификаторы
значений, генерятся дропдауном дизайнера; в .bpt лежат строками-хэшами
в DynamicEntitiesFields). Int-ID и строковое значение молча теряются
(null/'' в созданном элементе). Каноничный цикл для нового портала:
сгенерить шаблон с ПУСТЫМ энум-полем → импорт → проставить значения
дропдауном в дизайнере → экспорт → этот .bpt и есть деплой-артефакт
(хэши привязаны к конкретному порталу/энуму).

### SetVariableActivity — «Изменение переменных»
```
VariableValue: {"Variable1": "{=Document:CRM_ID}"}
```
Переменные объявляются в VARIABLES: `{Name:{Name,Description,Type,
Required,Multiple,Options,Default}}`; Type: string/integer/double/
date/bool/select/…

### SetFieldActivity — «Изменение документа» (ТОЛЬКО свой документ!)
```
FieldValue: {"UF_CRM_6_HOTEL": "…"}; ModifiedBy: {}; MergeMultipleFields: "N"
```
Для обновления ЧУЖИХ элементов СП не годится → только
CrmUpdateDynamicActivity.

## Сделки и задачи (эталон date-filters-heavy)

### CreateCrmDealDocumentActivity — «Создание новой сделки»
```
Fields: {   # канон-порядок ключей:
  TITLE, OPPORTUNITY, CURRENCY_ID, ACCOUNT_CURRENCY_ID, ASSIGNED_BY_ID,
  CATEGORY_ID, STAGE_ID, CLOSED, TYPE_ID, COMMENTS, CONTACT_ID,
  COMPANY_ID, SOURCE_ID }        # значения — литералы|выражения
```

### CrmGetDynamicInfoActivity — режим СДЕЛКИ (чтение полей сделки из БП смарт-процесса)
```
DynamicTypeId: "2"              # entityTypeId сделки
OnlyDynamicEntities: "N"        # отличие от режима СП ("Y"!)
DynamicFilterFields.items: [[
  {"object":"Document","field":"ID","operator":"=",
   "value":"{=Document:PARENT_ID_2 > id}"} ]]   # '> id' — извлечь число из crm-поля
ReturnFields: [...]             # результаты — {=Name:FIELD}
DynamicEntityFields: {FIELD:{Name,Type,BaseType}, Document:{Default:
  [crm, CCrmDocumentDeal, DEAL]}}
```
Кеш полей сделки в GetDynamic раздут дизайнером до 411 ключей
(CONTACT.*/COMPANY.*) — генератору достаточно кеша по ReturnFields.

### Task2Activity — участники
`ACCOMPLICES`/`AUDITORS`: список 'user_N' (пример: `['user_14','user_58']`);
пусто — `''` (строка, не список). Боевой канон полей:
CREATED_BY/RESPONSIBLE_ID = `{=Document:ASSIGNED_BY_ID}`, PRIORITY '1',
ALLOW_CHANGE_DEADLINE 'Y', ALLOW_TIME_TRACKING 'Y', TASK_CONTROL 'Y',
REQUIRED_RESULT 'N', TAG_NAMES {} DEPENDS_ON {}; props-хвост:
HoldToClose 'N', AUTO_LINK_TO_CRM_ENTITY 'Y', AsChildTask '',
CheckListItems {}, TimeEstimateHour/Min ''.

### Прочие формы того же корпуса
- CrmEventAddActivity: `{EventType:'INFO', EventText, EventUser:{}}` —
  событие таймлайна, НЕ комментарий (REST-ридера нет, см. probe-режим).
- DelayActivity: `TimeoutTime: '{=Variable:V_DL} 12:00:00'` — дата+время
  строковой конкатенацией.
- StartWorkflowActivity: `TemplateId='220',
  DocumentId='{=Document:PARENT_ID_2}', TemplateParameters={'update':
  'C14:UC_6LZY0A'}` — литерал стадии в параметре.

## Глобальные константы портала (эталон company-stage)

`{=GlobalConst:Constant1790938720619}` — ссылка на константу в props
активности (в эталоне — CrmTimelineCommentAdd.CommentText). Значение
живёт НА ПОРТАЛЕ (Настройки БП → Константы), в .bpt — только ссылка:
смена значения без правки шаблонов. CONSTANTS самого шаблона при этом
пустые; namespace GlobalConst легитимен и при пустых CONSTANTS
(эталон loop-counter, whitelist в bpt_build).

## CrmControlNotifyActivity — «Уведомить руководство»

Штатная CRM-механика уведомления руководителю ответственного. Форма
(порядок ключей эталонный):
```
MessageText: "<текст>"
ToUsers: {}                # доп. адресаты; пусто — пустой PHP-массив
ToHead: "Y"                # руководителю ответственного
```
Билдер: `control_notify(message, to_head='Y', users=None)`. Гоча:
пустой ToUsers через roundtrip читается как `[]` (PHP `a:0:{}`
амбивалентен) — байты не меняются. Канон УЗЛА 5-ключевой: БЕЗ `Node`,
`Properties` вторым ключом (Type→Properties→Name→Activated→Children) —
экспорт дизайнера этого класса Node не несёт; act()-форма давала
расхождение байт (ловится реплика-фикстурой в тестах).

## Задания согласования (эталоны stage-flow / stage-flow-extended)

Реализованы в bpt_build.py: terminate_others (TerminateActivity,
KillWorkflow=Y, TerminateType=allExceptCurrentByDocumentAndTemplate —
дедупликатор инстансов диспетчера), approve (ApproveActivity: кнопки
TaskButton1/2Message, CommentRequired N/Y/YR — YR обязателен только при
Decline; ApproveMinPercent — СТРОКА; дети [Accept-ветка,
Decline-ветка]), request_info (+optional=RequestInformationOptionalActivity
c Cancel; RequestedInformation поля→результаты {=ActivityName:Name}),
review, delay (интервал|дата), change_stage (CrmChangeStatusActivity),
timeline_comment, im_notify, absence_entry (AbsenceActivity — legacy,
НЕТ delete/update), calendar_event (Calendar2Activity).

- AbsenceType enum: подтверждены VACATION, LEAVESICK (в дропдауне
  больше).

## Циклы

WhileActivity: `propertyvariablecondition [["cikl","=","Значение","0"]]`
(новая форма) или `mixedcondition [{object:"Variable",field,operator,
value,joiner:"0"}]` (старая). Тело — `Children=[SequenceActivity]`.

ForEachActivity: `{'Variable':'ids','Object':'Variable'}` — итерация по
мульти-переменной; значение итерации — через доп.результаты.

Билдер: `bpt_build.while_loop(condition, children)` / `for_each(name,
children)`. Подсчёт мульти-поля без count(): ForEach по мульти-полю +
счётчик `V_COUNT = V_COUNT + 1` внутри ветки, после цикла —
`ADULTS = {=Variable:V_COUNT}`.

Условная дата в переменной — штатный if() в выражении, IfElse не нужен:

    V_HOTEL_FROM = {{=if({=Variable:V_ARRIVE}="Y",
        dateadd({=Variable:V_START}, "1 day"), {=Variable:V_START})}}

## Специфика облака (собрано на живых порталах)

- REST `userfieldconfig.add` и AJAX `main.userfieldconfig.add`
  (lowercase) мертвы («Вы не можете создавать пользовательские поля»)
  даже из админ-сессии. Рабочий путь: AJAX `main.userFieldConfig.add`
  (camelCase!), `field[entityId]=CRM_<type_id>`, csrf в заголовке
  `X-Bitrix-Csrf-Token`.
- UF-имена: `UF_CRM_<TYPE_ID>_<LOGICAL>` (TYPE_ID ≠ entityTypeId!).
- `crm.item.*` запись значений — ТОЛЬКО camelCase-ключи (`ufCrm6Nights`),
  UPPER-ключи молча игнорируются. Чтение в ответах тоже camelCase.
- `crm.item.fields` UF-поля СП НЕ показывает; верификация = item.add/get.
- `crm.type.add`: isClientEnabled ставится ТОЛЬКО при создании (потом —
  «Client is disabled in the provided factory»).
- `bizproc.workflow.template.add` с вебхука: ACCESS_DENIED «Application
  context required» → **деплой шаблонов = UI-импорт .bpt**.
- `bizproc.workflow.start` по REST разрешён — фолбэк запуска.

## ГОТЧА: UF типа `crm` — настройки привязки

`field[settings][ENTITY_TYPE]=CONTACT` при создании (AJAX/REST) МОЛЧА
игнорируется — поле создаётся с `settings={"LEAD":"Y"}` (привязка к
лидам!). Правильная форма settings для UF:crm — поэнтитетный флаг-мап:

    settings: {"CONTACT":"Y","LEAD":"N","COMPANY":"N","DYNAMIC_1038":"Y", …}

Лечится постфактум: `userfieldconfig.update` (REST) settings-мапу
применяет корректно.

## Probe-режим: контракт журналирования переменных

У SV-формул нет рантайма — ошибки видны только на боевом прогоне.
Probe = дебаг-копия шаблона, журналирующая значения рабочих переменных
после каждого set_vars. Нативного ридера журнала выполнения БП через
REST нет (`bizproc.activity.log` только пишет и только под
app-активности — дизайнер-шаблоны туда не пишутся), поэтому канал —
таймлайн-комментарии.

### Канал: CrmTimelineCommentAdd

- Активность: `timeline_comment()` (bpt_build.py) — форма
  корпус-калибрована; автор по умолчанию `{=Document:ASSIGNED_BY_ID}`.
- Носитель: таймлайн ТЕКУЩЕГО документа (probe-копия гоняется на
  тестовой сущности).
- Чтение: `crm.timeline.comment.list`, filter `{ENTITY_ID, ENTITY_TYPE}`
  (ENTITY_TYPE: 'deal'/'lead'/'contact'/'company'; для СП —
  `DYNAMIC_<entityTypeId>`, голое число → HTTP 400), order `{ID: ASC}`,
  пагинация `start` (50/стр), select ID, CREATED, AUTHOR_ID, COMMENT;
  серверный поиск по тексту не гарантируется → фильтр по тегу
  клиентский.
- Fallback для не-CRM документов: im_notify владельцу + чтение
  `im.dialog.messages.get`.

### Текст probe-комментария (контракт; парсер обязан его держать)

```
[probe] after <Name исходной SetVariableActivity>
<имя_переменной>={=Variable:<имя_переменной>}
...                                              # одна строка на КАЖДЫЙ
                                                  # ключ VariableValue SV
```

- Строка 1 — тег `[probe]` (якорь фильтра) + уникальный Name исходного
  SV (однозначная идентификация шага).
- Строки данных: движок подставляет `{=Variable:X}` значением;
  не-строковые типы рендерятся строкой; значение до конца строки —
  «=» внутри значения допустим, сплит по ПЕРВОМУ «=».
- Многострочность — `\n` в CommentText; Title вставки в дизайнере:
  `[probe] after <Name SV>`.
- set_vars внутри while/foreach даёт СЕРИЮ комментариев (хронология
  итераций) — фича, не дубль.

### Рантайм-факты живых прогонов

- Канал подтверждён с двух независимых писателей (probe-вставка и
  скопированная вручную в дизайнере CrmTimelineCommentAdd).
- Многострочность `\n` в CommentText сохраняется.
- Подстановка в тексте: голые `{=...}`-ссылки подставляются;
  `{{=...}}`-выражения НЕ вычисляются (рендерятся сырой строкой) —
  журналировать значения ТОЛЬКО через `{=Variable:X}`.
- Автор комментария обязателен: `CommentUser=['{=Document:ASSIGNED_BY_ID}']`
  на элементе с ПУСТЫМ ответственным молча не пишет ничего (активность
  «выполняется», комментариев нет) — перед прогоном убедиться, что у
  тестовой сущности проставлен ответственный.
- SV с конкатенацией (`V_M_FULL = {{='ex_' . {=Document:ID} . '_full'}}`)
  после выполнения хранит СЫРУЮ формулу с подставленными внутренними
  рефами (`{{='ex_' . 4 . '_full'}}`), а не результат конкатенации —
  журнал показывает это напрямую.
- Событийные активности пишут НЕ-комментарий (событие таймлайна) —
  REST-ридера для них нет; DBG-канал = только CrmTimelineCommentAdd.

Мини-фикстура дерева для тестов генератора:
`tests/fixtures/probe_tree.json` (SV на корневом уровне, SV в ветках
if_else, SV в теле while — все три уровня вложенности, где probe обязан
вставить журналирование). Живой журнал (обезличен):
`tests/fixtures/probe_journal_live.json`.

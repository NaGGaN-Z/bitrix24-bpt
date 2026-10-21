# Эталоны активностей нового дизайнера (портал portal-a)

Источники: `bpt/etalons/hotel-booking.bpt`, `hotel-booking-v2.bpt` (UI-экспорт юзера,
СП «Туры»), корпус `etalons-ref/` (6 боевых .bpt). Дерево целиком:
`out/etalon-bp24.json`, `out/etalon-bp24_2.json`.

## Сериализация (T6, закрыт)

- `bpt_serialize.py`: writer BYTE-EXACT 7/7 по корпусу + оба эталона портала.
- Контейнер: `zlib(PHP serialize)`, payload верхнего уровня:
  VERSION/TEMPLATE/PARAMETERS/VARIABLES/CONSTANTS/DOCUMENT_FIELDS.
- Гоча парсера `bpt_export.php_unserialize`: int-ключи dict'ов портит
  (`i:14;` → `'_14'`) и сворачивает списки — для байт-точного теста в
  `bpt_serialize.php_unserialize_exact` (int-ключи сохраняются).
- Float: PHP `serialize_precision=-1` (1.0 → `d:1;`), writer это делает.

## Классы активностей нового дизайнера (подтверждено экспортом)

### CrmCreateDynamicActivity — «Создать элемент смарт-процесса»
```
DynamicTypeId: "1038"              # ЦЕЛЕВОЙ entityTypeId, СТРОКОЙ
OnlyDynamicEntities: "Y"
DynamicEntitiesFields: {           # ВСЕ поля цели списком, пустое = не ставить
  "1038_TITLE": "Туры",            # ключ = <etid>_<ПОЛЕ>  (с префиксом etid!)
  "1038_UF_CRM_6_NIGHTS": "",      # значение = литерал ИЛИ выражение
  "1038_UF_CRM_6_TOURISTS": {},    # мульти-crm = {} (список выражений?)
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
    {"object":"Document","field":"ID","operator":"=","value":"{=Document:TIME_CREATE}"},
    "AND"
  ]]
}
DynamicEntitiesFields: {           # БЕЗ префикса etid (отличие от Create!)
  "UF_CRM_8_PRICE_NETTO": ""
}
```
Фильтр каскада: `field=UF_CRM_8_TOUR_LINK, value={=Document:ID}` + второе
условие по `UF_CRM_8_ROUTE`/`UF_CRM_8_SERVICE_TYPE` для выборки типа услуги.
(Поведение «все совпавшие или первый» — выяснится на E2E; для демо-кейса
совпадение единично.)

### SetVariableActivity — «Изменение переменных»
```
VariableValue: {"Variable1": "{=Document:CRM_ID}"}
```
Переменные объявляются в VARIABLES: {Name:{Name,Description,Type,Required,
Multiple,Options,Default}}; Type: string/integer/double/date/bool/select/...

### SetFieldActivity — «Изменение документа» (ТОЛЬКО свой документ!)
```
FieldValue: {"UF_CRM_6_HOTEL": "..."}; ModifiedBy: {}; MergeMultipleFields: "N"
```
Для обновления ЧУЖИХ элементов СП он не годится → только CrmUpdateDynamicActivity.

## Специфика портала portal-a (собрано в T4/T5)

- REST `userfieldconfig.add` и AJAX `main.userfieldconfig.add` (lowercase) —
  мертвы («Вы не можете создавать пользовательские поля») даже из админ-сессии.
  Рабочий путь: AJAX `main.userFieldConfig.add` (camelCase!),
  `field[entityId]=CRM_<type_id>`, csrf в заголовке `X-Bitrix-Csrf-Token`.
- UF-имена: `UF_CRM_<TYPE_ID>_<LOGICAL>` (type_id = 6/8, НЕ entityTypeId 1038/1040!).
- `crm.item.*` запись значений — ТОЛЬКО camelCase-ключи (`ufCrm6Nights`),
  UPPER-ключи молча игнорируются. Чтение в ответах тоже camelCase.
- `crm.item.fields` UF-поля СП НЕ показывает; верификация = item.add/get.
- Энумы (b_user_field_enum, глобальные ID): трансфер 44=групповой, 46=индивидуальный;
  тип услуги 48=проживание, 50=перелёт, 52=трансфер, 54=страховка, 56=страховка от невыезда.
- `crm.type.add`: isClientEnabled ставится ТОЛЬКО при создании (потом —
  «Client is disabled in the provided factory»).
- `bizproc.workflow.template.add` с вебхука: ACCESS_DENIED «Application context
  required» → **деплой шаблонов = UI-импорт .bpt** (T3 закрыт негативно).
- `bizproc.workflow.start` по REST на demo-лицензии разрешён (доки) — фолбэк запуска.

## Маппинг полей портала (живой источник: out/ufmap.json)

- Туры: type_id=6, entityTypeId=1038, 20 полей (238–276)
- Услуги: type_id=8, entityTypeId=1040, 22 поля (278–320)
- Логика имени: `UF_CRM_<type_id>_<LOGICAL>`; в crm.item REST: `ufCrm<tid><Logical>`

## КАНОНИЧНЫЙ таргетинг CrmUpdateDynamic (найден юзером, работает)

`DynamicId` выражения НЕ вычисляет (строка уходит как литерал → фоллбэк
«первый элемент типа»). Рабочая форма — фильтр по ID цели:

    DynamicFilterFields.items = [[
      {"object":"Document", "field":"ID", "operator":"=",
       "value":"{=Document:UF_CRM_6_SVC_HOTEL_ID}"}, "AND"
    ]]

т.е. условие на родное поле `ID` цели, значение — выражение из тех-поля тура.
Тех-поля тура (UF_CRM_6_SVC_*_ID, string) заполняет БП-1 через
дополнительный результат create-активности: `{=A_...:ItemId}`.

`V_HOTEL_FROM` в каскаде = `{{=if({=Variable:V_ARRIVE}="Y", dateadd({=Variable:V_START}, "1 day"), {=Variable:V_START})}}` — if() штатный, IfElse не нужен.

## ГОТЧА: UF типа `crm` — настройки привязки

`field[settings][ENTITY_TYPE]=CONTACT` при создании (AJAX/REST) МОЛЧА
игнорируется — поле создаётся с `settings={"LEAD":"Y"}` (привязка к лидам!).
Правильная форма settings для UF:crm — ПОЭНТИТЕТНЫЙ флаг-мап:

    settings: {"CONTACT":"Y","LEAD":"N","COMPANY":"N","DYNAMIC_1038":"Y", ...}

Лечится постфактум: `userfieldconfig.update` (REST) settings-мапу применяет
корректно (проверено). Затронуто и исправлено: TOURISTS→CONTACT,
PARTNER→COMPANY, TOUR_LINK→DYNAMIC_1038, TOURIST_LINK→CONTACT.

## РАЗГАДКА ЭНУМА (финал)

`CrmCreateDynamicActivity` пишет enumeration-поле ТОЛЬКО в хэш-форме дизайнера
(внутренние md5-подобные идентификаторы значений, генерятся дизайнером при
выборе через дропдаун; в .bpt лежат в DynamicEntitiesFields как строки-хэши).
Int-ID и строковое значение — молча теряются (null/'' в созданном элементе).

Каноничный цикл для нового портала: сгенерить шаблон с ПУСТЫМ энум-полем →
импорт → в дизайнере проставить значения дропдауном → экспорт → этот .bpt и
есть деплой-артефакт (хэши привязаны к конкретному порталу/энуму).

Живое подтверждение: СП «Услуги» тура 4 — все 7 услуг с типами 48/50/52/54/56.

## Подсчёт мульти-поля (туристы) — отложено в этап 2

Чистого count() в выражениях нет. Рабочий костыль: ForEachActivity по
мульти-полю (туристы) + счётчик `V_COUNT = V_COUNT + 1` внутри ветки,
после цикла — `ADULTS = {=Variable:V_COUNT}`. Использование: авто-кол-во
гостей в отеле и per-person услуги (услуга на каждого туриста со своими
датами — «прописывает отдельно услуги» из звонка). Форма ForEachActivity —
по эталону из bpt_migration (доп.результаты = значение итератора).

## Циклы — эталонные формы (из bpt_migration, НЕ новый дизайнер)

WhileActivity: `propertyvariablecondition [["cikl","=","Значение","0"]]`
(новая форма) или `mixedcondition [{object:"Variable",field,operator,value,joiner:"0"}]`
(старая). Тело — Children=[SequenceActivity].
ForEachActivity: `{'Variable':'ids','Object':'Variable'}` — итерация по
мульти-переменной; значение итерации — через доп.результаты.
Билдер: `bpt_build.while_loop(condition, children)` / `for_each(name, children)`.
Сценарий: ForEach по туристам + счётчик = кол-во гостей; per-person услуги — этап 2.

## Новые классы (2026-09-02, эталоны stage-flow/stage-flow-extended портала acmelodge)

Реализованы в bpt_build.py: terminate_others (TerminateActivity,
KillWorkflow=Y, TerminateType=allExceptCurrentByDocumentAndTemplate —
дедупликатор инстансов диспетчера), approve (ApproveActivity: кнопки
TaskButton1/2Message, CommentRequired N/Y/YR — YR обязателен только при
Decline; ApproveMinPercent — СТРОКА; дети [Accept-ветка, Decline-ветка]),
request_info (+optional=RequestInformationOptionalActivity c Cancel;
RequestedInformation поля→результаты {=ActivityName:Name}), review, delay
(интервал|дата), change_stage (CrmChangeStatusActivity), timeline_comment,
im_notify, absence_entry (AbsenceActivity — legacy, НЕТ delete/update),
calendar_event (Calendar2Activity).

Гочи сериализации нового дизайнера:
- Числовидные значения — СТРОКИ: MessageType '4', TimeoutDuration '40',
  ApproveMinPercent '50'.
- Порядок ключей Properties байт-критичен: собрать dict в эталонном
  порядке вставки (review: TaskButton* ДО Timeout*).
- Каждый узел несёт 'Node': None (4-й ключ; порядок Type,Name,Activated,Node,
  Properties,Children — измерено 332 узла корпуса, 2026-10-02).
- Пустые PARAMETERS/VARIABLES/CONSTANTS — [] не {}.
- AbsenceType enum: подтверждены VACATION, LEAVESICK (в дропдауне больше).
- Эталон stage-flow-extended Title «Add absence сhart event» содержит кириллическую
  «с» (опечатка юзера) — не копировать в новые шаблоны.

## Классы из date-filters-heavy (портал portal-b, 2026-10-02)

Экспорт = диспетчер ВзП + тестовые активности-эталоны владельца. Кеш полей
сделки в GetDynamic раздут дизайнером до 411 ключей (CONTACT.*/COMPANY.*) —
генератору достаточно кеша по ReturnFields. Roundtrip файла byte-exact
(184299B). Билдеры в bpt_build.py: `get_deal_info` / `create_deal` / `task`
/ `event_add`.

### CreateCrmDealDocumentActivity — «Создание новой сделки» (из БП любого типа)
```
Fields: {   # канон-порядок ключей:
  TITLE, OPPORTUNITY, CURRENCY_ID, ACCOUNT_CURRENCY_ID, ASSIGNED_BY_ID,
  CATEGORY_ID, STAGE_ID, CLOSED, TYPE_ID, COMMENTS, CONTACT_ID,
  COMPANY_ID, SOURCE_ID }        # значения — литералы|выражения
```
Эталон собран под кейс «Реактивация → Успех»: CATEGORY_ID='0' (Обращения),
STAGE_ID='NEW', SOURCE_ID='REPEAT_SALE', CONTACT_ID/COMPANY_ID — выражениями.

### CrmGetDynamicInfoActivity — режим СДЕЛКИ (чтение полей сделки из СП-БП)
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

### Task2Activity — участники (соисполнители/наблюдатели, C43)
`ACCOMPLICES`/`AUDITORS`: список 'user_N' (эталон: `['user_14','user_58']`);
пусто — `''` (строка, не список). Боевой канон полей (Task2_13371024):
CREATED_BY/RESPONSIBLE_ID = `{=Document:ASSIGNED_BY_ID}`, PRIORITY '1',
ALLOW_CHANGE_DEADLINE 'Y', ALLOW_TIME_TRACKING 'Y', TASK_CONTROL 'Y',
REQUIRED_RESULT 'N', TAG_NAMES {} DEPENDS_ON {}; props-хвост:
HoldToClose 'N', AUTO_LINK_TO_CRM_ENTITY 'Y', AsChildTask '',
CheckListItems {}, TimeEstimateHour/Min ''.

### Прочее из того же экспорта
- CrmEventAddActivity: `{EventType:'INFO', EventText, EventUser:{}}` —
  событие таймлайна, НЕ комментарий (REST-ридера нет, см. probe-режим).
- DelayActivity: `TimeoutTime: '{=Variable:V_DL} 12:00:00'` — дата+время
  строковой конкатенацией.
- StartWorkflowActivity: `TemplateId='220', DocumentId='{=Document:PARENT_ID_2}',
  TemplateParameters={'update':'C14:UC_6LZY0A'}` — литерал стадии в параметре.

### Глобальная константа портала (эталон company-stage, portal-b)
`{=GlobalConst:Constant1790938720619}` — ссылка на константу в props
активности (в эталоне — CrmTimelineCommentAdd.CommentText). Значение живёт
НА ПОРТАЛЕ (Настройки БП → Константы), в .bpt — только ссылка: смена
значения без правки шаблонов (кейс: техпользователь Агентство Тур — В2).
CONSTANTS самого шаблона при этом пустые; namespace GlobalConst легитимен
и при пустых CONSTANTS (см. bpt-1347, whitelist bpt_build). Roundtrip
byte-exact.

### CrmControlNotifyActivity — «Уведомить руководство» (эталон company-stage-control)
«Контроль руководителю» из ТЗ: штатная CRM-механика уведомления
руководителю ответственного. Форма (порядок ключей эталонный):
```
MessageText: "<текст>"     # в эталоне заглушка '1441'
ToUsers: {}                # доп. адресаты; пусто — пустой PHP-массив
ToHead: "Y"                # руководителю (голове) ответственного
```
Билдер: `control_notify(message, to_head='Y', users=None)`. Гоча: пустой
ToUsers через roundtrip читается как `[]` (PHP a:0:{} амбивалентен) —
байты не меняются. Канон УЗЛА 5-ключевой: БЕЗ `Node`, `Properties` вторым
ключом (Type→Properties→Name→Activated→Children) — Designer-экспорт этого
класса не несёт Node; act()-форма давала расхождение байт (реплика-фикстура
поймала, 2026-10-02). Эталон-эволюция company-stage: 15:59 GlobalConst-комментарий
(архив: bpt/company-stage.bpt) → 16:26 контроль (bpt/company-stage-control.bpt); оба
roundtrip byte-exact.

## Probe-режим (волна 2, TODO №3): контракт журналирования переменных

Дефицит: у SV-формул нет рантайма — ошибки видны только на боевом прогоне.
Probe = дебаг-копия шаблона, журналирующая значения рабочих переменных после
каждого set_vars. Канал зафиксирован REST-фактами (ридера нативного журнала
выполнения БП через REST нет; `bizproc.activity.log` только пишет и только
под app-активности с event_token — дизайнер-шаблоны туда не пишутся).

### Канал (MVP): CrmTimelineCommentAdd

- Активность: `timeline_comment()` (bpt_build.py) — форма корпус-калибрована;
  автор по дефолту `{=Document:ASSIGNED_BY_ID}`.
- Носитель: таймлайн ТЕКУЩЕГО документа (probe-копия гоняется на тестовой
  сущности — комментарии ложатся в её таймлайн).
- Чтение: `crm.timeline.comment.list`, filter `{ENTITY_ID, ENTITY_TYPE}`
  (ENTITY_TYPE: 'deal'/'lead'/'contact'/'company'; для СП — entityTypeId,
  проверяется на E2E), order `{ID: ASC}`, пагинация `start` (50/стр),
  select `ID, CREATED, AUTHOR_ID, COMMENT`; серверный поиск по тексту не
  гарантируется → фильтр по тегу клиентский.
- Fallback для не-CRM документов (вариант-Б, НЕ MVP): im_notify владельцу +
  чтение `im.dialog.messages.get`.

### Текст probe-комментария (контракт; парсер T5 обязан его держать)

```
[probe] after <Name исходной SetVariableActivity>
<имя_переменной>={=Variable:<имя_переменной>}
...                                              # одна строка на КАЖДЫЙ
                                                 # ключ VariableValue SV
```

- Строка 1 — тег `[probe]` (якорь фильтра) + уникальный Name исходного SV
  (однозначная идентификация шага).
- Строки данных: рантайм подставляет `{=Variable:X}` значением (движок
  подставляет ссылки в текстовых props; не-строковые типы рендерятся
  строкой; значение до конца строки — «=» внутри значения допустим,
  сплит по ПЕРВОМУ «=»).
- Многострочность — `\n` в CommentText; Title вставки в дизайнере:
  `[probe] after <Name SV>`.
- set_vars внутри while/foreach даёт СЕРИЮ комментариев (хронология
  итераций) — фича, не дубль.
- Открытый рантайм-вопрос (закрывается живым прогоном T6): сохраняет ли
  таймлайн многострочность; при коллапсе строк — пивот на `; `-джойнер
  с ревизией парсера.

### Рантайм-факты живого прогона (T6, портал portal-b, СП 1068, 2026-10-02)

- Канал подтверждён с двух независимых писателей (probe-вставка и
  скопированная вручную в дизайнере CrmTimelineCommentAdd): комментарии
  читаются `crm.timeline.comment.list`, ENTITY_TYPE для СП —
  `DYNAMIC_<etid>` (голый числовой etid → HTTP 400).
- Многострочность `\n` в CommentText сохраняется (вопрос выше закрыт).
- Подстановка в тексте: голые `{=...}`-ссылки подставляются; `{{=...}}`-
  выражения НЕ вычисляются (рендерятся сырой строкой) — журналировать
  значения ТОЛЬКО через `{=Variable:X}`.
- Автор комментария обязателен: `CommentUser=['{=Document:ASSIGNED_BY_ID}']`
  на элементе с ПУСТЫМ ответственным молча не пишет ничего (активность
  «выполняется», комментариев нет) — перед прогоном убедиться, что у
  тестовой сущности проставлен ответственный.
- Probe-находка (кейс-пример инструмента): SV
  `V_M_FULL = {{='ex_' . {=Document:ID} . '_full'}}` после выполнения
  хранит СЫРУЮ формулу с подставленными внутренними рефами
  (`{{='ex_' . 4 . '_full'}}`), а не результат конкатенации — журнал
  это показал напрямую.
- Событийные активности (CrmEventAddActivity «Запись события в crm»)
  пишут НЕ-комментарий (событие таймлайна) — REST-ридера для них нет;
  DBG-канал = только CrmTimelineCommentAdd.

Мини-фикстура дерева для тестов генератора: `tests/fixtures/probe_tree.json`
(SV на корневом уровне, SV в ветках if_else, SV в теле while — все три
уровня вложенности, где probe обязан вставить журналирование).
Санитизированная живая фикстура журнала: `tests/fixtures/probe_journal_live.json`.

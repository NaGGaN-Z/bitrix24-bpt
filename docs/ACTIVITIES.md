# Классы активностей (справочник по эталонам)

Формы сняты с реальных экспортов нового дизайнера (12 боевых шаблонов,
33 класса). Порядок ключей `Properties` — канонический, байт-критичен.
Числовидные значения — строки. Общие правила узла — `docs/FORMAT.md`.

Билдер — функция `bpt_build.py`, собирающая класс (пусто = собирается
вручную как узел).

## Каркас

### SequentialWorkflowActivity — корень шаблона
- `Properties`: `Title` (+ `Permission` в старых экспортах; в новых не добавлять)
- `Children` — верхний уровень процесса
- Билдер: каркас `scaffold()`

### SequenceActivity — последовательный блок
- `Properties`: `Title`
- Эталонная обёртка тела циклов и веток заданий (у `SequenceActivity`
  нет `Activated`/`Node`/`EditorComment` — сокращённая форма узла)
- Билдер: `sequence(children, title)`

### TerminateActivity — терминация процесса
- Полная форма: `StateTitle` (текст статуса), `KillWorkflow` `'Y'`,
  `TerminateType`: `allExceptCurrentByDocumentAndTemplate` — убить все
  инстансы ЭТОГО шаблона для документа, кроме текущего (дедупликатор;
  держать первой активностью шаблона-диспетчера)
- Минимальная форма: только `Title` («Прерывание процесса») — простой стоп
- Билдер: `terminate_others(title)`

### EmptyBlockActivity — заглушка-маркер
- Свойств почти нет (`Title`, `EditorComment`); Title вида
  `!!! ОПИСАНИЕ НЕЗАКРЫТОГО !!!` — виден в дизайнере как TODO
- Билдер: `placeholder(marker, comment)`

## Переменные и поля

### SetVariableActivity — «Изменение переменных»
- `VariableValue`: `{ИМЯ: значение}`; вычисляемые выражения — `{{=...}}`,
  подстановки — `{=...}`
- Переменные обязаны быть объявлены в `VARIABLES` шаблона
  (`variable(name, type, ...)`)
- Билдер: `set_vars(mapping, title)`

### SetFieldActivity — «Изменение документа»
- `FieldValue`: `{ПОЛЕ: значение}`, `ModifiedBy: []`,
  `MergeMultipleFields: 'N'`
- Пишет ТОЛЬКО поля текущего документа; для чужих элементов смарт-процессов
  использовать `CrmUpdateDynamicActivity`
- Билдер: `set_fields(mapping, title)`

## Ветвление и циклы

### IfElseActivity — условие
- `Properties`: только `Title`; логика — в детях `IfElseBranchActivity`

### IfElseBranchActivity — ветка условия
- Ветка else: `truecondition: '1'`
- Условие по переменной: `propertyvariablecondition:
  [[имя, оператор, значение, '0']]` (хвост `'0'` обязателен)
- Смешанное условие (доп. результаты активностей / переменные / документ):
  `mixedcondition: [{object, field, operator, value, joiner}]`, где
  `object` — `Name` активности (её результаты), `'Variable'`, `'Document'`;
  `joiner`: `'0'` первая строка, `'1'` = OR
- Старая форма: `fieldcondition: [["ИМЯ_ПОЛЯ"]]` — условие по полю
  документа без оператора (встречается в шаблонах старого дизайнера)
- Билдеры: `if_else(branches)`, `if_else_mixed(branches)`

### WhileActivity — цикл с условием
- `propertyvariablecondition: [[имя, оператор, значение, '0']]`
- Тело — ровно один ребёнок `SequenceActivity`
- Билдер: `while_loop(condition, children)`

### ForEachActivity — итератор по мульти-значению
- `Variable` — имя мульти-переменной, `Object: 'Variable'`
- Значение итерации читается через дополнительные результаты активности
- Билдер: `for_each(variable_name, children)`

## Задания (согласования, запросы)

Общие для заданий: `Users` — список адресатов (ID/выражения),
`TimeoutDuration`/`TimeoutDurationType` — контрольный срок,
`AccessControl: 'N'`, `DelegationType: '0'`, `ShowComment`/`CommentRequired`/
`CommentLabelMessage` — комментарий, `SetStatusMessage`/`StatusMessage` —
статус-строка. Результат задания читается как `{=ActivityName:FieldName}`.

### ApproveActivity — согласование (две кнопки)
- `ApproveType` (`all`/`any`), `ApproveMinPercent` (строка!), `ApproveWaitForAll`,
  `TaskButton1Message`/`TaskButton2Message` — подписи кнопок,
  `CommentRequired`: `'N'` | `'Y'` | `'YR'` (обязателен только при отказе)
- Дети: `[Sequence-ветка Accept, Sequence-ветка Decline]`
- Билдер: `approve(users, name, ...)`

### RequestInformationActivity — запрос доп. информации
- `RequestedInformation`: список полей
  `{Title, Name, Description, Type, Required, Multiple}` —
  каждое поле становится результатом `{=ActivityName:Name}`
- Билдер: `request_info(users, fields, name, ...)`

### RequestInformationOptionalActivity — запрос с отменой
- То же + `CancelType: 'any'`, `TaskButtonCancelMessage`, `SaveVariables: 'N'`
- Дети: `[Sequence-ветка Submit, Sequence-ветка Cancel]`
- Билдер: `request_info(..., optional=True)`

### ReviewActivity — ознакомление (одна кнопка)
- Форма как у Approve, но без кнопки отказа; дети — `[Sequence-ветка Done]`
  или пусто (по эталону)
- Билдер: `review(users, name, ...)`

## Ожидание

### DelayActivity — пауза
- Интервал: `TimeoutDuration: '40'`, `TimeoutDurationType: 's'|'m'|'h'|'d'`
- Точная дата: `TimeoutTime` (выражение-дата, напр. `{=System:Now}`),
  `TimeoutTimeIsLocal: 'N'`
- `WriteToLog: 'N'`
- Билдер: `delay(duration, unit)` / `delay(until=...)`

## Уведомления и записи

### IMNotifyActivity — уведомление сотруднику
- `MessageSite` — ТЕКСТ сообщения (нейминг-гоча нового дизайнера:
  текст в `MessageSite`, не в `MessageOut` — тот для внешней доставки),
  `MessageType: '4'`, `MessageUserFrom`/`MessageUserTo` — списки
- Билдер: `im_notify(users_to, message)`

### CrmTimelineCommentAdd — запись в историю CRM
- `CommentText`, `CommentUser` — список авторов-выражений
- Билдер: `timeline_comment(text, users)`

### LogActivity — запись в журнал процесса
- `Text`, `SetVariable` (устаревший класс)
- Эквивалент современных шаблонов: `CrmTimelineCommentAdd`

### AbsenceActivity — график отсутствий
- `AbsenceName`, `AbsenceDesrc` (опечатка-канон!), `AbsenceFrom`/`AbsenceTo`
  (даты/выражения), `AbsenceState`/`AbsenceFinishState`, `AbsenceType`
  (enum: `VACATION`, `LEAVESICK`...), `AbsenceSiteId: 's1'`, `AbsenceUser`
- Legacy-класс: нет delete/update созданных записей
- Билдер: `absence_entry(users, date_from, date_to, name, ...)`

### Calendar2Activity — событие календаря
- `CalendarName`, `CalendarDesrc` (опечатка-канон), `CalendarFrom`/`To`,
  `CalendarType`, `CalendarOwnerId`, `CalendarSection`, `CalendarTimezone`,
  `CalendarUser`
- Билдер: `calendar_event(name, date_from, date_to, ...)`

## Сотрудники

### GetUserActivity — резолвер сотрудника
- `UserType` (`boss` — руководитель), `MaxLevel` (уровней вверх),
  `UserParameter` — список ID/выражений исходного юзера,
  `ReserveUserParameter`, `SkipAbsent`/`SkipTimeman` (+ `*Reserve`-пары)
- Результат — доп-результат: `{=Name:User}`
- Билдер: `get_user(user_parameter, ...)`

### GetUserInfoActivity — чтение полей профиля
- `GetUser` — список `'user_<ID>'`/выражений; `UserFields`:
  `{FIELD: {Name, Type}}` — каждое поле как доп-результат
- Билдер: `get_user_info(user, fields)`

## CRM и смарт-процессы

### CrmChangeStatusActivity — смена стадии
- `TargetStatus` — ID стадии, `ModifiedBy: []`
- Ставить ПОСЛЕДНЕЙ активностью ветки: после смены стадии инстанс
  может быть убит следующим процессом автоматизации
- Билдер: `change_stage(target_status)`

### CrmCreateDynamicActivity — создать элемент СП
- `DynamicTypeId` — целевой entityTypeId СТРОКОЙ; `OnlyDynamicEntities: 'Y'`
- `DynamicEntitiesFields` — ВСЕ поля цели списком с префиксом
  `<etid>_<ПОЛЕ>` (`1038_TITLE`); пустая строка = не ставить; ключи БЕЗ
  префикса у `fields` билдера — префикс ставится автоматически
- Ссылка на ID созданного: `{=CreateActivityName:ItemId}`
- Билдер: `create_dynamic(target_etid, fields, name)` +
  `create_dynamic_item_id_expr(node)`

### CrmUpdateDynamicActivity — изменить чужие элементы СП
- `DynamicEntitiesFields` — БЕЗ префикса etid (отличие от Create!)
- Таргетинг: `DynamicId` (прямой ID; ВНИМАНИЕ — на части сборок выражения
  в нём НЕ вычисляются) ИЛИ `DynamicFilterFields` — фильтр
  `{'items': [[{object, field, operator, value}, 'AND'], ...]}`.
  Каноничный рабочий паттерн: фильтр по родному полю `ID` цели,
  значение — выражение из тех-поля документа
- Билдер: `update_dynamic(target_etid, fields, item_id_expr|filter_conditions)`

### CrmGetDynamicInfoActivity — чтение элементов СП по фильтру
- `DynamicTypeId`, `ReturnFields` — список полей результата,
  `DynamicFilterFields` — фильтр (каждый ряд склеивается `'AND'`;
  `object`: `'Document'` | `'undefined'` (поле целевой сущности) |
  `'Variable'`), `DynamicEntityFields` — кэш-описание полей цели
  (копия из эталона + ключ `Document`)
- Результаты — доп-результаты по имени активности: `{=Name:FIELD}`
- Билдер: `get_dynamic_info(etid, return_fields, filter_rows, ...)`

## Документы и вложенные запуски

### CreateDocumentActivity — создать документ (базовый класс)
- `Fields`: `{ПОЛЕ: значение|выражение}` — значения литералы или
  выражения; модификаторы вывода через ` > `:
  `{=Document:CONTACT_ID > id}`, `{=Document:TITLE > printable}`

### CreateCrmContactDocumentActivity — создать контакт
- `Fields` — как у базового класса; множественные поля вложенной формой:
  `PHONE.PHONE.n1.{VALUE, VALUE_TYPE}`

### CreateCrmDealDocumentActivity — создать сделку
- `Fields`: `CATEGORY_ID`, `STAGE_ID`, `CONTACT_ID` — выражения
  (для привязок — с модификатором `> id`)

### CreateListsDocumentActivity — элемент универсального списка
- Дополнительно `DocumentType: ['lists']`; в `Fields` — `IBLOCK_ID`
  инфоблока и `NAME`

### StartWorkflowActivity — запуск другого шаблона
- `DocumentId` — выражение-ссылка на документ-цель
  (напр. `{=Document:UF_CRM_7_PRKONT}`)
- `TemplateId` — ID запускаемого шаблона (строка),
  `UseSubscription` — `'Y'|'N'` (подписка на завершение)
- `TemplateParameters` — карта параметров шаблона-цели
  `{имя_параметра: значение}`; имена обязаны совпадать с объявленными
  в PARAMETERS целевого шаблона

### WebHookActivity — исходящий вебхук
- `Handler` — URL внешнего обработчика (единственное значимое свойство)
- Движок шлёт POST с данными документа; ответ не влияет на поток

### rest_<hash> — REST-активность приложения
- `Type` = `rest_` + 32-hex идентификатор регистрации приложения
  (Маркетплейс / локальное REST-приложение) — у каждого портала свои хэши
- `messageText` — текст/команда, интерпретируемая приложением;
  `files` — вложения; `AuthUserId` — от чьего имени зовём;
  `UseSubscription`, `TimeoutDuration`/`TimeoutDurationType`,
  `SetStatusMessage`/`StatusMessage` — подписка/таймаут/статус
- Класс не переносим между порталами без перерегистрации приложения

## Enum-поля в CrmCreateDynamic (разгадка)

`CrmCreateDynamicActivity` пишет enumeration-поле ТОЛЬКО в хэш-форме
дизайнера (внутренние идентификаторы значений, генерируются дизайнером
при выборе через дропдаун). Int-ID и строковое значение молча теряются.

Каноничный цикл для нового портала: сгенерить шаблон с ПУСТЫМ
enum-полем → импорт → в дизайнере проставить значения дропдауном →
экспорт → этот `.bpt` и есть деплой-артефакт (хэши привязаны к
конкретному порталу/энуму).

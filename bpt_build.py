#!/usr/bin/env python3
"""bpt_build.py — универсальный конструктор шаблонов БП Битрикс24 (.bpt).

Слой над bpt_serialize/bpt_export: builders активностей + каркас шаблона +
сериализация с самопроверкой. Бизнес-логика (какие поля/правила) — вне этого
файла; здесь только МЕХАНИКА, формы сняты с живых экспортов нового дизайнера
(см. etalons.md рядом).

Поддержанные классы (расширяй по мере вскрытия новых эталонов):
  SetVariableActivity, SetFieldActivity, IfElseActivity(+Branch),
  CrmCreateDynamicActivity, CrmUpdateDynamicActivity,
  WhileActivity, ForEachActivity,
  TerminateActivity, ApproveActivity, RequestInformationActivity(+Optional),
  ReviewActivity, DelayActivity (интервал|дата), CrmChangeStatusActivity,
  CrmTimelineCommentAdd, IMNotifyActivity,
  AbsenceActivity, Calendar2Activity
  (формы 2026-09-02 сняты с projects/bpt/etalons/bp-1177.bpt и bp-1187.bpt)

CLI:
  python3 bpt_build.py make TREE.json OUT.bpt     # json-дерево -> .bpt
  python3 bpt_build.py check OUT.bpt TREE.json    # roundtrip-проверка
"""
import hashlib
import itertools
import json
import os
import sys
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bpt_serialize import php_serialize, php_unserialize_exact  # noqa: E402
from bpt_export import php_unserialize  # noqa: E402

VERSION = 2
_ACT_SEQ = itertools.count()  # уникализатор: одинаковые активности обязаны
                             # иметь РАЗНЫЕ Name (гоча импорта: "has a duplicate")
# Человекочитаемые Name: <токен>_<номер> — латиница (движок/выражения),
# русская метка живёт в Title (видна в дизайнере).
_NAME_TOKEN = {
    'SequenceActivity': 'Seq', 'TerminateActivity': 'Stop',
    'SetVariableActivity': 'SetVar', 'SetFieldActivity': 'SetFld',
    'IfElseActivity': 'Branch', 'IfElseBranchActivity': 'When',
    'WhileActivity': 'Loop', 'For EachActivity': 'Each',
    'ForEachActivity': 'Each', 'ApproveActivity': 'Approve',
    'RequestInformationActivity': 'Ask',
    'RequestInformationOptionalActivity': 'AskOpt',
    'ReviewActivity': 'Read', 'DelayActivity': 'Wait',
    'CrmChangeStatusActivity': 'Go', 'CrmTimelineCommentAdd': 'Log',
    'IMNotifyActivity': 'Notify', 'AbsenceActivity': 'Absence',
    'Calendar2Activity': 'Cal', 'GetUserActivity': 'Who',
    'GetUserInfoActivity': 'Info', 'CrmCreateDynamicActivity': 'Create',
    'CrmUpdateDynamicActivity': 'Update',
}


def act(a_type: str, title: str, props: dict, children=None, name: str | None = None) -> dict:
    """Узел активности. Name = <токен>_<счётчик> (уникален, латиница);
    Title — человеческая метка (русский — ок, отображается в дизайнере)."""
    if name is None:
        name = '%s_%d' % (_NAME_TOKEN.get(a_type, 'Act'),
                          next(_ACT_SEQ))
    return {'Type': a_type, 'Name': name, 'Activated': 'Y', 'Node': None,
            'Properties': dict(props, Title=title, EditorComment=''),
            'Children': children if children is not None else []}


def set_vars(mapping: dict, title: str = 'Изменение переменных') -> dict:
    """VariableValue: {ИМЯ: значение|выражение}. Вычисляемые — {{=...}}, подстановки — {=...}."""
    return act('SetVariableActivity', title, {'VariableValue': mapping})


def set_fields(mapping: dict, title: str = 'Изменение полей') -> dict:
    """Запись полей ТЕКУЩЕГО документа (не чужих!). Порядок props — эталон."""
    return act('SetFieldActivity', title,
               {'FieldValue': mapping, 'ModifiedBy': [],
                'MergeMultipleFields': 'N'})


def create_dynamic(target_etid, fields: dict, name: str, blank_fields=None,
                   system_blanks=('XML_ID', 'CREATED_BY', 'ASSIGNED_BY_ID',
                                  'CATEGORY_ID', 'STAGE_ID', 'LAST_ACTIVITY_BY',
                                  'LAST_ACTIVITY_TIME')):
    """Создание элемента смарт-процесса target_etid.
    fields: {ПОЛЕ: значение|выражение} (ключи БЕЗ префикса etid — префикс ставится сам).
    blank_fields: UF-имена, добавляемые пустыми (для картины в дизайнере); опционально."""
    defl = {f'{target_etid}_{k}': '' for k in (blank_fields or ())}
    for k in system_blanks:
        defl[f'{target_etid}_{k}'] = ''
    for k, v in fields.items():
        defl[f'{target_etid}_{k}'] = v
    defl[f'{target_etid}_TITLE'] = fields.get('TITLE', '')
    defl[f'{target_etid}_OPENED'] = fields.get('OPENED', 'Y')
    return act('CrmCreateDynamicActivity', 'Создать элемент смарт-процесса',
               {'DynamicTypeId': str(target_etid), 'OnlyDynamicEntities': 'Y',
                'DynamicEntitiesFields': defl}, name=name)


def create_dynamic_item_id_expr(create_node: dict) -> str:
    """Ссылка на ID созданного элемента (дополнительный результат активности)."""
    return '{=%s:ItemId}' % create_node['Name']


def update_dynamic(target_etid, fields: dict, item_id_expr: str | None = None,
                   filter_conditions: list | None = None,
                   title: str = 'Изменить элемент смарт-процесса') -> dict:
    """Обновление ЧУЖИХ элементов СП. Таргетинг — ИЛИ item_id_expr (прямой ID),
    ИЛИ filter_conditions = [(поле_цели, значение), ...] (AND).
    ВНИМАНИЕ: на части сборок выражения в DynamicId не вычисляются и фильтры по
    crm-UF не резолвятся — рабочий паттерн: фильтр по родному полю ID цели,
    значение — выражение из тех-поля документа (см. etalons.md)."""
    props = {'DynamicTypeId': str(target_etid), 'DynamicId': ''}
    if filter_conditions is not None:
        items = []
        for i, (fld, val) in enumerate(filter_conditions):
            if i:
                items.append('AND')
            items.append({'object': 'Document', 'field': fld,
                          'operator': '=', 'value': val})
        props['DynamicFilterFields'] = {'items': [items]}
    if item_id_expr is not None:
        props['DynamicId'] = item_id_expr
    props['DynamicEntitiesFields'] = fields
    return act('CrmUpdateDynamicActivity', title, props)


def if_else(branches: list) -> dict:
    """branches: [(title, condition|None, activities), ...]; condition=None = else.
    condition: [имя_переменной, оператор, значение] (хвост '0' добавится).
    Правая часть: '{=Variable:...}' для переменных, литерал для констант."""
    children = []
    for title, cond, acts in branches:
        props = {'Title': title, 'EditorComment': ''}
        if cond is None:
            props['truecondition'] = '1'
        else:
            props['propertyvariablecondition'] = [cond + ['0']]
        children.append({'Type': 'IfElseBranchActivity',
                         'Name': act('X', title, {})['Name'],
                         'Activated': 'Y', 'Properties': props,
                         'Children': acts})
    return act('IfElseActivity', 'Условие', {}, children)


def if_else_mixed(branches: list) -> dict:
    """Ветвление по mixedcondition (эталон bp-1197 Exit-ветка): условия на
    доп.результаты активностей и переменные.
    branches: [(title, rows|None, activities)]; rows=None = else.
    rows: [(object, field, operator, value, joiner)] — object: имя активности
    (её результаты), 'Variable', 'Document'; joiner: '0' первая строка,
    далее '1' (OR) / прочие по UI."""
    children = []
    for title, rows, acts in branches:
        props = {'Title': title, 'EditorComment': ''}
        if rows is None:
            props['truecondition'] = '1'
        else:
            props['mixedcondition'] = [
                {'object': o, 'field': f, 'operator': op, 'value': v,
                 'joiner': j} for (o, f, op, v, j) in rows]
        children.append({'Type': 'IfElseBranchActivity',
                         'Name': act('X', title, {})['Name'],
                         'Activated': 'Y', 'Properties': props,
                         'Children': acts})
    return act('IfElseActivity', 'Условие', {}, children)


def while_loop(condition: list, children: list, title: str = 'Цикл') -> dict:
    """condition: [имя_переменной, оператор, значение, '0'] (как в if_else).
    Тело оборачивается в SequenceActivity (по эталонам bp-2015/ZID)."""
    body = sequence(children, title)
    return act('WhileActivity', title,
               {'propertyvariablecondition': [condition]}, [body])


def for_each(variable_name: str, children: list,
             title: str = 'Итератор', source: str = 'Variable') -> dict:
    """Итератор по мульти-значению. source='Variable': Object=Variable,
    Variable=имя мульти-переменной. Значение итерации — через доп.результаты
    (см. эталон «Запуск создания ZID.bpt»: Variable=ids, Object=Variable)."""
    body = sequence(children, title)
    return act('ForEachActivity', title,
               {'Variable': variable_name, 'Object': source}, [body])


def variable(name: str, v_type: str, label: str = '', default: str = '',
             required: str = '0', multiple: str = '0') -> dict:
    """Объявление переменной шаблона. Для переменных-полей заданий
    (RequestedInformation) — 1:1 по Name/Type/Required/Multiple."""
    return {'Name': label or name, 'Description': '', 'Type': v_type,
            'Required': required, 'Multiple': multiple, 'Options': '',
            'Default': default}


def get_user(user_parameter, max_level='1', user_type='boss',
             reserve=None, skip_absent='Y', skip_timeman='N',
             title='Найти руководителя') -> dict:
    """Нативный резолвер сотрудника: UserType='boss' — руководитель
    (MaxLevel — сколько уровней вверх ищем главу; SkipAbsent='Y' — пропускать
    отсутствующих). user_parameter: список ID/выражений исходного юзера.
    Результат — доп-результат активности: {=Name:User}."""
    return act('GetUserActivity', title,
               {'UserType': user_type, 'MaxLevel': max_level,
                'UserParameter': list(user_parameter),
                'ReserveUserParameter': list(reserve or []),
                'SkipAbsent': skip_absent, 'SkipAbsentReserve': skip_absent,
                'SkipTimeman': skip_timeman, 'SkipTimemanReserve': skip_timeman})


def get_user_info(user, fields, title='Данные сотрудника') -> dict:
    """Чтение полей профиля юзера. user: ['user_231'] или выражение;
    fields: {'USER_WORK_POSITION': ('Position', 'string'), ...} —
    результаты как доп-результаты {=Name:USER_WORK_POSITION}."""
    return act('GetUserInfoActivity', title,
               {'GetUser': list(user),
                'UserFields': {k: {'Name': n, 'Type': t}
                               for k, (n, t) in fields.items()}})


def placeholder(marker: str, comment: str = '') -> dict:
    """Заглушка-маркер незакрытой функциональности (EmptyBlockActivity из
    корпуса эталонов). Title = '!!! ... !!!' — видно в дизайнере."""
    props = {}
    if comment:
        props['EditorComment'] = comment
    return act('EmptyBlockActivity', f'!!! {marker} !!!', props)


def get_dynamic_info(etid, return_fields, filter_rows, fields_cache,
                     document_meta, title='Get SPA item information'):
    """Чтение элементов СП по фильтру (эталон bp-1197). Результаты —
    доп. результаты по имени активности: {=Name:FIELD}.
    filter_rows: [(object, field, operator, value), ...] где object:
    'Document' | 'undefined' (поле целевой сущности) | 'Variable'.
    Каждый ряд склеивается 'AND' (форма: [[cond,'AND'],[cond,'AND'],...]).
    fields_cache: {FIELD: {...}} для DynamicEntityFields; document_meta —
    ключ 'Document' (копия из эталона)."""
    items = []
    for obj, fld, op, val in filter_rows:
        items.append([{'object': obj, 'field': fld, 'operator': op,
                       'value': val}, 'AND'])
    ef = dict(fields_cache)
    ef['Document'] = document_meta
    return act('CrmGetDynamicInfoActivity', title,
               {'DynamicTypeId': str(etid),
                'ReturnFields': list(return_fields),
                'OnlyDynamicEntities': 'Y',
                'DynamicFilterFields': {'items': items},
                'DynamicEntityFields': ef})


# ---------- новые классы (эталоны bp-1177/bp-1187, 2026-09-02) ----------
# Порядок ключей props повторяет эталонный ПОБАЙТОВО (PHP dict упорядочен);
# числовидные значения в эталонах — СТРОКИ ('4', '40', '50').

def sequence(children: list, title: str = 'Последовательность') -> dict:
    """Последовательный блок — эталонная обёртка веток заданий."""
    return {'Type': 'SequenceActivity', 'Name': act('X', title, {})['Name'],
            'Activated': 'Y', 'Node': None,
            'Properties': {'Title': title}, 'Children': children}


def terminate_others(title: str = 'Стоп: убить дубли') -> dict:
    """Дедупликатор: убить все процессы ЭТОГО шаблона для документа, кроме
    текущего. Держать первой активностью шаблона-диспетчера."""
    return act('TerminateActivity', title,
               {'StateTitle': 'Workflow terminated', 'KillWorkflow': 'Y',
                'TerminateType': 'allExceptCurrentByDocumentAndTemplate'})


def approve(users, name, description='', status_message='Approval in progress',
            button_yes='Accept', button_no='Decline',
            comment_required='YR', approve_type='all', min_percent='50',
            children=None, title='Согласование') -> dict:
    """Задание-согласование с двумя кнопками.
    CommentRequired: 'N' | 'Y' | 'YR' (YR = обязателен только при Decline).
    children: [ветка Accept, ветка Decline]; None -> две пустые Sequence."""
    if children is None:
        children = [[], []]
    props = {'ApproveType': approve_type, 'OverdueDate': '',
             'ApproveMinPercent': min_percent, 'ApproveWaitForAll': 'N',
             'Name': name, 'Description': description, 'Parameters': '',
             'StatusMessage': status_message, 'SetStatusMessage': 'Y',
             'TimeoutDuration': '', 'TimeoutDurationType': 's',
             'TaskButton1Message': button_yes,
             'TaskButton2Message': button_no,
             'CommentLabelMessage': 'Notes', 'ShowComment': 'Y',
             'CommentRequired': comment_required, 'AccessControl': 'N',
             'DelegationType': '0', 'Users': list(users)}
    return act('ApproveActivity', title, props,
               [sequence(c) for c in children])


def request_info(users, fields, name, description='',
                 status_message='Waiting for additional information',
                 button='Save', comment_required='N', optional=False,
                 cancel_button='Cancel', children=None, title=None) -> dict:
    """Задание «запросить доп.информацию» (заполнение полей).
    fields: list of dict(Title, Name, Description, Type, Required, Multiple)
    — Name = имя результата, читается как {=ActivityName:Name}.
    optional=True -> RequestInformationOptionalActivity (кнопка Cancel);
    children: optional — [ветка Submit, ветка Cancel], иначе — [ветка Submit];
    None -> пустые ветки по канону (2 для optional, 0 для обычного)."""
    a_type = ('RequestInformationOptionalActivity' if optional
              else 'RequestInformationActivity')
    if children is None:
        children = [[], []] if optional else []
    props = {'Users': list(users), 'Name': name, 'Description': description,
             'TaskButtonMessage': button, 'ShowComment': 'Y',
             'CommentRequired': comment_required,
             'CommentLabelMessage': 'Notes', 'SetStatusMessage': 'Y',
             'StatusMessage': status_message,
             'TimeoutDuration': '', 'TimeoutDurationType': 's',
             'AccessControl': 'N', 'DelegationType': '0',
             'RequestedInformation': [
                 {'Title': f.get('Title', ''), 'Name': f['Name'],
                  'Description': f.get('Description', ''),
                  'Type': f.get('Type', 'string'),
                  'Required': f.get('Required', '0'),
                  'Multiple': f.get('Multiple', '0')}
                 for f in fields],
             'OverdueDate': ''}
    if optional:
        props['CancelType'] = 'any'
        props['TaskButtonCancelMessage'] = cancel_button
        props['SaveVariables'] = 'N'
    if title is None:
        title = ('Запрос информации (можно отменить)' if optional
                 else 'Запрос информации')
    return act(a_type, title, props, [sequence(c) for c in children])


def review(users, name, description='', status_message='Familiarization',
           button='Done', children=None, title='Ознакомление') -> dict:
    """Задание-ознакомление (одна кнопка Done). children: [ветка Done];
    None -> веток нет (эталон)."""
    if children is None:
        children = []
    props = {'ApproveType': 'all', 'OverdueDate': '',
             'Name': name, 'Description': description, 'Parameters': '',
             'StatusMessage': status_message, 'SetStatusMessage': 'Y',
             'TaskButtonMessage': button, 'CommentLabelMessage': 'Notes',
             'ShowComment': 'Y', 'CommentRequired': 'N',
             'TimeoutDuration': '', 'TimeoutDurationType': 's',
             'AccessControl': 'N', 'DelegationType': '0',
             'Users': list(users)}
    return act('ReviewActivity', title, props, [sequence(c) for c in children])


def delay(duration=None, unit='m', until=None, is_local='N',
          write_log='N', title='Пауза') -> dict:
    """Пауза. Две формы: interval (duration='40', unit='s'|'m'|'h'|'d')
    ИЛИ точная дата (until='{=System:Now}' / выражение-дата)."""
    if until is not None:
        return act('DelayActivity', title,
                   {'TimeoutTime': until, 'TimeoutTimeIsLocal': is_local,
                    'WriteToLog': write_log})
    return act('DelayActivity', title,
               {'TimeoutDuration': duration, 'TimeoutDurationType': unit,
                'WriteToLog': write_log})


def change_stage(target_status: str, title='Переход стадии') -> dict:
    """Смена стадии ТЕКУЩЕГО документа. ВАЖНО: ставить ПОСЛЕДНЕЙ активностью
    ветки (после неё инстанс может быть убит новым)."""
    return act('CrmChangeStatusActivity', title,
               {'TargetStatus': target_status, 'ModifiedBy': []})


def timeline_comment(text, users=None, title='Запись в историю') -> dict:
    """Комментарий в таймлайн (неизменяемая история).
    users: список авторов-выражений, напр. ['{=Document:ASSIGNED_BY_ID}']."""
    return act('CrmTimelineCommentAdd', title,
               {'CommentText': text,
                'CommentUser': list(users or ['{=Document:ASSIGNED_BY_ID}'])})


def im_notify(users_to, message, users_from=None, title='Уведомление',
              message_type='4') -> dict:
    """Уведомление сотруднику(-ам). ВАЖНО: ТЕКСТ сообщения хранится в
    свойстве MessageSite (гоча нейминга нового дизайнера, подтверждена
    маркером «ГДЕ ТЕКСТ?» в bp-1195); MessageOut — внешняя доставка."""
    return act('IMNotifyActivity', title,
               {'MessageSite': message, 'MessageOut': '',
                'MessageType': message_type,
                'MessageUserFrom': list(users_from or ['1']),
                'MessageUserTo': list(users_to)})


def absence_entry(users, date_from, date_to, name, description='',
                  absence_type='VACATION', state='', finish_state='',
                  title='Запись в график отсутствий') -> dict:
    """Запись в график отсутствий (legacy-класс; НЕТ delete/update!).
    users: список 'user_<ID>' или выражений; date_from/to: даты/выражения;
    state/finish_state — свободные строки-статусы на начало/конец (обычно '')."""
    return act('AbsenceActivity', title,
               {'AbsenceName': name, 'AbsenceDesrc': description,
                'AbsenceFrom': date_from, 'AbsenceTo': date_to,
                'AbsenceState': state, 'AbsenceFinishState': finish_state,
                'AbsenceType': absence_type, 'AbsenceSiteId': 's1',
                'AbsenceUser': list(users)})


def calendar_event(name, date_from, date_to, users=None, section='',
                   cal_type='', owner_id='', timezone='Europe/Moscow',
                   description='', title='Событие календаря') -> dict:
    """Событие календаря (Calendar2Activity; строковые props по эталону)."""
    return act('Calendar2Activity', title,
               {'CalendarName': name, 'CalendarDesrc': description,
                'CalendarFrom': date_from, 'CalendarTo': date_to,
                'CalendarType': cal_type, 'CalendarOwnerId': owner_id,
                'CalendarSection': section, 'CalendarTimezone': timezone,
                'CalendarUser': list(users or ['1'])})


def scaffold(children: list, variables: dict, doc_fields: dict,
             root_props: dict | None = None, parameters: dict | None = None,
             constants: dict | None = None) -> dict:
    """Каркас шаблона. root_props/doc_fields — из эталонного экспорта того же
    типа документа (копируются verbatim; Permission НЕ добавляем — эталоны
    bp-1187 его не содержат). Пустые PARAMETERS/VARIABLES/CONSTANTS
    обязаны быть [] (PHP a:0:{} парсится обратно в list)."""
    props = dict(root_props or {})
    norm = lambda d: d if d else []
    return {'VERSION': VERSION,
            'TEMPLATE': [{'Type': 'SequentialWorkflowActivity', 'Name': 'Template',
                          'Activated': 'Y', 'Node': None, 'Properties': props,
                          'Children': children}],
            'PARAMETERS': norm(parameters), 'VARIABLES': norm(variables),
            'CONSTANTS': norm(constants), 'DOCUMENT_FIELDS': doc_fields}


def save(tree: dict, path: str) -> int:
    """Сериализация с самопроверкой (re-serialize == payload). Возвращает размер."""
    payload = php_serialize(tree)
    back = php_unserialize_exact(payload)
    assert php_serialize(back) == payload, f'{path}: re-serialize mismatch!'
    blob = zlib.compress(payload)
    open(path, 'wb').write(blob)
    return len(blob)


def load(path: str) -> dict:
    return php_unserialize_exact(zlib.decompress(open(path, 'rb').read()))


def main() -> None:
    cmd, tree_path, out_path = sys.argv[1], sys.argv[2], sys.argv[3]
    if cmd == 'make':
        tree = json.load(open(tree_path))
        print(f'{out_path}: {save(tree, out_path)} B')
    elif cmd == 'check':
        tree = json.load(open(tree_path))
        payload = zlib.decompress(open(out_path, 'rb').read())
        ok = php_serialize(tree) == payload
        print('BYTE-EXACT vs json' if ok else 'MISMATCH vs json')
    sys.exit(0)


if __name__ == '__main__':
    main()

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
  (формы 2026-09-02 сняты с bpt/etalons/stage-flow.bpt и stage-flow-extended.bpt)
  CreateCrmDealDocumentActivity, CrmEventAddActivity, Task2Activity (полная
  форма с ACCOMPLICES/AUDITORS), GetDynamicInfo в режиме сделки
  (DynamicTypeId=2, OnlyDynamicEntities=N)
  (формы 2026-10-02 сняты с date-filters-heavy — экспорт портала portal-b)

CLI:
  python3 bpt_build.py make TREE.json OUT.bpt     # json-дерево -> .bpt
  python3 bpt_build.py check TREE.json OUT.bpt    # roundtrip-проверка
                                                    # (argv-порядок как в make)
  python3 bpt_build.py probe TREE.json OUT.bpt    # дебаг-копия с [probe]-
                                                    # журналированием переменных
"""
import copy
import itertools
import os
import re
import sys
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bpt_serialize import load_json, php_serialize, php_unserialize_exact, decompress_bpt  # noqa: E402
from bpt_graft_forms import DEFAULT_NORM  # noqa: E402 — единственный
# арбитр формы Default по Multiple (корпус-данные; реестры наследуют,
# не объявляют — аудит 2026-10-16 №4)
from bpt_funcs import (FUNCS, INTERVAL_UNITS, NEAR_MISS,  # noqa: E402
                       NONCANON_OPERATORS, calls as _fn_calls,
                       tokenize as _fn_tokenize)

VERSION = 2
_ACT_SEQ = itertools.count()  # уникализатор: одинаковые активности обязаны
                             # иметь РАЗНЫЕ Name (гоча импорта: "has a duplicate")
# Человекочитаемые Name: <токен>_<номер> — латиница (движок/выражения),
# русская метка живёт в Title (видна в дизайнере).
_NAME_TOKEN = {
    'TerminateActivity': 'Stop',
    'SetVariableActivity': 'SetVar', 'SetFieldActivity': 'SetFld',
    'IfElseActivity': 'Branch',
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
    'Task2Activity': 'Task', 'CreateCrmDealDocumentActivity': 'NewDeal',
    'CrmEventAddActivity': 'Event', 'CrmControlNotifyActivity': 'Ctrl',
    'CrmGetDynamicInfoActivity': 'GetDyn',
    'CrmGenerateEntityDocumentActivity': 'Doc',
}
# Промер корпуса (bpt-builder-core T4): IfElseBranchActivity/SequenceActivity
# в builder-репликах (notify-history-canonical) несут токен Act (Act_21, Act_47 —
# 23+22 узла), записи 'When'/'Seq' были мёртвым кодом и удалены; портальные
# имена вида TP_02 — генерация дизайнера, не наша.


def _alloc_name(a_type: str) -> str:
    """Свежее авто-имя узла: <токен>_<счётчик> (1 тик _ACT_SEQ на узел —
    нумерация плотная, промер T4). Прямая аллокация вместо обхода через
    act('X') (строившего выбрасываемый узел ради имени)."""
    return '%s_%d' % (_NAME_TOKEN.get(a_type, 'Act'), next(_ACT_SEQ))

# Платформенные энум-хэши (TODO №5a). Значения каналов/типов дизайнера в
# REST не документированы — источник = корпус эталонов, расшифровка ключей
# по семантике Titles узлов. Новые ключи вносить по снятым эталонам.
# КЛИЕНТСКИЕ энумы (значения UF-полей конкретного портала) сюда НЕ входят —
# им место в клиентском слое поверх этих словарей.
MESSAGE_TYPE = {  # IMNotifyActivity.MessageType — канал доставки
    'NOTIFY': '4',  # «Уведомление» — корпус 25/25
}
ABSENCE_TYPE = {  # AbsenceActivity.AbsenceType — вид отсутствия
    'VACATION': 'VACATION',  # «Отпуск», график отсутствий — корпус 4/4
}


def _enum(table: dict, key: str, fname: str, param: str) -> str:
    """Ключ энум-хэша -> платформенное значение. Неизвестный ключ — ошибка
    сборки: молчаливый typo унёс бы в портал мусорный тип."""
    try:
        return table[key]
    except KeyError:
        print(f'[enum] {fname}: {param}={key!r} — неизвестный ключ; '
              f'допустимо: {", ".join(sorted(table))}', file=sys.stderr)
        raise


def _user_list(value) -> list:
    """Принимающий шов user-полей (аудит 2026-10-16 №5): str — ОДИН адресат
    (голый list(x) рвал 'user_231' на 8 «юзеров»-символов на wire — гейты
    молчали, импорт проходил); None/пусто — []; list/tuple — копия.
    Канонические пустые формы вызова ({}, '', дефолт-списки) остаются на
    стороне вызывающего — шов их не навязывает."""
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    return list(value)


def act(a_type: str, title: str, props: dict, children=None, name: str | None = None,
        editor_comment: str = '', activated: str = 'Y') -> dict:
    """Узел активности. Name = <токен>_<счётчик> (уникален, латиница);
    Title — человеческая метка (русский — ок, отображается в дизайнере).
    editor_comment — примечание дизайнера (в корпусе 41/447 непустых;
    порядок props каноничен: поля → Title → EditorComment).
    activated='N' — деактивированный узел (дизайнерский черновик; корпус:
    notify-history-canonical Act_41, loop-counter); мусор ≠ Y/N — ValueError.
    clone() принудительно 'Y' — клон всегда живая копия."""
    if activated not in ('Y', 'N'):
        raise ValueError(f"activated={activated!r}: ждём 'Y' | 'N'")
    if name is None:
        name = _alloc_name(a_type)
    # копия входов (аудит): dict(props, …) — shallow; вложенные list/dict
    # оставались общими с caller'ом — пост-мутация тихо меняла узел
    props = copy.deepcopy(props)
    children = copy.deepcopy(children) if children is not None else []
    return {'Type': a_type, 'Name': name, 'Activated': activated, 'Node': None,
            'Properties': dict(props, Title=title, EditorComment=editor_comment),
            'Children': children}


def branch(props: dict, children=None,
           a_type: str = 'IfElseBranchActivity',
           activated: str = 'Y') -> dict:
    """Standalone-конструктор ветки IfElse (TODO №1; поглощает par()/branch()-
    хелперы клиентских билд-скриптов). props — ПОЛНЫЙ Properties ветки,
    включая Title: порядок ключей — зона ответственности вызывающего (канон
    if_else: Title, EditorComment, cond; if_else_field: Title,
    fieldcondition, EditorComment). Каркас канонический: Type, Name (авто
    Act_N), Activated, Node, Properties, Children — единая точка формы для
    if_else-семейства и ручных веток (mixedcondition вне семейства).
    activated='N' — деактивированная ветка (гейт Y/N, см. act())."""
    if activated not in ('Y', 'N'):
        raise ValueError(f"activated={activated!r}: ждём 'Y' | 'N'")
    # копия входов — как в act() (анти-алиасинг, аудит)
    props = copy.deepcopy(props)
    children = copy.deepcopy(children) if children is not None else []
    return {'Type': a_type, 'Name': _alloc_name(a_type), 'Activated': activated,
            'Node': None, 'Properties': props,
            'Children': children}


def set_vars(mapping: dict, title: str = 'Изменение переменных') -> dict:
    """VariableValue: {ИМЯ: значение|выражение}. Вычисляемые — {{=...}}, подстановки — {=...}."""
    return act('SetVariableActivity', title, {'VariableValue': mapping})


def clone(node: dict, title: str | None = None) -> dict:
    """Deep-copy узла с БЕЗУСЛОВНЫМ переименованием: корень и все дочерние
    Name получают свежие авто-имена (дубль Name = reject импорта B24 —
    переименование не опционально). Внутренние {=Name:X}-ссылки на
    переименованные узлы перепривязываются (старое имя -> новое) только в
    границах клона; ссылки на узлы вне клона и сам оригинал не меняются.
    Ссылки извне, державшиеся на имя оригинала, к клону НЕ привязываются:
    если оригинала в шаблоне нет — validate_refs честно репортит висячек
    (фича-детектор). Activated принудительно 'Y'. title — новый
    Properties.Title (необязательно). Поглощает uid()/task()-хелперы
    билд-скриптов (копия эталонного узла + правки полей поверх клона).
    Лог: [clone] old -> new на каждый узел (stderr)."""
    new = copy.deepcopy(node)
    renames = {}

    def rename(n):
        old = n['Name']
        fresh = '%s_%d' % (_NAME_TOKEN.get(n['Type'], 'Act'), next(_ACT_SEQ))
        renames[old] = fresh
        print(f'[clone] {old} -> {fresh}', file=sys.stderr)
        n['Name'] = fresh
        n['Activated'] = 'Y'
        for ch in n.get('Children') or []:
            rename(ch)

    rename(new)
    if title is not None:
        new['Properties']['Title'] = title
    if renames:
        pat = re.compile(r'\{=(' + '|'.join(re.escape(o) for o in renames)
                         + r'):')

        def rebind(v):
            if isinstance(v, dict):
                # №6 (аудит 2026-10-16): mixedcondition.object — bare-name
                # канал условия (без {=}-обёртки; корпус: 31 ряд, домен =
                # имена активностей ∪ режимы Variable/Document). Режимы в
                # renames не бывают (узловые Name — токены), замена по ∈
                # renames их не задевает; ссылки на узлы вне клона — тоже.
                return {k: (renames[x] if k == 'object'
                            and isinstance(x, str) and x in renames
                            else rebind(x)) for k, x in v.items()}
            if isinstance(v, list):
                return [rebind(x) for x in v]
            if isinstance(v, str):
                return pat.sub(lambda m: '{=' + renames[m.group(1)] + ':', v)
            return v

        new = rebind(new)
    return new


def set_fields(mapping: dict, title: str = 'Изменение полей',
               modified_by: list | None = None) -> dict:
    """Запись полей ТЕКУЩЕГО документа (не чужих!). Порядок props — эталон
    (FieldValue, ModifiedBy, MergeMultipleFields, Title, EditorComment).
    modified_by: кого указать автором изменения (корпус: 45×[] и
    1×['{=Document:ASSIGNED_BY_ID}'])."""
    return act('SetFieldActivity', title,
               {'FieldValue': mapping, 'ModifiedBy': _user_list(modified_by),
                'MergeMultipleFields': 'N'})


_CREATE_ORDER = ('TITLE', 'XML_ID', 'CREATED_BY', 'ASSIGNED_BY_ID', 'OPENED',
                 'COMPANY_ID', 'CONTACT_ID', 'CONTACT_IDS', 'CATEGORY_ID',
                 'STAGE_ID', 'LAST_ACTIVITY_BY', 'LAST_ACTIVITY_TIME')


def fwd_ref(target_name, field):
    """Ссылка на результат ПОЗДНЕЙ в билде активности (корпус 1/71:
    notify-history-canonical, SetFld_24 -> явно-именованная цель). Паттерн:
    цели дай явный act(..., name=target_name); валидатор знает все имена
    шаблона, порядок построения роли не играет. Босая форма {=Name:Field}."""
    return f'{{={target_name}:{field}}}'


def _id_str(value, ctx: str) -> str:
    """ID-слот: None — громкая ошибка сборки (str(None)='None' в пропертях
    = тихая порча, аудит), int коэрцится в str (канон s:"40")."""
    if value is None:
        raise ValueError(f'{ctx}: обязательный ID, None недопустим')
    return str(value)


def create_dynamic(target_etid, fields: dict, name: str, blank_fields=None,
                   blank_multi=(), system_blanks=('XML_ID', 'CREATED_BY',
                                                  'ASSIGNED_BY_ID', 'CATEGORY_ID',
                                                  'STAGE_ID', 'LAST_ACTIVITY_BY',
                                                  'LAST_ACTIVITY_TIME')):
    """Создание элемента смарт-процесса target_etid.
    fields: {ПОЛЕ: значение|выражение} (ключи БЕЗ префикса etid — префикс ставится сам).
    blank_fields: UF-имена, добавляемые пустыми '' (для картины в дизайнере);
    blank_multi: то же, но мульти-поля — пустым СПИСКОМ [] (корпус: CONTACT_IDS=[],
    одиночные — ''). Порядок полей канонический (11/11 эталонов): TITLE первым,
    OPENED 5-м, системный скелет, затем UF в порядке вызова."""
    vals = {k: '' for k in (blank_fields or ())}
    vals.update({k: [] for k in blank_multi})
    for k in system_blanks:
        vals.setdefault(k, '')
    prefix = f'{target_etid}_'
    for k in list(fields):
        if isinstance(k, str) and k.startswith(prefix):
            # копипаста ключа из эталонного экспорта: префикс уже там —
            # дубль '154_154_TITLE' = тихая порча (аудит), ремонт + WARN
            print(f'[FIX] WARN create_dynamic: ключ {k!r} уже с etid-'
                  f'префиксом — дубль префикса убран', file=sys.stderr)
            fields = dict(fields)
            fields[k[len(prefix):]] = fields.pop(k)
    vals.update(fields)
    vals['TITLE'] = fields.get('TITLE', '')
    vals['OPENED'] = fields.get('OPENED', 'Y')
    order = ([k for k in _CREATE_ORDER if k in vals]
             + [k for k in vals if k not in _CREATE_ORDER])
    defl = {f'{target_etid}_{k}': vals[k] for k in order}
    return act('CrmCreateDynamicActivity', 'Создать элемент смарт-процесса',
               {'DynamicTypeId': _id_str(target_etid, 'create_dynamic: target_etid'),
                'OnlyDynamicEntities': 'Y',
                'DynamicEntitiesFields': defl}, name=name)


def create_dynamic_item_id_expr(create_node: dict) -> str:
    """Ссылка на ID созданного элемента (дополнительный результат активности)."""
    return '{=%s:ItemId}' % create_node['Name']


def update_dynamic(target_etid, fields: dict, item_id_expr: str | None = None,
                   filter_conditions: list | None = None,
                   title: str = 'Изменить элемент смарт-процесса') -> dict:
    """Обновление ЧУЖИХ элементов СП. Таргетинг — строго ИЛИ item_id_expr
    (прямой ID), ИЛИ filter_conditions = [(поле_цели, значение), ...] (AND);
    оба сразу — ValueError (шаблон понёс бы и DynamicId, и DynamicFilterFields).
    Форма items каноническая: ряды-пары [условие,'AND'] с хвостовым джойнером
    даже у одиночного условия (19/19 фильтр-рядов корпуса, та же форма в
    get_dynamic_info). ВНИМАНИЕ: на части сборок выражения в DynamicId не
    вычисляются и фильтры по crm-UF не резолвятся — рабочий паттерн: фильтр по
    родному полю ID цели, значение — выражение из тех-поля документа
    (см. etalons.md)."""
    if item_id_expr is not None and filter_conditions is not None:
        raise ValueError('update_dynamic: таргетинг ИЛИ-ИЛИ — item_id_expr и '
                         'filter_conditions вместе запрещены')
    if item_id_expr is None and filter_conditions is None:
        # DynamicId='' без фильтра: семантика портала неизвестна (открытый
        # вопрос аудита) — нетаргетированный update не собираем молча
        raise ValueError('update_dynamic: таргетинг обязателен — '
                         'item_id_expr ИЛИ filter_conditions '
                         "(DynamicId='' без фильтра = неопределённость)")
    props = {'DynamicTypeId': _id_str(target_etid, 'update_dynamic: target_etid'),
             'DynamicId': ''}
    if filter_conditions is not None:
        props['DynamicFilterFields'] = {'items': [
            [{'object': 'Document', 'field': fld, 'operator': '=', 'value': val},
             'AND'] for fld, val in filter_conditions]}
    elif item_id_expr is not None:
        print('[FIX] update_dynamic: DynamicId-путь (item_id_expr) не '
              'вычисляется на части сборок портала — рекомендован '
              'filter_conditions по родному полю ID цели (etalons.md)',
              file=sys.stderr)
        props['DynamicId'] = item_id_expr
    props['DynamicEntitiesFields'] = fields
    return act('CrmUpdateDynamicActivity', title, props)


def if_else_field(branches: list, title: str = 'Условие') -> dict:
    """Ветвление по ПОЛЮ документа (fieldcondition; 15/75 веток корпуса:
    loop-counter ×12, canonical ×3). branches: [(title, row|None, activities)];
    row=None = else. row: [имя_поля, оператор, значение] РОВНО 3 элем
    (хвост '0' добавится сам; 4-элем = копипаста из экспорта — гейт).
    Операторы корпуса: '=', 'empty', '!empty'. Порядок ключей ветки:
    Title, fieldcondition, EditorComment (канон-сэмпл canonical).
    title — Title IfElse-узла (корпус: не только 'Условие')."""
    _ifelse_guard(branches, [r for _, r, _ in branches], 'if_else_field')
    children = []
    for t, row, acts in branches:
        props = {'Title': t}
        if row is None:
            props['truecondition'] = '1'
        else:
            if len(row) != 3:
                raise ValueError(f'if_else_field: ряд [поле, оператор, '
                                 f'значение] — {len(row)} элем (хвост \'0\' '
                                 f'дописывается сам)')
            props['fieldcondition'] = [list(row) + ['0']]
        props['EditorComment'] = ''
        children.append(branch(props, acts))
    return act('IfElseActivity', title, {}, children)


def start_workflow(template_id, document_id, params: dict | None = None,
                   title: str = 'Запустить БП', use_subscription: str = 'N',
                   editor_comment: str = '') -> dict:
    """Запуск подчинённого шаблона на другом документе (канон loop-counter ×11).
    template_id: ID шаблона-цели НА ПОРТАЛЕ (после импорта цели вписать ID!).
    document_id: ID/выражение документа цели. params: {имя_параметра шаблона:
    значение|выражение} — по канону передаются ВСЕ параметры (неиспользуемые '').
    Ссылка на запущенный процесс — доп.результаты активности."""
    return act('StartWorkflowActivity', title,
               {'DocumentId': _id_str(document_id, 'start_workflow: document_id'),
                'TemplateId': _id_str(template_id, 'start_workflow: template_id'),
                'UseSubscription': use_subscription,
                'TemplateParameters': dict(params or {})},
               editor_comment=editor_comment)


def _ifelse_guard(branches: list, conds: list, fname: str) -> None:
    """Кардинальность ветвления: >= 2 ветки, <= 1 else (cond=None).
    IfElse БЕЗ else легитимен (эталон notify-history-canonical: 2 условные ветки);
    сиротская одиночная ветка и две else — аномалии quirks-доки."""
    if len(branches) < 2:
        raise ValueError(f'{fname}: {len(branches)} ветка — сирота; нужно >= 2')
    if sum(1 for c in conds if c is None) > 1:
        raise ValueError(f'{fname}: более одной else-ветки (cond=None)')


def if_else(branches: list, title: str = 'Условие') -> dict:
    """branches: [(title, condition|None, activities), ...]; condition=None =
    else (не более одной). condition: [имя_переменной, оператор, значение]
    РОВНО 3 элем (хвост '0' добавится сам; 4-элем = копипаста — гейт).
    Правая часть: '{=Variable:...}' для переменных, литерал для констант.
    Ключи ветки канонические: Node 4-м (75/75 корпуса).
    title — Title IfElse-узла (корпус: 'router' и др.)."""
    _ifelse_guard(branches, [c for _, c, _ in branches], 'if_else')
    children = []
    for t, cond, acts in branches:
        props = {'Title': t, 'EditorComment': ''}
        if cond is None:
            props['truecondition'] = '1'
        else:
            if len(cond) != 3:
                raise ValueError(f'if_else: условие [имя, оператор, значение]'
                                 f' — {len(cond)} элем (хвост \'0\' '
                                 f'дописывается сам)')
            props['propertyvariablecondition'] = [cond + ['0']]
        children.append(branch(props, acts))
    return act('IfElseActivity', title, {}, children)


def if_else_mixed(branches: list, title: str = 'Условие') -> dict:
    """Ветвление по mixedcondition (эталон loop-idle Exit-ветка): условия на
    доп.результаты активностей и переменные.
    branches: [(title, rows|None, activities)]; rows=None = else.
    rows: [(object, field, operator, value, joiner)] РОВНО 5 элем — object:
    имя активности (её результаты), 'Variable', 'Document'; joiner: '0'
    первая строка, далее '1' (OR) / прочие по UI.
    title — Title IfElse-узла (корпус: 'WHOIS' и др.)."""
    _ifelse_guard(branches, [r for _, r, _ in branches], 'if_else_mixed')
    children = []
    for t, rows, acts in branches:
        props = {'Title': t, 'EditorComment': ''}
        if rows is None:
            props['truecondition'] = '1'
        else:
            for row in rows:
                if len(row) != 5:
                    raise ValueError(f'if_else_mixed: ряд (object, field, '
                                     f'operator, value, joiner) — '
                                     f'{len(row)} элем')
            props['mixedcondition'] = [
                {'object': o, 'field': f, 'operator': op, 'value': v,
                 'joiner': j} for (o, f, op, v, j) in rows]
        children.append(branch(props, acts))
    return act('IfElseActivity', title, {}, children)


def while_loop(condition: list, children: list, title: str = 'Цикл',
               body_title: str = 'Sequence of actions') -> dict:
    """condition: [имя_переменной, оператор, значение] (хвост '0'
    допишется сам, как в if_else) ИЛИ уже 4-элем с хвостом; иное число
    элем — ValueError (1/5+ = порча ряда, гейт).
    Тело оборачивается в SequenceActivity (по эталонам внешнего корпуса);
    body-Title ≠ Title цикла — канон 'Sequence of actions' (6/8 эталонных
    While; исключения типа date-window-branch «Пока даты неверны» — через body_title)."""
    if len(condition) == 3:
        condition = condition + ['0']
    elif len(condition) != 4:
        raise ValueError(f"while_loop: condition [имя, оператор, значение"
                         f"(, '0')] — {len(condition)} элем")
    body = sequence(children, body_title)
    return act('WhileActivity', title,
               {'propertyvariablecondition': [condition]}, [body])


# Режимы источника ForEach (Object): портал-канон v20 (задача project-x, дифф
# v19->v20 — Object=Parameter/Constant доказаны порталом наравне с
# Variable/Document; анти-паттерн — индукция из единственного эталонного
# режима). ЕДИНСТВЕННОЕ объявление домена: graft form-lint (FL-05) и
# validate наследуют через for_each_object_ok, реестр A-имён корпуса
# (bpt_graft_forms.FOR_EACH_MODES) — данные, не арбитр (аудит №1).
FOR_EACH_SOURCES = frozenset({'Variable', 'Parameter', 'Constant', 'Document'})


def for_each_object_ok(obj: str, names: set) -> bool:
    """Единый предикат домена ForEach Object (канон v20): режим
    FOR_EACH_SOURCES ∪ имена активностей шаблона (корпус
    notify-history-canonical Object=A73347_… — промер 2026-10-16: все 7
    корпусных A-имён живут как Name узлов своих деревьев)."""
    return obj in FOR_EACH_SOURCES or obj in names


def for_each(variable_name: str, children: list,
             title: str = 'Итератор', source: str = 'Variable',
             body_title: str = 'Sequence of actions',
             source_activity: str | None = None) -> dict:
    """Итератор по мульти-значению. Два режима Object:
      source          — режим источника, портал-канон v20 (задача project-x):
                        {Variable, Parameter, Constant, Document};
                        неизвестный режим — ValueError сборки (ретро №3);
      source_activity — имя-режим: Object=<Name активности шаблона>
                        (итерация доп-результатов; корпус
                        notify-history-canonical). Взаимоисключаем с source;
                        существование имени проверяет validate по
                        ФИНАЛЬНОМУ дереву (fwd-паттерн: цель может быть
                        позже в сборке — аудит №1).
    source='Variable': Object=Variable, Variable=имя мульти-переменной.
    Значение итерации — через доп.результаты (см. эталон внешнего
    корпуса: Variable=ids, Object=Variable). Тело —
    SequenceActivity с канон-Title 'Sequence of actions'."""
    if source_activity is not None:
        if source != 'Variable':
            raise ValueError('for_each: задан source_activity — source '
                             f'не указывают (получен {source!r})')
        obj = source_activity
    elif source not in FOR_EACH_SOURCES:
        raise ValueError(f'for_each: source={source!r} вне портал-канона '
                         f'v20: {sorted(FOR_EACH_SOURCES)}')
    else:
        obj = source
    body = sequence(children, body_title)
    return act('ForEachActivity', title,
               {'Variable': variable_name, 'Object': obj}, [body])


def variable(name: str, v_type: str, label: str = '', default: str = '',
             required: str = '0', multiple: str = '0',
             title: str | None = None, description: str = '') -> dict:
    """Объявление переменной шаблона. Две канон-формы (корпус): базовая
    Name,Description,Type,Required,Multiple,Options,Default (40/47; Options
    и Default присутствуют и пустыми) и title-форма Name,Title,Description,
    Type,Required,Multiple (6/47, stage-flow/1187/1195-canonical; без Options,
    Default — только если непуст). description — пояснение (4/47 непустых).
    Default-форма по Multiple (аудит №4, волна 2): арбитр — DEFAULT_NORM
    (единый источник, корпус: multi -> list|None, single -> str);
    multi-пусто = [] (промер 2026-10-16: 130+ Default=[] при Multiple=1),
    непустой list — pass-through; рассинхрон типа — ValueError сборки
    (обе стороны гейта)."""
    mult = str(multiple)
    norm = DEFAULT_NORM.get(mult)
    if norm is None:
        raise ValueError(f"variable: multiple={multiple!r} — только '0'|'1'")
    if default != '' and type(default).__name__ not in norm:
        raise ValueError(
            f'variable {name!r}: Default типа {type(default).__name__} '
            f'при Multiple={mult} — корпус несёт {sorted(norm)}')
    if default == '' and mult == '1':
        default = []
    if title is not None:
        d = {'Name': label or name, 'Title': title, 'Description': description,
             'Type': v_type, 'Required': required, 'Multiple': multiple}
        if default:
            d['Default'] = default
        return d
    return {'Name': label or name, 'Description': description, 'Type': v_type,
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
                'UserParameter': _user_list(user_parameter),
                'ReserveUserParameter': _user_list(reserve),
                'SkipAbsent': skip_absent, 'SkipAbsentReserve': skip_absent,
                'SkipTimeman': skip_timeman, 'SkipTimemanReserve': skip_timeman})


def get_user_info(user, fields, title='Данные сотрудника') -> dict:
    """Чтение полей профиля юзера. user: ['user_231'] или выражение;
    fields: {'USER_WORK_POSITION': ('Position', 'string'), ...} —
    результаты как доп-результаты {=Name:USER_WORK_POSITION}."""
    return act('GetUserInfoActivity', title,
               {'GetUser': _user_list(user),
                'UserFields': {k: {'Name': n, 'Type': t}
                               for k, (n, t) in fields.items()}})


def placeholder(marker: str, comment: str = '', children=None,
                minimized: bool = False) -> dict:
    """Заглушка-маркер незакрытой функциональности (EmptyBlockActivity из
    корпуса эталонов). Title = '!!! ... !!!' — видно в дизайнере.
    children: черновая подсхема внутри маркера — доминирующая форма корпуса
    (10/11 несут Sequence-контент). minimized=True эмитит _DesMinimized='Y'
    (свёрнутый блок, 4/10 корпуса). ВАЖНО: comment восстанавливается после
    act() — act() устанавливает EditorComment='' (см. TOFIX/S4-паритет);
    EditorComment идёт первым ключом, как в эталонах."""
    props = {'EditorComment': comment}
    if minimized:
        props['_DesMinimized'] = 'Y'
    node = act('EmptyBlockActivity', f'!!! {marker} !!!', props, children)
    node['Properties']['EditorComment'] = comment
    return node


def get_dynamic_info(etid, return_fields, filter_rows, fields_cache,
                     document_meta=None, title='Get SPA item information',
                     only_dynamic='Y'):
    """Чтение элементов СП по фильтру (эталон loop-idle). Результаты —
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
    if document_meta is not None:
        ef['Document'] = document_meta
    return act('CrmGetDynamicInfoActivity', title,
               {'DynamicTypeId': str(etid),
                'ReturnFields': list(return_fields),
                'OnlyDynamicEntities': only_dynamic,
                'DynamicFilterFields': {'items': items},
                'DynamicEntityFields': ef})


# ---------- классы из date-filters-heavy (портал portal-b, 2026-10-02) ----------
# Порядок ключей Properties/Fields повторяет эталон побайтно-критично
# (PHP dict упорядочен); числовидные значения — СТРОКИ, как в эталонах.

_DEAL_DOCUMENT_META = {'Type': 'document', 'Name': 'Сделка',
                       'Default': ['crm', 'CCrmDocumentDeal', 'DEAL']}


def get_deal_info(return_fields, filter_rows, fields_cache,
                  title: str = 'Получить информацию об элементе CRM') -> dict:
    """Чтение полей СДЕЛКИ из БП на любом документе (эталон date-filters-heavy:
    DynamicTypeId='2', OnlyDynamicEntities='N' — отличия от режима СП).
    filter_rows: [(object, field, operator, value), ...] — канонический
    таргетинг по родному ID: field='ID', value='{=Document:PARENT_ID_2 > id}'
    (суффикс '> id' достаёт числовой id из crm-поля; см. etalons.md).
    fields_cache: {FIELD: {Name,Type,BaseType}} — минимум: возвращаемые
    поля (кеш дизайнера в эталоне раздут на 411 ключей CONTACT.*/COMPANY.*,
    для генерации достаточно запрошенного). Результаты — {=Name:FIELD}."""
    return get_dynamic_info('2', return_fields, filter_rows, fields_cache,
                            document_meta=copy.deepcopy(_DEAL_DOCUMENT_META),
                            title=title, only_dynamic='N')


_DEAL_CREATE_ORDER = ('TITLE', 'OPPORTUNITY', 'CURRENCY_ID', 'ACCOUNT_CURRENCY_ID',
                      'ASSIGNED_BY_ID', 'CATEGORY_ID', 'STAGE_ID', 'CLOSED',
                      'TYPE_ID', 'COMMENTS', 'CONTACT_ID', 'COMPANY_ID',
                      'SOURCE_ID')


def create_deal(fields: dict, name: str | None = None,
                title: str = 'Создание новой сделки') -> dict:
    """Создание СДЕЛКИ из БП любого типа документа (CreateCrmDealDocumentActivity,
    эталон date-filters-heavy: CATEGORY_ID='0' Обращения + SOURCE_ID='REPEAT_SALE' —
    форма кейса «Реактивация → Успех»). fields: {ПОЛЕ: значение|выражение}
    поверх канонического скелета пустых ('' — дизайнер нормализует);
    CONTACT_ID/COMPANY_ID тянуть выражением из предшествующего get_deal_info
    (типа {=GetDeal:CONTACT_ID}). ID созданной сделки — доп.результат
    активности (имя ключа проверить на E2E: ItemId|ID)."""
    vals = {k: '' for k in _DEAL_CREATE_ORDER}
    vals.update(fields)
    vals['TITLE'] = fields.get('TITLE', '')
    return act('CreateCrmDealDocumentActivity', title, {'Fields': vals},
               name=name)


def task(title, responsible: str = '{=Document:ASSIGNED_BY_ID}',
         description: str | None = None,
         creator: str = '{=Document:ASSIGNED_BY_ID}', deadline: str = '',
         accomplices: tuple | list = (), auditors: tuple | list = (),
         priority: str = '1', crm_link: str = 'Y',
         allow_change_deadline: str = 'Y', time_tracking: str = 'Y',
         task_control: str = 'Y', hold_to_close: str = 'N',
         name: str | None = None,
         task_title: str = 'Поставить задачу') -> dict:
    """Задача (Task2Activity, полная форма; эталон date-filters-heavy — боевая
    Task2_13371024 + тестовая с участниками). accomplices/auditors —
    соисполнители/наблюдатели: список 'user_N' (эталон: ACCOMPLICES=
    ['user_14','user_58']); пусто → '' (строка, не список). description
    по умолчанию = title (канон живых задач диспетчеров). deadline —
    выражение/литерал; crm_link (AUTO_LINK_TO_CRM_ENTITY)='Y' привязывает
    задачу к текущему CRM-документу. hold_to_close='Y' — БП ЖДЁТ закрытия
    задачи (паттерн «сверка оплат»: нулевые данные → удерживающая задача
    → после закрытия пересчёт и продолжение)."""
    f = {'TITLE': title,
         'CREATED_BY': creator,
         'RESPONSIBLE_ID': responsible,
         'DESCRIPTION': title if description is None else description,
         'ACCOMPLICES': _user_list(accomplices) or '',
         'START_DATE_PLAN': '',
         'END_DATE_PLAN': '',
         'DEADLINE': deadline,
         'PRIORITY': priority,
         'GROUP_ID': '',
         'FLOW_ID': '',
         'ALLOW_CHANGE_DEADLINE': allow_change_deadline,
         'ALLOW_TIME_TRACKING': time_tracking,
         'TASK_CONTROL': task_control,
         'AUDITORS': _user_list(auditors) or '',
         'PARENT_ID': '',
         'TAG_NAMES': {},
         'REQUIRED_RESULT': 'N',
         'DEPENDS_ON': {},
         'UF_CRM_TASK': '',
         'UF_TASK_WEBDAV_FILES': ''}
    return act('Task2Activity', task_title,
               {'Fields': f, 'HoldToClose': hold_to_close,
                'AUTO_LINK_TO_CRM_ENTITY': crm_link, 'AsChildTask': '',
                'CheckListItems': {}, 'TimeEstimateHour': '',
                'TimeEstimateMin': ''}, name=name)


def event_add(text: str, event_type: str = 'INFO', users=None,
              title: str = 'Запись события в crm') -> dict:
    """Событие таймлайна CRM (CrmEventAddActivity, эталон date-filters-heavy:
    EventType='INFO'). НЕ комментарий — REST-ридера нет (etalons.md,
    probe-режим); для журналирования использовать timeline_comment.
    users: список адресатов-выражений; пусто → {}."""
    return act('CrmEventAddActivity', title,
               {'EventType': event_type, 'EventText': text,
                'EventUser': _user_list(users) or {}})


def control_notify(message: str, to_head: str = 'Y', users=None,
                   title: str = 'Уведомить руководство') -> dict:
    """Контроль руководителю (CrmControlNotifyActivity, эталон company-stage:
    ToHead='Y' — уходит руководителю ответственного). Штатная CRM-механика
    «Уведомить руководство» из карточки. users (ToUsers) — доп. адресаты
    помимо руководителя; пусто → {} (эталонная форма). message — текст
    уведомления (в эталоне заглушка '1441').
    ВАЖНО: 5-ключевой канон узла — БЕЗ 'Node', Properties вторым ключом
    (реплика-фикстура company-stage-control; Designer-экспорт этой активности
    не несёт Node — act()-форма давала расхождение байт)."""
    return {'Type': 'CrmControlNotifyActivity',
            'Properties': {'MessageText': message,
                           'ToUsers': _user_list(users) or {},
                           'ToHead': to_head, 'Title': title},
            'Name': _alloc_name('CrmControlNotifyActivity'),
            'Activated': 'Y', 'Children': []}


# ---------- новые классы (эталоны stage-flow/stage-flow-extended, 2026-09-02) ----------
# Порядок ключей props повторяет эталонный ПОБАЙТОВО (PHP dict упорядочен);
# числовидные значения в эталонах — СТРОКИ ('4', '40', '50').

def sequence(children: list, title: str = 'Последовательность') -> dict:
    """Последовательный блок — эталонная обёртка веток заданий (токен имени
    Act — канон реплик корпуса, промер bpt-builder-core T4)."""
    return {'Type': 'SequenceActivity', 'Name': _alloc_name('SequenceActivity'),
            'Activated': 'Y', 'Node': None,
            'Properties': {'Title': title}, 'Children': children}


def terminate(title: str = 'Прерывание процесса') -> dict:
    """Мягкий стоп текущего запуска: bare-форма Properties={Title} (эталон
    loop-counter, без KillWorkflow/TerminateType/EditorComment). Прочие запуски
    шаблона по этому документу НЕ трогает. Убийство дублей — terminate_others."""
    node = act('TerminateActivity', title, {})
    node['Properties'] = {'Title': title}
    return node


def terminate_others(title: str = 'Стоп: убить дубли') -> dict:
    """Дедупликатор: убить все процессы ЭТОГО шаблона для документа, кроме
    текущего (StateTitle/KillWorkflow/TerminateType — полная форма).
    Держать первой активностью шаблона-диспетчера. Мягкий стоп — terminate."""
    return act('TerminateActivity', title,
               {'StateTitle': 'Workflow terminated', 'KillWorkflow': 'Y',
                'TerminateType': 'allExceptCurrentByDocumentAndTemplate'})


def approve(users, name, description='', status_message='Approval in progress',
            button_yes='Accept', button_no='Decline',
            comment_required='YR', approve_type='all', min_percent='50',
            children=None, title='Согласование', node_name=None) -> dict:
    """Задание-согласование с двумя кнопками. name = СТАТУСНОЕ имя задания
    (Properties.Name); node-Name под ссылку {=Name:...} — только node_name.
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
              'DelegationType': '0', 'Users': _user_list(users)}
    return act('ApproveActivity', title, props,
               [sequence(c) for c in children], name=node_name)


def request_info(users, fields, name, description='',
                 status_message='Waiting for additional information',
                 button='Save', comment_required='N', optional=False,
                 cancel_button='Cancel', children=None, title=None,
                 node_name=None) -> dict:
    """Задание «запросить доп.информацию» (заполнение полей). name =
    СТАТУСНОЕ имя (Properties.Name); node-Name под {=Name:...} — node_name.
    fields: list of dict(Title, Name, Description, Type, Required, Multiple)
    — Name = имя результата, читается как {=ActivityName:Name}.
    optional=True -> RequestInformationOptionalActivity (кнопка Cancel);
    children: optional — [ветка Submit, ветка Cancel], иначе — [ветка Submit];
    None -> пустые ветки по канону (2 для optional, 0 для обычного)."""
    a_type = ('RequestInformationOptionalActivity' if optional
              else 'RequestInformationActivity')
    if children is None:
        children = [[], []] if optional else []
    props = {'Users': _user_list(users), 'Name': name, 'Description': description,
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
                  'Multiple': f.get('Multiple', '0'),
                  # корпус 1/16: Default последним, пустой рудимент —
                  # эмитим только если явно в спеке (опц-ин)
                  **({'Default': f['Default']} if 'Default' in f else {})}
                 for f in fields],
             'OverdueDate': ''}
    if optional:
        props['CancelType'] = 'any'
        props['TaskButtonCancelMessage'] = cancel_button
        props['SaveVariables'] = 'N'
    if title is None:
        title = ('Запрос информации (можно отменить)' if optional
                 else 'Запрос информации')
    return act(a_type, title, props, [sequence(c) for c in children],
               name=node_name)


def review(users, name, description='', status_message='Familiarization',
           button='Done', children=None, title='Ознакомление',
           node_name=None) -> dict:
    """Задание-ознакомление (одна кнопка Done). name = AbsenceName-подобное
    статусное имя; node-Name под {=Name:...} — node_name. children: [ветка Done];
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
              'Users': _user_list(users)}
    return act('ReviewActivity', title, props, [sequence(c) for c in children],
               name=node_name)


def delay(duration=None, unit='m', until=None, is_local='N',
          write_log='N', title='Пауза') -> dict:
    """Пауза. Две формы (XOR): interval (duration='40', unit='s'|'m'|'h'|'d')
    ИЛИ точная дата (until='{=System:Now}' / выражение-дата). Без аргументов
    и оба сразу — ValueError; числовой duration коэрцится в str (канон
    s:"40", аудит: delay(40)→i:40)."""
    if duration is None and until is None:
        raise ValueError('delay: нужна форма — duration ИЛИ until '
                         '(TimeoutDuration None = N в сериализации)')
    if duration is not None and until is not None:
        raise ValueError('delay: XOR — duration и until вместе запрещены')
    if until is not None:
        return act('DelayActivity', title,
                   {'TimeoutTime': until, 'TimeoutTimeIsLocal': is_local,
                    'WriteToLog': write_log})
    return act('DelayActivity', title,
               {'TimeoutDuration': str(duration), 'TimeoutDurationType': unit,
                'WriteToLog': write_log})


def log_entry(text, title='Log entry', name=None) -> dict:
    """Запись в журнал выполнения БП (LogActivity; корпус notify-history ×2).
    Канон: Text → SetVariable '0' → Title 'Log entry' → EditorComment.
    text — обычно выражение на доп.результат ({=Name:GetUser},
    {=Name:ErrorMessage})."""
    return act('LogActivity', title, {'Text': text, 'SetVariable': '0'},
               name=name)


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
                'CommentUser': _user_list(users)
                or ['{=Document:ASSIGNED_BY_ID}']})


def im_notify(users_to, message, users_from=None, title='Уведомление',
              message_type='NOTIFY', name=None) -> dict:
    """Уведомление сотруднику(-ам). ВАЖНО: ТЕКСТ сообщения хранится в
    свойстве MessageSite (гоча нейминга нового дизайнера, подтверждена
    маркером «ГДЕ ТЕКСТ?» в notify-history); MessageOut — внешняя доставка.
    message_type: ключ MESSAGE_TYPE (не сырое значение дизайнера).
    name: явный node-Name (для fwd_ref-целей и читаемости)."""
    return act('IMNotifyActivity', title,
               {'MessageSite': message, 'MessageOut': '',
                'MessageType': _enum(MESSAGE_TYPE, message_type,
                                     'im_notify', 'message_type'),
                'MessageUserFrom': _user_list(users_from) or ['1'],
                'MessageUserTo': _user_list(users_to)}, name=name)


def absence_entry(users, date_from, date_to, name, description='',
                  absence_type='VACATION', state='', finish_state='',
                  title='Запись в график отсутствий', node_name=None) -> dict:
    """Запись в график отсутствий (legacy-класс; НЕТ delete/update!).
    users: список 'user_<ID>' или выражений; date_from/to: даты/выражения;
    state/finish_state — свободные строки-статусы на начало/конец (обычно '');
    absence_type: ключ ABSENCE_TYPE."""
    return act('AbsenceActivity', title,
               {'AbsenceName': name, 'AbsenceDesrc': description,
                'AbsenceFrom': date_from, 'AbsenceTo': date_to,
                'AbsenceState': state, 'AbsenceFinishState': finish_state,
                'AbsenceType': _enum(ABSENCE_TYPE, absence_type,
                                     'absence_entry', 'absence_type'),
                 'AbsenceSiteId': 's1',
                 'AbsenceUser': _user_list(users)}, name=node_name)


def calendar_event(name, date_from, date_to, users=None, section='',
                   cal_type='', owner_id='', timezone='Europe/Moscow',
                   description='', title='Событие календаря',
                   node_name=None) -> dict:
    """Событие календаря (Calendar2Activity; строковые props по эталону)."""
    return act('Calendar2Activity', title,
               {'CalendarName': name, 'CalendarDesrc': description,
                'CalendarFrom': date_from, 'CalendarTo': date_to,
                'CalendarType': cal_type, 'CalendarOwnerId': owner_id,
                'CalendarSection': section, 'CalendarTimezone': timezone,
                'CalendarUser': _user_list(users) or ['1']}, name=node_name)


def scaffold(children: list, variables: dict, doc_fields: dict,
             root_props: dict | None = None, parameters: dict | None = None,
             constants: dict | None = None,
             bare_root: bool = False) -> dict:
    """Каркас шаблона. Сбрасывает _ACT_SEQ: имена (и байты .bpt) детерминированы
    в пределах шаблона и не зависят от того, что собиралось раньше в процессе
    (мульти-шаблонные билд-скрипты стабильны). root_props/doc_fields — из эталонного экспорта того же
    типа документа (копируются verbatim; Permission НЕ добавляем — эталоны
    stage-flow-extended его не содержат). Пустые PARAMETERS/VARIABLES/CONSTANTS
    обязаны быть [] (PHP a:0:{} парсится обратно в list).
    bare_root=True — корень без Activated/Node (форма company-stage ×2: 4-ключевой
    корень Type/Name/Properties/Children; дефолт — 6-ключевой канон)."""
    global _ACT_SEQ
    _ACT_SEQ = itertools.count()
    props = dict(root_props or {})

    def norm(d):
        return d if d else []

    root = {'Type': 'SequentialWorkflowActivity', 'Name': 'Template'}
    if not bare_root:
        root['Activated'] = 'Y'
        root['Node'] = None
    root['Properties'] = props
    root['Children'] = children
    return {'VERSION': VERSION,
            'TEMPLATE': [root],
            'PARAMETERS': norm(parameters), 'VARIABLES': norm(variables),
            'CONSTANTS': norm(constants), 'DOCUMENT_FIELDS': doc_fields}


def probe(tree: dict, tag: str = '[probe]', users=None) -> dict:
    """Дебаг-копия шаблона (TODO №3): после КАЖДОЙ SetVariableActivity —
    журналирующая CrmTimelineCommentAdd по контракту etalons.md
    («Probe-режим»): строка 1 '<tag> after <Name исходного SV>', далее
    '<ключ>={=Variable:<ключ>}' на каждый ключ VariableValue (рантайм
    подставляет значения). Исходник НЕ мутируется (deepcopy). Вставка — в
    тот же Children-уровень, что и исходный SV: set_vars в теле while/foreach
    даёт СЕРИЮ комментариев по итерациям (хронология — фича). Name вставок —
    свежие из локального пула с пропуском занятых (повторный probe по
    уже-пробированному дереву дублей Name не даёт); глобальный _ACT_SEQ не
    тратится — вывод детерминирован независимо от истории билда."""
    new_tree = copy.deepcopy(tree)
    taken = set()

    def harvest_names(v):
        if isinstance(v, dict):
            if isinstance(v.get('Name'), str):
                taken.add(v['Name'])
            for x in v.values():
                harvest_names(x)
        elif isinstance(v, list):
            for x in v:
                harvest_names(x)

    harvest_names(new_tree.get('TEMPLATE'))
    alloc = itertools.count()
    token = _NAME_TOKEN['CrmTimelineCommentAdd']

    def fresh_name():
        while True:
            n = f'{token}_{next(alloc)}'
            if n not in taken:
                taken.add(n)
                return n

    def journal_node(sv):
        vv = (sv.get('Properties') or {}).get('VariableValue')
        keys = list(vv) if isinstance(vv, dict) else []
        head = f'{tag} after {sv["Name"]}'
        text = '\n'.join([head] + [f'{k}={{=Variable:{k}}}' for k in keys])
        return act('CrmTimelineCommentAdd', head,
                   {'CommentText': text,
                    'CommentUser': _user_list(users)
                    or ['{=Document:ASSIGNED_BY_ID}']},
                   name=fresh_name())

    def rebuild(node):
        out = []
        for ch in (node.get('Children') or []):
            if _is_node(ch):
                rebuild(ch)
            out.append(ch)
            if isinstance(ch, dict) and ch.get('Type') == 'SetVariableActivity':
                out.append(journal_node(ch))
        node['Children'] = out

    for root in (new_tree.get('TEMPLATE') or []):
        rebuild(root)
    return new_tree


_REF_RE = re.compile(r'\{=\s*([A-Za-z_]\w*)\s*:\s*([A-Za-z_]\w*)')
_NS_WHITELIST = {'Document', 'Variable', 'GlobalConst', 'System', 'Template',
                 'User', 'Constant', 'Parameter'}


def _lint_expr(s: str) -> list:
    """Синтакс-линт выражений в строке: каждый `{{` обязан открывать
    вычисляемое `{{=...}}` (без `=` — мусор вида {{ID}}), вложенные `{{`__
    внутри тела запрещены, незакрытые `{{=`/`{=` — ошибка. Легитимная форма
    (корпус, 6/6): `{{=func({=Document:X}, ...)}}` c одиночными рефами внутри."""
    problems, i, n = [], 0, len(s)
    while i < n:
        if s.startswith('{{', i):
            if not s.startswith('{{=', i):
                problems.append(f'двойные фигурные без = (мусор?): {s[i:i + 24]!r}')
                i += 2
                continue
            close = s.find('}}', i + 3)
            if close == -1:
                problems.append(f'незакрытое выражение: {s[i:i + 30]!r}')
                break
            body = s[i + 3:close]
            if '{{=' in body:
                problems.append(f'[FN-06 ERROR] вложенное {{{{= внутри '
                                f'{{{{=…}}}} — внутренние выражения пишутся '
                                f'без обёртки: {s[i:close + 2][:60]!r}')
            elif '{{' in body:
                problems.append(f'вложенные двойные фигурные внутри выражения: '
                                f'{s[i:close + 2][:60]!r}')
            i = close + 2
        elif s.startswith('{=', i):
            close = s.find('}', i)
            if close == -1:
                problems.append(f'незакрытая ссылка {{=: {s[i:i + 30]!r}')
                break
            i = close + 1
        else:
            i += 1
    return problems


def validate_refs(tree: dict) -> list:
    """Гейт ссылочной целостности `{=Ns:Field}` и синтаксиса выражений: Ns —
    либо известный namespace (whitelist; {=GlobalConst:...} легитимны и при
    пустых CONSTANTS — loop-counter), либо Name активности этого шаблона;
    `{=Variable:X}` — X объявлен в VARIABLES. Полевая проверка прочих
    секций (Constant/Parameter/Document) пробована волной 6 bpt-graft и
    ОТКЛОНЕНА корпусом: манифесты экспортов недозаполнены (approval-stages
    {=Document:ASSIGNED_BY}, bp_external {=Constant:url} — живые ссылки вне
    секций); полный резолвер с манифестами активностей и калибровкой —
    bpt_graft.resolve_refs (TODO №7). Строки линтуются на мусорные
    `{{...}}`-формы. Возвращает список проблем (пустой = ок). Эталоны не
    править: реальный висячок в корпусе — notify-history-canonical, {=Act_12:ID} ×2
    (узла Act_12 нет) — детектор обязан ловить, а не «чинить»."""
    names, strings = set(), []

    def harvest(node, collect_names):
        if isinstance(node, dict):
            if collect_names and node.get('Type') \
                    and isinstance(node.get('Name'), str):
                names.add(node['Name'])
            for v in node.values():
                harvest(v, collect_names)
        elif isinstance(node, list):
            for v in node:
                harvest(v, collect_names)
        elif isinstance(node, str):
            strings.append(node)

    harvest(tree.get('TEMPLATE'), True)
    harvest((tree.get('VARIABLES'), tree.get('PARAMETERS'),
             tree.get('CONSTANTS')), False)
    variables = tree.get('VARIABLES')
    declared = set(variables.keys()) if isinstance(variables, dict) else set()
    problems = []
    for s in strings:
        for ns, name in _REF_RE.findall(s):
            if ns == 'Variable':
                if name not in declared:
                    problems.append(f'{{=Variable:{name}}} — не объявлена в VARIABLES')
            elif ns not in _NS_WHITELIST and ns not in names:
                problems.append(f'{{={ns}:{name}}} — ни namespace, ни Name '
                                f'активности шаблона (висячая ссылка)')
        problems.extend(_lint_expr(s))
    return problems


_DIGIT_KEY_RE = re.compile(r'^-?\d+$')
_SCAR_KEY_RE = re.compile(r'^_-?\d+$')

# Канон функций калькулятора выражений — реестр bpt_funcs.FUNCS (волна 4,
# T3; источник: урок 4912, скилл bitrix24-bpt references/expression-runtime.md
# §1). Вне {{=}}-обёртки НЕ вычисляются. Hand-list _EXPR_FUNCS поглощён:
# живой баг закрыт — в списке стоял number_format, а канон урока 4912
# numberformat (голое =numberformat(…) раньше не ловилось §1-рулой).
_EXPR_FUNC_RE = re.compile(
    r'\b(?:' + '|'.join(sorted(FUNCS)) + r')\s*\(')
_COND_KEYS = ('mixedcondition', 'propertyvariablecondition', 'fieldcondition')

# --- expr_lint (TODO №4): правила expression-runtime --------------------------
# Калибровка по корпусу (12 эталонов, ноль срабатываний): единственный
# datediff несёт '%d' (не '%a'); dateadd без datediff (компенсация не
# нужна); money-имён в выражениях нет; round отсутствует.
_MONEY_FIELD_RE = re.compile(
    r'(?i)^[A-Z0-9_]*(?:PRICE|SUM|OPPORTUNITY|AMOUNT|COST)[A-Z0-9_]*$')
_REF_IN_BODY_RE = re.compile(r'\{=(\w+):(\w+)\}')
_ARITH_BY_REF_RE = re.compile(r'\{=[^}]*\}\s*[+\-*/]|[+\-*/]\s*\{=')
_PERIOD_VAR_RE = re.compile(r'(?i)\b(?:rps|rpe|period_start|period_end)\b')

# Функции, чей результат гарантированно непуст (даже на пустых
# аргументах: floatval('') = 0) — якоря flow-анализа непустоты
# переменных для FN-10 (референс expression-runtime §2)
_NONEMPTY_FN_RE = re.compile(
    r'^\{\{=(?:date|round|numberformat|intval|floatval|abs|ceil|floor|'
    r'min|max|dateadd|workdateadd|addworkdays|datediff|randstring)\(')
# голый реф как if-аргумент «, {=Ns:Field},» / «, {=Ns:Field})» — признак
# пусто-возможной ветки (грубый, но согласован с FN-10-детекцией)
_BARE_REF_ARG_RE = re.compile(r',\s*\{=\w+:\w+\}\s*[,)]')
# r-паттерн гварда: if(cond по x, литерал, голый реф того же x) —
# непуст в обеих ветках (cond отфильтровал пустоту) — стиль *_r-переменных
# booking-chain (pay_r/ntm_r/mat_r)
_R_GUARD_RE = re.compile(
    r"\{\{=if\(\{=Variable:(\w+)\}[^,]*,\s*'[^']*',\s*\{=Variable:\1\}")


def _ver_tuple(v: str) -> tuple:
    return tuple(int(p) for p in v.split('.'))


def _portal_version(portal: str):
    """Профиль портала для FN-03: 'cloud' -> None (проверок нет — облако
    всегда на актуальном модуле), 'box@X.Y[.Z]' -> кортеж версии,
    невалидное -> None."""
    if not portal or portal == 'cloud' or not portal.startswith('box@'):
        return None
    try:
        return _ver_tuple(portal[4:])
    except ValueError:
        return None


def _arg_tokens(toks: list, i_open: int, argno: int):
    """Токены argno-го аргумента вызова (1-based, верхний уровень) или
    None, если аргумента меньше. i_open — индекс токена '(' вызова."""
    depth, cur, args = 0, [], []
    for t in toks[i_open:]:
        kind, text = t[0], t[1]
        if kind == 'op' and text == '(':
            depth += 1
            if depth == 1:
                continue  # открывающая скобка самого вызова
        elif kind == 'op' and text == ')':
            depth -= 1
            if depth == 0:
                args.append(cur)
                break
        elif depth == 1 and kind == 'op' and text == ',':
            args.append(cur)
            cur = []
            continue
        if depth >= 1:
            cur.append(t)
    return args[argno - 1] if len(args) >= argno else None


_CYR_RE = re.compile('[А-Яа-яЁё]')
_ARITH_OPS = ('+', '-', '*', '/')
_FN_ERROR_RE = re.compile(r'^\[FN-\d{2} ERROR\] ')


def _fn_rules(body: str, portal: str, profile,
              nonempty: frozenset | set = frozenset()) -> list:
    """FN-правила канона урока 4912 по телу одного {{=…}} (без обёртки).
    Severity — в префиксе находки: `[FN-NN ERROR|WARN|INFO] …`; validate()
    маршрутизует ERROR в errors. FN-06 (вложенное {{=) — в _lint_expr.
    nonempty — переменные, гарантированно непустые по flow-анализу
    шаблона (validate); для голых строковых вызовов — пусто (все
    Variable-рефы без гварда консервативно ERROR)."""
    out, toks = [], _fn_tokenize(body)
    cls = _fn_calls(toks)
    flagged_ops = set()
    for name, argc, i_open, i_close in cls:
        lname = name.lower()
        if lname in ('true', 'false'):
            continue  # литералы; true() в примере урока 4912 легитимно
        rec = FUNCS.get(lname)
        if rec is None:
            if lname in NEAR_MISS:
                out.append(f'[FN-01 ERROR] функция {name} вне канона урока '
                           f'4912 — пишется {NEAR_MISS[lname]}: '
                           f'{body[:48]!r}')
            else:
                out.append(f'[FN-01 ERROR] неизвестная функция {name} — нет '
                           f'в каноне (урок 4912) и в корпусе: '
                           f'{body[:48]!r}')
            continue
        if not rec['doc']:
            out.append(f'[FN-01 WARN] {name} работает в корпусе, но не '
                       f'документирована — риск на других сборках: '
                       f'{body[:48]!r}')
        if i_close is None:
            continue  # незакрытая скобка — синтакс-ошибка другого уровня
        lo, hi = rec['arity']
        if argc < lo or (hi is not None and argc > hi):
            top = '∞' if hi is None else hi
            out.append(f'[FN-02 ERROR] {name}: аргументов {argc}, канон '
                       f'{lo}..{top}: {body[:48]!r}')
        if profile is not None and rec['since'] \
                and _ver_tuple(rec['since']) > profile:
            out.append(f'[FN-03 WARN] {name} доступна с bizproc '
                       f'{rec["since"]} — ниже профиля {portal}: '
                       f'{body[:48]!r}')
        if lname in ('dateadd', 'workdateadd'):
            arg2 = _arg_tokens(toks, i_open, 2)
            if arg2 is not None and len(arg2) == 1 and arg2[0][0] == 'str':
                lit = arg2[0][1][1:-1]
                if '{=' in lit:
                    out.append(f'[FN-05 WARN] {{=…}} в кавычках интервала не '
                               f'подставится — собери интервал через &: '
                               f'{body[:48]!r}')
                else:
                    for w in re.findall(r'[A-Za-z]+', lit):
                        wl = w.lower()
                        if wl not in INTERVAL_UNITS[lname]:
                            out.append(f'[FN-05 WARN] юнит {w!r} не из '
                                       f'канона {lname}: {body[:48]!r}')
                        elif wl == 'm':
                            out.append(f"[FN-05 INFO] юнит 'm' = месяцы; "
                                       f"минуты — 'i'/'min': {body[:48]!r}")
        if lname in ('date', 'locdate'):
            after = toks[i_close + 1] if i_close + 1 < len(toks) else None
            before = toks[i_open - 2] if i_open >= 2 else None
            ar = after[1] if after and after[0] == 'op' \
                and after[1] in _ARITH_OPS else None
            br = before[1] if before and before[0] == 'op' \
                and before[1] in _ARITH_OPS else None
            if ar or br:
                out.append(f'[FN-07 ERROR] {name}() возвращает СТРОКУ — '
                           f'арифметика {ar or br} не вычислится '
                           f'(§3 runtime): {body[:48]!r}')
        if lname == 'if' and i_close is not None:
            # FN-10: голый реф в value-ветке if(), НЕ загвардированный
            # условием ЭТОГО ЖЕ if — пустая подстановка вырезается,
            # формула становится невалидной и уезжает в документ
            # literal-текстом (кейсы v20/v22: if(var='', {=A:TITLE}, …),
            # if(total=0, '', {=Variable:bnum_val}) — условие про другое
            # значение). БЕЗОПАСНА канон-форма if(x = '', '-',
            # {=Variable:x}): условие гарантирует непустоту value-ветки
            # (референс expression-runtime §2: пустые ПЕРЕМЕННЫЕ-аргументы
            # НЕ безопасны — прежнее «опасны только поля» было ложью).
            cond = _arg_tokens(toks, i_open, 1) or []
            guarded = set()
            for t in cond:
                if t[0] != 'ref' or not isinstance(t[1], str):
                    continue
                m = re.match(r'\{=(\w+):(\w+)\}', t[1])
                if m:
                    guarded.add((m.group(1), m.group(2)))
            for argno in (2, 3):
                arg = _arg_tokens(toks, i_open, argno)
                if arg is None or len(arg) != 1 or arg[0][0] != 'ref' \
                        or not isinstance(arg[0][1], str):
                    continue
                m = re.match(r'\{=(\w+):(\w+)\}', arg[0][1])
                if m and (m.group(1), m.group(2)) in guarded:
                    continue  # тот же источник, что в условии — гвард есть
                if m and m.group(1) == 'Variable' \
                        and m.group(2) in nonempty:
                    continue  # flow: переменная гарантированно непуста
                out.append(
                    f'[FN-10 ERROR] if(): value-ветка {argno} — голый '
                    f'реф {arg[0][1]} без гварда в условии: пустая '
                    f'подстановка вырезается -> literal-мусор в документе; '
                    f'гвард if(x = \'\', …), IfElse-активность или предгвард-'
                    f'переменная (паттерн bnum_r): {body[:56]!r}')
        if lname == 'strtotime':
            for t in (_arg_tokens(toks, i_open, 1) or []):
                if t[0] == 'str' and _CYR_RE.search(t[1]):
                    out.append(f'[FN-08 WARN] strtotime парсит только '
                               f'английские даты — кириллица в литерале '
                               f'вернёт пустоту: {body[:48]!r}')
    for kind, text, _ in toks:
        if kind == 'op' and text in NONCANON_OPERATORS \
                and text not in flagged_ops:
            flagged_ops.add(text)
            out.append(f'[FN-04 WARN] оператор {text} не документирован — '
                       f'канон {NONCANON_OPERATORS[text]!r}: {body[:48]!r}')
        elif kind == 'op' and text == '%' and '%' not in flagged_ops:
            flagged_ops.add('%')
            out.append(f'[FN-09 INFO] оператор % — проценты, не modulo '
                       f'(урок 4912): {body[:48]!r}')
    return out


def expr_lint(s: str, sink_multiple: bool = False, portal: str = 'cloud',
              nonempty: frozenset | set = frozenset()) -> list:
    """Линтер строк-формул: правила expression-runtime (TODO №4) + FN-правила
    канона функций урока 4912 (волна 4, TODO №7).
    Доменные рулы (WARN, кроме INFO-прочерков):
      - datediff с '%a' без компенсации if(date('Hi'...)) в той же формуле —
        floor 24-часовых занижает разницу на 1 при временах заезда<выезда
        (§3 runtime);
      - money-имя поля (PRICE/SUM/OPPORTUNITY/AMOUNT/COST) в арифметике
        рядом с рефом без floatval( — money приходит строкой '2899.04|CAD'
        (§4 runtime);
      - датa-периода (rps/rpe/period_start/period_end) в datediff без клипа
        ±1 — границы месяца (§3 runtime);
      - [INFO] round() в Multiple-приёмник без прочерка if(x=0,'-',...):
        нулевые элементы выбрасываются из списков несимметрично (§7).
    FN-рулы (severity в префиксе `[FN-NN SEV]`): FN-01 имя/канон, FN-02
    арность, FN-03 since-версия vs портал, FN-04 ==/!=/&&/||, FN-05 юниты
    dateadd, FN-07 date()/locdate() в арифметике, FN-08 кириллица в
    strtotime, FN-09 оператор %. FN-06 — в _lint_expr (структура строки).
    Возвращает список однострочных находок (пустой = чисто)."""
    findings = []
    profile = _portal_version(portal)
    for m in re.finditer(r'\{\{=(.*?)\}\}', s, re.S):
        body = m.group(1)
        if "'%a'" in body or '"%a"' in body:
            # клип ±1 (§3, границы периода 00:00) — автор уже думал о
            # граничной арифметике; Hi-компенсация нужна ночам/периодам
            # с временами, не клипам
            if "date('Hi'" not in body and 'date("Hi"' not in body \
                    and not re.search(r'[+\-]\s*1\b', body):
                findings.append(
                    f"datediff('%a') без компенсации if(date('Hi',E) < "
                    f"date('Hi',B),1,0) — floor занижает на 1 (§3): "
                    f"{body[:56]!r}")
        if _ARITH_BY_REF_RE.search(body) and 'floatval(' not in body:
            for _, field in _REF_IN_BODY_RE.findall(body):
                if _MONEY_FIELD_RE.match(field):
                    findings.append(
                        f'money-поле {field} в арифметике без floatval() — '
                        f"money приходит строкой 'N|CCY' (§4): {body[:56]!r}")
                    break
        if (_PERIOD_VAR_RE.search(body) and 'datediff(' in body
                and not re.search(r'[+\-]\s*1\b', body)):
            findings.append(
                'дата периода (rps/rpe) в datediff без клипа ±1 по границе '
                f'месяца (§3): {body[:56]!r}')
        findings.extend(_fn_rules(body, portal=portal, profile=profile,
                                  nonempty=nonempty))
    if sink_multiple and 'round(' in s and "'-'" not in s:
        findings.append(
            "[INFO] round() в Multiple-приёмник без прочерка "
            f"if(x=0,'-',round(x,N)) — нули выбрасываются из списков (§7): "
            f"{s[:56]!r}")
    return findings


def _is_node(d) -> bool:
    """Узел активити = каркас {Type, Properties, Children} (не путать с
    RI-полями Properties.RequestedInformation — у них тоже есть Name/Type)."""
    return (isinstance(d, dict) and 'Type' in d and 'Properties' in d
            and 'Children' in d)


def _walk_nodes(v, path, out):
    if _is_node(v):
        out.append((path, v))
        v = v.get('Children')
        path = f'{path}.Children'
    if isinstance(v, dict):
        for k, x in v.items():
            _walk_nodes(x, f'{path}.{k}', out)
    elif isinstance(v, list):
        for i, x in enumerate(v):
            _walk_nodes(x, f'{path}[{i}]', out)


def _declared_variables(tree: dict) -> dict:
    variables = tree.get('VARIABLES')
    return variables if isinstance(variables, dict) else {}


def validate(tree: dict, portal: str = 'cloud') -> tuple:
    """Единое ядро build-валидатора (TODO №2): (errors, warnings). ERROR —
    блокируют save()/make; WARN — однострочный вывод, запись продолжается.
    portal='cloud' (дефолт) — FN-03-проверки since-версий пропускаются;
    portal='box@X.Y' — WARN на функциях, доступных с более позднего
    bizproc. Поглощает validate_refs (висячие ссылки, undeclared-переменные,
    мусорные {{...}}) и кардинальность if_else (для загруженных/рукописных
    деревьев; при билде остаётся и _ifelse_guard). Рулы:
      ERROR  IfElse-дети только IfElseBranchActivity; кардинальность >= 2
             ветки / <= 1 else; Name узлов пуст/дубль (reject импорта);
             explode-самоссылка (зацикливание doc-side, §7 runtime);
             [FN-01/02/07] имя/арность/строковая дата в арифметике
             (expr_lint → _fn_rules, канон урока 4912);
      WARN   mixedcondition.value с функцией/выражением (не вычисляются,
             §5 — литералы и голые ссылки легитимны, калибровка корпуса);
             SV-функция в голом =expr (не вычисляется без {{=}}, §1);
             explode в не-Multiple-приёмник (список не отрендерится, §7);
             if(...) c {=...}-рефами в значении условия (пустой реф ломает
             парсер, §2); [FN-03/04/05/08] since/операторы/юниты/кириллица.
    Калибровано на корпусе: 12/12 эталонов — ноль ложных (canonical несёт
    только известные висячки Act_12 ×2; FN-промер 2026-10-02 — чисто)."""
    errors = list(validate_refs(tree))
    warnings = []
    nodes = []
    _walk_nodes(tree.get('TEMPLATE'), 'TEMPLATE', nodes)
    declared = _declared_variables(tree)

    # flow-слой непустоты для FN-10: переменная гарантированно непуста,
    # если ХОТЬ ОДНО присваивание — непустой литерал ('0' ZEROING, '-'
    # ветки паттерна bnum_r) или {{=…}} из _NONEMPTY_FN_RE (floatval('')
    # = 0 и т.п.). Голые рефы полей/переменных пусто-возможны.
    # Консервативно-оптимистичен: неветвлённая непустая запись = гвард.
    nonempty_vars = set()
    for _path, node in nodes:
        vv = (node.get('Properties') or {}).get('VariableValue')
        if not isinstance(vv, dict):
            continue
        for var, val in vv.items():
            vals = val if isinstance(val, list) else [val]
            for v in vals:
                if isinstance(v, str) and v and '{=' not in v:
                    nonempty_vars.add(var)      # непустой литерал
                elif isinstance(v, str) and _NONEMPTY_FN_RE.match(v):
                    nonempty_vars.add(var)      # round/date/…-обёртка
                elif isinstance(v, str) and v.startswith('{{=if(') \
                        and not _BARE_REF_ARG_RE.search(v):
                    # if-обёртка без голых реф-веток: обе ветки —
                    # числа/выражения (pay_month v4-канона)
                    nonempty_vars.add(var)
                elif isinstance(v, str) and _R_GUARD_RE.search(v):
                    # r-паттерн: литерал-ветка + реф того же источника,
                    # что в cond — непуст в обеих ветках (pay_r v19)
                    nonempty_vars.add(var)

    # --- Name узлов: пуст/дубль (reject импорта B24) ---
    seen = {}
    for path, node in nodes:
        name = node.get('Name')
        if not isinstance(name, str) or not name:
            errors.append(f'{path}: узел без Name — reject импорта')
        elif name in seen:
            errors.append(f'{path}: дубль Name {name!r} (уже {seen[name]}) — '
                          f'reject импорта')
        else:
            seen[name] = path

    for path, node in nodes:
        typ = node.get('Type')
        children = node.get('Children') or []
        props = node.get('Properties') or {}
        if typ == 'ForEachActivity':
            # домен Object: режимы ∪ имена активностей шаблона —
            # единый предикат (аудит №1; имя-режим проверяется по
            # финальному дереву, fwd-паттерн легитимен на сборке)
            obj = props.get('Object')
            if isinstance(obj, str) and not for_each_object_ok(obj,
                                                               set(seen)):
                errors.append(
                    f'{path}: ForEach Object={obj!r} — не режим '
                    f'{sorted(FOR_EACH_SOURCES)} и не Name активности '
                    'шаблона (канон v20)')
        if typ == 'IfElseActivity':
            for i, ch in enumerate(children):
                ch_t = ch.get('Type') if isinstance(ch, dict) else type(ch).__name__
                if ch_t != 'IfElseBranchActivity':
                    errors.append(f'{path}: IfElse-ребёнок[{i}] {ch_t!r} — '
                                  f'только IfElseBranchActivity (reject импорта)')
            branches = [c for c in children
                        if isinstance(c, dict)
                        and c.get('Type') == 'IfElseBranchActivity']
            if len(branches) < 2:
                errors.append(f'{path}: {len(branches)} ветка-сирота; нужно >= 2')
            n_else = sum(1 for b in branches
                         if (b.get('Properties') or {}).get('truecondition'))
            if n_else > 1:
                errors.append(f'{path}: более одной else-ветки ({n_else})')
        if typ == 'SetVariableActivity':
            for var, val in (props.get('VariableValue') or {}).items():
                # линтуем и str, и элементы list-значений ($-аппенды rd_*
                # живут в списках — FN-10-кейс v22 был именно там)
                items = ([val] if isinstance(val, str)
                         else [x for x in val if isinstance(x, str)]
                         if isinstance(val, list) else [])
                sink = str((declared.get(var) or {}).get('Multiple')) == '1'
                for item in items:
                    for finding in expr_lint(item, sink_multiple=sink,
                                             portal=portal,
                                             nonempty=nonempty_vars):
                        if _FN_ERROR_RE.match(finding):
                            errors.append(f'{path}: SV {var}: {finding}')
                        else:
                            warnings.append(f'{path}: SV {var}: {finding}')
                if isinstance(val, str) and val.startswith('=') \
                        and not val.startswith('{{=') \
                        and _EXPR_FUNC_RE.search(val):
                    warnings.append(
                        f'{path}: SV {var} = голый =expr с функцией — '
                        f'не вычислится ({{{{=...}}}} обязателен): {val[:48]!r}')
                # explode-самоссылка: только str-формы (списочный
                # explode-перепод rd_eg=explode('$',rd_eg) — живой
                # канон-паттерн v19+, не самоссылка-зацикливание)
                if isinstance(val, str) and 'explode(' in val:
                    for src_ in set(_REF_RE.findall(val)):
                        if src_[0] == 'Variable' and src_[1] == var:
                            errors.append(
                                f'{path}: explode-самоссылка {var!r} — '
                                f'писать в ДРУГУЮ переменную (зацикливание)')
                    d = declared.get(var)
                    if d is not None and str(d.get('Multiple')) != '1':
                        warnings.append(
                            f'{path}: explode пишет в не-Multiple {var!r} — '
                            f'список в документе не отрендерится')
        for key in _COND_KEYS:
            rows = props.get(key)
            if not isinstance(rows, list):
                continue
            for i, row in enumerate(rows):
                # две формы ряда: dict (mixedcondition: value по ключу) и
                # list [field, operator, value, joiner] (propertyvariable/
                # fieldcondition: value третьим элементом)
                if isinstance(row, dict):
                    v = row.get('value')
                    vals = v if isinstance(v, list) else [v]
                elif isinstance(row, (list, tuple)) and len(row) >= 3:
                    vals = [row[2]]
                else:
                    continue
                for v in vals:
                    if not isinstance(v, str):
                        continue
                    if _EXPR_FUNC_RE.search(v) or v.startswith('{{='):
                        warnings.append(
                            f'{path}.{key}[{i}]: функция/выражение в value '
                            f'условия не вычисляется (§5 runtime): {v[:48]!r}')
                    if 'if(' in v and '{=' in v:
                        warnings.append(
                            f'{path}.{key}[{i}]: if() c {{=...}}-рефами в '
                            f'value — пустой реф ломает парсер (§2 runtime): '
                            f'{v[:48]!r}')

    # --- прочерк-гард explode-цепочек (грабля #4, кейс v22->v23) -------
    # mseparator=2 в docx-плейсхолдерах выбрасывает нулевые/пустые
    # элементы списка. РАЗНОРОДНЫЙ гвард-статус внутри группы
    # синхронных колонок (несколько $-аппендов exploded-переменных в
    # одном SV) — потенциал рассинхрона строк: одна колонка выбрасывает
    # элемент, соседняя нет (v22->v23: EG/Keys/Cleaning пустыми при
    # pet=650 — забытый if(x=0,'-',…) при гвардёных соседях).
    # Severity WARN (не ERROR): разнородность — НОРМА корпуса всех эпох
    # (v4+: rd_bby/chin/chout голые при гвардёном rd_bnum — безопасно,
    # т.к. пусто-возможность снята семантикой отбора итератора,
    # статически невидимой). WARN = «проверь синхронность выброса».
    exploded = set()
    for path, node in nodes:
        vv = (node.get('Properties') or {}).get('VariableValue')
        if not isinstance(vv, dict):
            continue
        for val in vv.values():
            vals = val if isinstance(val, list) else [val]
            for v in vals:
                if isinstance(v, str) and 'explode(' in v:
                    exploded.update(m.group(1) for m in re.finditer(
                        r'explode\([^,]+,\s*\{=Variable:(\w+)\}\)', v))
    if exploded:
        for path, node in nodes:
            vv = (node.get('Properties') or {}).get('VariableValue')
            if not isinstance(vv, dict):
                continue
            grp = {}
            for var, val in vv.items():
                if var not in exploded or not isinstance(val, list):
                    continue
                for item in val:
                    if isinstance(item, str) and '$' in item:
                        value_part = item.split('$', 1)[1]
                        bare_refs = [
                            m for m in re.findall(
                                r'\{=Variable:(\w+)\}', value_part)
                            if m not in nonempty_vars]
                        if 'if(' in value_part or not re.findall(
                                r'\{=\w+:\w+\}', value_part):
                            # if-гвард ИЛИ литерал (колонки-заглушки '—'
                            # v19: literal ≠ гвард — из разнородности
                            # исключаются)
                            grp[var] = 'guarded' \
                                if 'if(' in value_part else 'literal'
                        elif bare_refs:
                            grp[var] = 'bare'
                        else:
                            grp[var] = 'guarded'  # только nonempty-рефы
            bare = [v for v, st in grp.items() if st == 'bare']
            guarded = [v for v, st in grp.items() if st == 'guarded']
            if len(grp) >= 2 and bare and guarded:
                warnings.append(
                    f'{path}: $-аппенды explode-группы с РАЗНОРОДНЫМ '
                    f'гвардом — колонки {bare} без if(x=0,…) выбрасывают '
                    f'нули при гвардёных соседях: рассинхрон строк '
                    f'таблицы, если пустотность колонок несинхронна '
                    f'(грабля #4, кейс v22->v23)')
    return errors, warnings


def _lint_keys(tree: dict) -> tuple:
    """Гейт JSON-шва: числовой str-ключ ('14') в PHP-дереве невозможен
    (PHP автокастует числовые ключи массивов в int) — это шрам
    json-границы, ошибка; ключ '_N' — сигнатура шрама lossy-парсера
    старого export-JSON, предупреждение. Возвращает (errors, warnings)."""
    errors, warnings = [], []

    def walk(v, path):
        if isinstance(v, dict):
            for k, x in v.items():
                if isinstance(k, str):
                    if _DIGIT_KEY_RE.match(k):
                        errors.append(f'{path}.{k!r}: числовой str-ключ — '
                                      f'в PHP он int; грузи через load_json')
                    elif _SCAR_KEY_RE.match(k):
                        warnings.append(f'{path}.{k!r}: ключ вида _N — шрам '
                                        f'lossy-парсера старого export-JSON')
                walk(x, f'{path}.{k!r}')
        elif isinstance(v, list):
            for i, x in enumerate(v):
                walk(x, f'{path}[{i}]')

    walk(tree, '$')
    return errors, warnings


def save(tree: dict, path: str) -> int:
    """Сериализация с гейтами: validate() — ERROR-рулы блокируют запись,
    WARN-рулы пропускаются с однострочным выводом в stderr; самопроверка
    (re-serialize == payload; явная проверка вместо assert — живёт под
    python3 -O). Запись атомарна (tmp + fsync + rename): обрыв/ENOSPC не
    оставляют усечённый .bpt. Возвращает размер."""
    errors, warnings = validate(tree)
    for w in warnings:
        print(f'[validate] WARN {path}: {w}', file=sys.stderr)
    if errors:
        raise ValueError(f'{path}: валидация не прошла:\n  '
                         + '\n  '.join(errors))
    errs, warns = _lint_keys(tree)
    for w in warns:
        print(f'[FIX] WARN {path}: {w}', file=sys.stderr)
    if errs:
        raise ValueError(f'{path}: числовые str-ключи (JSON-шрам):\n  '
                         + '\n  '.join(errs))
    payload = php_serialize(tree)
    back = php_unserialize_exact(payload)
    if php_serialize(back) != payload:
        raise ValueError(f'{path}: re-serialize mismatch!')
    blob = zlib.compress(payload)
    tmp = path + '.tmp'
    with open(tmp, 'wb') as f:
        f.write(blob)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    return len(blob)


def load(path: str) -> dict:
    return php_unserialize_exact(zlib.decompress(open(path, 'rb').read()))


USAGE = ('usage: bpt_build.py make TREE.json OUT.bpt [--portal=…]\n'
         '       bpt_build.py check TREE.json OUT.bpt [--portal=…]\n'
         '       bpt_build.py probe TREE.json OUT.bpt  # дебаг-копия: [probe]-'
         'журналирование после каждого set_vars (etalons.md); полный гейт '
         'save() включён\n'
         '       bpt_build.py validate TREE.json [--portal=…]\n'
         '         # exit: 0 чисто / 1 ERROR-находки / 2 usage (WARN не '
         'меняют exit)\n'
         '         # --portal=cloud|box@X.Y — профиль для FN-03 (since-'
         'версии функций; cloud = без проверок, дефолт)')


def _portal_ok(portal: str) -> bool:
    if portal == 'cloud':
        return True
    if not portal.startswith('box@'):
        return False
    try:
        _ver_tuple(portal[4:])
    except ValueError:
        return False
    return True


def _split_portal(args: list) -> tuple:
    """Выделить --portal=VALUE: -> (позиционные args, portal | None);
    дубль флага -> (args, '!')."""
    portal, rest = None, []
    for a in args:
        if a.startswith('--portal='):
            if portal is not None:
                return rest, '!'
            portal = a[len('--portal='):]
        else:
            rest.append(a)
    return rest, portal


def main() -> None:
    args = sys.argv[1:]
    if args[:1] == ['validate']:
        rest, portal = _split_portal(args[1:])
        if len(rest) != 1 or portal == '!' \
                or (portal is not None and not _portal_ok(portal)):
            print(USAGE, file=sys.stderr)
            sys.exit(2)
        tree = load_json(open(rest[0], encoding='utf-8').read())
        errors, warnings = validate(tree, portal=portal or 'cloud')
        for w in warnings:
            print(f'[validate] WARN {w}')
        for e in errors:
            print(f'[validate] ERROR {e}')
        print(f'{"OK" if not errors else "FAIL"}: {rest[0]}: '
              f'{len(errors)} err, {len(warnings)} warn')
        sys.exit(1 if errors else 0)
    if args[:1] == ['probe']:
        rest = args[1:]
        author = None
        if len(rest) >= 3 and rest[-2] == '--author':
            author = rest[-1]
            rest = rest[:-2]
        if len(rest) != 2:
            print(USAGE, file=sys.stderr)
            sys.exit(2)
        tree_path, out_path = rest
        tree = load_json(open(tree_path, encoding='utf-8').read())
        nodes = []
        _walk_nodes(tree.get('TEMPLATE'), '$', nodes)
        n_sv = sum(1 for _, n in nodes
                   if n.get('Type') == 'SetVariableActivity')
        # автор журнала: пустой ответственный сущности = портал молча не
        # пишет комментарии (аудит); --author user_ID задаёт CommentUser
        probed = probe(tree, users=[f'user_{author}'] if author else None)
        size = save(probed, out_path)
        print(f'[probe] {n_sv} journal inserts after {n_sv} set_vars')
        print(f'{out_path}: {size} B')
        sys.exit(0)
    rest, portal = _split_portal(args)
    if len(rest) != 3 or rest[0] not in ('make', 'check') \
            or portal == '!' or (portal is not None
                                 and not _portal_ok(portal)):
        print(USAGE, file=sys.stderr)
        sys.exit(2)
    cmd, tree_path, out_path = rest
    tree = load_json(open(tree_path, encoding='utf-8').read())
    if cmd == 'make':
        if portal is not None:
            # make с явным профилем: FN-03 WARN до записи (save() сам
            # валидирует с cloud-дефолтом — сигнатура без изменений)
            errs, warns = validate(tree, portal=portal)
            for w in warns:
                print(f'[validate] WARN {w}', file=sys.stderr)
            for e in errs:
                print(f'[validate] ERROR {e}', file=sys.stderr)
        print(f'{out_path}: {save(tree, out_path)} B')
    elif cmd == 'check':
        errors, warnings = validate(tree, portal=portal or 'cloud')
        for w in warnings:
            print(f'[validate] WARN {w}', file=sys.stderr)
        if errors:
            print(f'{out_path}: валидация не прошла:', file=sys.stderr)
            for p in errors:
                print(f'  {p}', file=sys.stderr)
            sys.exit(1)
        payload = decompress_bpt(open(out_path, 'rb').read())
        if php_serialize(tree) != payload:
            print('MISMATCH vs json', file=sys.stderr)
            sys.exit(1)
        print('BYTE-EXACT vs json')
    sys.exit(0)


if __name__ == '__main__':
    main()

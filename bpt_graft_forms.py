#!/usr/bin/env python3
"""bpt_graft_forms.py — реестры форм для form-lint graft (волна 6, T5).

ДАННЫЕ, не код: сгенерировано tests/harvest_forms.py из живого корпуса
(23 .bpt: etalons + регресс-база booking-chain (сан-корпус)).
Ручная правка запрещена — регенерация: python3 tests/harvest_forms.py >
bpt_graft_forms.py (сверка: --check). Рантайм-тесты внешних путей не
трогают (bpt = standalone GH-зеркало); корпуса растёт -> перегенерить.

Содержимое:
  PROP_KEYS        — ключи Properties по классам активностей (case-fold
                     реестр ключей: конвенция в реальных БП СМЕШАННАЯ —
                     условия lowercase: mixedcondition/truecondition/cond);
  SECTION_FIELDS   — разрешённые ключи записей секций (каркас 7 полей
                     {Default,Description,Multiple,Name,Options,Required,
                     Type} + title/Label-формы корпуса);
  FOR_EACH_MODES   — режимы источника ForEach (портал-канон v20: Object
                     ∈ {Variable, Parameter, Constant, Document});
  DEFAULT_NORM     — форма Default по Multiple (multiple -> list|None,
                     single -> str; multi-Default значением не пережил
                     импорт — все записи списка list, ни одной str при
                     Multiple=1).
"""

PROP_KEYS = {
    'AbsenceActivity': frozenset({'AbsenceDesrc', 'AbsenceFinishState', 'AbsenceFrom', 'AbsenceName', 'AbsenceSiteId', 'AbsenceState', 'AbsenceTo', 'AbsenceType', 'AbsenceUser', 'EditorComment', 'Title'}),
    'ApproveActivity': frozenset({'AccessControl', 'ApproveMinPercent', 'ApproveType', 'ApproveWaitForAll', 'CommentLabelMessage', 'CommentRequired', 'DelegationType', 'Description', 'EditorComment', 'Name', 'OverdueDate', 'Parameters', 'SetStatusMessage', 'ShowComment', 'StatusMessage', 'TaskButton1Message', 'TaskButton2Message', 'TimeoutDuration', 'TimeoutDurationType', 'Title', 'Users'}),
    'Calendar2Activity': frozenset({'CalendarDesrc', 'CalendarFrom', 'CalendarName', 'CalendarOwnerId', 'CalendarSection', 'CalendarTimezone', 'CalendarTo', 'CalendarType', 'CalendarUser', 'EditorComment', 'Title'}),
    'CreateCrmContactDocumentActivity': frozenset({'EditorComment', 'Fields', 'Title'}),
    'CreateCrmDealDocumentActivity': frozenset({'EditorComment', 'Fields', 'Title'}),
    'CreateDocumentActivity': frozenset({'EditorComment', 'Fields', 'Title'}),
    'CreateListsDocumentActivity': frozenset({'DocumentType', 'EditorComment', 'Fields', 'Title'}),
    'CrmChangeStatusActivity': frozenset({'EditorComment', 'ModifiedBy', 'TargetStatus', 'Title'}),
    'CrmControlNotifyActivity': frozenset({'MessageText', 'Title', 'ToHead', 'ToUsers'}),
    'CrmCreateDynamicActivity': frozenset({'DynamicEntitiesFields', 'DynamicTypeId', 'EditorComment', 'OnlyDynamicEntities', 'Title'}),
    'CrmEventAddActivity': frozenset({'EditorComment', 'EventText', 'EventType', 'EventUser', 'Title'}),
    'CrmGenerateEntityDocumentActivity': frozenset({'CreateActivity', 'EditorComment', 'EnablePublicUrl', 'MyCompanyBankDetailId', 'MyCompanyId', 'MyCompanyRequisiteId', 'TemplateId', 'Title', 'UseSubscription', 'Values', 'WithStamps'}),
    'CrmGetDynamicInfoActivity': frozenset({'DynamicEntityFields', 'DynamicFilterFields', 'DynamicTypeId', 'EditorComment', 'OnlyDynamicEntities', 'ReturnFields', 'Title'}),
    'CrmTimelineCommentAdd': frozenset({'CommentText', 'CommentUser', 'EditorComment', 'Title'}),
    'CrmUpdateDynamicActivity': frozenset({'DynamicEntitiesFields', 'DynamicFilterFields', 'DynamicId', 'DynamicTypeId', 'EditorComment', 'Title'}),
    'DelayActivity': frozenset({'EditorComment', 'TimeoutDuration', 'TimeoutDurationType', 'TimeoutTime', 'TimeoutTimeIsLocal', 'Title', 'WriteToLog'}),
    'EmptyBlockActivity': frozenset({'EditorComment', 'Title', '_DesMinimized'}),
    'ForEachActivity': frozenset({'EditorComment', 'Object', 'Title', 'Variable'}),
    'GetUserActivity': frozenset({'EditorComment', 'MaxLevel', 'ReserveUserParameter', 'SkipAbsent', 'SkipAbsentReserve', 'SkipTimeman', 'SkipTimemanReserve', 'Title', 'UserParameter', 'UserType'}),
    'GetUserInfoActivity': frozenset({'EditorComment', 'GetUser', 'Title', 'UserFields'}),
    'IMNotifyActivity': frozenset({'EditorComment', 'MessageOut', 'MessageSite', 'MessageType', 'MessageUserFrom', 'MessageUserTo', 'Title'}),
    'IfElseActivity': frozenset({'EditorComment', 'Title', '_DesMinimized'}),
    'IfElseBranchActivity': frozenset({'EditorComment', 'Title', 'conditionstring', 'fieldcondition', 'mixedcondition', 'propertyvariablecondition', 'truecondition'}),
    'LogActivity': frozenset({'EditorComment', 'SetVariable', 'Text', 'Title'}),
    'ParallelActivity': frozenset({'EditorComment', 'Title'}),
    'RequestInformationActivity': frozenset({'AccessControl', 'CommentLabelMessage', 'CommentRequired', 'DelegationType', 'Description', 'EditorComment', 'Name', 'OverdueDate', 'RequestedInformation', 'SetStatusMessage', 'ShowComment', 'StatusMessage', 'TaskButtonMessage', 'TimeoutDuration', 'TimeoutDurationType', 'Title', 'Users'}),
    'RequestInformationOptionalActivity': frozenset({'AccessControl', 'CancelType', 'CommentLabelMessage', 'CommentRequired', 'DelegationType', 'Description', 'EditorComment', 'Name', 'OverdueDate', 'RequestedInformation', 'SaveVariables', 'SetStatusMessage', 'ShowComment', 'StatusMessage', 'TaskButtonCancelMessage', 'TaskButtonMessage', 'TimeoutDuration', 'TimeoutDurationType', 'Title', 'Users'}),
    'ReviewActivity': frozenset({'AccessControl', 'ApproveType', 'CommentLabelMessage', 'CommentRequired', 'DelegationType', 'Description', 'EditorComment', 'Name', 'OverdueDate', 'Parameters', 'SetStatusMessage', 'ShowComment', 'StatusMessage', 'TaskButtonMessage', 'TimeoutDuration', 'TimeoutDurationType', 'Title', 'Users'}),
    'SequenceActivity': frozenset({'Title'}),
    'SequentialWorkflowActivity': frozenset({'Permission', 'Title'}),
    'SetFieldActivity': frozenset({'EditorComment', 'FieldValue', 'MergeMultipleFields', 'ModifiedBy', 'Title'}),
    'SetVariableActivity': frozenset({'EditorComment', 'Title', 'VariableValue'}),
    'StartWorkflowActivity': frozenset({'DocumentId', 'EditorComment', 'TemplateId', 'TemplateParameters', 'Title', 'UseSubscription'}),
    'Task2Activity': frozenset({'AUTO_LINK_TO_CRM_ENTITY', 'AsChildTask', 'CheckListItems', 'EditorComment', 'Fields', 'HoldToClose', 'TimeEstimateHour', 'TimeEstimateMin', 'Title'}),
    'TerminateActivity': frozenset({'EditorComment', 'KillWorkflow', 'StateTitle', 'TerminateType', 'Title'}),
    'WebHookActivity': frozenset({'EditorComment', 'Handler', 'Title'}),
    'WhileActivity': frozenset({'EditorComment', 'Title', '_DesMinimized', 'propertyvariablecondition'}),
    'rest_60d07322f81b4d2f1f17970378d6398e': frozenset({'AuthUserId', 'EditorComment', 'SetStatusMessage', 'StatusMessage', 'TimeoutDuration', 'TimeoutDurationType', 'Title', 'UseSubscription', 'files', 'messageText'}),
}

SECTION_FIELDS = frozenset({'Default', 'Description', 'Multiple', 'Name', 'Options', 'Required', 'Type', 'Title', 'Label'})

FOR_EACH_MODES = frozenset({'Constant', 'Parameter', 'TP_327'})

# Multiple -> допустимые python-типы Default (корпус): single='str', multiple='list'|None
DEFAULT_NORM = {'0': frozenset({'str'}), '1': frozenset({'list', 'NoneType'})}

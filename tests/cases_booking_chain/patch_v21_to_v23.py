#!/usr/bin/env python3
"""Переход booking-chain v21 -> v23 на graft API (регресс-база, паттерн-лекарство).

Спецификация — внешний архив проекта/build/patch_v23.py (ревью отчёта 45141):
extra по источникам (Pet/Keys/Cleaning), bnum_r через IfElse-АКТИВНОСТЬ
(канонные !empty-ветки) — лекарство от v22-регресса FN-10: значение
никогда не пусто, голых рефов в if()-ветках нет.

Имена вставляемых узлов — канонические (random-выпад patch_v23
зафиксирован в booking-chain_v23.bpt: bnum_r-IfElse A54919_…, ветки A61618_…/
A92307_…, SV A83452_…/A47940_…).

Запуск: python3 tests/cases_booking_chain/patch_v21_to_v23.py <in.bpt> <out.bpt>
Выход байт-в-байт = канон booking-chain_v23.bpt.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BPT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, BPT)

from bpt_graft import GraftTree  # noqa: E402

SRC = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    HERE, '..', 'fixtures', 'booking-chain', 'booking-chain_v21.bpt')
DST = sys.argv[2] if len(sys.argv) > 2 else os.path.join(
    HERE, '..', 'fixtures', 'booking-chain', 'booking-chain_v23_rebuilt.bpt')

DEAL_ACT = 'TP_09'
NEW_FIELDS = ['UF_CRM_1774870873', 'UF_CRM_1775123936',
              'UF_CRM_EXTRA_CLEANING']
SHAPE = {'Name': None, 'Type': 'string', 'Filterable': '1',
         'Editable': '1', 'Required': '0', 'Multiple': '0',
         'BaseType': 'string'}

gt = GraftTree.load(SRC)
sv = lambda name: gt.locate(kind='SetVariableActivity', name=name,  # noqa: E731
                            expect=1)

# 1. ReturnFields + манифест сделки: три источника ПАРОЙ
deal = gt.locate(kind='CrmGetDynamicInfoActivity', name=DEAL_ACT, expect=1)
for f in NEW_FIELDS:
    gt.expose_field(deal, f, shape=dict(SHAPE, Name=f))

# 2. rd fmt: раздельные аккумуляторы + сумма
fmt = sv('TP_22')
gt.sv_set(fmt, {var: '{{=floatval({=' + DEAL_ACT + ':' + fld + '})}}'
                for var, fld in (('eg_raw', 'UF_CRM_1774870980'),
                                 ('pet_raw', 'UF_CRM_1774870873'),
                                 ('keys_raw', 'UF_CRM_1775123936'),
                                 ('clean_raw', 'UF_CRM_EXTRA_CLEANING'))})
gt.sv_set(fmt, {'extra_total_raw':
                '{{={=Variable:eg_raw} + {=Variable:pet_raw}'
                ' + {=Variable:keys_raw} + {=Variable:clean_raw}}}'})


def _sv(name, variables, title):
    return {'Type': 'SetVariableActivity', 'Name': name, 'Activated': 'Y',
            'Properties': {'VariableValue': variables, 'Title': title,
                           'EditorComment': ''}, 'Children': []}


# 3. bnum_r через IfElse-АКТИВНОСТЬ после bv IfElse «bnum fallback» —
# непусто -> значение, пусто -> '-'; никогда не пуст (лекарство FN-10)
bnum_r_ifelse = {
    'Type': 'IfElseActivity', 'Name': 'TP_10',
    'Activated': 'Y',
    'Properties': {'Title': 'bnum_r', 'EditorComment': ''},
    'Children': [
        {'Type': 'IfElseBranchActivity', 'Name': 'TP_12',
         'Activated': 'Y',
         'Properties': {'Title': 'bnum_r',
                        'fieldcondition': [['bnum_val', '!empty', '', '0']]},
         'Children': [_sv('TP_16',
                          {'bnum_r': '{=Variable:bnum_val}'}, 'bnum_r ok')]},
        {'Type': 'IfElseBranchActivity', 'Name': 'TP_20',
         'Activated': 'Y',
         'Properties': {'Title': 'bnum_r', 'truecondition': '1'},
         'Children': [_sv('TP_06',
                          {'bnum_r': '-'}, 'bnum_r dash')]},
    ]}
bv = gt.locate(kind='IfElseActivity', title='bnum fallback', expect=1)
gt.insert_child(bv, bnum_r_ifelse, position='after')

# 4. rd append: синхронные аппенды (значение колонки: 0/пусто -> '-';
# прочерк обязателен — mseparator выбрасывает нули, грабля #4)
COLS = {
    'rd_eg': "if({=Variable:eg_raw} = 0, '-', round({=Variable:eg_raw},2))",
    'rd_pet': "if({=Variable:pet_raw} = 0, '-', round({=Variable:pet_raw},2))",
    'rd_keys': "if({=Variable:keys_raw} = 0, '-', round({=Variable:keys_raw},2))",
    'rd_clean': "if({=Variable:clean_raw} = 0, '-', round({=Variable:clean_raw},2))",
    'rd_eg_bnum': '{=Variable:bnum_r}',
    'rd_eg_chin': "date('Y-m-d', {=Variable:chin_val})",
    'rd_eg_chout': "date('Y-m-d', {=Variable:chout_val})",
}
Z = '{=Variable:extra_total_raw} = 0'
rda = sv('TP_08')
gt.sv_set(rda, {nm: ['{=Variable:' + nm + '}$' + '{{=if(' + Z + ", '', "
                     + expr + ')}}'] for nm, expr in COLS.items()})
gt.sv_set(rda, {
    'eg_total': '{{={=Variable:eg_total} + {=Variable:extra_total_raw}}}',
    'eg_count': '{{={=Variable:eg_count} + '
                'if({=Variable:extra_total_raw} > 0, 1, 0)}}'})

# 5. VARIABLES (rd_eg существует с v19 — declare только новых)
M = {'Description': '', 'Type': 'string', 'Required': '0', 'Multiple': '1',
     'Options': '', 'Default': []}
V = dict(M, Multiple='0', Default='')
gt.declare('VARIABLES', {nm: dict(M) for nm in
                         ('rd_pet', 'rd_keys', 'rd_clean', 'rd_eg_bnum',
                          'rd_eg_chin', 'rd_eg_chout')})
gt.declare('VARIABLES', {nm: dict(V) for nm in
                         ('pet_raw', 'keys_raw', 'clean_raw',
                          'extra_total_raw', 'bnum_r')})

# 6. explode-SV + guards + Values генераторов
explode_svs = gt.locate_all(
    kind='SetVariableActivity',
    pred=lambda p: 'rd_bnum' in (p.get('VariableValue') or {})
    and 'explode' in str((p.get('VariableValue') or {})
                         .get('rd_bnum', '')),
    expect=2)
for ref in explode_svs:
    gt.sv_set(ref, {nm: ["{{=explode('$',{=Variable:" + nm + "})}}"]
                    for nm in COLS})
guards = sv('TP_21')
gt.sv_set(guards, {nm: "{{=if({=Variable:" + nm + "} = '', '-', "
                       "{=Variable:" + nm + "})}}" for nm in COLS})
for a in gt.locate_all(kind='CrmGenerateEntityDocumentActivity'):
    gt.props_merge(a, {'Values': {nm: '{=Variable:' + nm + '}'
                                  for nm in COLS}})

gt.save(DST)
print(f'{DST}: ok ({len(gt.intent)} intent-записей)')

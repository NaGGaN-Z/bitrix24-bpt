#!/usr/bin/env python3
"""Переход booking-chain v21 -> v22 на graft API (регресс-база, КАНОН-РЕГРЕСС).

Спецификация — внешний архив проекта/build/patch_v22.py (2026-10-15):
расширенный extra (4 источника сделки) + фильтрация нулей в Extra
Details (согласованные аппенды: нулевые строки -> пустота ->
mseparator выбрасывает -> выравнивание колонок сохраняется).

ВНИМАНИЕ: канон v22 несёт ИСТОРИЧЕСКИЙ РЕГРЕСС — rd_eg_bnum value =
голый {=Variable:bnum_val} в if-ветке при чужом условии (пустая
подстановка вырезается -> literal-мусор; референс expression-runtime
§2, FN-10). Гейт save() ОБЯЗАН такой переход блокировать — потому
финал через save_raw() (байты мимо гейтов): кейс = байт-в-байт
документация баги. Лекарство — кейс patch_v21_to_v23 (паттерн bnum_r
через IfElse-активность).

Запуск: python3 tests/cases_booking_chain/patch_v21_to_v22.py <in.bpt> <out.bpt>
Выход байт-в-байт = канон booking-chain_v22.bpt.
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
    HERE, '..', 'fixtures', 'booking-chain', 'booking-chain_v22_rebuilt.bpt')

DEAL_ACT = 'TP_09'
EG_FIELDS = ['UF_CRM_1774870980', 'UF_CRM_1774870873',
             'UF_CRM_1775123936', 'UF_CRM_EXTRA_CLEANING']

gt = GraftTree.load(SRC)
sv = lambda name: gt.locate(kind='SetVariableActivity', name=name,  # noqa: E731
                            expect=1)

# 1. ReturnFields + манифест сделки: три новых источника ПАРОЙ
# (шейп из спецификации патча — Name=имя поля; манифестного донора в
# базе нет)
deal = gt.locate(kind='CrmGetDynamicInfoActivity', name=DEAL_ACT, expect=1)
for f in EG_FIELDS[1:]:
    if f in deal.props['ReturnFields']:
        continue
    gt.expose_field(deal, f, shape={
        'Name': f, 'Type': 'string', 'Filterable': '1', 'Editable': '1',
        'Required': '0', 'Multiple': '0', 'BaseType': 'string'})

# 2. eg_raw = сумма четырёх источников
gt.sv_set(sv('TP_22'), {
    'eg_raw': '{{=' + ' + '.join(
        'floatval({=' + DEAL_ACT + ':' + f + '})' for f in EG_FIELDS)
    + '}}'})

# 3. rd append: четыре согласованных аппенда, нули -> пусто
ZERO = '{=Variable:eg_raw} = 0'
gt.sv_set(sv('TP_08'), {
    'rd_eg': ['{=Variable:rd_eg}${{=if(' + ZERO + ", '', "
              'round({=Variable:eg_raw},2))}}'],
    'rd_eg_bnum': ['{=Variable:rd_eg_bnum}${{=if(' + ZERO + ", '', "
                   '{=Variable:bnum_val})}}'],
    'rd_eg_chin': ["{=Variable:rd_eg_chin}${{=if(" + ZERO + ", '', "
                   "date('Y-m-d', {=Variable:chin_val}))}}"],
    'rd_eg_chout': ["{=Variable:rd_eg_chout}${{=if(" + ZERO + ", '', "
                    "date('Y-m-d', {=Variable:chout_val}))}}"]})

# 4. VARIABLES: три multi-переменных (Multiple=1, Default=[])
M = {'Description': '', 'Type': 'string', 'Required': '0', 'Multiple': '1',
     'Options': '', 'Default': []}
gt.declare('VARIABLES', {nm: dict(M) for nm in
                         ('rd_eg_bnum', 'rd_eg_chin', 'rd_eg_chout')})

# 5. guards + explode (признак explode-SV — rd_bnum с explode)
explode_svs = gt.locate_all(
    kind='SetVariableActivity',
    pred=lambda p: 'rd_bnum' in (p.get('VariableValue') or {})
    and 'explode' in str((p.get('VariableValue') or {})
                         .get('rd_bnum', '')),
    expect=2)
for ref in explode_svs:
    gt.sv_set(ref, {nm: ["{{=explode('$',{=Variable:" + nm + "})}}"]
                    for nm in ('rd_eg_bnum', 'rd_eg_chin', 'rd_eg_chout')})
guards = sv('TP_21')
gt.sv_set(guards, {nm: "{{=if({=Variable:" + nm + "} = '', '-', "
                       "{=Variable:" + nm + "})}}"
                   for nm in ('rd_eg_bnum', 'rd_eg_chin', 'rd_eg_chout')})

# 6. Values генераторов
for a in gt.locate_all(kind='CrmGenerateEntityDocumentActivity'):
    gt.props_merge(a, {'Values': {
        nm: '{=Variable:' + nm + '}'
        for nm in ('rd_eg_bnum', 'rd_eg_chin', 'rd_eg_chout')}})

gt.save_raw(DST)  # канон-регресс: FN-10 блокирует save — так и задумано
print(f'{DST}: ok RAW ({len(gt.intent)} intent-записей)')

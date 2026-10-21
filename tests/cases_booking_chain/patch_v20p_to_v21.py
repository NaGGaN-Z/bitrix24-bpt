#!/usr/bin/env python3
"""Переход booking-chain-v20-portal -> booking-chain v21 на graft API (регресс-база).

Спецификация — внешний архив проекта/build/patch_v21.py (2026-10-15):
конкатенации obj/license на доказанном механизме ($-аппенд + explode;
if-конкатенации не вычисляются рантаймом — отчёт 45141). Источник —
живой портальный шаблон с ручными правками владельца (константы,
«Objects», Log в ForEach) — их НЕ трогаем (кейс «портал-канон поверх
базы»).

Запуск: python3 tests/cases_booking_chain/patch_v20p_to_v21.py <in.bpt> <out.bpt>
Выход байт-в-байт = канон booking-chain_v21.bpt.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BPT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, BPT)

from bpt_graft import GraftTree  # noqa: E402

SRC = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    HERE, '..', 'fixtures', 'booking-chain', 'booking-chain-v20-portal.bpt')
DST = sys.argv[2] if len(sys.argv) > 2 else os.path.join(
    HERE, '..', 'fixtures', 'booking-chain', 'booking-chain_v21_rebuilt.bpt')

OBJ = 'TP_07'
LIC = 'TP_14'

gt = GraftTree.load(SRC)

# 1. SV «unit sums»: аппенды вместо if-конкатенаций (Title-первичный
# предикат — уникальный заголовок узла)
sums = gt.locate(kind='SetVariableActivity', title='unit sums', expect=1)
vv = sums.props.get('VariableValue') or {}
assert 'obj_titles' in vv and '{{=if(' in vv['obj_titles'], \
    'не та база: unit sums без if-конкатенаций'
gt.sv_set(sums, {
    'obj_titles': ['{=Variable:obj_titles}${=' + OBJ + ':TITLE}'],
    'license_txt': ['{=Variable:license_txt}${=' + LIC
                    + ':UF_CRM_81_EXPIRY}']})

# 2. VARIABLES -> multi (точечная правка полей записи)
for nm in ('obj_titles', 'license_txt'):
    gt.payload_set('VARIABLES', nm, {'Multiple': '1', 'Default': []})

# 3. Explode-SV (оба пост-цикловых; признак — rd_bnum с explode)
explode_svs = gt.locate_all(
    kind='SetVariableActivity',
    pred=lambda p: 'rd_bnum' in (p.get('VariableValue') or {})
    and 'explode' in str((p.get('VariableValue') or {})
                         .get('rd_bnum', '')),
    expect=2)
for ref in explode_svs:
    gt.sv_set(ref, {
        'obj_titles': ["{{=explode('$',{=Variable:obj_titles})}}"],
        'license_txt': ["{{=explode('$',{=Variable:license_txt})}}"]})

# 4. Values генераторов: obj/license — голый референс массива
# (генератор сам рендерит массив через запятую; implode не нужен)
for a in gt.locate_all(kind='CrmGenerateEntityDocumentActivity'):
    gt.props_merge(a, {'Values': {
        'obj': '{=Variable:obj_titles}',
        'license': '{=Variable:license_txt}'}})

gt.save(DST)
print(f'{DST}: ok ({len(gt.intent)} intent-записей)')
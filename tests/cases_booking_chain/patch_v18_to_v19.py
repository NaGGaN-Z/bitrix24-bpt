#!/usr/bin/env python3
"""Переход booking-chain v18 -> v19 на graft API (регресс-база волны 6).

Спецификация — внешний архив проекта/build/patch_v19.py (живой патч
2026-10-14): Extra Guest (ТЗ задача 3) — доп. доходы за экстра-гостей.
Грабли канона (важно для байт-точности): порядок вставки ключей
VARIABLES пачками (eg_*/rd_* затем *_val — dict упорядочен);
explode-формулы БЕЗ пробела после запятой; rd-аппенды — списковая
форма значений.

Запуск: python3 tests/cases_booking_chain/patch_v18_to_v19.py <in.bpt> <out.bpt>
Выход байт-в-байт = канон booking-chain_v19.bpt.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BPT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, BPT)

from bpt_graft import GraftTree  # noqa: E402

SRC = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    HERE, '..', 'fixtures', 'booking-chain', 'booking-chain_v18.bpt')
DST = sys.argv[2] if len(sys.argv) > 2 else os.path.join(
    HERE, '..', 'fixtures', 'booking-chain', 'booking-chain_v19_rebuilt.bpt')

EG_FIELD = 'UF_CRM_1774870980'
DEAL_ACT = 'TP_09'

gt = GraftTree.load(SRC)
sv = lambda name: gt.locate(kind='SetVariableActivity', name=name,  # noqa: E731
                            expect=1)

# 1. ReturnFields сделки (auto: манифест-входа нет — UF работает, v19-факт)
deal = gt.locate(kind='CrmGetDynamicInfoActivity', name=DEAL_ACT, expect=1)
gt.expose_field(deal, EG_FIELD)

# 2. ZEROING
gt.sv_set(sv('TP_13'),
          {'eg_total': '0', 'eg_count': '0', 'rd_eg': '', 'rd_unit': ''})

# 3. rd fmt: eg_raw + промежуточные для прочерков (поле в if() нельзя —
# грабля #5: пустые FIELD-рефы валят парсер)
gt.sv_set(sv('TP_22'), {
    'eg_raw': '{{=floatval({=' + DEAL_ACT + ':' + EG_FIELD + '})}}',
    'bby_val': '{=' + DEAL_ACT + ':CONTACT_ID>printable}',
    'chin_val': '{=' + DEAL_ACT + ':UF_CRM_OCT_RESERV_BEGIN}',
    'chout_val': '{=' + DEAL_ACT + ':UF_CRM_OCT_RESERV_END}'})

# 4. rd append: eg_total/eg_count/rd_eg + прочерки по пустым/нулевым
# (стиль pay_r; прочерк обязателен: mseparator=2 выбрасывает нули —
# грабля #4)
gt.sv_set(sv('TP_08'), {
    'eg_total': '{{={=Variable:eg_total} + {=Variable:eg_raw}}}',
    'eg_count': '{{={=Variable:eg_count} + '
                'if({=Variable:eg_raw} > 0, 1, 0)}}',
    'rd_eg': ['{=Variable:rd_eg}${{=if({=Variable:eg_raw} = 0, \'-\', '
              'round({=Variable:eg_raw},2))}}'],
    'rd_unit': ['{=Variable:rd_unit}${=Template:obj>printable}'],
    'rd_bnum': ["{=Variable:rd_bnum}${{=if({=Variable:bnum_val} = '', "
                "'-', {=Variable:bnum_val})}}"],
    'rd_bby': ["{=Variable:rd_bby}${{=if({=Variable:bby_val} = '', "
               "'-', {=Variable:bby_val})}}"],
    'rd_chin': ["{=Variable:rd_chin}${{=if({=Variable:chin_val} = '', "
                "'-', date('Y-m-d', {=Variable:chin_val}))}}"],
    'rd_chout': ["{=Variable:rd_chout}${{=if({=Variable:chout_val} = '', "
                 "'-', date('Y-m-d', {=Variable:chout_val}))}}"],
    'rd_night': ["{=Variable:rd_night}${{=if({=Variable:night_val} = 0, "
                 "'-', {=Variable:night_val})}}"]})

# 5. empty guards (+ EG в тоталы: guards — пост-цикл, до net_income)
gt.sv_set(sv('TP_21'), {
    'rd_eg': "{{=if({=Variable:rd_eg} = '', '-', {=Variable:rd_eg})}}",
    'rd_unit': "{{=if({=Variable:rd_unit} = '', '-', "
               "{=Variable:rd_unit})}}",
    'total4': '={=Variable:total4}+{=Variable:eg_total}'})

# 6. Explode (обе штуки)
for name in ('TP_01', 'TP_18'):
    gt.sv_set(sv(name), {
        'rd_eg': ["{{=explode('$',{=Variable:rd_eg})}}"],
        'rd_unit': ["{{=explode('$',{=Variable:rd_unit})}}"]})

# 7. VARIABLES — пачками в порядке канона (eg_*/rd_* затем *_val:
# dict-порядок = байты)
V = {'Description': '', 'Type': 'string', 'Required': '0', 'Multiple': '0',
     'Options': '', 'Default': ''}
M = dict(V, Multiple='1', Default=[])
gt.declare('VARIABLES', {'eg_total': dict(V), 'eg_count': dict(V),
                         'eg_raw': dict(V), 'rd_eg': dict(M),
                         'rd_unit': dict(M)})
gt.declare('VARIABLES', {'bby_val': dict(V), 'chin_val': dict(V),
                         'chout_val': dict(V)})

# 8. Генераторы docx (кроме OLD TplId 13): Values += eg/rd-набор
gens = gt.locate_all(kind='CrmGenerateEntityDocumentActivity', expect=5)
for a in gens:
    if str(a.props.get('TemplateId')) == '13':
        continue
    gt.props_merge(a, {'Values': {
        'eg_sum': '{{=round({=Variable:eg_total},2)}}',
        'eg_hst': '{{=round({=Variable:eg_total}*0.13,2)}}',
        'eg_count': '{=Variable:eg_count}',
        'rd_eg': '{=Variable:rd_eg}',
        'rd_unit': '{=Variable:rd_unit}'}})

gt.save(DST)
print(f'{DST}: ok ({len(gt.intent)} intent-записей)')

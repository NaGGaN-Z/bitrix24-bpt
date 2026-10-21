#!/usr/bin/env python3
"""Переход booking-chain v17 -> v18 на graft API (регресс-база волны 6).

Спецификация — внешний архив проекта/build/patch_v18.py (живой патч
2026-10-07): Other expenses с OE-процентом — HST от subtotal.
Контекст-сужение обязательно: '{=A…:UF_45}*0.13' живёт в ДВУХ ветках,
правится только узел с oe_percent (ветка без процента не трогается).

Запуск: python3 tests/cases_booking_chain/patch_v17_to_v18.py <in.bpt> <out.bpt>
Выход байт-в-байт = канон booking-chain_v18.bpt (zlib уровня исходника — 9).
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BPT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, BPT)

from bpt_graft import GraftTree  # noqa: E402

SRC = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    HERE, '..', 'fixtures', 'booking-chain', 'booking-chain_v17.bpt')
DST = sys.argv[2] if len(sys.argv) > 2 else os.path.join(
    HERE, '..', 'fixtures', 'booking-chain', 'booking-chain_v18_rebuilt.bpt')

gt = GraftTree.load(SRC)

# Патч: oe_hst += (OE$ * oe_percent) * 13/100 — только ветка с процентом;
# контекст — oe_percent в ЗНАЧЕНИИ oe_subtotal (первичный предикат,
# ключом sv_keys не ловится — значение, не ключ)
ctx = gt.locate(
    kind='SetVariableActivity',
    pred=lambda p: 'oe_percent' in str(
        (p.get('VariableValue') or {}).get('oe_subtotal', '')),
    expect=1)
gt.sv_replace(
    'oe_hst',
    '{=TP_04:UF_CRM_45_1783485570}*0.13',
    '({=TP_04:UF_CRM_45_1783485570}'
    '*{=Variable:oe_percent})*0.13',
    expect=1, refs=ctx)

gt.save(DST)
print(f'{DST}: ok ({len(gt.intent)} intent-записей)')

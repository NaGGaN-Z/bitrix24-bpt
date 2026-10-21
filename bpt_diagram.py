#!/usr/bin/env python3
"""bpt_diagram.py — проекция дерева БП (волна 7) + legacy D2/PlantUML.

ВОЛНА 7 (план внутренний план волны 7): python-слой
архитектуры «HTML-обёртка» — exact-парс .bpt → JSON-контракт
bpt_diagram_runtime.js (единственный лейаутер), paint-адаптер поверх
bpt_export.diff_templates (identity-якоря, rename-иммунный; грабля project-x),
HTML-упаковщик (свёртка + инспекция, двойной клик без сервера).
python лейаутит НИЧЕГО: только парсит, красит, упаковывает.

Слои:
- load_tree(src, base=None, rename_map=None) → {root, templateData}:
  JSON-контракт runtime; base → призраки удалённого (интерколяция в
  children по порядку A, парент-пейринг по Name; rename_map из
  paint_map закрывает rename-родительский пейринг — без карты rename
  деградирует до «призрак старого + added нового»);
- templateData — Параметры/Переменные/Константы из корня .bpt
  (PARAMETERS/VARIABLES/CONSTANTS; DOCUMENT_FIELDS — поля документа,
  НЕ попадают в блок данных: не просили, шум);
- paint_map(base, target) → {Name: added|modified|removed}: events →
  карта; kind='activated' и 'sect' НЕ красят (летучий timestamp /
  секции шаблона), kind='error' игнор с WARN-счётчиком;
- pack_html(tree, paint, out) → один .diagram.html (runtime inline,
  </script-безопасная инъекция JSON).

Legacy (прототип 2026-10-15, ЗАМОРОЖЕНО — не развиваем, живёт для
сравнения): to_d2/to_puml. Боевые факты прототипа: D2 строго
--layout=elk (dagre не умеет self-loop на контейнере); пунктир в D2
запрещён; PlantUML elseif искажает B24-IfElse (first-match vs «все
совпавшие») — пумл только без покраски. Артефакты сравнения:
внутренний архив diagram-test/.
"""
import json
import os
import re
import sys
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bpt_serialize import php_unserialize_exact  # noqa: E402

RUNTIME_JS = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          'bpt_diagram_runtime.js')

# ---------- debug-лог (stderr, BPT_DIAGRAM_DEBUG=1) ----------

def _debug(msg: str) -> None:
    if os.environ.get('BPT_DIAGRAM_DEBUG') == '1':
        print(f'[diagram] {msg}', file=sys.stderr)


# ---------- волна 7: парс в JSON-контракт runtime ----------

def load_template(src: str) -> dict:
    """Файл .bpt → дерево (zlib-блоб UI-экспорта или raw payload;
    exact-парсер: int-ключи живы). Тот же контракт, что
    bpt_export._load_bpt_tree — локальная копия, чтобы тянуть только
    bpt_serialize (модуль открывается без B24-стека)."""
    blob = open(src, 'rb').read()
    try:
        payload = zlib.decompress(blob)
    except zlib.error:
        payload = blob
    return php_unserialize_exact(payload)


def is_act(x) -> bool:
    return isinstance(x, dict) and 'Type' in x and 'Properties' in x \
        and 'Children' in x


def subtree_size(node: dict) -> int:
    n = 0
    stack = [node]
    while stack:
        cur = stack.pop()
        if is_act(cur):
            n += 1
            stack += cur.get('Children') or []
    return n


def cond_subtitle(props: dict):
    """Короткий текст условия ветки для субтитра под чипом (рендер решает
    сам, показывать ли). Канонические cond-ключи bpt_export.COND_PROPS;
    истинный truecondition = ветка «всегда»."""
    tc = props.get('truecondition')
    if tc and str(tc) not in ('0', ''):
        return 'всегда'
    fc = props.get('fieldcondition')
    if isinstance(fc, list) and fc:
        rows = [str(r[0]) for r in fc[:2]
                if isinstance(r, (list, tuple)) and r]
        return 'поля: ' + ', '.join(rows) + ('…' if len(fc) > 2 else '')
    pc = props.get('propertyvariablecondition')
    if isinstance(pc, list) and pc:
        return f'по переменным ({len(pc)})'
    mc = props.get('mixedcondition')
    if isinstance(mc, list) and mc:
        return f'смешанное ({len(mc)})'
    return None


def _norm_data_items(sect) -> list:
    """Секция .bpt (dict name→item | list items) → нормализованный список
    {name, title, type, required, default?}. Required в живых данных —
    строка '1'; пустые/безымянные элементы пропускаются."""
    items = list(sect.values()) if isinstance(sect, dict) else list(sect or [])
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        name = str(it.get('Name') or '')
        if not name:
            continue
        req = str(it.get('Required') or '')
        entry = {
            'name': name,
            'title': str(it.get('Title') or name),
            'type': str(it.get('Type') or ''),
            'required': req not in ('', '0', 'N', 'false', 'False'),
        }
        if it.get('Default') is not None and str(it.get('Default')) != '':
            entry['default'] = str(it.get('Default'))
        opts = it.get('Options')
        if isinstance(opts, dict) and opts:
            # UF:crm: привязка к сущностям — включённые ('Y') ключи Options
            crm = sorted(k for k, v in opts.items()
                         if str(v).upper() == 'Y')
            if crm:
                entry['crm'] = crm
        out.append(entry)
    return out


def template_data(tpl: dict) -> dict:
    """Параметры/Переменные/Константы корня .bpt → блок данных (три ключа,
    возможно пустые; пустые секции runtime не рисует). DOCUMENT_FIELDS
    сознательно не включён — поля документа, не блок данных шаблона."""
    return {
        'parameters': _norm_data_items(tpl.get('PARAMETERS')),
        'variables': _norm_data_items(tpl.get('VARIABLES')),
        'constants': _norm_data_items(tpl.get('CONSTANTS')),
    }


def _ghost_marker(a_child: dict) -> dict:
    props = a_child.get('Properties') or {}
    return {
        'ghost': True,
        'name': str(a_child.get('Name') or props.get('Title') or ''),
        'title': str(props.get('Title') or a_child.get('Type') or ''),
        'count': subtree_size(a_child),
        'children': [],
    }


def walk_json(b_node: dict, a_node, rename_map=None) -> dict:
    """B-узел (+ спаренный по Name A-узел или None) → json-узел runtime.
    Дети B интеркалируются призраками удалённых детей A «по порядку A»:
    B-порядок сохраняется внутри, призрак встаёт на свою A-позицию между
    выжившими якорями; хвост B без якорей (added) идёт в конец.
    rename_map {old: new} — карта переименований из diff_templates:
    ребёнок A, чьё имя переименовано в B, считается ВЫЖИВШИМ (призрак не
    ставится, пара для рекурсии — через новое имя). Без карты —
    деградация v1: rename виден как призрак старого + added нового."""
    rename_map = rename_map or {}
    props = b_node.get('Properties') or {}
    b_type = b_node.get('Type', '')
    title = str(props.get('Title') or b_type)
    if b_type == 'IfElseBranchActivity':
        # дизайнер подписывает безусловную ветку «Истина»: нет cond-ключей
        # или truecondition='1'; дефолтный Title 'Condition'/пустой — шум
        # портала (кейс TP_15 в booking-chain)
        cond_keys = ('truecondition', 'fieldcondition',
                     'propertyvariablecondition', 'mixedcondition')
        has_cond = any(k in props for k in cond_keys)
        unconditional = not has_cond \
            or str(props.get('truecondition') or '') == '1'
        if title in ('', 'Condition') and unconditional:
            title = 'Истина'
    out = {
        'name': str(b_node.get('Name') or title or b_type),
        'type': b_type,
        'title': title,
        'props': props,
    }
    if b_type == 'IfElseBranchActivity':
        sub = cond_subtitle(props)
        if sub:
            out['subtitle'] = sub
    kids = [c for c in (b_node.get('Children') or []) if is_act(c)]
    base_kids = []
    if a_node is not None:
        base_kids = [c for c in (a_node.get('Children') or []) if is_act(c)]
    b_names = {c.get('Name') for c in kids}
    a_by_b_name = {}
    for c in base_kids:
        an = c.get('Name')
        a_by_b_name.setdefault(rename_map.get(an, an), c)
        a_by_b_name.setdefault(an, c)
    merged = []
    bi = 0
    for a_child in base_kids:
        an = a_child.get('Name')
        target = rename_map.get(an, an)
        if target in b_names:
            while bi < len(kids) and kids[bi].get('Name') != target:
                cur = kids[bi]
                merged.append(walk_json(cur,
                                        a_by_b_name.get(cur.get('Name')),
                                        rename_map))
                bi += 1
            if bi < len(kids):
                merged.append(walk_json(kids[bi], a_child, rename_map))
                bi += 1
        else:
            merged.append(_ghost_marker(a_child))
    while bi < len(kids):
        cur = kids[bi]
        merged.append(walk_json(cur, a_by_b_name.get(cur.get('Name')),
                                rename_map))
        bi += 1
    out['children'] = merged
    return out


def load_tree(src: str, base: str = None, rename_map: dict = None) -> dict:
    """Файл .bpt → JSON-контракт runtime {root, templateData}.
    base — файл BASE-версии: включает призраков удалённого.
    rename_map {old: new} — карта переименований (stats['rename_map'] из
    paint_map): patch-пейринг по переименованным именам, иначе rename
    выглядит как призрак старого + added нового (деградация v1)."""
    tpl = load_template(src)
    roots = tpl.get('TEMPLATE') or []
    root = roots[0] if roots and is_act(roots[0]) else None
    if root is None:
        raise ValueError(f'TEMPLATE-корень бит: {src}')
    base_root = None
    if base:
        base_tpl = load_template(base)
        broots = base_tpl.get('TEMPLATE') or []
        base_root = broots[0] if broots and is_act(broots[0]) else None
        if base_root is None:
            _debug(f'WARN: BASE-корень бит, призраки выключены: {base}')
    tree = {
        'root': walk_json(root, base_root, rename_map),
        'templateData': template_data(tpl),
    }
    n_nodes = subtree_size(root)
    n_ghost = _count_ghosts(tree['root'])
    td = tree['templateData']
    _debug(f'load_tree: узлов={n_nodes} призраков={n_ghost} '
           f'данные: п={len(td["parameters"])} в={len(td["variables"])} '
           f'к={len(td["constants"])}')
    return tree


def _count_ghosts(node: dict) -> int:
    n = 0
    stack = [node]
    while stack:
        cur = stack.pop()
        if not isinstance(cur, dict):
            continue
        if cur.get('ghost'):
            n += 1
        stack += cur.get('children') or []
    return n


# ---------- волна 7: paint-адаптер поверх diff_templates ----------

def walk_names(node: dict) -> list:
    """Все Name activity-поддерева (для added/removed — красим целиком)."""
    acc = []
    stack = [node]
    while stack:
        cur = stack.pop()
        if is_act(cur):
            if cur.get('Name'):
                acc.append(str(cur['Name']))
            stack += cur.get('Children') or []
    return acc


def paint_map(base_path: str, target_path: str) -> tuple:
    """(base.bpt, target.bpt) → ({Name: класс}, stats).
    Источник покраски — diff_templates (bpt_export): identity-якоря,
    rename-иммунный. added/removed красят поддерево целиком (event несёт
    node); sv/cond/prop → modified по event['name'] (аддитивное поле,
    _content_pass); kind='activated' (летучий timestamp реимпорта) и
    'sect' (секции шаблона — блок данных не красим) НЕ красят; renames —
    ожидаемые находки, не дифф."""
    from collections import Counter

    from bpt_export import diff_templates, identity_key, render_diff  # лениво
    a_tree = load_template(base_path)
    if not a_tree.get('TEMPLATE'):
        # аудит: BASE без/с пустым TEMPLATE — тихая деградация (exit 0,
        # «+0 -0 ~0») либо IndexError на TEMPLATE:[]; громкий отказ
        raise ValueError(f'BASE без TEMPLATE (пуст или отсутствует): '
                         f'{base_path} — дифф не строится')
    b_tree = load_template(target_path)
    events, renames = diff_templates(a_tree, b_tree)
    # moved: added-узлы, чья identity уже была в BASE. align_children
    # матчает детей ВНУТРИ родителя — переезд узла в другой контейнер
    # выглядит как «− тут / + там»; решение владельца — счётчик moved
    def moved_key(node):
        # identity + Title: у SetVariableActivity identity — только ключи
        # VariableValue (без Title), два разных SV слиплись бы в ложный
        # moved (грабли синтетики paint_added_subtree_whole)
        return (identity_key(node),
                str((node.get('Properties') or {}).get('Title') or ''))

    base_counts = Counter()
    bstack = [a_tree.get('TEMPLATE', [None])[0]]
    while bstack:
        cur = bstack.pop()
        if is_act(cur):
            base_counts[moved_key(cur)] += 1
            bstack += cur.get('Children') or []
    # классификация moved одним проходом с расходованием multiset:
    # moved-узлы НЕ красятся added (решение владельца «и не красить»)
    work = Counter(base_counts)
    moved_names = set()
    moved_total = 0
    for e in events:
        if e.get('kind') != 'added' or not e.get('node'):
            continue
        kstack = [e['node']]
        while kstack:
            cur = kstack.pop()
            if not is_act(cur):
                continue
            k = moved_key(cur)
            if work[k] > 0:
                work[k] -= 1
                if cur.get('Name'):
                    moved_names.add(str(cur['Name']))
                moved_total += 1
            kstack += cur.get('Children') or []
    paint = {}
    n_ign = 0
    n_err = 0
    for e in events:
        kind = e.get('kind')
        if kind in ('added', 'removed'):
            for nm in walk_names(e.get('node') or {}):
                if kind == 'added' and nm in moved_names:
                    continue  # moved — не красим
                paint[nm] = kind
        elif kind in ('sv', 'cond', 'prop'):
            nm = e.get('name')
            if nm:
                paint[str(nm)] = 'modified'
        elif kind == 'error':
            n_err += 1
        else:  # activated, sect
            n_ign += 1
    stats = {'events': len(events), 'ignored': n_ign, 'errors': n_err,
             'renames': len(renames),
             'rename_map': {r['old']: r['new'] for r in renames
                            if r.get('old') and r.get('new')
                            and r.get('kind') != 'variable'},
             'classes': {k: sum(1 for v in paint.values() if v == k)
                         for k in ('added', 'modified', 'removed')},
             'report': render_diff(events, renames)}
    # per-block дифф: клик по блоку — его персональные изменения
    # (правка владельца: полный список разом — каша)
    by_name = {}
    for e in events:
        lines = [ln for ln in render_diff([e], [])
                 if not ln.startswith('[diff] summary:')]
        if e.get('kind') == 'added' and e.get('node'):
            # moved-доля поддерева события (из готового набора moved_names)
            total = moved = 0
            kstack = [e['node']]
            while kstack:
                cur = kstack.pop()
                if not is_act(cur):
                    continue
                total += 1
                if cur.get('Name') and str(cur['Name']) in moved_names:
                    moved += 1
                kstack += cur.get('Children') or []
            if moved == total and total:
                lines = [ln.replace('+ B only:', '~ moved:', 1)
                         if ln.startswith('[diff] +') else ln
                         for ln in lines]
            elif moved:
                lines = [ln + f' (moved: {moved}/{total})'
                         if ln.startswith('[diff] +') else ln
                         for ln in lines]
        if e.get('kind') == 'sect':
            # секции привязываем к записям блока данных (клик по
            # переменной — её дифф), в глобальном остаётся только счётчик
            lines = [ln for ln in lines
                     if not ln.startswith('[diff] ===')]
            base_key = str(e.get('key', '')).split('.')[0]
            nm = ('data:' + str(e.get('section', '')).lower() + ':'
                  + base_key)
            by_name.setdefault(nm, []).extend(lines)
            continue
        # added/removed — строка только на КОРНЕ поддерева: потомкам она
        # чужая (репорт владельца: IfElse внутри добавленного Iterator
        # показывал дифф Iterator'а); у потомков — свой класс краски
        if e.get('kind') in ('added', 'removed'):
            nm = (e.get('node') or {}).get('Name')
            names = [str(nm)] if nm else []
        elif e.get('name'):
            names = [str(e['name'])]
        else:
            names = []
        for nm in names:
            by_name.setdefault(nm, []).extend(lines)
    for r in renames:
        if r.get('kind') == 'variable':
            continue  # переименование переменной — в global-отчёт, не к блоку
        lines = [ln for ln in render_diff([], [r])
                 if not ln.startswith('[diff] summary:')]
        for nm in (r.get('old'), r.get('new')):
            if nm:
                by_name.setdefault(str(nm), []).extend(lines)
    summary = next((ln[16:] for ln in reversed(stats['report'])
                    if ln.startswith('[diff] summary:')), '')
    if summary and moved_total:
        summary += f' moved={moved_total}'
    stats['moved'] = moved_total
    var_lines = [ln for ln in stats['report']
                 if ln.startswith('[diff] = Variable ')]
    stats['diff_view'] = {'by_name': by_name,
                          'global': ([summary] if summary else []) +
                                    var_lines}
    _debug(f'paint_map: events={stats["events"]} '
           f'классы={stats["classes"]} проигнорировано={n_ign} '
           f'errors={n_err} renames={stats["renames"]}')
    return paint, stats


# ---------- волна 7: HTML-упаковщик ----------

_HTML_SHELL = """<!doctype html>
<html><head><meta charset="utf-8">
<title>BP diagram</title>
<style>
 body{margin:0;display:flex;font-family:'Segoe UI',Arial,sans-serif;height:100vh;
      background:#1e242c}
 #canvas{flex:1;overflow:auto;padding:12px}
 #stage{transform-origin:0 0;width:max-content}
 #stage svg{display:block}
 #panel{width:340px;border-left:1px solid #2a323c;padding:12px;overflow:auto;
        background:#1a1f26;box-sizing:border-box}
 #panel h3{margin:4px 0 8px;word-break:break-all;color:#dbe4ec}
 pre{white-space:pre-wrap;background:#222932;padding:8px;border-radius:6px;
     max-height:60vh;overflow:auto;font-size:11px;color:#c5cfd8}
 .row{margin:2px 0;font-size:12px;color:#aab6c2;white-space:pre-wrap;
      word-break:break-word}
 .row b{color:#dbe4ec}
 .val{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:11px;
      white-space:pre-wrap;word-break:break-word}
 .hint{color:#77828e;font-size:11px;margin-bottom:10px}
 g[data-name]{cursor:pointer}
 g[data-fold-icon]{cursor:pointer}
 #toolbar{display:flex;gap:6px;margin-bottom:10px;flex-wrap:wrap}
 #toolbar button{background:#262f38;color:#c5cfd8;border:1px solid #3d4854;
      border-radius:6px;padding:3px 10px;font-size:12px;cursor:pointer}
 #toolbar button:hover{background:#35414f}
 #zoomlab{color:#77828e;font-size:11px;align-self:center;min-width:38px;
      text-align:center}
 body.day{background:#eef1f4}
 body.day #panel{background:#fafbfc;border-color:#dfe4e9}
 body.day #panel h3{color:#2c3a4a}
 body.day .row{color:#555}
 body.day .row b{color:#2c3a4a}
 body.day pre{background:#f0f2f4;color:#333}
 body.day #toolbar button{background:#fff;color:#42505c;border-color:#c8d0d8}
 body.day #toolbar button:hover{background:#eef2f7}
 body.day #zoomlab{color:#8a95a1}
</style></head>
<body>
<div id="canvas"><div id="stage"></div></div>
<div id="panel">
 <div id="toolbar">
  <button id="btn-theme" title="День/ночь (ночью подсветка мягче)">☀ День</button>
  <button id="btn-zout" title="Уменьшить">−</button>
  <span id="zoomlab">100%</span>
  <button id="btn-zin" title="Увеличить">+</button>
  <button id="btn-zreset" title="Сбросить зум">100%</button>
 </div>
 <div class="hint">Колесо мыши над канвой — зум (панель не масштабируется),
   потянуть мышью — панорама. Клик по иконке ▾/▸ — свёртка, клик по
   плашке — инспекция, клик по чипу ветки — условия.</div>
 <div id="difflist"></div>
 <div id="difflist"></div>
 <div id="info"></div>
</div>
<script>
__RUNTIME__
</script>
<script>
const DATA = __PAYLOAD__;
let fold = (DATA.fold || []).slice();
let theme = 'night';   // ночная — дефолт (слово владельца 2026-10-16)
let zoom = 1;
function render(){
  const res = layout(DATA.tree, DATA.paint, fold,
    { palette: theme === 'night' ? 'night' : 'default' });
  document.getElementById('stage').innerHTML = res.svg;
  document.getElementById('zoomlab').textContent = Math.round(zoom * 100) + '%';
}
function applyTheme(){
  document.body.className = theme === 'day' ? 'day' : '';
  document.getElementById('btn-theme').textContent =
    theme === 'day' ? '🌙 Ночь' : '☀ День';
  render();
}
function setZoom(z, anchor){
  const c = document.getElementById('canvas');
  const old = zoom;
  zoom = Math.min(4, Math.max(0.2, z));
  if (anchor){
    // зум вокруг точки курсора: скролл компенсирует изменение масштаба
    const r = c.getBoundingClientRect();
    const ax = anchor.x - r.left + c.scrollLeft;
    const ay = anchor.y - r.top + c.scrollTop;
    c.scrollLeft = ax * (zoom / old) - (anchor.x - r.left);
    c.scrollTop = ay * (zoom / old) - (anchor.y - r.top);
  }
  const st = document.getElementById('stage');
  st.style.transform = 'scale(' + zoom + ')';
  document.getElementById('zoomlab').textContent = Math.round(zoom * 100) + '%';
}
function findNode(node, name){
  if (node && node.name === name) return node;
  const ch = (node && node.children) || [];
  for (const c of ch){ const r = findNode(c, name); if (r) return r; }
  return null;
}
function row(parent, label, value){
  const d = document.createElement('div'); d.className = 'row';
  const b = document.createElement('b'); b.textContent = label + ': ';
  d.appendChild(b); d.appendChild(document.createTextNode(String(value)));
  parent.appendChild(d);
}
const COND_KEYS = ['truecondition', 'fieldcondition',
                   'propertyvariablecondition', 'mixedcondition'];
function fmtCondValue(v){
  if (v === null || v === undefined) return '';
  if (typeof v === 'object') return JSON.stringify(v);
  return String(v);
}
function condRows(props){
  // читаемые строки условий IfElse-ветки/While (canon bpt_export.COND_PROPS)
  const rows = [];
  if (String(props.truecondition || '') === '1'){
    rows.push('всегда (truecondition = 1)');
  }
  // канон строки условия: [имя, оператор, значение, joiner]:
  // joiner '0' = И, '1' = ИЛИ — как строка соединяется с ПРЕДЫДУЩЕЙ
  // (проверено по корпусу: fieldcondition «лид !empty '1' ИЛИ сделка
  // !empty»; слово владельца — джойнер должен быть виден)
  const pushCond = (list, label) => {
    for (let i = 0; i < list.length; i++){
      const r = list[i];
      if (!Array.isArray(r)) continue;
      if (r.length < 3){
        rows.push(label + ': ' + r.map(fmtCondValue).join(' '));
        continue;
      }
      let line = label + ': ' + r[0] + ' ' + r[1] + ' ' + r[2];
      if (i > 0) line = (String(r[3]) === '1' ? 'ИЛИ ' : 'И ') + line;
      rows.push(line);
    }
  };
  const fc = props.fieldcondition;
  if (Array.isArray(fc)) pushCond(fc, 'поле');
  const pc = props.propertyvariablecondition;
  if (Array.isArray(pc)) pushCond(pc, 'переменная');
  const mc = props.mixedcondition;
  if (Array.isArray(mc)){
    for (const r of mc){
      if (r && typeof r === 'object'){
        rows.push('смешанное: ' + String(r.object || '') + '.'
          + String(r.field || '') + ' ' + String(r.operator || '')
          + ' ' + fmtCondValue(r.value));
      }
    }
  }
  return rows;
}
function fmtVal(v, indent){
  // Переносы внутри значений показываем как есть (pre-wrap); строка,
  // содержащая JSON — разворачивается в дерево (кейс rest_method_params).
  // ВАЖНО: этот JS живёт в питоновском шаблоне, поэтому обратный слэш-n
  // в строковых литералах ниже удвоен — сырой байт переноса строки рвёт
  // JS-строку и весь bootstrap (канва пустая; кейс сломанного
  // bootstrap-мокапа).
  // По той же причине в комментариях выше-ниже запрещены примеры
  // escape-последовательностей.
  const pad = '  '.repeat(indent);
  const isScalar = (x) => x === null || typeof x !== 'object';
  const isCondDict = (x) => x && typeof x === 'object'
    && !Array.isArray(x) && ('object' in x) && ('field' in x)
    && ('operator' in x);
  const condDictLine = (x) => String(x.object) + '.' + String(x.field)
    + ' ' + String(x.operator) + ' ' + (
      isScalar(x.value) ? String(x.value) : JSON.stringify(x.value));
  if (isCondDict(v)){
    // DynamicFilterFields: канон условия одним бортом
    return condDictLine(v);
  }
  if (typeof v === 'string'){
    const t = v.trim();
    if ((t.startsWith('{') && t.endsWith('}'))
        || (t.startsWith('[') && t.endsWith(']'))){
      try { return fmtVal(JSON.parse(t), indent); } catch (e) { /* не JSON */ }
    }
    return v;
  }
  if (Array.isArray(v)){
    if (!v.length) return '[]';
    // пара [условие, joiner] из DynamicFilterFields — одной строкой
    if (v.length === 2 && isCondDict(v[0]) && typeof v[1] === 'string'){
      return condDictLine(v[0]) + ' (' + v[1] + ')';
    }
    if (v.every(isScalar)){
      return v.map((x) => (isCondDict(x) ? condDictLine(x)
        : (x === null ? 'null' : String(x)))).join(', ');
    }
    const items = v.map((x) => {
      const body = fmtVal(x, indent + 1).replace(/^[\\n]+/, '');
      return pad + '- ' + body.split('\\n').join('\\n' + pad + '  ');
    });
    return '\\n' + items.join('\\n');
  }
  if (v && typeof v === 'object'){
    const keys = Object.keys(v);
    if (!keys.length) return '{}';
    return '\\n' + keys.map((k) => {
      const body = fmtVal(v[k], indent + 1);
      return pad + k + ':' + (body.startsWith('\\n') ? '' : ' ') + body;
    }).join('\\n');
  }
  return String(v);
}
function showProps(name){
  const el = document.getElementById('info');
  el.textContent = '';
  const h = document.createElement('h3'); h.textContent = name;
  el.appendChild(h);
  const n = findNode(DATA.tree.root, name);
  if (!n){ const d = document.createElement('div'); d.className='row';
    d.textContent = 'узел не найден в дереве (призрак BASE?)';
    el.appendChild(d); return; }
  row(el, 'Type', n.type);
  if (n.title) row(el, 'Title', n.title);
  if (n.subtitle) row(el, 'Сводка условия', n.subtitle);
  const cls = DATA.paint[name]; if (cls) row(el, 'Класс диффа', cls);
  const props = n.props || {};
  const cond = condRows(props);
  if (cond.length){
    const hc = document.createElement('div');
    hc.className = 'row';
    hc.style.marginTop = '8px';
    hc.style.fontWeight = 'bold';
    hc.style.color = '#dbe4ec';
    hc.textContent = 'Условие:';
    el.appendChild(hc);
    for (const line of cond){
      const d = document.createElement('div'); d.className = 'row';
      d.textContent = '· ' + line;
      el.appendChild(d);
    }
  }
  const dl = (DATA.diff && DATA.diff.by_name) ? DATA.diff.by_name[name]
    : null;
  if (dl && dl.length){
    const hd = document.createElement('div');
    hd.className = 'row';
    hd.style.marginTop = '8px';
    hd.style.fontWeight = 'bold';
    hd.style.color = '#dbe4ec';
    hd.textContent = 'Изменения:';
    el.appendChild(hd);
    for (const ln of dl){
      const d = document.createElement('div');
      d.className = 'row';
      d.style.fontFamily = 'ui-monospace,Menlo,Consolas,monospace';
      d.style.fontSize = '10.5px';
      d.textContent = ln.replace('[diff] ', '');
      el.appendChild(d);
    }
  }
  const pre = document.createElement('div');
  pre.style.marginTop = '8px';
  // заголовок свойства жирным, значение — моно с переносами (полотно
  // цельного pre не читается — правка владельца 2026-10-16)
  for (const k of Object.keys(props)){
    const d = document.createElement('div'); d.className = 'row';
    const b = document.createElement('b'); b.textContent = k + ':';
    const s = document.createElement('span'); s.className = 'val';
    s.textContent = fmtVal(props[k], 1);
    d.appendChild(b); d.appendChild(document.createTextNode(' '));
    d.appendChild(s);
    pre.appendChild(d);
  }
  el.appendChild(pre);
}
function showData(section, name){
  const el = document.getElementById('info');
  el.textContent = '';
  const h = document.createElement('h3');
  h.textContent = section + ': ' + name;
  el.appendChild(h);
  const list = (DATA.tree.templateData || {})[section] || [];
  const it = list.find(x => x.name === name);
  if (!it){ const d = document.createElement('div'); d.className='row';
    d.textContent = 'запись не найдена'; el.appendChild(d); return; }
  row(el, 'Name', it.name);
  if (it.title) row(el, 'Title', it.title);
  row(el, 'Type', it.type);
  row(el, 'Обязателен', it.required ? 'да' : 'нет');
  if (it.crm) row(el, 'CRM-сущности', it.crm.join(', '));
  if (it.default !== undefined) row(el, 'Default', it.default);
  const dl = (DATA.diff && DATA.diff.by_name)
    ? DATA.diff.by_name['data:' + section + ':' + name] : null;
  if (dl && dl.length){
    const hd = document.createElement('div');
    hd.className = 'row';
    hd.style.marginTop = '8px';
    hd.style.fontWeight = 'bold';
    hd.style.color = '#dbe4ec';
    hd.textContent = 'Изменения:';
    el.appendChild(hd);
    for (const ln of dl){
      const d = document.createElement('div');
      d.className = 'row';
      d.style.fontFamily = 'ui-monospace,Menlo,Consolas,monospace';
      d.style.fontSize = '10.5px';
      d.textContent = ln.replace('[diff] ', '');
      el.appendChild(d);
    }
  }
}
document.getElementById('btn-theme').addEventListener('click', function(){
  theme = theme === 'night' ? 'day' : 'night';
  applyTheme();
});
document.getElementById('btn-zin').addEventListener('click',
  () => setZoom(zoom * 1.2));
document.getElementById('btn-zout').addEventListener('click',
  () => setZoom(zoom / 1.2));
document.getElementById('btn-zreset').addEventListener('click',
  () => setZoom(1));
document.getElementById('canvas').addEventListener('click', function(ev){
  // иконка свёртки — fold/разворот (правка владельца: клик по ИКОНКЕ
  // сворачивает, клик по ТЕЛУ блока — инспекция)
  const ico = ev.target.closest ? ev.target.closest('g[data-fold-icon]')
    : null;
  if (ico){
    const nm = ico.getAttribute('data-fold-icon');
    const i = fold.indexOf(nm);
    if (i >= 0) fold.splice(i, 1); else fold.push(nm);
    render(); return;
  }
  const g = ev.target.closest ? ev.target.closest('g[data-name]') : null;
  if (!g) return;
  const name = g.getAttribute('data-name');
  const kind = g.getAttribute('data-kind');
  if (kind === 'template-data'){
    showData(g.getAttribute('data-section'), g.getAttribute('data-name'));
  } else if (name.indexOf('data:') === 0){
    const parts = name.split(':');
    showData(parts[1], parts.slice(2).join(':'));
  } else {
    showProps(name);
  }
});
document.getElementById('canvas').addEventListener('wheel', function(ev){
  ev.preventDefault();
  setZoom(zoom * (ev.deltaY < 0 ? 1.1 : 0.9),
          { x: ev.clientX, y: ev.clientY });
}, { passive: false });
// панорама перетаскиванием фона канвы
(function(){
  const c = document.getElementById('canvas');
  let drag = null;
  c.addEventListener('mousedown', function(ev){
    if (ev.target.closest('g[data-name]')) return;  // не мешаем кликам по узлам
    drag = { x: ev.clientX, y: ev.clientY,
             sl: c.scrollLeft, st: c.scrollTop };
    ev.preventDefault();
  });
  window.addEventListener('mousemove', function(ev){
    if (!drag) return;
    c.scrollLeft = drag.sl - (ev.clientX - drag.x);
    c.scrollTop = drag.st - (ev.clientY - drag.y);
  });
  window.addEventListener('mouseup', () => { drag = null; });
})();
(function showDiff(){
  const box = document.getElementById('difflist');
  if (!box) return;
  const lines = DATA.diff || [];
  if (!lines.length) return;
  const h = document.createElement('div');
  h.className = 'row';
  h.style.fontWeight = 'bold';
  h.style.marginBottom = '6px';
  h.textContent = 'Изменения (' + lines.length + ')';
  box.appendChild(h);
  for (const ln of lines){
    const d = document.createElement('div');
    d.className = 'row';
    d.style.fontFamily = 'ui-monospace,Menlo,Consolas,monospace';
    d.style.fontSize = '10.5px';
    d.textContent = ln.replace('[diff] ', '');
    box.appendChild(d);
  }
})();
(function showDiff(){
  const box = document.getElementById('difflist');
  if (!box) return;
  const diff = DATA.diff || {};
  const glob = diff.global || [];
  const nBlocks = Object.keys(diff.by_name || {}).length;
  if (!nBlocks && !glob.length) return;
  const h = document.createElement('div');
  h.className = 'row';
  h.style.fontWeight = 'bold';
  h.style.marginBottom = '6px';
  h.textContent = 'Дифф (' + nBlocks + ' бл.)';
  box.appendChild(h);
  for (const ln of glob){
    const d = document.createElement('div');
    d.className = 'row';
    d.style.fontFamily = 'ui-monospace,Menlo,Consolas,monospace';
    d.style.fontSize = '11px';
    d.textContent = ln;
    box.appendChild(d);
  }
  const tip = document.createElement('div');
  tip.className = 'hint';
  tip.textContent = 'Изменения конкретного блока — клик по плашке ' +
    '(или по переменной в блоке данных).';
  box.appendChild(tip);
})();
render();
</script>
</body></html>
"""


def pack_html(tree: dict, paint: dict, out_path: str,
              fold: list = None, diff_report: list = None) -> str:
    """Один .diagram.html: runtime inline + JSON + бутстрап (свёртка +
    инспекция + блок данных). JSON-инъекция экранирует ВСЕ `<` → \\u003c:
    HTML5-токенизатор в script-data-escaped — `<!--` в данных делает
    настоящий </script> незакрывающим, `<script` открывает вложенный
    контекст; эскейп только </ был недостаточен (аудит 2026-10-16 №9).
    \\u-эскейпы разворачивает JSON.parse рантайма — строки roundtrip."""
    with open(RUNTIME_JS, encoding='utf-8') as f:
        runtime = f.read()
    if runtime.startswith('#!'):
        # шебанг легален только на позиции 0 (hashbang-грамматика ES2022);
        # после \n шаблона html он становится invalid token в браузере
        runtime = runtime.split('\n', 1)[1]
    if '</script' in runtime.lower():
        raise ValueError('runtime содержит </script — инъекция небезопасна')
    payload = json.dumps({'tree': tree, 'paint': paint or {},
                          'fold': fold or [],
                          'diff': diff_report or {'by_name': {},
                                                  'global': []}},
                         ensure_ascii=False, default=str)
    payload = payload.replace('<', '\\u003c')
    html = _HTML_SHELL.replace('__RUNTIME__', runtime) \
                      .replace('__PAYLOAD__', payload)
    if out_path == '-':
        # stdout-контракт '--out -' (аудит 2026-10-16 F6): html в stdout,
        # служебный лог — в stderr, иначе он рвёт поток
        sys.stdout.write(html)
        print(f'[diagram] html: {len(html.encode("utf-8"))} байт, '
              f'runtime inline OK -> stdout', file=sys.stderr)
        return '-'
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(html)
    n = os.path.getsize(out_path)
    print(f'[diagram] html: {n} байт, runtime inline OK -> {out_path}')
    return out_path


# ---------- волна 7: mermaid (третичный, статика для GH-инлайна) ----------

def _mmd_label(s, limit: int = 48) -> str:
    """Лейбл mermaid: whitespace схлопнуть, clip, кавычка → #quot;."""
    s = ' '.join(str(s or '').split())
    if len(s) > limit:
        s = s[:limit - 1] + '…'
    return s.replace('"', '#quot;')


def to_mermaid(tree: dict) -> str:
    """JSON-контракт load_tree → mermaid flowchart TD. Без покраски и
    призраков (призраки пропускаются); контейнеры While/ForEach/Parallel →
    subgraph, IfElse → ромб с лейблами веток (пустая ветка — стаб
    «(пусто)»), templateData непуст → subgraph «Данные шаблона».
    Синтаксис консервативный (кавычки всегда, без HTML-меток) — валиден
    в GH-инлайне без рендер-твиков. ЕслиElse-merge в mermaid явной точки
    не имеет: выходы всех веток ребрятся в следующий узел цепочки."""
    lines = ['flowchart TD']
    counter = [0]

    def nid(prefix='N'):
        counter[0] += 1
        return f'{prefix}{counter[0]}'

    def chain(children):
        """Последовательность; → (entry_ids, exit_ids)."""
        entry = None
        exits = []
        for ch in children:
            ins, outs = render(ch)
            if entry is None:
                entry = ins
            elif exits and ins:
                for e in exits:
                    for i in ins:
                        lines.append(f'{e} --> {i}')
            exits = outs
        return entry, (exits or [])

    def render(node):
        if node.get('ghost'):
            return [], []
        t = node['type']
        label = _mmd_label(node.get('title') or node['name'])
        kids = [c for c in node.get('children') or [] if not c.get('ghost')]
        if t == 'SequentialWorkflowActivity':
            return chain(kids)
        if t == 'IfElseActivity':
            d = nid('D')
            lines.append(f'{d}{{"{label}"}}')
            outs = []
            for br in kids:
                bkids = [c for c in br.get('children') or []
                         if not c.get('ghost')]
                blabel = _mmd_label(br.get('title') or br['name'], 30)
                if bkids:
                    bins, bouts = chain(bkids)
                    if bins:
                        lines.append(f'{d} -->|"{blabel}"| {bins[0]}')
                        outs += bouts
                else:
                    stub = nid('S')
                    lines.append(f'{stub}(("(пусто)"))')
                    lines.append(f'{d} -->|"{blabel}"| {stub}')
                    outs.append(stub)
            return [d], outs
        if t in ('WhileActivity', 'ForEachActivity', 'ParallelActivity'):
            s = nid('L')
            h = nid('H')
            lines.append(f'subgraph {s}["{label}"]')
            lines.append(f'{h}["{label}"]')
            if kids:
                bins, bouts = chain(kids)
                if bins:
                    lines.append(f'{h} --> {bins[0]}')
            else:
                bouts = [h]
            lines.append('end')
            if t != 'ParallelActivity':
                lines.append(f'{h} -.->|"повтор"| {s}')
            return [s], bouts
        n = nid('N')
        lines.append(f'{n}["{label}"]')
        if kids:
            bins, bouts = chain(kids)
            if bins:
                lines.append(f'{n} --> {bins[0]}')
            return [n], bouts
        return [n], [n]

    root = tree.get('root') or {}
    ins, outs = chain([root])
    st, sp = nid('ST'), nid('SP')
    lines.append(f'{st}(["Начало"])')
    lines.append(f'{sp}(["Конец"])')
    if ins:
        lines.append(f'{st} --> {ins[0]}')
        for e in outs:
            lines.append(f'{e} --> {sp}')
    else:
        lines.append(f'{st} --> {sp}')

    td = tree.get('templateData') or {}
    data_sections = [(key, label)
                     for key, label in (('parameters', 'Параметры'),
                                        ('variables', 'Переменные'),
                                        ('constants', 'Константы'))
                     if td.get(key)]
    if data_sections:
        ds = nid('DT')
        lines.append(f'subgraph {ds}["Данные шаблона"]')
        for key, label in data_sections:
            for it in td[key]:
                dn = nid('DD')
                lines.append(f'{dn}["{label}: '
                             f'{_mmd_label(it["name"], 24)} — '
                             f'{_mmd_label(it["type"], 14)}"]')
        lines.append('end')
    return '\n'.join(lines) + '\n'


# ---------- legacy: D2 / PlantUML (ЗАМОРОЖЕНО, не развиваем) ----------

SHAPES_D2 = {
    'IfElseActivity': 'diamond',
    'TerminateActivity': 'oval',
    'SetVariableActivity': 'text',
    'SequenceActivity': 'text',
    'SequentialActivity': 'text',
}
SMALL = ('SetVariableActivity', 'SequenceActivity', 'SequentialActivity')
CONTAINERS = {
    'WhileActivity': 'While',
    'ForEachActivity': 'ForEach',
    'ParallelActivity': 'PARALLEL',
}


def esc(s):
    # backslash первым (аудит: d2-строки ломались на неэкранированном \),
    # кавычка → ' (проще эскейпа), перенос — пробел
    return str(s).replace('\\', '\\\\').replace('"', "'").replace('\n', ' ')[:60]


def sid(name, pool):
    i = pool.get(name, len(pool))
    pool[name] = i
    return f'n{i}'


def title(node):
    return esc((node.get('Properties') or {}).get('Title') or node.get('Type'))


def kind(node):
    return node.get('Type', '')


def chain_d2(items, out, indent, pool, src_edge=True):
    """Последовательность узлов внутри уровня; возвращает (первый, последний)."""
    prev = None
    first = None
    for it in items:
        cur = emit_d2(it, out, indent, pool)
        if cur is None:
            continue
        if first is None:
            first = cur
        if prev is not None:
            out.append(f'{indent}{prev} -> {cur}')
        prev = cur
    return first, prev


def emit_d2(node, out, indent, pool):
    t, k = title(node), kind(node)
    if k in ('IfElseBranchActivity',):
        return None  # ветка = ребро от родителя, конверт не рисуем
    nid = sid(node.get('Name', t), pool)
    pad = indent
    if k in CONTAINERS:
        cname = f'c{nid}'
        out.append(f'{pad}{cname}: "{CONTAINERS[k]}: {t}" {{')
        f, last = chain_d2(node.get('Children') or [], out, pad + '  ', pool)
        out.append(f'{pad}}}')
        if k in ('WhileActivity', 'ForEachActivity'):
            out.append(f'{pad}{cname} -> {cname}: повтор {{style.bold: true}}')
        return cname
    shape = SHAPES_D2.get(k)
    if shape:
        out.append(f'{pad}{nid}: "{t}" {{shape: {shape}}}')
    else:
        out.append(f'{pad}{nid}: "{t}"')
    kids = [c for c in (node.get('Children') or []) if is_act(c)]
    for c in kids:
        ck = kind(c)
        if ck == 'IfElseBranchActivity':
            bt = title(c)
            bchildren = [x for x in (c.get('Children') or []) if is_act(x)]
            if bchildren:
                f, _l = chain_d2(bchildren, out, pad + '  ', pool)
                if f is not None:
                    out.append(f'{pad}{nid} -> {f}: "{bt}"')
            else:
                stub = f'{nid}_else'
                out.append(f'{pad}{stub}: "∅" {{shape: circle}}')
                out.append(f'{pad}{nid} -> {stub}: "{bt}"')
        else:
            child = emit_d2(c, out, pad, pool)
            if child is not None:
                out.append(f'{pad}{nid} -> {child}')
    return nid


def to_d2(tree):
    pool, out = {}, [
        'direction: down',
        'classes: {',
        '  added: {',
        '    style: {',
        '      fill: "#d5f5d5"',
        '      stroke: "#2e7d32"',
        '      stroke-width: 2',
        '    }',
        '  }',
        '  modified: {',
        '    style: {',
        '      fill: "#fff3cd"',
        '      stroke: "#b8860b"',
        '      stroke-width: 2',
        '    }',
        '  }',
        '  removed: {',
        '    style: {',
        '      fill: "#f8d7da"',
        '      stroke: "#c62828"',
        '      font-color: "#999"',
        '    }',
        '  }',
        '}',
    ]
    chain_d2(tree['TEMPLATE'], out, '', pool)
    src = '\n'.join(out)
    return src


def paint_cash(src):
    """Демо-дельта: CASH-узлы → modified; + graft (added) + призрак (removed)."""
    lines = src.split('\n')
    marks, res = [], []
    for ln in lines:
        m = re.match(r'^(\s*)(n\d+|c n\d+|c\d+): "(.*?)"', ln)
        if m and 'CASH' in m.group(3):
            marks.append(f'{m.group(2)}.class: modified')
        res.append(ln)
    res += marks
    res += [
        'DEMO_added: "SV graft: commission = PAY*0.03" {shape: text}',
        'DEMO_added.class: added',
        'DEMO_ghost: "Terminate (удалён)" {shape: oval}',
        'DEMO_ghost.class: removed',
        'DEMO_added -> DEMO_ghost',
    ]
    return '\n'.join(res)


# ---------- legacy: PlantUML activity ----------

def emit_pu(node, out, indent):
    k, t = kind(node), title(node)
    pad = indent
    if k == 'SequentialWorkflowActivity':
        out.append(f'{pad}start')
        for c in node.get('Children') or []:
            emit_pu(c, out, pad)
        out.append(f'{pad}stop')
    elif k == 'IfElseActivity':
        branches = [c for c in (node.get('Children') or []) if is_act(c)]
        last = len(branches) - 1
        for i, c in enumerate(branches):
            bt = title(c)
            if i == 0:
                out.append(f'{pad}if ({bt}) then (да)')
            elif i == last:
                out.append(f'{pad}else ({bt})')
            else:
                out.append(f'{pad}elseif ({bt}) then (да)')
            for x in c.get('Children') or []:
                emit_pu(x, out, pad + '  ')
        out.append(f'{pad}endif')
    elif k == 'TerminateActivity':
        out.append(f'{pad}:«{t}»;')
        out.append(f'{pad}end')
    elif k == 'WhileActivity':
        out.append(f'{pad}repeat')
        for c in node.get('Children') or []:
            emit_pu(c, out, pad + '  ')
        out.append(f'{pad}repeat while ({t})')
    elif k == 'ForEachActivity':
        out.append(f'{pad}partition "{t}" {{')
        for c in node.get('Children') or []:
            emit_pu(c, out, pad + '  ')
        out.append(f'{pad}}}')
    elif k == 'ParallelActivity':
        out.append(f'{pad}fork')
        for c in node.get('Children') or []:
            emit_pu(c, out, pad + '  ')
        out.append(f'{pad}end fork')
    elif k == 'IfElseBranchActivity':
        for x in node.get('Children') or []:
            emit_pu(x, out, pad)
    else:
        kids = [x for x in (node.get('Children') or []) if is_act(x)]
        if kids:
            out.append(f'{pad}partition "{t}" {{')
            for c in kids:
                emit_pu(c, out, pad + '  ')
            out.append(f'{pad}}}')
        else:
            out.append(f'{pad}:{t};')


def to_puml(tree):
    out = ['@startuml', 'skinparam defaultTextSize 12']
    for c in tree['TEMPLATE']:
        emit_pu(c, out, '')
    out.append('@enduml')
    return '\n'.join(out)


if __name__ == '__main__':
    # legacy-CLI прототипа: .bpt → .d2/.puml (волна 7 живёт в
    # bpt_export.py diagram — см. T5)
    src, dst = sys.argv[1], sys.argv[2]
    tree = php_unserialize_exact(zlib.decompress(open(src, 'rb').read()))
    open(dst + '.d2', 'w').write(to_d2(tree))
    open(dst + '.puml', 'w').write(to_puml(tree))
    print(f'{dst}.d2 / {dst}.puml')

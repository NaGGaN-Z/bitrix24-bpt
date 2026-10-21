#!/usr/bin/env python3
"""bpt_graft.py — хирургия существующих .bpt (волна 6 bpt-graft).

Конструкторы (bpt_build) — новому; ЭТОТ модуль — правке существующих
экспортов портала: load -> locate -> edit -> save с гейтами. Канон
операций — живые патчи задачи project-x (внешний архив проекта/build/patch_v*.py:
v18/v19 — спецификация API; обе ретро авторов сошлись: для правки чужого
дерева конструкторы бесполезны или опасны, реэмиссия = риск перепривязки
Name-ссылок).

Контракт 1:1 старого тела: load сохраняет дерево ДОctype-точно
(php_unserialize_exact), save жмёт zlib-уровнем ИСХОДНИКА (заголовок zlib
уровень не кодирует однозначно — детект перекомпрессией уровней 1-9 с
побайтовой сверкой; патчи project-x жмут lvl 9, save() билдера — дефолт 6:
различие критично для byte-repro канонов).

Форма — python API: патч-скрипт + graft-операции = intent-декларация.
Каждая операция пишется в intent-лог (человекочитаемо, stderr): позиция
вставки (SV-аккумулятор до Explode — порядок-чувствительность) видна
в логе, семантика — на ревью.

Уровни логов: GRAFT_LOG_LEVEL=DEBUG|INFO|QUIET (default INFO; DEBUG —
детали обходов/сравнений, ERROR — всегда).

CLI (T7): bpt_graft.py gates BASE.bpt PATCHED.bpt — standalone-гейты.
"""
import copy
import fnmatch
import itertools
import os
import re
import sys
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bpt_serialize import php_unserialize_exact  # noqa: E402
from bpt_build import _NAME_TOKEN, validate as _bb_validate  # noqa: E402

_LOG_LEVELS = {'QUIET': 0, 'INFO': 1, 'DEBUG': 2}
_LOG = _LOG_LEVELS.get(os.environ.get('GRAFT_LOG_LEVEL', 'INFO').upper(), 1)

# {=Ns:Field}-токен; свободные namespace'ы без полевой модели
# (System:Now, GlobalConst, Template:…, User; Document/Constant —
# манифесты экспортов недозаполнены, контрпримеры в NS_SECTIONS-доке)
_REF_RE = re.compile(r'\{=\s*([A-Za-z_]\w*)\s*:\s*([A-Za-z_]\w*)')
_REF_NS_FREE = frozenset({'GlobalConst', 'System', 'Template', 'User',
                          'Document', 'Constant'})


def _path_join(path: str, key) -> str:
    return f'{path}.{key}' if isinstance(key, str) else f'{path}[{key}]'


def _log(tag: str, msg: str, debug: bool = False) -> None:
    if _LOG >= (2 if debug else 1):
        print(f'[{tag}] {msg}', file=sys.stderr)


class GraftError(Exception):
    """Отказ операции/гейта graft: expect-контракт, path-diff, lint."""


class NodeRef:
    """Живая ссылка на узел дерева (dict-объект): правки через ref
    мутируют дерево GraftTree. path вычисляется лениво по identity
    (id-обход от корня) — после вставок/удалений выше путь остаётся
    верным; узел, удалённый из дерева, даёт path=None (ошибка usage)."""

    __slots__ = ('node', 'graft')

    def __init__(self, node: dict, graft: 'GraftTree'):
        self.node = node
        self.graft = graft

    @property
    def path(self) -> str:
        p = self.graft.path_of(self.node)
        if p is None:
            raise GraftError('узел больше не в дереве (удалён?) — '
                             'устаревший NodeRef')
        return p

    @property
    def name(self) -> str:
        return self.node.get('Name', '')

    @property
    def type(self) -> str:
        return self.node.get('Type', '')

    @property
    def props(self) -> dict:
        return self.node.setdefault('Properties', {})

    def __repr__(self):
        return f'<NodeRef {self.type} {self.name!r} @ {self.path}>'


def _harvest_names(v, acc: set) -> set:
    """Все Name-строки dict-объектов внутри значения (паттерн probe(),
    bpt_build.py:800-824): шире узловых Name — RequestedInformation-поля
    и прочие Name-ключи тоже резервируются (защита от коллизий)."""
    if isinstance(v, dict):
        if isinstance(v.get('Name'), str):
            acc.add(v['Name'])
        for x in v.values():
            _harvest_names(x, acc)
    elif isinstance(v, list):
        for x in v:
            _harvest_names(x, acc)
    return acc


def _detect_zlib_level(blob: bytes, payload: bytes):
    """Уровень компрессии исходника. Заголовок zlib даёт грубый FLEVEL-
    хинт (биты 6-7 FLG: fastest/fast/default/maximum), перебор 1-9 с
    побайтовой сверкой — истина. None = python-zlib не воспроизводит
    blob ни на одном уровне (чужой компрессор/версия) — save() тогда
    пишет дефолтом с WARN, byte-repro против такого канона невозможен."""
    flevel = (blob[1] >> 6) & 3 if len(blob) > 1 else 2
    hint = {0: (1,), 1: (2, 3, 4, 5), 2: (6,), 3: (9, 8, 7)}[flevel]
    order = list(hint) + [x for x in range(1, 10) if x not in hint]
    for lvl in order:
        if zlib.compress(payload, lvl) == blob:
            return lvl
    return None


class GraftTree:
    """Дерево .bpt для хирургии: load -> locate/edit -> save.

    Атрибуты:
      tree        — живое дерево (мутации правками);
      snapshot    — deepcopy на load (основа path-diff);
      zlib_level  — инт исходника (None = не воспроизведён python-zlib);
      intent      — список записей операций (op/path/детали) — источник
                    intent_paths для гейта path-diff на save.
    """

    def __init__(self, tree: dict, zlib_level, src: str = '<memory>'):
        self.tree = tree
        self.snapshot = copy.deepcopy(tree)
        self.zlib_level = zlib_level
        self.src = src
        self.intent = []
        self._taken = _harvest_names(tree.get('TEMPLATE'), set())
        self._alloc = itertools.count()
        # выведенные из оборота имена (delete/rename_rebind): alloc и
        # явная вставка их не перевыдают — висячие {=Name:…} не могут
        # тихо перепривязаться на семантически чужой узел (аудит №3)
        self._retired = set()
        # журнал переименований old->new (rename_rebind): path-diff
        # матчит списки узлов по ФИНАЛЬНОМУ имени (аудит №2)
        self._renames = {}
        # снапшот находок базы (delta-validate на save блокирует только
        # НОВЫЕ ошибки — исходник с висячками правится не за один приём)
        self.base_findings = _bb_validate(tree)
        self._base_refs = self.resolve_refs_of(self.snapshot)
        _log('graft', f'load {src}: snapshot {len(self._taken)} names, '
                      f'zlib level={zlib_level}, база: '
                      f'{len(self.base_findings[0])} err / '
                      f'{len(self.base_findings[1])} warn')

    # ---- constructor ----
    @classmethod
    def load(cls, src: str) -> 'GraftTree':
        blob = open(src, 'rb').read()
        from bpt_serialize import decompress_bpt  # локально: анти-цикл
        # zlib ИЛИ raw-payload (тег-гейт + bomba-лимит) — симметрия чтения
        # .bpt с export/diagram/serialize (аудит: raw падал zlib.error)
        payload = decompress_bpt(blob)
        tree = php_unserialize_exact(payload)
        return cls(tree, _detect_zlib_level(blob, payload), src)

    # ---- paths ----
    def path_of(self, node) -> str | None:
        """Путь узла '$.…' по identity (id). Ключи dict: str-ключ
        '.key', int-ключ и list-index '[N]' (отображение неоднозначно,
        сопоставление path-diff — точное по строкам, консистентное)."""
        found = None

        def walk(v, path):
            nonlocal found
            if found is not None:
                return
            if v is node:
                found = path
                return
            if isinstance(v, dict):
                for k, x in v.items():
                    walk(x, f'{path}.{k}' if isinstance(k, str) and k.isidentifier()
                         else f'{path}[{k}]')
                    if found is not None:
                        return
            elif isinstance(v, list):
                for i, x in enumerate(v):
                    walk(x, f'{path}[{i}]')
                    if found is not None:
                        return

        walk(self.tree, '$')
        return found

    # ---- names ----
    def alloc_name(self, a_type: str) -> str:
        """Свежее имя узла <токен>_<N> с пропуском занятых И выведенных
        из оборота (Names базового дерева учтены; дубль Name = reject
        импорта B24; retired не перевыдаются — аудит №3). Локальный
        счётчик — вывод детерминирован, глобальный _ACT_SEQ
        билдера не тратится (паттерн probe())."""
        token = _NAME_TOKEN.get(a_type, 'Act')
        while True:
            n = f'{token}_{next(self._alloc)}'
            if n not in self._taken and n not in self._retired:
                self._taken.add(n)
                return n

    # ---- intent ----
    def _intent(self, op: str, detail: str = '', path: str = '',
                node=None, suffix: str = '') -> None:
        """Запись intent-лога. path — напечатанный путь (на момент
        операции); node+suffix — ленивое разрешение для path-diff на
        save: пути узлов, задвинутых поздней вставкой, пересчитываются
        (id-обход), фиксация «на тогда» дала бы ложное несоответствие.
        Для удалённых/перемещённых узлов — только строка (node больше
        не резолвится однозначно)."""
        if node is not None:
            base = self.path_of(node)
            if base is not None:
                path = base + suffix
        rec = {'op': op, 'path': path, 'node': node, 'suffix': suffix,
               'detail': detail}
        self.intent.append(rec)
        _log('graft', f'{op} {detail}'.strip() + (f' @ {path}' if path else ''))

    def intent_paths(self) -> set:
        """Разрешённые пути intent-операций (path-diff-гейт: меняется
        только то, что задекларировано). Узел вне дерева (удалён после
        операции) даёт зафиксированную при операции строку."""
        out = set()
        for r in self.intent:
            if r.get('node') is not None:
                base = self.path_of(r['node'])
                out.add((base + r['suffix']) if base is not None
                        else r['path'])
            elif r['path']:
                out.add(r['path'])
        return out

    def intent_report(self) -> str:
        """Человекочитаемый intent-лог (path: op detail)."""
        return '\n'.join(f"{r['path']}: {r['op']} {r['detail']}".rstrip()
                         for r in self.intent)

    # ---- locate (T2) ----------------------------------------------------

    def _iter_nodes(self):
        """DFS-обход активити-узлов TEMPLATE (document order): корень
        TEMPLATE[0] первым, затем Children рекурсивно."""
        def walk(v):
            if isinstance(v, dict) and 'Type' in v and 'Properties' in v \
                    and 'Children' in v:
                yield v
                yield from walk(v.get('Children'))
            elif isinstance(v, list):
                for x in v:
                    yield from walk(x)
        yield from walk(self.tree.get('TEMPLATE'))

    def locate_all(self, *, kind=None, title=None, sv_keys=None, pred=None,
                   name=None, expect=None) -> list:
        """Поиск узлов по ПЕРВИЧНЫМ предикатам (двое практиков сходятся:
        Title / состав ключей VariableValue / содержимое условий —
        идентифицируют семантику; name= — вторичный хэндл).
          kind     — точное равенство Type;
          title    — подстрока в Properties.Title;
          sv_keys  — итерируемое: узел содержит ВСЕ ключи в
                     Properties.VariableValue (подмножество, не равенство);
          pred     — callable(Properties: dict) -> bool;
          name     — точное равенство Name.
        expect=N: контракт «нашли ровно N» (наследие assert patched==1/==4
        патчей project-x): не ровно N -> GraftError с перечислением найденных
        (Name+Title). Возвращает список NodeRef."""
        keys = list(sv_keys) if sv_keys is not None else None
        found = []
        for n in self._iter_nodes():
            props = n.get('Properties') or {}
            if kind is not None and n.get('Type') != kind:
                continue
            if name is not None and n.get('Name') != name:
                continue
            if title is not None and title not in (props.get('Title') or ''):
                continue
            if keys is not None:
                vv = props.get('VariableValue')
                if not isinstance(vv, dict) or not set(keys) <= set(vv):
                    continue
            if pred is not None and not pred(props):
                continue
            found.append(NodeRef(n, self))
        if expect is not None and len(found) != expect:
            listing = '; '.join(f"{n.node.get('Name')}("
                               f"{(n.node.get('Properties') or {}).get('Title')!r})"
                               for n in found) or 'ничего'
            raise GraftError(f'locate{self._crit(kind, title, sv_keys, pred, name)}: '
                             f'нашли {len(found)}, ожидалось {expect}: {listing}')
        _log('locate', f'найдено {len(found)}'
             + (f' / ожидалось {expect}' if expect is not None else '')
             + ' по ' + self._crit(kind, title, sv_keys, pred, name),
             debug=True)
        return found

    @staticmethod
    def _crit(kind, title, sv_keys, pred, name) -> str:
        c = []
        if kind is not None:
            c.append(f'kind={kind}')
        if title is not None:
            c.append(f'title~{title!r}')
        if sv_keys is not None:
            c.append(f'sv_keys={list(sv_keys)}')
        if pred is not None:
            c.append(f'pred={getattr(pred, "__name__", pred)}')
        if name is not None:
            c.append(f'name={name}')
        return '(' + ', '.join(c) + ')' if c else '(все узлы)'

    def locate(self, **crit) -> NodeRef:
        """Одиночный locate: expect=1 по умолчанию (не ровно один — отказ
        с перечислением). Критерии те же, что locate_all."""
        crit.setdefault('expect', 1)
        refs = self.locate_all(**crit)
        return refs[0]

    # ---- edit (T3) ------------------------------------------------------

    def sv_set(self, ref: NodeRef, mapping, value=None) -> int:
        """Запись в VariableValue SV-узла. Две формы:
        sv_set(ref, {'k': v, ...}) | sv_set(ref, 'k', v) — значения
        любые (строка/список: списочная форма rd_* патчей v19)."""
        if not isinstance(mapping, dict):
            if value is None:
                raise GraftError('sv_set: dict-маппинг или (ключ, значение)')
            mapping = {mapping: value}
        vv = ref.props.setdefault('VariableValue', {})
        for k, v in mapping.items():
            vv[k] = v
            self._intent('sv_set', k, node=ref.node,
                    suffix=f'.Properties.VariableValue.{k}')
        return len(mapping)

    @staticmethod
    def _replace_in(value, old: str, new: str):
        """Замены в значении SV: str и элементы list/dict-рекурсивно.
        Возвращает (новое_значение|None_если_без_изменений, счётчик)."""
        if isinstance(value, str):
            cnt = value.count(old)
            return (value.replace(old, new), cnt) if cnt else (None, 0)
        if isinstance(value, list):
            out, total, changed = [], 0, False
            for x in value:
                nx, cnt = GraftTree._replace_in(x, old, new)
                total += cnt
                changed = changed or nx is not None
                out.append(x if nx is None else nx)
            return (out, total) if changed else (None, total)
        if isinstance(value, dict):
            out, total, changed = {}, 0, False
            for k, x in value.items():
                nx, cnt = GraftTree._replace_in(x, old, new)
                total += cnt
                changed = changed or nx is not None
                out[k] = x if nx is None else nx
            return (out, total) if changed else (None, total)
        return None, 0

    def sv_replace(self, key_glob: str, old: str, new: str,
                   expect: int | None = None, refs=None) -> int:
        """Контекстный replace в формулах SV-узлов: ключи VariableValue по
        glob (fnmatch, '*' = все), old -> new в str-значениях и элементах
        списков. refs (NodeRef | список) — контекст-сужение: только эти
        узлы (кейс v18: '{=A:UF}*0.13' встречается в ДВУХ ветках, правится
        только узел с oe_percent — ветка без процента не трогается).
        expect считает ЗАМЕНЫ-ВХОЖДЕНИЯ (не узлы; ретро-кейс «expect=8
        замен»). Вложенные вхождения не трогаются: один проход
        str.replace по исходному значению — new, содержащий old, не
        переваривается повторно (v17-баг повторных прогонов исключён
        конструктивно)."""
        if not old or not new:
            raise GraftError(f'sv_replace: пустой old/new ({old!r}/{new!r})')
        if refs is None:
            targets = self.locate_all(kind='SetVariableActivity')
        elif isinstance(refs, NodeRef):
            targets = [refs]
        else:
            targets = list(refs)
        total = 0
        for ref in targets:
            vv = ref.props.get('VariableValue')
            if not isinstance(vv, dict):
                continue
            node_hits = []
            for k in vv:
                if not fnmatch.fnmatchcase(k, key_glob):
                    continue
                nv, cnt = self._replace_in(vv[k], old, new)
                if nv is not None:
                    vv[k] = nv
                    node_hits.append((k, cnt))
                    total += cnt
            for k, cnt in node_hits:
                self._intent('sv_replace', f'{k}: {cnt}× {old[:40]!r}→'
                                     f'{new[:40]!r}', node=ref.node,
                             suffix=f'.Properties.VariableValue.{k}')
        if expect is not None and total != expect:
            raise GraftError(f'sv_replace {key_glob!r} {old!r}->{new!r}: '
                             f'замен {total}, ожидалось {expect}')
        return total

    def props_append(self, ref: NodeRef, key: str, *values) -> int:
        """Append в списочный props (ReturnFields). Дубль значения в
        списке — GraftError (assert EG_FIELD not in …, патч v19)."""
        cur = ref.props.get(key)
        if cur is None:
            cur = ref.props[key] = []
        if not isinstance(cur, list):
            raise GraftError(f'props_append: {ref.path}.{key} — не список')
        for v in values:
            if v in cur:
                raise GraftError(f'props_append: {v!r} уже в '
                                 f'{ref.path}.{key}')
            cur.append(v)
            self._intent('props_append', f'{key} += {v!r}', node=ref.node,
                             suffix=f'.Properties.{key}')
        return len(values)

    def props_set(self, ref: NodeRef, key: str, value) -> None:
        """Установка props-ключа (перезапись/создание в конце)."""
        ref.props[key] = value
        self._intent('props_set', f'{key} = {str(value)[:48]!r}',
                         node=ref.node, suffix=f'.Properties.{key}')

    def props_merge(self, ref: NodeRef, mapping: dict) -> int:
        """Слияние вложенного dict в props (Values генераторов docx,
        патч v19): существующий ключ сохраняет позицию (dict.update),
        новые — в конец. Скаляр в существующий dict — GraftError (это
        props_set); dict в существующий скаляр — тоже."""
        for k, v in mapping.items():
            cur = ref.props.get(k)
            if cur is None:
                ref.props[k] = copy.deepcopy(v)
            elif isinstance(cur, dict) and isinstance(v, dict):
                cur.update(copy.deepcopy(v))
            elif isinstance(cur, dict) or isinstance(v, dict):
                raise GraftError(f'props_merge: {ref.path}.{k} — '
                                 f'{type(cur).__name__} против '
                                 f'{type(v).__name__}; скаляр = props_set')
            else:
                ref.props[k] = v
            self._intent('props_merge', f'{k}: += '
                            f'{list(v) if isinstance(v, dict) else 1} ключ(ей)',
                            node=ref.node, suffix=f'.Properties.{k}')
        return len(mapping)

    def _parent_slot(self, node) -> tuple:
        """(родительский список, индекс) для узла по identity; None, если
        узел — корень TEMPLATE[0] или вне дерева."""
        def walk(v):
            if isinstance(v, list):
                for i, x in enumerate(v):
                    if x is node:
                        return v, i
                    r = walk(x)
                    if r:
                        return r
            elif isinstance(v, dict):
                for x in v.values():
                    r = walk(x)
                    if r:
                        return r
            return None
        return walk(self.tree.get('TEMPLATE'))

    def _parent_of(self, node):
        """Узел-родитель (контейнер, чей Children содержит node), либо
        None (node — top-level ребёнок TEMPLATE[0]... или вне дерева)."""
        slot = self._parent_slot(node)
        if slot is None:
            return None
        lst = slot[0]

        def walk(v):
            if isinstance(v, dict):
                if v.get('Children') is lst:
                    return v
                for x in v.values():
                    r = walk(x)
                    if r is not None:
                        return r
            elif isinstance(v, list):
                for x in v:
                    r = walk(x)
                    if r is not None:
                        return r
            return None
        return walk(self.tree.get('TEMPLATE'))

    def insert_child(self, anchor: NodeRef, node: dict,
                     position: str = 'after', dedup: bool = False) -> NodeRef:
        """Вставка узла ЯКОРЕМ (не idx — патч выше сдвигает смысл
        индексов): after|before — соседом якоря (в списке его родителя);
        first|last — ребёнком якоря (в его Children). Порядок-чувствительно
        (SV-аккумулятор до Explode) — позиция фиксируется в intent-логе,
        семантика на ревью. Name вставляемого: отсутствует -> safe-alloc;
        коллизия с существующими -> GraftError (дубль = reject импорта).
        dedup=True — идемпотентность: в точке вставки уже стоит узел с
        тем же Type+Title -> GraftError с именем (класс прод-фоллов
        «повторный патч поверх живой базы»: дубль bnum_r в ручном v24
        был именно этим — рантайм-безопасен, но молчаливая грязь)."""
        if position in ('after', 'before'):
            slot = self._parent_slot(anchor.node)
            if slot is None:
                raise GraftError(f'insert_child: якорь {anchor.name!r} вне '
                                 'дерева')
            parent, idx = slot
            at = idx + 1 if position == 'after' else idx
        elif position in ('first', 'last'):
            children = anchor.node.get('Children')
            if children is None:
                raise GraftError(f'insert_child: якорь {anchor.name!r} '
                                 'листовой (Children: None) — контейнер '
                                 'невозможен')
            parent = children
            at = 0 if position == 'first' else len(parent)
        else:
            raise GraftError(f"insert_child: position {position!r} — "
                             'after|before|first|last')
        if dedup:
            title = (node.get('Properties') or {}).get('Title')
            for i, ex in enumerate(parent):
                if isinstance(ex, dict) and ex.get('Type') == node.get('Type') \
                        and (ex.get('Properties') or {}).get('Title') == title:
                    raise GraftError(
                        f'insert_child dedup: {node.get("Type")} '
                        f'{title!r} уже в точке вставки [{i}], Name='
                        f'{ex.get("Name")!r} — повторный патч поверх '
                        'живой базы? Убери дубль или осознанно dedup=False')
        nm = node.get('Name')
        if not isinstance(nm, str) or not nm:
            node['Name'] = self.alloc_name(node.get('Type', ''))
        elif nm in self._taken:
            raise GraftError(f'insert_child: Name {nm!r} уже занят — '
                             'rename_rebind() сначала')
        elif nm in self._retired:
            raise GraftError(f'insert_child: Name {nm!r} выведен из '
                             'оборота (delete/rename_rebind) — имена не '
                             'перевыдаются, висячие ссылки не перепривязать')
        self._taken.add(node['Name'])
        parent.insert(at, node)
        ref = NodeRef(node, self)
        self._intent('insert_child',
                     f'{node.get("Type")} {node["Name"]!r} position='
                     f'{position} якорь {anchor.name!r}', node=node)
        return ref

    def delete(self, ref: NodeRef) -> None:
        """Удаление узла. Ссылки {=Name:…} на удаляемое имя остаются в
        дереве висячими — имя ВЫВОДИТСЯ из оборота (retired): перевыдача
        новому узлу значила бы тихую перепривязку висячих ссылок на
        семантически чужой узел при зелёных гейтах (аудит №3)."""
        slot = self._parent_slot(ref.node)
        if slot is None:
            raise GraftError(f'delete: узел {ref.name!r} вне дерева')
        path = ref.path
        old_name = ref.name
        dangling = self._count_ref_usage(old_name)
        if dangling:
            _log('graft', f'delete {old_name!r}: в дереве останутся '
                          f'{dangling} ссылок {{={old_name}:…}} — имя '
                          'выведено из оборота, перевыдачи не будет')
        parent, idx = slot
        del parent[idx]
        self._taken.discard(old_name)
        self._retired.add(old_name)
        self._intent('delete', f'{ref.type} {old_name!r}', path)

    def _count_ref_usage(self, node_name: str) -> int:
        """Число {=name:…}-ссылок по всему дереву (TEMPLATE + секции)."""
        pat = '{=' + node_name + ':'
        cnt = [0]

        def walk(v):
            if isinstance(v, str):
                cnt[0] += v.count(pat)
            elif isinstance(v, dict):
                for x in v.values():
                    walk(x)
            elif isinstance(v, list):
                for x in v:
                    walk(x)
        walk(self.tree)
        return cnt[0]

    def move(self, ref: NodeRef, anchor: NodeRef,
             position: str = 'after') -> None:
        """Перенос узла к якорю (позиции как insert_child). Путь меняется
        — path-diff увидит сдвиг соседей, intent покрывает диапазоном.
        Перенос внутрь самого себя (якорь в перемещаемом поддереве) —
        GraftError (цикл в дереве)."""
        if self._node_contains(ref.node, anchor.node):
            raise GraftError(f'move: якорь {anchor.name!r} внутри '
                             f'перемещаемого {ref.name!r} — цикл')
        slot = self._parent_slot(ref.node)
        if slot is None:
            raise GraftError(f'move: узел {ref.name!r} вне дерева')
        parent, idx = slot
        node = parent.pop(idx)
        nm = node.get('Name')
        if isinstance(nm, str):
            self._taken.discard(nm)  # insert_child перевнесёт своё имя
        try:
            self.insert_child(anchor, node, position)
        except GraftError:
            parent.insert(idx, node)  # откат — якорь мог быть внутри нас
            if isinstance(nm, str):
                self._taken.add(nm)
            raise
        self._intent('move', f'{node.get("Name")!r} -> {position} '
                      f'{anchor.name!r}', node=node)
        # сдвиг соседей внутри списка-приёмника: попарный спуск path-diff
        # пометит их позиции modified — покрываем диапазоном списка
        holder = (anchor.node if position in ('first', 'last')
                  else self._parent_of(anchor.node))
        if holder is not None:
            self._intent('move-range', f'сдвиг соседей {anchor.name!r}',
                         node=holder, suffix='.Children')

    @staticmethod
    def _node_contains(root, target) -> bool:
        """target внутри поддерева root (identity)."""
        if root is target:
            return True
        if isinstance(root, dict):
            return any(GraftTree._node_contains(x, target)
                       for x in root.values())
        if isinstance(root, list):
            return any(GraftTree._node_contains(x, target)
                       for x in root)
        return False

    def rename_rebind(self, ref: NodeRef) -> str:
        """Переименование узла с перепривязкой ссылок {=old:…} -> {=new:…}
        по ВСЕМУ дереву (пол-логики clone(); ссылки на узлы вне шаблона
        не существуют — всё дерево и есть шаблон). Новое имя — ТОЛЬКО
        safe-alloc (строкой от агента нельзя — защита от орфан-кейса
        дублей A51294/A62907). Мутация in-place: живые NodeRef остаются
        валидными. Возвращает новое имя."""
        old = ref.node.get('Name')
        new = self.alloc_name(ref.node.get('Type', ''))
        ref.node['Name'] = new
        self._taken.discard(old)
        self._retired.add(old)     # перевыдача старого запрещена (№3)
        self._renames[old] = new   # path-diff: пары по финальному имени (№2)
        pat = '{=' + old + ':'
        cnt = [0]
        touched = []

        def walk(v, path):
            """In-place замена строк в контейнерах (dict/list объекты
            не пересоздаются — NodeRef живы); touched — пути перепривязок
            (path-diff обязан их покрыть intent-ом)."""
            if isinstance(v, str):
                if pat in v:
                    cnt[0] += v.count(pat)
                    touched.append(path)
                    return v.replace(pat, '{=' + new + ':'), True
                return v, False
            if isinstance(v, dict):
                changed = False
                for k in list(v):
                    nv, ch = walk(v[k], f'{path}.{k}')
                    if ch:
                        v[k] = nv
                        changed = True
                return v, changed
            if isinstance(v, list):
                changed = False
                for i in range(len(v)):
                    nv, ch = walk(v[i], f'{path}[{i}]')
                    if ch:
                        v[i] = nv
                        changed = True
                return v, changed
            return v, False

        walk(self.tree, '$')
        self._intent('rename_rebind', f'{old!r} -> {new!r} '
                      f'({cnt[0]} ссылок перепривязано)',
                     node=ref.node, suffix='.Name')
        for p in touched:
            self._intent('rebind', f'{{={old}:…}} -> {{={new}:…}}', path=p)
        return new

    def expose_field(self, ref: NodeRef, field: str,
                     shape_from: str | None = None,
                     manifest: str = 'auto', shape: dict | None = None) -> None:
        """Экспорт поля активности чтения ПАРОЙ: ReturnFields (что читаем)
        + DynamicEntityFields (манифест доступного — без него {=Act:Field}
        может быть «unavailable»; ID требует манифеста — находка практики;
        НО UF-поля работают и без манифеста — боевой факт v19: EG-поле в
        ReturnFields, манифест пуст, портал принял).
        Шейп-источник (приоритет): существующий вход манифеста > готовый
        shape=dict (патч v22 пишет шейп явно) > сиблинг shape_from
        (deepcopy) > отсутствует.
        manifest: 'auto' — манифест-вход если есть источник, иначе
        ReturnFields-only + WARN; True — манифест обязателен (отказ без
        источника); False — только ReturnFields (форма патча v19)."""
        if manifest not in ('auto', True, False):
            raise GraftError("expose_field: manifest='auto'|True|False")
        props = ref.props
        if 'ReturnFields' not in props:
            raise GraftError(f'expose_field: {ref.path} без ReturnFields — '
                             'не активность чтения?')
        if field in props['ReturnFields']:
            raise GraftError(f'expose_field: {field!r} уже в ReturnFields '
                             f'{ref.path}')
        entry = None
        src = ''
        mf = props.get('DynamicEntityFields')
        if mf is None and manifest is not False:
            raise GraftError(f'expose_field: {ref.path} без '
                             'DynamicEntityFields — задай manifest=False')
        if mf is not None and field in mf:
            entry = copy.deepcopy(mf[field])
            src = 'манифест'
        elif shape is not None:
            entry = dict(shape)
            src = 'готовый шейп'
        elif shape_from is not None:
            if not isinstance(mf, dict) or shape_from not in mf:
                raise GraftError(f'expose_field: сиблинг {shape_from!r} '
                                 f'нет в манифесте {ref.path}')
            entry = copy.deepcopy(mf[shape_from])
            src = f'шейп {shape_from!r}'
        elif manifest is True:
            raise GraftError(f'expose_field: {field!r} нет в манифесте '
                             f'{ref.path}, shape/shape_from не задан — '
                             'манифест обязателен (manifest=True)')
        elif manifest == 'auto':
            _log('graft', f'expose_field {field}: без манифест-входа '
                          f'({ref.path}) — UF-поля работают (v19), для '
                          'прочих {=Act:Field} риск «unavailable»')
        props['ReturnFields'].append(field)
        if entry is not None:
            props.setdefault('DynamicEntityFields', {})[field] = entry
        self._intent('expose_field', f'{field}'
                     + (f' ({src})' if src else ' (ReturnFields-only)'),
                     node=ref.node, suffix='.Properties')

    # ---- payload-секции (T3) --------------------------------------------

    # Доноры шейпов секций (каркас 8 полей един; источник — корпус, T5
    # разрастёт в bpt_graft_forms): Name обязана == ключу у VARIABLES/
    # CONSTANTS; у PARAMETERS Name = человекочитаемый лейбл ≠ ключ
    # (грабля booking-chain: rps «Reporting period start»).
    _DONOR_SHAPES = {
        'VARIABLES': {'Name': '', 'Description': '', 'Type': 'string',
                      'Required': '0', 'Multiple': '0', 'Options': '',
                      'Default': ''},
        'CONSTANTS': {'Name': '', 'Description': '', 'Type': 'user',
                      'Required': '0', 'Multiple': '0', 'Options': '',
                      'Default': 'user_1'},  # донор: внешний корпус (adm)
        'PARAMETERS': {'Name': '', 'Description': '', 'Type': 'string',
                       'Required': '0', 'Multiple': '0', 'Options': '',
                       'Default': ''},  # донор: booking-chain_v19 rps (UF:date)
    }

    def _section(self, name: str) -> dict:
        sec = self.tree.get(name)
        if sec is None:
            raise GraftError(f'секции {name} нет в payload')
        if isinstance(sec, list):
            if sec:
                raise GraftError(f'секция {name} — непустой list?!')
            sec = self.tree[name] = {}  # PHP a:0:{} парсится в list
        return sec

    def declare(self, section: str, entries: dict, strict: bool = True) -> int:
        """Объявление НОВЫХ записей секции VARIABLES/CONSTANTS/PARAMETERS
        (существующий ключ — GraftError: это payload_set). strict=True —
        только поля каркаса-донора; Name: VARIABLES/CONSTANTS = ключ
        (принудительно), PARAMETERS = человекочитаемый лейбл из entries
        (обязателен — грабля booking-chain). Default нормализуется по Multiple
        (multiple -> [] — кейс mgr: multi-Default значением не пережил
        импорт; user -> 'user_N' строкой)."""
        if section not in self._DONOR_SHAPES:
            raise GraftError(f'declare: секция {section!r} — VARIABLES | '
                             'CONSTANTS | PARAMETERS')
        donor = self._DONOR_SHAPES[section]
        sec = self._section(section)
        for key, spec in entries.items():
            if key in sec:
                raise GraftError(f'declare: {section}[{key!r}] уже есть — '
                                 'точечная правка = payload_set')
            spec = dict(spec or {})
            if strict:
                extra = set(spec) - set(donor)
                if extra:
                    raise GraftError(f'declare {section}[{key!r}]: лишние '
                                     f'поля {sorted(extra)} (strict)')
            rec = {k: copy.deepcopy(v) for k, v in donor.items()}
            rec.update(copy.deepcopy(spec))
            if section == 'PARAMETERS':
                if not rec.get('Name'):
                    raise GraftError(f'declare PARAMETERS[{key!r}]: Name = '
                                     'человекочитаемый лейбл — обязателен '
                                     '(грабля booking-chain)')
            else:
                rec['Name'] = key
            if str(rec.get('Multiple')) == '1' and rec.get('Default') == '':
                rec['Default'] = []
                _log('graft', f'declare {section}[{key}]: Default -> [] '
                              '(Multiple=1)')
            sec[key] = rec
            self._intent('declare', f'{section}[{key}] '
                                     f'Type={rec.get("Type")}',
                         node=self.tree, suffix=f'.{section}.{key}')
        return len(entries)

    def generators_values(self, mapping: dict, skip_tpl=()) -> int:
        """Values-merge во ВСЕ генераторы документов (идиома каждого
        патча project-x: доп. поля docx-шаблонов). skip_tpl — TemplateId,
        которые не трогать (канон project-x: '13' = OLD-шаблон). Возвращает
        число затронутых генераторов; expect-контракт — за вызовом."""
        n = 0
        for ref in self.locate_all(kind='CrmGenerateEntityDocumentActivity'):
            if str(ref.props.get('TemplateId')) in {str(x) for x in skip_tpl}:
                continue
            self.props_merge(ref, {'Values': mapping})
            n += 1
        return n

    def payload_set(self, section: str, key: str, value) -> None:
        """Запись/обновление ПО КЛЮЧУ внутри payload-секции
        (DOCUMENT_FIELDS/VARIABLES/PARAMETERS/CONSTANTS; append-семантика
        dict, НЕ замена секции). Формы value:
          скаляр  — у dict-записи правится Default (смена значения
                    константы/дефолта параметра = точечная правка);
          dict    — merge в существующую запись (позиция ключей
                    сохраняется) или добавление новой записи целиком;
          list    — полная замена значения по ключу.
        Гранулярность path-diff сохраняется ($.SECTION.key[.field])."""
        allowed = ('DOCUMENT_FIELDS', 'VARIABLES', 'PARAMETERS', 'CONSTANTS')
        if section not in allowed:
            raise GraftError(f'payload_set: секция {section!r} — одна из '
                             f'{allowed}')
        sec = self._section(section)
        cur = sec.get(key)
        if isinstance(cur, dict) and isinstance(value, dict):
            cur.update(copy.deepcopy(value))
            for k in value:
                self._intent('payload_set', f'{section}[{key}].{k}',
                             node=self.tree,
                             suffix=f'.{section}.{key}.{k}')
        elif isinstance(cur, dict) and not isinstance(value, (dict, list)):
            cur['Default'] = value
            self._intent('payload_set', f'{section}[{key}].Default='
                                     f'{str(value)[:40]!r}',
                         node=self.tree,
                         suffix=f'.{section}.{key}.Default')
        else:
            sec[key] = copy.deepcopy(value)
            self._intent('payload_set', f'{section}[{key}] = '
                                     f'{type(value).__name__}',
                         node=self.tree, suffix=f'.{section}.{key}')

    # ---- variable rename -------------------------------------------------

    @staticmethod
    def _var_name_re(name: str):
        r"""Токен ссылки на переменную с границей имени: `{=Variable:name}`
        (пробелы вокруг ':' допустимы, соседний идентификатор — нет)."""
        return re.compile(r'\{=\s*Variable\s*:\s*' + re.escape(name)
                          + r'(?![A-Za-z0-9_])(\s*\})')

    def _var_occurrences(self, name: str) -> int:
        """Счёт привязок идентификатора переменной в payload (без записи
        VARIABLES): ключи VariableValue, `{=Variable:name}` в строках,
        ряды propertyvariablecondition, ForEachActivity.Variable,
        mixedcondition object=Variable/field=name. Единый предикат
        var_rename/expect (без мутаций)."""
        pat = self._var_name_re(name)
        n = 0

        def walk(v):
            nonlocal n
            if isinstance(v, dict):
                vv = v.get('VariableValue')
                if isinstance(vv, dict) and name in vv:
                    n += 1
                if v.get('object') == 'Variable' and v.get('field') == name:
                    n += 1
                if v.get('Type') == 'ForEachActivity' and \
                        (v.get('Properties') or {}).get('Variable') == name:
                    n += 1
                pvc = v.get('propertyvariablecondition')
                if isinstance(pvc, list):
                    for row in pvc:
                        if isinstance(row, list) and row and row[0] == name:
                            n += 1
                for x in v.values():
                    walk(x)
            elif isinstance(v, list):
                for x in v:
                    walk(x)
            elif isinstance(v, str):
                n += len(pat.findall(v))

        walk(self.tree)
        return n

    def var_rename(self, old: str, new: str,
                   expect: int | None = None) -> int:
        """Переименовать переменную шаблона: ключ VARIABLES (позиция
        сохраняется) + все привязки в теле — `{=Variable:old}` (любые
        строки/списки), ключи VariableValue, ряды
        propertyvariablecondition, ForEachActivity.Properties.Variable,
        mixedcondition object=Variable/field. Name-лейбл записи НЕ
        меняется: в VARIABLES это display-title, идентификатор — ключ
        (канон `variable()`: Name = label or name).
        expect — контракт числа вхождений, проверяется ДО мутации
        (отказ атомарен). Возвращает число перепривязанных вхождений."""
        if not old or not new:
            raise GraftError('var_rename: пустой old/new')
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', new):
            raise GraftError(f'var_rename: {new!r} — не идентификатор')
        sec = self._section('VARIABLES')
        if old not in sec:
            raise GraftError(f'var_rename: {old!r} нет в VARIABLES')
        if new in sec:
            raise GraftError(f'var_rename: {new!r} уже в VARIABLES')
        total = self._var_occurrences(old)
        if expect is not None and total != expect:
            raise GraftError(f'var_rename {old}->{new}: вхождений {total}, '
                             f'ожидалось {expect}')
        items = list(sec.items())
        sec.clear()
        for k, v in items:
            sec[new if k == old else k] = v
        self._intent('var_rename', f'VARIABLES[{old!r}] -> {new!r}',
                     node=sec, suffix=f'.{old}')
        self._intent('var_rename', f'VARIABLES[{old!r}] -> {new!r}',
                     node=sec, suffix=f'.{new}')
        pat = self._var_name_re(old)
        done = 0

        def rebind(s: str) -> tuple[str, int]:
            cnt = len(pat.findall(s))
            if not cnt:
                return s, 0
            return pat.sub(lambda m: '{=Variable:' + new + m.group(1), s), cnt

        def walk(v):
            nonlocal done
            if isinstance(v, dict):
                vv = v.get('VariableValue')
                if isinstance(vv, dict) and old in vv:
                    rows = list(vv.items())
                    vv.clear()
                    for k, x in rows:
                        vv[new if k == old else k] = x
                    done += 1
                    self._intent('var_rename', f'VariableValue[{old!r}] -> '
                                 f'{new!r}', node=vv, suffix=f'.{old}')
                    self._intent('var_rename', f'VariableValue[{old!r}] -> '
                                 f'{new!r}', node=vv, suffix=f'.{new}')
                if v.get('object') == 'Variable' and v.get('field') == old:
                    v['field'] = new
                    done += 1
                    self._intent('var_rename', f'condition field {old!r} -> '
                                 f'{new!r}', node=v, suffix='.field')
                if v.get('Type') == 'ForEachActivity':
                    props = v.get('Properties') or {}
                    if props.get('Variable') == old:
                        props['Variable'] = new
                        done += 1
                        self._intent('var_rename', f'ForEach.Variable {old!r}'
                                     f' -> {new!r}', node=props,
                                     suffix='.Variable')
                pvc = v.get('propertyvariablecondition')
                if isinstance(pvc, list):
                    for row in pvc:
                        if isinstance(row, list) and row and row[0] == old:
                            row[0] = new
                            done += 1
                            self._intent('var_rename', 'propertyvariable'
                                         f'condition {old!r} -> {new!r}',
                                         node=row, suffix='[0]')
                for k, x in list(v.items()):
                    if isinstance(x, str):
                        nx, cnt = rebind(x)
                        if cnt:
                            v[k] = nx
                            done += cnt
                            self._intent('var_rename', f'{k}: {old!r} -> '
                                         f'{new!r}', node=v, suffix=f'.{k}')
                    else:
                        walk(x)
            elif isinstance(v, list):
                for i, x in enumerate(list(v)):
                    if isinstance(x, str):
                        nx, cnt = rebind(x)
                        if cnt:
                            v[i] = nx
                            done += cnt
                            self._intent('var_rename', f'[{i}]: {old!r} -> '
                                         f'{new!r}', node=v, suffix=f'[{i}]')
                    else:
                        walk(x)

        walk(self.tree)
        return done

    # ---- path-diff / delta-validate (T4) ---------------------------------

    def path_diff(self) -> dict:
        """Сравнение snapshot↔текущего с гранулярностью «добавление
        поддерева ≠ правка родителя»: элементы списка — added/removed
        ЦЕЛИКОМ (без спуска внутрь); единственная пара серединных
        элементов = модификация (рекурсивный спуск до точного пути).
        Возвращает {путь: 'added'|'removed'|'modified'}; пустой = дерево
        не менялось. Консервативно: сложная перестановка середин серий —
        шум added/removed (гейт не пропускает, но не слепнет)."""
        out = {}

        def diff(snap, cur, path):
            if snap == cur:
                return
            if isinstance(snap, dict) and isinstance(cur, dict):
                for k in snap:
                    if k not in cur:
                        out[f'{path}.{k}' if isinstance(k, str)
                            else f'{path}[{k}]'] = 'removed'
                for k in cur:
                    if k not in snap:
                        out[f'{path}.{k}' if isinstance(k, str)
                            else f'{path}[{k}]'] = 'added'
                    else:
                        diff(snap[k], cur[k],
                             f'{path}.{k}' if isinstance(k, str)
                             else f'{path}[{k}]')
                return
            if isinstance(snap, list) and isinstance(cur, list):
                # Списки УЗЛОВ: матчинг по Name (канонически уникален —
                # дубль = reject импорта). Правки сдвинутых вставкой
                # соседей спариваются точно (prefix/suffix-тримминг
                # деградирует на «вставка + правка одного из
                # сдвинутых» — серии неравных длин дают ложные
                # added/removed); непарные старые = removed, новые =
                # added (поддерево целиком, без спуска).
                # Rename-осведерённость (аудит №2): snap-имена
                # ремапятся журналом _renames (цепочки X→Y→Z), пары
                # матчатся по ФИНАЛЬНОМУ имени — переименованный узел
                # спаривается, дифф спускается до '.Name' под
                # intent rename_rebind; коллизия финальных имён —
                # консервативный index-diff (шум, гейт не слепнет).
                snap_named = all(isinstance(x, dict) and isinstance(
                    x.get('Name'), str) for x in snap)
                cur_named = all(isinstance(x, dict) and isinstance(
                    x.get('Name'), str) for x in cur)
                if snap_named and cur_named and (snap or cur):
                    def _final(nm):
                        seen = set()
                        while nm in self._renames and nm not in seen:
                            seen.add(nm)
                            nm = self._renames[nm]
                        return nm
                    snap_fin = {}
                    collide = False
                    for i, x in enumerate(snap):
                        f = _final(x['Name'])
                        if f in snap_fin:
                            collide = True
                            break
                        snap_fin[f] = i
                    if not collide:
                        cur_idx = {x['Name']: i
                                   for i, x in enumerate(cur)}
                        for nm, i in snap_fin.items():
                            if nm not in cur_idx:
                                out[f'{path}[{i}]'] = 'removed'
                        for nm, i in cur_idx.items():
                            if nm in snap_fin:
                                diff(snap[snap_fin[nm]], cur[i],
                                     f'{path}[{i}]')
                            else:
                                out[f'{path}[{i}]'] = 'added'
                        return
                i = 0
                while i < len(snap) and i < len(cur) and snap[i] == cur[i]:
                    i += 1
                j = 0
                while (j < len(snap) - i and j < len(cur) - i
                       and snap[len(snap) - 1 - j] == cur[len(cur) - 1 - j]):
                    j += 1
                old_m, new_m = snap[i:len(snap) - j], cur[i:len(cur) - j]
                if not old_m and not new_m:
                    return
                if len(old_m) == len(new_m):
                    # равные длины середин = N модификаций на месте
                    # (разрозненные правки соседних узлов); вставка/удаление
                    # меняли бы длину
                    for x in range(len(old_m)):
                        diff(old_m[x], new_m[x], f'{path}[{i + x}]')
                    return
                for x in range(len(old_m)):
                    out[f'{path}[{i + x}]'] = 'removed'
                for x in range(len(new_m)):
                    out[f'{path}[{i + x}]'] = 'added'
                return
            out[path] = 'modified'

        diff(self.snapshot, self.tree, '$')
        if out:
            _log('path-diff', f'{len(out)} изменённых путей: '
                 + ', '.join(f'{p} ({k})' for p, k in
                             sorted(out.items())[:8])
                 + ('…' if len(out) > 8 else ''), debug=True)
        return out

    def check_intent_coverage(self, changed: dict) -> None:
        """Гейт path-diff: paths_changed ⊆ intent_paths (покрытие = путь
        совпал ИЛИ является потомком intent-пути — intent покрывает своё
        поддерево). Нарушение = самопроизвольная мутация (правка мимо
        graft-операций): отказ с перечнем путей."""
        allowed = self.intent_paths()

        def covered(p):
            return any(p == a or p.startswith(a + '.') or p.startswith(a + '[')
                       for a in allowed)

        orphans = [p for p in changed if not covered(p)]
        if orphans:
            raise GraftError('path-diff: изменения вне intent-декларации:\n  '
                             + '\n  '.join(f'{p} ({changed[p]})'
                                           for p in orphans))

    def delta_validate(self, base_findings=None) -> list:
        """Delta-validate: на load — снапшот находок validate() базы; на
        save — блокируют только НОВЫЕ ERROR (старые — пропуск с
        однострочным WARN: исходник с висячками правится не за один
        приём). Возвращает новые ошибки (пустой список = ок)."""
        if base_findings is None:
            base_findings = self.base_findings
        errors, warnings = _bb_validate(self.tree)
        base_err, base_warn = base_findings if base_findings else (set(), set())
        new_errors = [e for e in errors if e not in base_err]
        for e in errors:
            if e in base_err:
                _log('delta-validate', f'старая ошибка базы (пропуск): '
                      f'{e[:90]}')
        _log('delta-validate', f'ошибок {len(errors)} '
             f'(новых {len(new_errors)}), предупреждений {len(warnings)}')
        return new_errors

    @staticmethod
    def snapshot_findings(tree: dict):
        """Снапшот находок валидатора (для delta_validate при внешнем
        дереве; GraftTree хранит свой на load — base_findings)."""
        return _bb_validate(tree)

    # ---- form-lint (T5) --------------------------------------------------

    # coupled-пары ключей (обе стороны; корпус 104/104 полные):
    # класс -> (ключ А, ключ B): один без другого = аномалия формы
    COUPLED = {'CrmGetDynamicInfoActivity': ('ReturnFields',
                                             'DynamicEntityFields')}

    def form_lint(self) -> tuple:
        """Форм-lint — интегратор канона связей (реестры растут ДАННЫМИ:
        bpt_graft_forms.py, регенерация tests/harvest_forms.py). Правила:
          FL-01 ключи Properties: case-fold совпадение с известным ключом
               класса = ERROR с подсказкой (конвенция корпуса СМЕШАННАЯ —
               условия lowercase); ключ чужого класса / неизвестный = WARN;
          FL-02 Children: None = ERROR «портал ждёт массив» (прумер:
               0/46 файлов корпуса; serialize-НЕловимо, портал падает
               count(): Countable — единственный прод-фолл parking-sync);
          FL-03 coupled-keys: пара без второй стороны = ERROR;
          FL-04 записи секций: Default-форма по Multiple (multiple ->
               list|None; single -> str; кейс mgr: multi-Default
               значением не пережил импорт) = ERROR; ключи вне каркаса
               корпуса = WARN;
          FL-05 ForEach Object вне домена = ERROR (портал-канон v20:
                режимы FOR_EACH_SOURCES ∪ Name активностей шаблона —
                единый источник bpt_build.for_each_object_ok, аудит №1;
                анти-паттерн: экстраполяция из единственного
                эталонного режима).
        Возвращает (errors, warnings) — строки с [FL-NN] префиксами."""
        import bpt_graft_forms as forms
        # единый источник домена ForEach Object (аудит №1): режимы
        # объявлены ОДИН раз в bpt_build (канон v20), graft наследует
        # предикатом; имена активностей берутся из самого дерева
        # (промер 2026-10-16: все 7 корпусных A-имён Object живут как
        # Name узлов своих шаблонов — A-реестр harvest не арбитр)
        from bpt_build import FOR_EACH_SOURCES, for_each_object_ok
        errors, warnings = [], []
        all_keys = set()
        for ks in forms.PROP_KEYS.values():
            all_keys |= ks
        node_names = _harvest_names(self.tree.get('TEMPLATE'), set())

        def emit(sev, rule, path, msg, hint=''):
            line = f'[FL-{rule} {sev}] {path}: {msg}'
            if hint:
                line += f' — {hint}'
            (errors if sev == 'ERROR' else warnings).append(line)
            _log('form-lint', f'FL-{rule} {sev} {path}: {msg}'
                 + (f' ({hint})' if hint else ''), debug=True)

        def check_props(node, path):
            t = node.get('Type')
            props = node.get('Properties') or {}
            known = forms.PROP_KEYS.get(t)
            if known is None:
                if isinstance(t, str) and props:
                    emit('WARN', '01', path,
                         f'класс {t} вне корпуса — ключи не сверены')
            else:
                fold = {k.lower(): k for k in known}
                for k in props:
                    if k in known:
                        continue
                    hit = fold.get(k.lower())
                    if hit is not None:
                        emit('ERROR', '01', path,
                             f'ключ {k!r}: case-fold совпадает с {hit!r}',
                             f'пиши {hit}')
                    elif k in all_keys:
                        emit('WARN', '01', path,
                             f'ключ {k!r} известен чужому классу (не {t})')
                    else:
                        emit('WARN', '01', path,
                             f'ключ {k!r} неизвестен корпусу')
            if isinstance(t, str) and t in self.COUPLED:
                a, b = self.COUPLED[t]
                for x, y in ((a, b), (b, a)):
                    if x in props and y not in props:
                        emit('ERROR', '03', path,
                             f'{x} без пары {y} (обе стороны, '
                             'манифест/возврат не расщепляются)')
            if t == 'ForEachActivity':
                obj = props.get('Object')
                if isinstance(obj, str) and not for_each_object_ok(
                        obj, node_names):
                    emit('ERROR', '05', path,
                         f'Object={obj!r} вне портал-канона v20',
                         f'допустимо: режимы {sorted(FOR_EACH_SOURCES)} '
                         '∪ Name активностей шаблона')

        def walk(v, path, is_root=False):
            if isinstance(v, dict) and 'Type' in v and 'Properties' in v \
                    and 'Children' in v:
                if v.get('Children') is None:
                    emit('ERROR', '02', path,
                         'Children: None — портал ждёт массив '
                         '(падает count())', 'Children: []')
                check_props(v, path)
                walk(v.get('Children'), f'{path}.Children')
            elif isinstance(v, list):
                for i, x in enumerate(v):
                    walk(x, f'{path}[{i}]')

        walk(self.tree.get('TEMPLATE'), '$.TEMPLATE')

        for sec in ('PARAMETERS', 'CONSTANTS', 'VARIABLES'):
            s = self.tree.get(sec)
            if not isinstance(s, dict):
                continue
            for key, rec in s.items():
                path = f'$.{sec}.{key}'
                if not isinstance(rec, dict):
                    emit('WARN', '04', path,
                         f'запись {type(rec).__name__} — корпус несёт dict')
                    continue
                for k in rec:
                    if k not in forms.SECTION_FIELDS:
                        emit('WARN', '04', path,
                             f'ключ {k!r} вне каркаса корпуса')
                mult = str(rec.get('Multiple'))
                norm = forms.DEFAULT_NORM.get(mult)
                d = rec.get('Default')
                if norm is not None and d is not None \
                        and type(d).__name__ not in norm:
                    want = 'список' if mult == '1' else 'строка'
                    emit('ERROR', '04', path,
                         f'Default типа {type(d).__name__} при '
                         f'Multiple={mult}', f'корпус несёт {want}'
                         + ('|None' if mult == '1' else ''))
        _log('form-lint', f'{len(errors)} err, {len(warnings)} warn')
        return errors, warnings

    # ---- резолвер ссылок (T6) -------------------------------------------

    # namespace'ы доступности: {=Ns:Field} -> секция payload. Калибровка
    # по корпусу (46 .bpt): STRICT = Variable (кейс v11/v12 pf_gross
    # пойман, живые шаблоны чисты), Parameter; OUT-OF-MODEL = Document
    # (манифест экспортов неполон: approval-stages {=Document:ASSIGNED_BY},
    # loop-counter UF_7_PRKONT — живые ссылки вне DOCUMENT_FIELDS), Constant
    # (внешний корпус: {=Constant:url} вне CONSTANTS, рабочий
    # шаблон) — полевая проверка невозможна, не эмитим; System/
    # GlobalConst/Template/User — платформенные, без модели
    NS_SECTIONS = {'Variable': 'VARIABLES', 'Parameter': 'PARAMETERS'}
    # UF_*-поля портала читаются активностью БЕЗ манифест-входа (боевые
    # факты v19: EG-поле и UF_CRM_OCT_RESERV_ID в ReturnFields/формулах
    # при пустом манифесте — портал принял); родные (ID…) — строго
    # по манифесту (ID-причуда)

    def resolve_refs(self) -> list:
        """Резолвер ВСЕХ {=Ns:Field}/{{=}}-ссылок: availability-модель
        одним проходом вместо поштучных рул (ответ на «каждую граблю
        зашивать заебёшься» — новые грабли ссылочного семейства ловятся
        бесплатно). Проверяет (калибровка по корпусу 46 .bpt):
          {=Variable:X} ⊆ VARIABLES — STRICT (TODO №7: undeclared =
              «Automation rule refers to missing … variables», кейс
              v11/v12 pf_gross/pf_hst пойман корректно);
          {=Parameter:X} ⊆ PARAMETERS — STRICT;
          {=Act:Field}: Act — имя активности; для CrmGetDynamicInfo
              Field ∈ DynamicEntityFields ∪ ReturnFields ∪ UF_*
              (манифест доступного + чтение; ID-причуда: ID тоже
              требует манифеста); прочие классы — без полевой модели
              (доп. результаты разнородны), проверяется только имя;
          {=Document:X}, {=Constant:X} — ВНЕ МОДЕЛИ (манифесты экспортов
              недозаполнены, контрпримеры живых шаблонов в докстринге
              NS_SECTIONS); System/GlobalConst/Template/User — free.
        Токенайзер bpt_funcs.tokenize достаёт ref-токены и из {{=}}-тел.
        Возвращает список находок '[REF …]' (пустой = чисто)."""
        import bpt_graft_forms as forms
        sections = {}
        for ns, sec in self.NS_SECTIONS.items():
            s = self.tree.get(sec)
            sections[ns] = set(s) if isinstance(s, dict) else set()
        nodes = {}      # Name -> node (активности)
        manifests = {}  # Name -> доступные поля (GetDyn)
        for n in self._iter_nodes():
            nm = n.get('Name')
            if not isinstance(nm, str):
                continue
            nodes[nm] = n
            if n.get('Type') == 'CrmGetDynamicInfoActivity':
                props = n.get('Properties') or {}
                avail = set(props.get('DynamicEntityFields') or {})
                avail |= set(props.get('ReturnFields') or [])
                manifests[nm] = avail
        findings = []
        # unknown-класс активностей с ReturnFields (не GetDyn): пара
        # ReturnFields+манифест вне корпуса — не наш контур
        _ = forms

        def check_ref(ns, field, path):
            if ns in self.NS_SECTIONS:
                if field not in sections[ns]:
                    findings.append(
                        f'[REF ERROR] {path}: {{={ns}:{field}}} — нет в '
                        f'{self.NS_SECTIONS[ns]} (undeclared = reject '
                        'импорта)')
            elif ns in nodes:
                avail = manifests.get(ns)
                if avail is not None and field not in avail \
                        and not field.startswith('UF_'):
                    findings.append(
                        f'[REF ERROR] {path}: {{={ns}:{field}}} — поле '
                        'вне манифеста/ReturnFields активности '
                        '(«unavailable»; ID требует манифеста)')
            elif ns not in _REF_NS_FREE:
                findings.append(f'[REF ERROR] {path}: {{={ns}:{field}}} — '
                                'ни namespace, ни Name активности')

        def walk(v, path):
            if isinstance(v, str):
                if '{=' in v:
                    # findall покрывает и {{=}}-тела: одиночные {=Ns:Field}
                    # внутри выражений — подстроки той же строки
                    for ns, field in _REF_RE.findall(v):
                        check_ref(ns, field, path)
            elif isinstance(v, dict):
                for k, x in v.items():
                    walk(x, _path_join(path, k))
            elif isinstance(v, list):
                for i, x in enumerate(v):
                    walk(x, f'{path}[{i}]')

        walk(self.tree, '$')
        for f in findings:
            _log('resolver', f[:120], debug=True)
        _log('resolver', f'{len(findings)} нерезолвленных ссылок')
        return findings

    # ---- save / CLI (T7) -------------------------------------------------

    def save(self, dst: str, repro: str | None = None) -> int:
        """Полный save-контракт: path-diff ⊆ intent (самовольные мутации
        = отказ), delta-validate (блокируют только НОВЫЕ ошибки базы;
        база с висячками правится не за один приём), form-lint (ERROR —
        всегда: база корпуса FL-чиста, ERROR = несовместимость с
        порталом), резолвер (delta: новые нерезолвленные ссылки —
        блокируют), self-check re-serialize, атомарная запись
        (tmp+fsync+rename). zlib — уровень исходника (детект load);
        исходник с невоспроизводимым python-zlib блобом -> уровень 9
        (конвенция живых патчей project-x) + WARN: байты в любом случае
        меняются, портал ресжатое принимает (боевой опыт booking-chain).
        repro=путь_канона: побайтовая сверка после записи — лучший
        дым-тест регенерации. Возвращает размер блоба."""
        changed = self.path_diff()
        self.check_intent_coverage(changed)
        new_errors = self.delta_validate()
        if new_errors:
            raise GraftError('save: новые ошибки валидатора (база '
                             'проходила):\n  '
                             + '\n  '.join(new_errors))
        fl_err, fl_warn = self.form_lint()
        for w in fl_warn:
            _log('form-lint', f'WARN {w[:110]}')
        if fl_err:
            raise GraftError('save: form-lint ERROR:\n  '
                             + '\n  '.join(fl_err))
        base_refs = getattr(self, '_base_refs', None)
        if base_refs is None:
            base_refs = self._base_refs = self.resolve_refs_of(
                self.snapshot)
        refs = self.resolve_refs()
        new_refs = [r for r in refs if r not in base_refs]
        if new_refs:
            raise GraftError('save: новые нерезолвленные ссылки:\n  '
                             + '\n  '.join(new_refs))
        from bpt_serialize import decompress_bpt, php_serialize
        payload = php_serialize(self.tree)
        back = php_unserialize_exact(payload)
        if php_serialize(back) != payload:
            raise GraftError(f'save: re-serialize mismatch ({dst})')
        from bpt_build import _lint_keys
        errs, warns = _lint_keys(self.tree)
        for w in warns:
            _log('graft', f'WARN {w[:110]}')
        if errs:
            raise GraftError('save: числовые str-ключи (JSON-шрам):\n  '
                             + '\n  '.join(errs))
        level = self.zlib_level
        if level is None:
            level = 9
            _log('save', f'{self.src}: zlib исходника невоспроизводим '
                          f'python-zlib — пишу уровнем 9 (конвенция '
                          f'патчей project-x), байты канона не сойдутся')
        blob = zlib.compress(payload, level)
        if repro is not None:
            # сверка ДО записи: провал repro не должен оставлять dst
            # перезаписанным (аудит — dst писался до провала)
            canon = open(repro, 'rb').read()
            if blob != canon:
                # диагностика: semantic-diff канона против нашего (стек
                # bpt_export diff — identity-матчинг, иммунный к
                # переименованиям); ручной подсчёт узлов не нужен
                hint = ''
                try:
                    from bpt_export import diff_templates, render_diff
                    canon_tree = php_unserialize_exact(
                        decompress_bpt(canon))
                    events, renames = diff_templates(canon_tree, self.tree)
                    lines = render_diff(events, renames)
                    if lines:
                        hint = '\n  semantic-diff (канон -> наш):\n    ' \
                            + '\n    '.join(lines[:15]) \
                            + ('\n    …' if len(lines) > 15 else '')
                except Exception as e:  # noqa: BLE001 — диагностика не
                    # должна маскировать основной отказ
                    hint = f'\n  (semantic-diff недоступен: {e})'
                raise GraftError(
                    f'save: byte-repro ПРОВАЛ — {dst} ({len(blob)} B) != '
                    f'{repro} ({len(canon)} B); dst НЕ записан' + hint)
            _log('save', f'byte-repro OK: {dst} == {repro} '
                         f'({len(blob)} B, zlib {level})')
        tmp = dst + '.tmp'
        with open(tmp, 'wb') as f:
            f.write(blob)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, dst)
        _log('save', f'{dst}: {len(blob)} B (zlib {level}, '
                      f'{len(changed)} путей изменено, '
                      f'{len(self.intent)} intent-записей)')
        return len(blob)

    def save_raw(self, dst: str) -> int:
        """Запись БЕЗ гейтов (atomic, zlib исходника): для канонов-
        регрессов и дебаг-артефактов. Дерево с историческими багами,
        которые гейт обязан блокировать (FN-10 кейс v22: if-голый-реф),
        байт-в-байт воспроизводится только мимо delta-гейтов — это
        документация бага, не обход качества: новый код пишется через
        save(). Возвращает размер блоба."""
        from bpt_serialize import php_serialize
        payload = php_serialize(self.tree)
        level = self.zlib_level if self.zlib_level is not None else 9
        blob = zlib.compress(payload, level)
        tmp = dst + '.tmp'
        with open(tmp, 'wb') as f:
            f.write(blob)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, dst)
        _log('save', f'{dst}: {len(blob)} B RAW (гейты ПРОПУЩЕНЫ, zlib '
                      f'{level}) — канон-регресс/дебаг')
        return len(blob)

    def resolve_refs_of(self, tree: dict) -> list:
        """Резолв произвольного дерева (snapshot базы для delta-гейта
        ссылок на save; без мутации GraftTree)."""
        keep = self.tree
        try:
            self.tree = tree
            return self.resolve_refs()
        finally:
            self.tree = keep


USAGE = ('usage: bpt_graft.py gates BASE.bpt PATCHED.bpt\n'
         '         # standalone-гейты на любом артефакте (включая '
         'самодельные\n'
         '         # патчи в обход модуля): delta-validate + form-lint + '
         'резолвер\n'
         '         # ссылок PATCHED против базы BASE; path-diff не '
         'проверяется\n'
         '         # (intent-декларации у внешнего патча нет — это и есть\n'
         '         # сигнал писать через graft)\n'
         '       bpt_graft.py patch SRC.bpt DST.bpt [ОПЕРАЦИИ…] '
         '[--repro CANON.bpt]\n'
         '         # микро-патч без файла-скрипта (1-3 операции; полный '
         'save-гейт).\n'
         '         # ОПЕРАЦИИ (повторяемые):\n'
         '         #   --sv NODE.KEY=VALUE      sv_set по Name узла '
         '(VALUE — всё после первого =)\n'
         '         #   --sv-replace KEY=OLD::NEW  sv_replace по ключу '
         '(без контекста, expect=N обязателен)\n'
         '         #                             форма: '
         'KEY=OLD::NEW:EXPECT\n'
         '         #   --declare VAR[:TYPE[:multi]]   объявить переменную '
         '(TYPE=string дефолт; multi -> Multiple=1)\n'
         '         #   --expose NODE.FIELD       ReturnFields+манифест '
         'пары (auto-шейп)\n'
         '         #   --gen KEY=VALUE           Values во все генераторы '
         '(кроме --skip-tpl)\n'
         '         #   --skip-tpl ID[,ID…]       TemplateId генераторов, '
         'которые не трогать\n'
         'exit: 0 ok | 1 находки | 2 usage')


def _cli_gates(base_path: str, patched_path: str) -> int:
    base = GraftTree.load(base_path)
    patched = GraftTree.load(patched_path)
    errors = []
    warnings = []
    errors += [f'[VAL ERROR] {x}' for x in patched.delta_validate(base.base_findings)]
    fl_err, fl_warn = patched.form_lint()
    errors += fl_err
    warnings += [f'[FL WARN] {x[13:]}' for x in fl_warn]
    base_refs = base.resolve_refs()
    errors += [r for r in patched.resolve_refs() if r not in base_refs]
    for x in errors + warnings:
        print(x)
    # асимметрия аудита: WARN не рушит exit (save на WARN лишь логирует) —
    # exit 1 только ошибки/новые refs
    print(f'[gates] {patched_path}: {len(errors)} ошибок / {len(warnings)} '
          f'WARN (база {base_path}: {len(base.base_findings[0])} err / '
          f'{len(base_refs)} refs)')
    return 1 if errors else 0


def _cli_patch(args: list) -> int:
    """Микро-патч: SRC DST + операции-флаги; полный save-контракт.
    Каждая операция попадает в intent-лог (гейт path-diff видит их)."""
    positional, sv, svr, decl, expo, gen = [], [], [], [], [], []
    skip_tpl, repro = [], None
    specs = {'--sv': sv, '--sv-replace': svr, '--declare': decl,
             '--expose': expo, '--gen': gen}
    it = iter(args)
    while True:
        a = next(it, None)
        if a is None:
            break
        if a in specs:
            specs[a].append(next(it, None))
        elif a == '--skip-tpl':
            skip_tpl += [x for x in (next(it, '') or '').split(',') if x]
        elif a == '--repro':
            repro = next(it, None)
        elif a.startswith('--'):
            print(f'неизвестный флаг {a}', file=sys.stderr)
            print(USAGE, file=sys.stderr)
            return 2
        elif len(positional) < 2:
            positional.append(a)
        else:
            print(USAGE, file=sys.stderr)
            return 2
    if len(positional) != 2 or None in (sv + svr + decl + expo + gen) \
            or not (sv or svr or decl or expo or gen):
        print(USAGE, file=sys.stderr)
        return 2
    src, dst = positional
    gt = GraftTree.load(src)
    for spec in sv:
        node_name, key_val = spec.split('.', 1)
        key, value = key_val.split('=', 1)
        gt.sv_set(gt.locate(name=node_name, expect=1), key, value)
    for spec in svr:
        key, rest = spec.split('=', 1)
        old, new_expect = rest.rsplit('::', 1)
        new, expect = new_expect.rsplit(':', 1)
        gt.sv_replace(key, old, new, expect=int(expect))
    for spec in decl:
        parts = spec.split(':')
        var = parts[0]
        vtype = parts[1] if len(parts) > 1 and parts[1] else 'string'
        multi = len(parts) > 2 and parts[2] == 'multi'
        entry = {'Type': vtype}
        if multi:
            entry.update(Multiple='1')
        gt.declare('VARIABLES', {var: entry})
    for spec in expo:
        node_name, field = spec.split('.', 1)
        gt.expose_field(gt.locate(name=node_name, expect=1), field)
    if gen:
        gt.generators_values({k: v for k, _, v in
                              (g.partition('=') for g in gen)},
                             skip_tpl=skip_tpl)
    n = gt.save(dst, repro=repro)
    print(f'{dst}: {n} B, {len(gt.intent)} intent-записей')
    return 0


def main() -> None:
    args = sys.argv[1:]
    # exit-контракт CLI: битый/чужой файл — диагностика в stderr + exit 1,
    # не traceback (аудит: load/CLI traceback)
    if args[:1] == ['gates']:
        if len(args) != 3:
            print(USAGE, file=sys.stderr)
            sys.exit(2)
        try:
            sys.exit(_cli_gates(args[1], args[2]))
        except (GraftError, OSError, zlib.error, ValueError) as e:
            print(f'GATES FAILED: {e}', file=sys.stderr)
            sys.exit(1)
    if args[:1] == ['patch']:
        try:
            sys.exit(_cli_patch(args[1:]))
        except (GraftError, OSError, zlib.error, ValueError) as e:
            print(f'PATCH FAILED: {e}', file=sys.stderr)
            sys.exit(1)
    print(USAGE, file=sys.stderr)
    sys.exit(2)


if __name__ == '__main__':
    main()

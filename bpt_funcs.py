"""bpt_funcs.py — реестр функций калькулятора выражений БП + токенайзер.

Канон: курс dev.1c-bitrix.ru «Бизнес-процессы» (COURSE_ID=57), урок 4912
«Функции калькулятора выражений», выборка 2026-10-02 (страница обновлена
22.02.2026). 40 функций (в плане волны 4 фигурировало 38 — живая выборка
даёт 40; расхождение зафиксировано здесь, реестр следует выборке).
Версии — версии модуля bizproc (не портала): WARN FN-03 сверяет их с
профилем validate(portal='box@X.Y'), cloud-профиль проверки пропускает.

Корпус-промер (12 эталонов, разовый tmp-скрипт, 2026-10-02):

    {{=}}-тела     FN: dateadd ×5 (notify-history-canonical, tour-package, date-window-branch),
                   datediff ×1 (notify-history-canonical), if ×1 (date-window-branch);
                   corpus-only FN = НЕТ (doc=False-записей не нужно)
    операторы      '=' ×12; '=='/'!='/'&&'/'||'/'<>'/and/or/not = 0;
                   '%' ×1 — ложный след: '%d' внутри строкового литерала
                   datediff (оператором не является — токенайзер обязан
                   резать литералы, FN-09 считает только op-токены)
    dateadd-юниты  d ×1, day ×2 — известные; 'm'/unknown = 0
    голые =expr    SV с функциями вне {{=}}: 0 сайтов

Политика severity (ограничение владельца, план волны 4): FN вне
документации И вне корпуса → ERROR; corpus-only → реестр-запись doc=False
и WARN при использовании. Корпус corpus-only пуст — все UNKNOWN-имена
сегодня ERROR.

Zero-dep: только данные и чистый Python. Правила (FN-01…FN-09) живут в
bpt_build.expr_lint; этот модуль — источник канона и структура выражений.
"""

# --- реестр: name -> (arity=(min, max|None), since, doc, notes) ---------------
# arity по синтаксису урока 4912; None в max = вариадик. since = версия модуля
# bizproc, с которой функция доступна (None = всегда). doc = есть в уроке 4912.
FUNCS = {
    # -- дата/время --
    'abs':              {'arity': (1, 1), 'since': None, 'doc': True,
                         'notes': 'модуль числа'},
    'dateadd':          {'arity': (2, 2), 'since': None, 'doc': True,
                         'notes': "юниты INTERVAL_UNITS['dateadd']"},
    'datediff':         {'arity': (3, 3), 'since': None, 'doc': True,
                         'notes': "формат PHP DateInterval ('%a','%d',…)"},
    'date':             {'arity': (1, 2), 'since': None, 'doc': True,
                         'notes': 'возвращает СТРОКУ — арифметика запрещена '
                                  '(FN-07); дата-параметр опционален'},
    'workdateadd':      {'arity': (2, 3), 'since': '19.0.0', 'doc': True,
                         'notes': "юниты INTERVAL_UNITS['workdateadd']; "
                                  '3-й параметр — пользователь'},
    'addworkdays':      {'arity': (2, 2), 'since': None, 'doc': True,
                         'notes': 'N рабочих дней по календарю'},
    'isworkday':        {'arity': (1, 2), 'since': '19.0.0', 'doc': True,
                         'notes': '2-й параметр — пользователь'},
    'isworktime':       {'arity': (1, 2), 'since': '19.0.0', 'doc': True,
                         'notes': 'для Дата/Время'},
    'touserdate':       {'arity': (1, 2), 'since': None, 'doc': True,
                         'notes': 'серверное время -> таймзона пользователя'},
    'getuserdateoffset': {'arity': (1, 1), 'since': None, 'doc': True,
                          'notes': 'смещение зоны в секундах'},
    # -- логика --
    'if':               {'arity': (3, 3), 'since': None, 'doc': True,
                         'notes': 'условный оператор'},
    # and/or/not документированы в таблице операторов, но вызываются и как
    # функции (пример урока: if(or(a>2, b<10), …)) — в реестре, чтобы
    # _EXPR_FUNC_RE и FN-01 их знали
    'or':               {'arity': (2, None), 'since': None, 'doc': True,
                         'notes': 'оператор; допускает вызов or(a, b, …)'},
    'and':              {'arity': (2, None), 'since': None, 'doc': True,
                         'notes': 'оператор; допускает вызов and(a, b, …)'},
    'not':              {'arity': (1, 1), 'since': None, 'doc': True,
                         'notes': 'отрицание'},
    # -- числа --
    'intval':           {'arity': (1, 1), 'since': None, 'doc': True,
                         'notes': 'PHP-семантика ведущего числа'},
    'floatval':         {'arity': (1, 1), 'since': None, 'doc': True,
                         'notes': 'money-поля приходят строкой N|CCY (§4)'},
    'numberformat':     {'arity': (1, 4), 'since': None, 'doc': True,
                         'notes': 'PHP number_format: 1/2/4 аргумента '
                                  '(3 — невалидно); канон урока — БЕЗ '
                                  'подчёркивания (number_format — ловушка)'},
    'min':              {'arity': (1, None), 'since': None, 'doc': True,
                         'notes': 'вариадик'},
    'max':              {'arity': (1, None), 'since': None, 'doc': True,
                         'notes': 'вариадик'},
    'rand':             {'arity': (0, 2), 'since': None, 'doc': True,
                         'notes': 'вызов без параметров легитимен'},
    'round':            {'arity': (1, 2), 'since': None, 'doc': True,
                         'notes': 'в Multiple-приёмник — прочерк (§7)'},
    'ceil':             {'arity': (1, 1), 'since': None, 'doc': True,
                         'notes': 'округление вверх'},
    'floor':            {'arity': (1, 1), 'since': None, 'doc': True,
                         'notes': 'округление вниз'},
    # -- строки --
    'substr':           {'arity': (2, 3), 'since': None, 'doc': True,
                         'notes': 'позиции с 0'},
    'strpos':           {'arity': (2, 3), 'since': None, 'doc': True,
                         'notes': 'позиция первого вхождения'},
    'strlen':           {'arity': (1, 1), 'since': None, 'doc': True,
                         'notes': 'длина строки'},
    'implode':          {'arity': (2, 2), 'since': None, 'doc': True,
                         'notes': 'разделитель, массив'},
    'explode':          {'arity': (2, 2), 'since': None, 'doc': True,
                         'notes': 'разделитель, строка; писать в ДРУГУЮ '
                                  'Multiple-переменную (§7)'},
    'randstring':       {'arity': (1, 1), 'since': None, 'doc': True,
                         'notes': 'случайная строка заданной длины'},
    'merge':            {'arity': (2, None), 'since': None, 'doc': True,
                         'notes': 'array_merge, вариадик'},
    'urlencode':        {'arity': (1, 1), 'since': None, 'doc': True,
                         'notes': 'URL-кодирование'},
    'strtolower':       {'arity': (1, 1), 'since': '21.200.0', 'doc': True,
                         'notes': ''},
    'strtoupper':       {'arity': (1, 1), 'since': '21.200.0', 'doc': True,
                         'notes': ''},
    'ucwords':          {'arity': (1, 1), 'since': '21.200.0', 'doc': True,
                         'notes': ''},
    'ucfirst':          {'arity': (1, 1), 'since': '21.200.0', 'doc': True,
                         'notes': ''},
    'strtotime':        {'arity': (1, 1), 'since': '21.300.0', 'doc': True,
                         'notes': 'парсит ТОЛЬКО английские даты (FN-08)'},
    'locdate':          {'arity': (1, 2), 'since': '21.300.0', 'doc': True,
                         'notes': 'date() с учётом языка портала; возвращает '
                                  'СТРОКУ (FN-07)'},
    # -- множественные значения --
    'shuffle':          {'arity': (1, 1), 'since': '21.500.0', 'doc': True,
                         'notes': 'перемешать Multiple'},
    'firstvalue':       {'arity': (1, 1), 'since': '21.500.0', 'doc': True,
                         'notes': 'первое значение Multiple'},
    'swirl':            {'arity': (1, 1), 'since': '21.500.0', 'doc': True,
                         'notes': 'первое значение — в конец'},
    # -- разное --
    'getdocumenturl':   {'arity': (0, 2), 'since': '22.200.0', 'doc': True,
                         'notes': "формат 'bb'/'html', полная ссылка 0/1"},
    'trim':             {'arity': (1, 1), 'since': '22.300.0', 'doc': True,
                         'notes': 'пробелы по краям'},
    'settime':          {'arity': (3, 3), 'since': '22.500.0', 'doc': True,
                         'notes': 'дата, часы, минуты'},
}

# Известные ловушки-алиасы: имя пишут с подчёркиванием, канон урока — без.
# В реестр НЕ входят (corpus-only пуст, политика владельца: ERROR);
# используются только для fix-hint в сообщении FN-01.
NEAR_MISS = {
    'number_format': 'numberformat',
}

# --- интервал-юниты (урок 4912: регистр не важен, минус = вычитание) ----------
INTERVAL_UNITS = {
    'dateadd': frozenset(
        'y year years m month months d day days h hour hours '
        'i min minute minutes s second seconds'.split()),
    'workdateadd': frozenset(
        'd day days h hour hours i min minute minutes'.split()),
}

# --- операторы (таблица урока 4912) --------------------------------------------
CANON_OPERATORS = frozenset(
    '+ - * / = <> < > <= >= & ^ % and or not ( )'.split())

# Недокументированные двойные операторы: символы -> каноничный эквивалент
# (fix-hint для FN-04). В corpora — 0 сайтов (промер 2026-10-02).
NONCANON_OPERATORS = {'==': '=', '!=': '<>', '&&': 'and', '||': 'or'}


# --- токенайзер {{=}}-тел ------------------------------------------------------
# Наивный, но устойчивый к вложенным вызовам и запятым в строковых литералах.
# Владеет ТОЛЬКО структурой тел {{=…}}; brace-balancing/unclosed на уровне
# строки остаётся за bpt_build._lint_expr (два перекрывающихся сканера не
# плодим — план волны 4, T4).

_TOKEN_OPS = ('==', '!=', '<=', '>=', '<>', '&&', '||',
              '<', '>', '=', '+', '-', '*', '/', '&', '^', '%', '(', ')', ',')


def tokenize(body: str) -> list:
    """Тело {{=…}} (без обёртки) -> список (kind, text, start).

    kinds: 'ident', 'num', 'str', 'ref', 'op', 'bad'.
      - 'str'  — литерал в одинарных/двойных кавычка, экранирование \
              одноимённой кавычкой; запятые/скобки внутри — НЕ токены;
      - 'ref'  — {=Ns:Field} одиночные фигурные целиком (внутрь не лезем);
      - 'bad'  — незакрытый литерал (обрыв на краю тела).
    """
    toks, i, n = [], 0, len(body)
    while i < n:
        c = body[i]
        if c.isspace():
            i += 1
            continue
        if body.startswith('{=', i):
            close = body.find('}', i)
            if close == -1:
                toks.append(('ref', body[i:], i))
                break
            toks.append(('ref', body[i:close + 1], i))
            i = close + 1
            continue
        if c in '\'"':
            j, buf = i + 1, [c]
            while j < n:
                if body[j] == '\\' and j + 1 < n and body[j + 1] == c:
                    buf.append(body[j:j + 2])
                    j += 2
                    continue
                if body[j] == c:
                    break
                buf.append(body[j])
                j += 1
            if j >= n:  # незакрытый литерал
                toks.append(('bad', ''.join(buf), i))
                break
            buf.append(c)
            toks.append(('str', ''.join(buf), i))
            i = j + 1
            continue
        if c.isdigit() or (c == '.' and i + 1 < n and body[i + 1].isdigit()):
            j = i + 1
            while j < n and (body[j].isdigit() or body[j] == '.'):
                j += 1
            toks.append(('num', body[i:j], i))
            i = j
            continue
        if c.isalpha() or c == '_':
            j = i + 1
            while j < n and (body[j].isalnum() or body[j] == '_'):
                j += 1
            toks.append(('ident', body[i:j], i))
            i = j
            continue
        for op in _TOKEN_OPS:
            if body.startswith(op, i):
                toks.append(('op', op, i))
                i += len(op)
                break
        else:
            toks.append(('op', c, i))  # неизвестный символ — как op-мусор
            i += 1
    return toks


def calls(toks: list) -> list:
    """Список вызовов из токенов: [(name, argc, i_open, i_close), …].

    argc = топ-уровневые запятые + 1, если внутри есть хоть один
    value-токен (ident/num/str/ref); пустые скобки = 0. Вложенные вызовы
    — один аргумент. i_open — индекс токена '(', i_close — ')' или None,
    если скобка не закрылась (арность тогда не проверяем — синтакс-ошибка
    другого уровня).
    """
    out = []
    for k, (kind, text, start) in enumerate(toks):
        if kind != 'ident' or k + 1 >= len(toks) \
                or toks[k + 1][:2] != ('op', '('):
            continue
        depth, commas, has_value, i_close = 0, 0, False, None
        k2 = k + 1
        while k2 < len(toks):
            kind2, text2, _ = toks[k2]
            if kind2 == 'op' and text2 == '(':
                depth += 1
            elif kind2 == 'op' and text2 == ')':
                depth -= 1
                if depth == 0:
                    i_close = k2
                    break
            elif depth == 1 and kind2 == 'op' and text2 == ',':
                commas += 1
            elif depth >= 1 and kind2 != 'op':
                has_value = True
            k2 += 1
        argc = (commas + 1) if has_value else 0
        out.append((text, argc, k + 1, i_close))
    return out

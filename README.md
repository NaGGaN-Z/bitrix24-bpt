# bpt — тулчейн шаблонов бизнес-процессов Битрикс24 (.bpt as code)

Генерация, разбор и верификация шаблонов бизнес-процессов Битрикс24
**кодом, без визуального дизайнера**. Python 3.10+, зависимостей нет.

Три инструмента:

| Файл | Роль |
|---|---|
| `bpt_export.py` | REST-выгрузка всех шаблонов портала (`bizproc.workflow.template.list`) + парсер .bpt → инвентарь активностей (JSON + summary) |
| `bpt_serialize.py` | Byte-exact PHP-serialize writer для .bpt; CLI roundtrip-тест (`roundtrip`, `--all`) |
| `bpt_build.py` | Конструктор шаблонов: 25+ билдеров активностей, каркас, CLI `make/check` |

Документация:

- `docs/FORMAT.md` — формат контейнера `zlib(PHP-serialize)`, правила
  сериализации, байт-точность
- `docs/ACTIVITIES.md` — справочник 26 классов активностей нового
  дизайнера: канонические формы Properties, дети, гочи, билдеры

## Быстрый старт

```python
import bpt_build as bb

tree = bb.scaffold(
    children=[
        bb.terminate_others(),                      # дедупликатор инстансов
        bb.if_else([
            ('Согласовано', ['status', '=', 'DONE'], [
                bb.update_dynamic(1040, {'PRICE': '{{=Document:PRICE}}'},
                                  filter_conditions=[('ID', '{=Document:SVC_ID}')]),
                bb.change_stage('NEW_STAGE'),
            ]),
            ('Иначе', None, [                        # else-ветка
                bb.im_notify(['{=Document:ASSIGNED_BY_ID}'], 'Вернулось'),
            ]),
        ]),
    ],
    variables={'status': bb.variable('status', 'string', 'Статус')},
    doc_fields={...},                                # из эталонного экспорта
)
bb.save(tree, 'dispatcher.bpt')                      # самопроверка inside
```

CLI:

```bash
python3 bpt_build.py make tree.json out.bpt   # json-дерево -> .bpt
python3 bpt_build.py check out.bpt tree.json  # roundtrip-проверка
python3 bpt_serialize.py --all dir/           # гейт: все .bpt byte-exact
python3 bpt_export.py WEBHOOK_URL out_dir/    # инвентарь портала
```

## Деплой на портал

`bizproc.workflow.template.add` по REST с вебхука не работает
(`ACCESS_DENIED: Application context required`) — деплой шаблона:

1. Импорт `.bpt` через UI (Автоматизация → Бизнес-процессы → Импорт).
   Повторный импорт = upsert по имени шаблона.
2. Фолбэк запуска: `bizproc.workflow.start` по REST.

Для обновления существующего шаблона через REST привязка к приложению
не переносится — обновляйте тем же способом, которым шаблон создан.

## Гейт качества

Все `.bpt` проекта обязаны проходить byte-exact roundtrip:

```bash
python3 bpt_serialize.py --all . && echo OK
```

`semantic-ok` — минимально допустимый статус, `BYTE-EXACT` — цель.
Первый расходящийся байт печатается с контекстом.

## Эталоны

Формы активностей сняты с реальных экспортов; сами эталонные `.bpt`
в репозиторий не входят (`etalons/` в `.gitignore`): это шаблоны
конкретных порталов. Для нового портала снимите свои эталоны:
UI-экспорт любого шаблона → `bpt_serialize.py roundtrip` →
используйте как источник канонических форм.

## Возможные применения

- массовые миграции шаблонов между порталами;
- reverse-engineering и аудит чужих БП (инвентарь активностей);
- сложные маршрутизаторы/каскады (циклы по элементам СП,
  переменно-управляемое ветвление), которые мучительно кликать руками;
- шаблоны в git: диффы, ревью, откат.

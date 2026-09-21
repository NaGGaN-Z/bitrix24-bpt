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

Два варианта, у каждого своя ниша.

### Вариант 1 — ручной: импорт .bpt через UI

В списке шаблонов бизнес-процессов нужного типа документа — «Импорт»,
выбираете файл. Повторный импорт шаблона с тем же именем = upsert.

- работает на любом портале, ничего не разворачивая;
- шаблон не привязан к приложению — управляется из UI свободно;
- минус: ручной шаг; enum-поля требуют проставления значений в
  дизайнере после импорта (см. `docs/ACTIVITIES.md`, «Enum-поля»).

### Вариант 2 — REST: `bizproc.workflow.template.add`

Метод принимает наш файл напрямую:

```json
{
  "DOCUMENT_TYPE": ["crm", "CCrmDocumentDeal", "DEAL"],
  "NAME": "Диспетчер",
  "AUTO_EXECUTE": 1,
  "TEMPLATE_DATA": ["dispatcher.bpt", "<base64 содержимого .bpt>"]
}
```

- работает **только из контекста приложения** (локальное/публичное
  REST-приложение с oauth-токеном, scope `bizproc`); с входящего вебхука —
  `ACCESS_DENIED: Application context required`;
- `DOCUMENT_TYPE` — тройка строк: сделки `["crm","CCrmDocumentDeal","DEAL"]`,
  смарт-процессы `["crm","Bitrix\\Crm\\Integration\\BizProc\\Document\\Dynamic","DYNAMIC_<etid>"]`,
  списки `["lists","Bitrix\\Lists\\BizprocDocumentLists","iblock_<id>"]` и т.п.;
- `AUTO_EXECUTE`: 0 — нет, 1 — при создании, 2 — при изменении,
  3 — при создании и изменении;
- шаблон **привязывается к приложению**: последующие
  `template.update` / `template.delete` — только из контекста того же
  приложения; вебхук и UI-импорт его не перезапишут;
- запуск процесса `bizproc.workflow.start` работает и с вебхука.

Что выбрать: разовая работа с клиентским порталом без приложения —
вариант 1; свой контур с приложением и повторяемые деплои — вариант 2.

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

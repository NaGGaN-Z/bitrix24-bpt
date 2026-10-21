# Changelog

Формат: краткие волновые сводки. Публичный репозиторий ведёт историю
от сан-релиза (см. git-историю приватного канона для деталей волн).

## [Unreleased] — сан-релиз

- **serialize** — канонический PHP-сериализатор/десериализатор дерева
  шаблона + zlib-канон воспроизведения блобов (16 эталонов
  byte-exact, авто-детект уровня сжатия 9..1).
- **export** — инвентаризация и экспорт `.bpt` в Markdown
  (bpt_export.py): дерево активностей, переменные, условия;
  diff-режим пар шаблонов (structural / props / renames);
  probe-режим журнала сущности по webhook.
- **build** — валидатор шаблона (bb_validate, FN/E/W-диагностика) и
  генератор `.bpt` из python-DSL (activity-дерево → serialize).
- **graft** — якорная хирургия живых шаблонов: insert/replace/remove
  активностей по anchor-путям, var_rename с anchor-aware диффом
  переименований, delta_validate (FN-10 контроль unsafe-форм).
- **diagram** — Mermaid-рендер структуры шаблона: python-слой
  (bpt_diagram.py) и браузерный runtime (bpt_diagram_runtime.js).
- **forms** — платформенный реестр форм полей (bpt_graft_forms.py,
  генерация харвестом из корпуса, ручная правка запрещена).
- **tests** — zero-dep жгуты (239 проверок, двойной запуск: прямой
  python3 + pytest через conftest), skip-if-absent деградация
  корпусных кейсов, repo-lint (запрет сырой хирургии вне канона).

# ADR-020: Post-Entry Delta Suffix Setting

## Context

После `ADR-019` confirmation после записи использует тот же day-report, что и `/today`, и умеет показывать вклад новой записи.

Следующий шаг - дать пользователю отдельную настройку, показывать ли в конце строк суффикс вида:

- `(+7.6 г)`
- `(+250.0 мл)`

Это изменение касается пользовательских settings и рендеринга confirmation, но не меняет factual layer, goals или snapshot contract.

## Decision

### 1. Новая user preference

В `user_summary_preferences` добавляется отдельный флаг:

- `show_post_entry_delta_suffix`

По умолчанию:

- `show_post_entry_delta_suffix = true`

### 2. Scope настройки

Настройка влияет только на confirmation после успешной записи еды или воды.

Она не влияет на:

- `/today`
- factual summary
- progress against goals
- наличие delta-bar подсветки `▓` в режиме `bars`

### 3. Поведение

Если `show_post_entry_delta_suffix = true`, confirmation показывает в конце затронутых строк суффикс:

- `(+delta unit)`

Если `show_post_entry_delta_suffix = false`, этот суффикс скрывается.

При этом:

- в `text` mode строка остаётся без хвоста;
- в `bars` mode bar still keeps `█/▓/░`, но без текстового суффикса справа.

## Consequences

- Пользователь получает отдельный UX-toggle для текстового отображения добавочного вклада записи.
- Поведение остаётся простым: настройка управляет только suffix-layer, а не самой delta semantics.

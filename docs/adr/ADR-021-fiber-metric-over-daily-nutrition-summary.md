# ADR-021: Fiber Metric Over Daily Nutrition Summary

## Context

В системе уже есть:

- nutrition estimation contract и item-level metric storage;
- factual daily nutrition summary по КБЖУ;
- goals, daily snapshots и progress layer по nutrition metrics;
- `/today`, `/goal` и confirmation after write для текущих поддержанных метрик.

Следующий шаг - добавить `fiber` как ещё одну поддержанную nutrition metric, не вводя для неё отдельный слой, как это было сделано для воды.

## Decision

### 1. Scope шага

На этом шаге `fiber` добавляется как пятая обязательная nutrition metric food-layer:

- `calories`
- `protein`
- `fat`
- `carbs`
- `fiber`

`fiber` использует ту же единицу измерения, что и БЖУ:

- `g`

### 2. Nutrition contract

`fiber` становится частью обязательного nutrition estimation contract.

Это означает:

- nutrition provider должен возвращать `fiber` для каждого food item;
- item-level metric set считается полным только при наличии `fiber` вместе с КБЖУ;
- backfill неполных food entries теперь тоже ориентируется на набор КБЖУ + `fiber`.

### 3. Daily factual layer

`daily nutrition summary` расширяется на `fiber`.

`fiber` агрегируется тем же способом, что и КБЖУ:

- на уровне item;
- на уровне entry;
- на уровне day totals.

Если у food entry хотя бы у одного item нет `fiber`, вся entry исключается из day totals как неполная.

### 4. User preferences и goals

В `user_summary_preferences` добавляется:

- `show_fiber`

В `user_goal_preferences` добавляется:

- `fiber_goal`

В `daily_goal_snapshots` добавляется:

- `fiber_goal`

Default state:

- `show_fiber = true`
- `fiber_goal = 25`

### 5. Read-side и write-side rendering

`fiber` добавляется в:

- `/today`
- `/goal`
- confirmation after write
- `text` mode
- `bars` mode

В user-facing rendering используется короткая метка:

- `Кл`

### 6. Supported metric definition

`supported_metrics` получает новую поддержанную метрику:

- `code = fiber`
- `name = Fiber`
- `unit = g`

## Consequences

- Nutrition factual layer становится богаче, но остаётся симметричным: `fiber` живёт в том же food-layer, что и КБЖУ.
- Goals и progress по `fiber` не требуют отдельной архитектуры.
- Следующие шаги могут так же добавлять новые food metrics, если они соответствуют этому же contract.

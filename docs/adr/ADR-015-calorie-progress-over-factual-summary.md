# ADR-015: Calorie Goal Progress as a Read-Side Layer Over Daily Summary

## Context

На предыдущем шаге в системе появился отдельный snapshot-layer для пользовательских дневных calorie goals:

- текущее preference-state хранится в `user_goal_preferences`;
- историческая цель дня хранится в `daily_goal_snapshots`.

При этом `/today` по-прежнему показывает только factual nutrition summary без сравнения с целью.

Следующий шаг - добавить минимальный progress against goals так, чтобы:

- factual layer оставался отдельным и неизменным;
- progress вычислялся поверх factual summary и daily goal snapshot;
- historical смысл дня оставался привязан к уже зафиксированному goal snapshot;
- UI оставался минимальным и ограниченным калориями.

## Decision

### 1. Progress не является частью factual summary

`daily nutrition summary` продолжает описывать только факт:

- какие `food entries` вошли в день;
- какие totals получились по обязательным nutrition metrics;
- есть ли excluded entries.

Goal progress считается отдельным read-side слоем и не встраивается в factual summary model.

### 2. Первый поддержанный progress

На первом шаге поддерживается только `calorie progress`.

Он вычисляется как сравнение:

- `daily_nutrition_summary.totals.calories`;
- `daily_goal_snapshot.calorie_goal`.

Progress по `protein`, `fat` и `carbs` на этом шаге не добавляется.

### 3. Источник goal для progress

Для progress используется только `daily_goal_snapshot` конкретного пищевого дня.

Текущее значение из `user_goal_preferences` не должно напрямую участвовать в сравнении дня, если для этого дня уже существует snapshot.

Если snapshot для дня ещё не создан, `/today` может получить его через существующий lazy `get_or_create` use case.

### 4. Поведение `/today`

`/today` продолжает:

- определять текущий пищевой день по `timezone` пользователя и `nutrition_day_start_hour`;
- строить factual summary как раньше;
- учитывать `show_calories`, `show_protein`, `show_fat`, `show_carbs`.

Дополнительно:

- если `show_calories = true` и для дня доступен goal snapshot, строка калорий показывает и факт, и цель;
- если goal snapshot нет, строка калорий остаётся factual-only;
- если `show_calories = false`, progress по goal не показывается, даже если snapshot существует.

### 5. Формат первого шага

На первом шаге строка калорий в `/today` имеет один из двух форматов:

- без цели: `К: 540.0 ккал`
- с целью: `К: 540.0 / 1800 ккал`

Более богатый UI, например remaining calories, percent, status badges или coaching, на этом шаге не добавляется.

## Consequences

- Progress against goals появляется как отдельный слой поверх уже готовых factual и snapshot layers.
- `/today` получает минимально полезное сравнение факта с целью без пересмотра агрегирующего контракта.
- Следующий шаг сможет отдельно развивать richer goal UI без изменения source-of-truth модели дня.

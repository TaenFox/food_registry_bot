# ADR-018: Daily Water Summary And Goal

## Context

После `ADR-017` goals и progress уже работают для `calories`, `protein`, `fat`, `carbs` поверх factual `daily nutrition summary`.

Следующий шаг - добавить воду так, чтобы:

- не смешивать её с food nutrition totals;
- использовать ту же семантику пищевого дня;
- поддержать отдельный factual day layer и отдельный `water_goal`;
- показать воду в `/today` и в режиме `bars`.

## Decision

### 1. Daily factual layer по воде

Вода добавляется как отдельный factual слой `daily water summary`, а не как часть `daily nutrition summary`.

Он:

- выбирает только `entries` типа `water`;
- использует те же границы пищевого дня, что и nutrition summary:
  - `timezone` пользователя;
  - `nutrition_day_start_hour`;
- суммирует воду в миллилитрах;
- считает число включённых и исключённых water entries.

### 2. Правило включения water entries

В daily water summary включается только запись воды, у которой:

- все items имеют `name = water`;
- все items имеют `unit = ml`;
- у всех items есть положительное `quantity`.

Если запись этим условиям не соответствует, она исключается из factual water totals целиком.

### 3. User preferences и historical snapshots

В `user_summary_preferences` добавляется:

- `show_water`

В `user_goal_preferences` добавляется:

- `water_goal`

В `daily_goal_snapshots` добавляется:

- `water_goal`

Default state:

- `show_water = true`
- `water_goal = 2000`

`daily goal snapshot` по воде фиксируется по тем же правилам, что и для nutrition goals: лениво создаётся для конкретного пищевого дня и потом автоматически не пересчитывается.

### 4. Progress layer

Progress по воде считается отдельно от nutrition totals как:

- `daily_water_summary.total_ml` ↔ `daily_goal_snapshot.water_goal`

Этот progress включается в существующий read-side слой `/today`.

### 5. Поведение `/today`

`/today` теперь агрегирует два factual слоя в одних и тех же границах пищевого дня:

- daily nutrition summary;
- daily water summary.

В ответе:

- вода показывается строкой `В: факт / цель мл` в `text` mode;
- вода показывается строкой `В <бар> <процент>% <факт>/<цель> мл` в `bars` mode;
- если за день нет ни еды, ни воды, возвращается `Сегодня пока нет сохранённых записей.`

### 6. Поведение `/settings` и `/goal`

`/settings` получает настройку отображения воды:

- `Вода: on/off`

`/goal` получает water goal:

- просмотр текущего значения и snapshot для воды;
- команда установки вида `/goal water 2000`

## Consequences

- Вода получает свой дневной factual слой, не размывая food nutrition model.
- Семантика пищевого дня остаётся единой для еды, воды и goal snapshots.
- `/today` становится симметричным read-side для nutrition и воды.
- Следующим отдельным шагом можно строить аналогичный слой для других метрик, например `fiber`, если для них появится полноценный factual contract.

# ADR-014: Daily Calorie Goal Snapshots Over the Factual Nutrition Day

## Context

`daily nutrition summary` уже реализован как отдельный factual layer поверх сохранённых `food entries` и `entry_item_metrics`. Также в системе уже есть:

- `user.timezone`;
- `user_summary_preferences.nutrition_day_start_hour`;
- `/today` как thin layer над factual summary.

Следующий шаг - начать отдельный слой пользовательских nutrition goals так, чтобы:

- goals строились поверх factual layer, а не заменяли его;
- historical дни не меняли смысл после изменения текущей цели пользователя;
- goal snapshot был привязан к тому же `nutrition-day`, что и factual summary;
- первый шаг оставался минимальным и не требовал rich UI.

## Decision

### 1. Первый поддержанный goal

На первом шаге система поддерживает только один настраиваемый goal:

- `calorie_goal` - дневная цель по калориям в килокалориях.

Goals по `protein`, `fat` и `carbs` на этом шаге не добавляются.

### 2. Что остаётся factual layer

Factual layer не меняется и продолжает отвечать только за фактически сохранённые данные дня:

- выбор `food entries` по `timezone` пользователя и `nutrition_day_start_hour`;
- проверку полноты обязательных nutrition metrics;
- day totals по `calories`, `protein`, `fat`, `carbs`.

Goal layer не участвует в построении factual summary и не меняет правила агрегации факта.

### 3. Что является user preference

Текущее пользовательское goal-состояние хранится отдельно от summary preferences в таблице `user_goal_preferences`.

На первом шаге она содержит:

- `user_id`;
- `calorie_goal`.

Эта таблица описывает только текущее базовое preference-состояние пользователя и не должна использоваться как источник исторической цели прошлых дней.

### 4. Что является historical day snapshot

Историческая цель дня хранится отдельно в таблице `daily_goal_snapshots`.

На первом шаге каждый snapshot содержит:

- `user_id`;
- `summary_date`;
- `timezone`;
- `nutrition_day_start_hour`;
- `calorie_goal`.

`summary_date` - это локальная дата nutrition-day в той же семантике, что уже использует daily factual summary.

Поля `timezone` и `nutrition_day_start_hour` сохраняются внутри snapshot, чтобы исторический день оставался интерпретируемым даже после последующего изменения пользовательских настроек.

### 5. Когда создаётся daily goal snapshot

На первом шаге snapshot создаётся лениво через отдельный `get_or_create` use case при первом обращении goal-layer к конкретному nutrition-day.

Правила:

- если snapshot для `user_id + summary_date` уже существует, возвращается он;
- если snapshot не существует, но у пользователя нет текущего `calorie_goal`, новый snapshot не создаётся;
- если snapshot не существует и текущий `calorie_goal` задан, создаётся новый snapshot с текущими `timezone` и `nutrition_day_start_hour`.

Это означает, что historical goal не вычисляется повторно из текущего профиля и не меняется автоматически после создания snapshot.

### 6. К какому дню привязывается snapshot

Snapshot всегда привязывается к тому же локальному nutrition-day, который определяется через:

- `user.timezone`;
- `user_summary_preferences.nutrition_day_start_hour`;
- функцию определения `summary_date`, уже используемую daily factual layer.

Иными словами, goal snapshot и factual day summary адресуются одним и тем же `summary_date`, но snapshot дополнительно сохраняет семантику дня внутри своей записи.

### 7. Граница ответственности

`user_goal_preferences` отвечает за текущее preference-состояние:

- какая дневная calorie goal сейчас выбрана пользователем.

`daily_goal_snapshots` отвечают за историческое состояние:

- какая calorie goal была зафиксирована для конкретного nutrition-day.

Factual summary отвечает за факт:

- сколько калорий и других обязательных nutrition metrics было фактически сохранено в этом дне.

Progress against goals - это отдельный следующий слой:

- он сравнивает `daily_nutrition_summary.totals.calories` и `daily_goal_snapshot.calorie_goal`;
- он не обязан появляться в `/today` на этом шаге.

### 8. Минимальный пользовательский entrypoint

На первом шаге вводится минимальная техническая команда для установки calorie goal.

Она должна:

- сохранять текущее значение `calorie_goal` в `user_goal_preferences`;
- не пересчитывать автоматически прошлые snapshot'ы;
- при обращении к текущему дню использовать отдельный goal snapshot use case.

Rich settings UI и сравнение факта с целью откладываются на следующий шаг.

## Consequences

- В системе появляется отдельный, исторически корректный слой user goals поверх factual nutrition day.
- Daily goal больше не зависит только от текущего профиля пользователя.
- Следующий шаг progress against goals можно строить без пересмотра factual summary contract.

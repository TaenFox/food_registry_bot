# ADR-017: Macro Goals Over Daily Nutrition Summary

## Context

В системе уже есть:

- factual `daily nutrition summary` по `calories`, `protein`, `fat`, `carbs`;
- текущие goal preferences;
- historical daily goal snapshots;
- progress and bar rendering для калорий.

Следующий шаг - масштабировать goals на поддерживаемые nutrition metrics текущего factual layer, не смешивая это с ещё не реализованными water/fiber слоями.

## Decision

### 1. Scope шага

На этом шаге daily goals поддерживаются для:

- `calories`
- `protein`
- `fat`
- `carbs`

Goals для `water` и `fiber` пока не добавляются, потому что для них ещё нет симметричного factual day layer, встроенного в `/today`.

### 2. Current goal preferences

`user_goal_preferences` расширяется полями:

- `calorie_goal`
- `protein_goal`
- `fat_goal`
- `carbs_goal`

Если у пользователя ещё нет строки preferences, приложение использует default state:

- `calorie_goal = 1800`
- `protein_goal = 90`
- `fat_goal = 60`
- `carbs_goal = 210`

### 3. Historical day snapshot

`daily_goal_snapshots` расширяется полями:

- `calorie_goal`
- `protein_goal`
- `fat_goal`
- `carbs_goal`

Snapshot продолжает лениво создаваться для конкретного пищевого дня и после создания автоматически не пересчитывается.

### 4. Progress layer

Progress against goals на этом шаге вычисляется отдельно от factual summary как сравнение:

- `summary.totals.calories` ↔ `snapshot.calorie_goal`
- `summary.totals.protein` ↔ `snapshot.protein_goal`
- `summary.totals.fat` ↔ `snapshot.fat_goal`
- `summary.totals.carbs` ↔ `snapshot.carbs_goal`

### 5. Поведение `/today`

Если goal snapshot доступен, `/today` показывает факт против цели для всех включённых показателей:

- в `text` mode: `МЕТКА: факт / цель ед.`
- в `bars` mode: `МЕТКА <бар> <процент>% <факт>/<цель> ед.`

Если goal snapshot недоступен, строка остаётся factual-only, но на этом шаге это практически не используется, потому что preferences имеют default state и snapshot может быть создан лениво.

### 6. Минимальный entrypoint

`/goal` остаётся минимальным техническим entrypoint.

Поддерживаются:

- `/goal` - посмотреть текущие goals и snapshot текущего дня;
- `/goal 1800` - установить `calorie_goal`;
- `/goal protein 90`
- `/goal fat 60`
- `/goal carbs 210`

## Consequences

- Goal model становится согласованной с текущим factual nutrition layer.
- `/today` получает единый progress contract по КБЖУ.
- Следующим шагом можно отдельно строить water goals после появления daily water factual layer.

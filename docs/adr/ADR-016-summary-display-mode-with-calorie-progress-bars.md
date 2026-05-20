# ADR-016: Summary Display Mode With Calorie Progress Bars

## Context

`/today` уже умеет:

- строить factual nutrition summary;
- учитывать `user_summary_preferences`;
- показывать calorie progress against daily goal snapshot.

Следующий шаг - дать пользователю минимальную настройку способа отображения summary, не перепрыгивая через ещё не реализованные зависимости:

- goals по `protein`, `fat` и `carbs` пока не поддерживаются;
- factual layer по `fiber` в `/today` пока не реализован;
- post-entry delta rendering (`было / добавилось / осталось`) пока отсутствует.

## Decision

### 1. Новый preference

В `user_summary_preferences` добавляется `summary_display_mode`.

На этом шаге поддерживаются два значения:

- `text`
- `bars`

По умолчанию используется `text`.

### 2. Scope первого шага

Режим `bars` на этом шаге применяется только к calorie progress в `/today`.

Это означает:

- если у пользователя включён показ калорий;
- и для текущего пищевого дня есть calorie goal snapshot;
- и `summary_display_mode = bars`;

тогда калории показываются progress bar'ом.

Для `protein`, `fat` и `carbs` на этом шаге richer bars не добавляются. Они продолжают показываться обычными строками.

### 3. Поведение `/today`

`/today` продолжает определять текущий пищевой день и строить factual summary как раньше.

Дальше:

- в `text` mode поведение сохраняется как сейчас;
- в `bars` mode калории показываются как progress bar against goal;
- если calorie goal snapshot недоступен, калории показываются в обычном текстовом виде;
- если `show_calories = false`, progress bar не показывается.

### 4. Формат первого progress bar

На первом шаге используется:

- шкала длиной 10 символов внутри `[]`;
- заполненная часть - `█`;
- незаполненная часть - `░`;
- если цель превышена, overflow показывается справа от `]` символами `█`;
- рядом с калориями показываются факт, цель, процент и превышение/остаток в ккал.

Поскольку на этом шаге нет post-entry delta layer, символ `▓` не используется.

## Consequences

- В системе появляется отдельная preference-настройка способа отображения summary.
- Пользователь получает более наглядный calorie progress without changing factual or goal snapshot contracts.
- Следующий шаг сможет расширить bars на другие метрики после появления соответствующих goals.

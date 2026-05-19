# ADR-013: User Summary Preferences for Daily Nutrition Output

## Context

Daily nutrition factual layer уже реализован отдельно от Telegram presentation layer. Следующий шаг - дать пользователю минимальную управляемость тем, что именно показывается в summary-ответе, не смешивая это с goals, analytics и пересчётом источников данных.

На этом шаге нужно:

- ввести минимальную модель пользовательских summary preferences;
- не менять factual aggregation contract;
- реализовать `/settings` как минимальную точку чтения и изменения одной настройки;
- подключить `/today` к этой настройке.

При этом не нужно:

- строить общий экран всех будущих настроек пользователя;
- добавлять goals;
- менять состав сохраняемых nutrition metrics;
- смешивать display preferences с source-of-truth данными summary.

## Decision

### 1. Preferences хранятся отдельно от factual summary

Пользовательские настройки summary хранятся в отдельной таблице `user_summary_preferences`.

Эта таблица описывает только presentation preferences и не влияет на:

- выбор source entries;
- правила полноты данных;
- day-level aggregation;
- сохранённые nutrition metrics.

### 2. Первый поддержанный набор preferences

На первом шаге поддерживаются четыре настройки:

- `show_calories`
- `show_protein`
- `show_fat`
- `show_carbs`
- `nutrition_day_start_hour`

Они определяют, показывать ли пользователю отдельные строки по:

- калориям;
- белкам;
- жирам;
- углеводам.

`nutrition_day_start_hour` определяет, в какой локальный час начинается nutrition-day пользователя для `/today` и других дневных summaries.

### 3. Значение по умолчанию

Если у пользователя ещё нет строки в `user_summary_preferences`, приложение трактует это как default state:

- `show_calories = true`
- `show_protein = true`
- `show_fat = true`
- `show_carbs = true`
- `nutrition_day_start_hour = 4`

Строка может быть создана лениво при первом чтении `/settings` или при первом изменении настройки.

### 4. Граница ответственности

Factual summary layer обязан:

- вернуть полный factual result по дню без учёта display preferences.

Telegram presentation layer обязан:

- прочитать summary preferences пользователя;
- отформатировать `/today` только по включённым показателям;
- если включённых показателей нет, вернуть явный текст вместо пустого summary.

### 5. Минимальный Telegram flow

На этом шаге вводится `/settings`:

- команда показывает текущие значения summary preferences;
- команда показывает текущее значение `nutrition_day_start_hour`;
- под сообщением доступны inline toggle-кнопки;
- нажатие кнопки меняет значение в БД и обновляет сообщение.

Для `nutrition_day_start_hour` на этом шаге используются только заранее поддержанные значения:

- `00:00`
- `02:00`
- `04:00`
- `06:00`

Кнопка в `/settings` циклически переключает их.

### 6. Поведение `/today`

`/today` продолжает считать factual summary как раньше, но вывод зависит от `user_summary_preferences`.

На первом шаге:

- `/today` показывает отдельные строки только по включённым показателям;
- `/today` использует `nutrition_day_start_hour` пользователя при определении текущего nutrition-day;
- для БЖУ используются краткие префиксы строк:
  - `Б:`
  - `Ж:`
  - `У:`
- если все четыре toggles выключены, `/today` возвращает сообщение, что в summary сейчас нет включённых показателей;
- предупреждение о неполноте дня из-за исключённых food entries сохраняется, если summary вообще показывается.

## Consequences

- В системе появляется отдельный, расширяемый слой user-facing summary preferences.
- Factual layer остаётся чистым и пригодным для последующего добавления новых toggles.
- Следующие display preferences можно будет добавлять без пересмотра доменной агрегации.

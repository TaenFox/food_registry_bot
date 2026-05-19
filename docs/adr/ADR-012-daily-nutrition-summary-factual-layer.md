# ADR-012: Daily Nutrition Summary as an On-Demand Factual Layer

## Context

Extraction layer уже реализован, nutrition estimation contract выделен отдельно, а `food entries` сохраняются вместе с item-level nutrition metrics `calories`, `protein`, `fat` и `carbs`.

Следующий целостный шаг - построить первый корректный дневной factual layer для пользователя поверх уже сохранённых journal entries и nutrition metrics.

На этом шаге важно:

- не вводить пользовательские goals и сравнение с ними;
- не добавлять weekly/monthly analytics;
- не пересчитывать nutrition повторно при построении summary;
- не сохранять materialized day aggregate, если итог можно корректно считать on-demand.

При этом нужно явно зафиксировать:

- что именно считается дневной nutrition summary на первом шаге;
- по какой дате и таймзоне определяется день;
- какие записи участвуют в расчёте;
- как вести себя с historical данными, у которых часть метрик может отсутствовать.

## Decision

### 1. Что считается daily nutrition summary на первом шаге

Первый шаг daily nutrition summary - это factual summary за один локальный день пользователя, который строится только поверх уже сохранённых `food entries` и их `entry_item_metrics`.

Summary на этом шаге включает три уровня:

- `item-level source` - сохранённые item-level nutrition metrics конкретной позиции записи;
- `entry-level grouping` - grouped totals по одной записи еды;
- `day-level totals` - сумма по всем включённым food entries дня.

### 2. По какой дате и таймзоне считается день

День определяется по локальной дате пользователя, но с фиксированным смещением начала nutrition-day на `04:00`.

Для расчёта:

- берётся `user.timezone` из текущей модели пользователя;
- определяется локальный интервал `[04:00, 04:00 следующего дня)` для нужной даты;
- затем этот интервал переводится в UTC;
- в summary попадают записи, у которых `entry.occurred_at` лежит внутри этого UTC-интервала.

На первом шаге `/today` использует текущий момент времени и строит summary для локального `today` пользователя по его `timezone`.

Это означает:

- записи с локальным временем от `00:00` до `03:59:59` относятся к предыдущему nutrition-day;
- записи с локальным временем от `04:00` и позже относятся к текущему nutrition-day.

### 3. Какие entries участвуют и какие не участвуют

В summary участвуют только записи, для которых одновременно верно:

- `entry.user_id` принадлежит пользователю;
- `entry.entry_type = food`;
- `entry.occurred_at` попадает в локальный день пользователя;
- у каждого `entry_item` этой записи присутствует полный набор сохранённых метрик:
  - `calories`;
  - `protein`;
  - `fat`;
  - `carbs`.

В summary не участвуют:

- `water` entries;
- `workout` entries;
- `food entries` вне локального дня;
- `food entries`, у которых хотя бы у одного item отсутствует хотя бы одна обязательная метрика.

### 4. Какие метрики обязательны на первом шаге

На первом шаге daily nutrition summary использует только четыре обязательные nutrition metrics:

- `calories`;
- `protein`;
- `fat`;
- `carbs`.

Другие метрики в этот summary пока не включаются, даже если появятся в системе позже.

### 5. Что считается валидным source data

Валидным source data для daily summary считается только уже сохранённый item-level nutrition state в БД.

Это означает:

- приложение не запускает nutrition estimation повторно при расчёте summary;
- приложение не достраивает отсутствующие метрики эвристикой;
- приложение агрегирует только фактически сохранённые записи `entry_item_metrics`;
- item считается валидным источником только если у него есть все четыре обязательные метрики.

### 6. Как вести себя с частично неполными historical данными

Historical данные могут оставаться неполными из старых записей до введения обязательного nutrition write flow или до завершения backfill.

На первом шаге применяется правило:

- если у food entry неполный metric set хотя бы у одного item, вся entry исключается из day totals;
- summary при этом помечается как неполный через признак наличия исключённых food entries;
- пользовательский слой может сообщить, что итог дня неполный из-за записей без полного набора метрик.

Это решение лучше, чем частично суммировать отдельные items одной entry, потому что сохраняет целостность entry-level grouping и не скрывает неполноту данных.

### 7. Граница ответственности

Приложение обязано:

- определить локальный день пользователя по `user.timezone`;
- выбрать `food entries` по `occurred_at`;
- проверить полноту metric set на уровне каждого item;
- агрегировать item metrics в entry totals;
- агрегировать entry totals в day totals;
- явно сообщать, есть ли исключённые food entries.

Nutrition layer не обязан:

- участвовать в расчёте daily summary;
- пересчитывать historical entries при чтении summary;
- строить entry-level или day-level aggregates.

### 8. Хранение результата

На этом шаге отдельная materialized таблица дневных агрегатов не вводится.

Daily summary считается `on-demand` поверх:

- `users`;
- `entries`;
- `entry_items`;
- `entry_item_metrics`;
- `supported_metrics`.

## Consequences

- В системе появляется первый корректный дневной factual layer без новой materialized модели.
- Новый summary опирается только на уже сохранённые nutrition metrics и не смешивается с повторной estimation-логикой.
- Historical неполнота данных становится наблюдаемой: итог дня может быть неполным, но это явно отражается в контракте.
- Следующие шаги, такие как user goals, progress against goals и period analytics, можно строить поверх этого factual layer отдельно.

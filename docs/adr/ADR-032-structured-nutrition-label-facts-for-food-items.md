# ADR-032: Structured Nutrition Label Facts for Food Items

## Context

Food write flow уже разделён на два шага:

- extraction извлекает journal entries;
- nutrition layer считает и сохраняет КБЖУ и `fiber`.

Практический пробел текущего шага: пользователь может явно прислать factual данные с этикетки продукта текстом или фото, но nutrition layer видит только `item.name`, `quantity` и `unit`. В итоге:

- factual label data теряется между extraction и nutrition;
- nutrition estimation вынуждена делать более слабую оценку даже тогда, когда пользователь уже дал явные цифры;
- backfill не может переиспользовать label facts без отдельного канала хранения.

Нужно расширить контракт так, чтобы:

- label facts извлекались как отдельная структурированная сущность;
- работали и для текста, и для фото упаковки;
- не смешивались с workout metrics;
- могли использоваться и в live write flow, и в later backfill без новой таблицы.

## Decision

### 1. В extraction contract добавляется `item.nutrition_label`

Для `food` items extraction layer теперь может возвращать:

```json
{
  "nutrition_label": {
    "basis": "per_100g",
    "calories": 480,
    "protein": 15,
    "fat": 37,
    "carbs": 22
  }
}
```

`nutrition_label`:

- допустим только для `food` items;
- содержит factual label values, явно присутствующие в тексте пользователя или на фото упаковки;
- обязан включать `calories`, `protein`, `fat`, `carbs`;
- может дополнительно включать `fiber`;
- указывает `basis`:
  - `per_100g`;
  - `per_100ml`;
  - `per_serving`;
  - `unknown`, если цифры этикетки видны, но база расчёта не распознана надёжно.

Для `per_serving` дополнительно обязательны `serving_quantity` и `serving_unit`.

### 2. Nutrition layer получает label facts как приоритетный factual input

`NutritionEstimationRequest.items[]` расширяется полем `nutrition_label`.

Если `nutrition_label` присутствует:

- nutrition layer использует его как приоритетный factual source;
- при `per_100g` или `per_100ml` пересчитывает метрики на фактический `quantity/unit`, если они известны;
- при `per_serving` опирается на `serving_quantity` и `serving_unit`;
- при `unknown` может делать осторожное best-effort inference basis, но должен снижать confidence, если basis пришлось угадывать;
- если в `nutrition_label` отсутствует `fiber`, nutrition layer может оценить только недостающую `fiber`, не переписывая явные label facts по КБЖУ.

Если `nutrition_label` отсутствует, nutrition layer работает по прежнему правилу через `name` и optional `quantity/unit`.

### 3. Для backfill используется уже существующий extraction trace

Новая таблица для label facts не вводится.

Источник для later backfill:

- `entries.extraction_raw_payload`.

При сохранении journal entries каждый `Entry` теперь хранит не общий multi-entry extraction payload сообщения, а entry-specific payload только для своей записи. Это позволяет:

- детерминированно восстановить `nutrition_label` для каждого saved food item;
- использовать те же factual label data в backfill nutrition;
- не вводить отдельную trace/model сущность на текущем шаге.

### 4. Workout metrics остаются отдельным contract

`item.metrics` в extraction contract по-прежнему используются только для workout facts вроде `workout_calories`.

Food label data не кладутся в `item.metrics`, а живут отдельно в `item.nutrition_label`, чтобы не смешивать:

- factual workout metrics;
- factual product label facts;
- nutrition estimation result.

## Consequences

- Пользовательские label facts из текста и фото перестают теряться между extraction и nutrition.
- Nutrition layer получает более сильный factual input без отказа от своей отдельной ответственности за итоговые item metrics.
- Backfill может переиспользовать те же label facts без новой схемы БД.
- Контракт становится богаче, но остаётся инкрементальным: label data опциональны и не ломают старые сообщения без этикетки.

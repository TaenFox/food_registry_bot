# ADR-008: Nutrition Estimation Contract over Journal Payload

## Context

Extraction layer уже выделен в отдельный контракт и умеет сохранять нормализованные journal entries. Следующий целостный шаг - ввести отдельную границу для nutrition estimation, не смешивая её с extraction contract, Telegram handler и дневными агрегатами.

На этом шаге нужно определить:

- какой минимальный nutrition payload возвращает отдельный nutrition layer;
- на каком уровне делается расчёт;
- какие метрики обязательны на первом шаге;
- где проходит граница между обязанностями nutrition layer и приложением.

При этом в этом шаге не нужно:

- считать итог дня или реализовывать `/today`;
- добавлять пользовательские цели и workout-эффекты;
- строить полноценный nutrition pipeline с продуктовой базой, retries и explainability UI;
- смешивать extraction result и nutrition result в одну неразделённую сущность.

## Decision

### 1. Отдельная граница поверх journal payload

Nutrition layer работает не с сырым Telegram input, а с уже нормализованным списком item-кандидатов, который приложение строит поверх:

- уже сохранённых journal entries;
- или уже полученного extraction payload.

Для этого приложение формирует отдельный nutrition request:

```json
{
  "items": [
    {
      "client_item_id": "entry-42:item-0",
      "name": "гречка",
      "quantity": 200,
      "unit": "g"
    },
    {
      "client_item_id": "entry-42:item-1",
      "name": "омлет"
    }
  ]
}
```

`client_item_id` - технический идентификатор item внутри запроса, который приложение само назначает.

### 2. Уровень расчёта на первом шаге

На первом шаге nutrition estimation считается только на `item-level`.

Это значит:

- nutrition layer получает плоский список отдельных item-кандидатов;
- для каждого item возвращается отдельная оценка nutrition;
- `entry-level` агрегаты пока не считаются;
- дневные агрегаты, цели и итоговые summaries пока не считаются.

### 3. Какие items участвуют в первом шаге

В nutrition request попадают все items, которые приложение считает food items для текущего шага:

- `entry.type = food`;
- у item есть непустой `name`.

На этом шаге не считаются:

- `water` entries;
- не-food entries.

Если у food item есть `quantity` и `unit`, они передаются в nutrition layer как нормализованная опора для оценки.

Если `quantity` и `unit` отсутствуют, nutrition layer всё равно обязана вернуть оценку метрик, но должна выразить более низкую уверенность через `confidence`.

### 4. Нормализованный nutrition result

Nutrition layer обязан вернуть JSON-объект вида:

```json
{
  "items": [
    {
      "client_item_id": "entry-42:item-0",
      "metrics": [
        {"code": "calories", "value": 220, "confidence": "medium"},
        {"code": "protein", "value": 7.6, "confidence": "medium"},
        {"code": "fat", "value": 2.2, "confidence": "medium"},
        {"code": "carbs", "value": 42.8, "confidence": "medium"}
      ]
    }
  ]
}
```

Корневой объект обязан содержать непустой массив `items`.

Для каждого item result обязательны:

- `client_item_id` - идентификатор item из запроса;
- `metrics` - непустой массив item-level nutrition metrics.

Для каждой metric result обязательны:

- `code` - код поддерживаемой метрики;
- `value` - неотрицательное значение метрики;
- `confidence` - уровень доверия `low`, `medium` или `high`.

На первом шаге для каждого item должны присутствовать ровно четыре метрики:

- `calories`;
- `protein`;
- `fat`;
- `carbs`.

### 5. Опциональные поля первого шага

Внутри нормализованного nutrition result на первом шаге опциональных полей нет.

Следующие поля и концепции в контракт пока не включаются:

- `fiber`, `sugar`, `sodium` и другие дополнительные метрики;
- `explanation`, `reasoning`, `sources`;
- entry-level totals;
- дневные totals;
- пользовательские цели и отклонения от них.

Техническая transport-обвязка сервиса может отдельно передавать:

- `nutrition_provider`;
- `nutrition_model`;
- `raw_payload`.

Но эти поля не входят в нормализованный nutrition payload.

### 6. Граница ответственности

Nutrition layer / LLM обязана:

- вернуть JSON строго по контракту без свободного текста;
- вернуть результат для каждого `client_item_id` из запроса;
- не придумывать новые `client_item_id`;
- вернуть все четыре обязательные метрики для каждого item;
- вернуть `confidence` для каждой метрики;
- использовать только item-level result без скрытых агрегатов.

Приложение обязано:

- отобрать food items из journal payload;
- нормализовать и валидировать доступные `quantity` и `unit`;
- назначить `client_item_id`;
- валидировать структуру nutrition result;
- убедиться, что ответ покрывает ровно тот набор `client_item_id`, который был в запросе;
- трактовать response с пропусками, дубликатами или лишними item как невалидный.

### 7. Что считается невалидным nutrition result

Невалидным считается любой payload, в котором:

- отсутствует корневой объект `{"items": [...]}`;
- `items` пустой;
- у item отсутствует `client_item_id`;
- у item отсутствует массив `metrics`;
- у item отсутствует хотя бы одна из обязательных метрик `calories`, `protein`, `fat`, `carbs`;
- у item есть неизвестная метрика;
- хотя бы одно значение метрики отрицательное;
- у метрики отсутствует `confidence`;
- у метрики неизвестный `confidence`;
- есть дублирующиеся `client_item_id`;
- внутри item есть дублирующиеся metric `code`;
- есть `client_item_id`, которых не было в запросе;
- отсутствует result хотя бы для одного `client_item_id` из запроса;
- присутствуют неизвестные поля;
- JSON повреждён или дополнен произвольным текстом.

## Consequences

- В системе появляется отдельная nutrition boundary поверх journal payload, а не над Telegram input.
- Контракт остаётся узким и обратимым: только item-level, четыре базовые метрики и дискретный confidence.
- Следующий шаг можно делать независимо от дневных агрегатов и UI-объяснений.

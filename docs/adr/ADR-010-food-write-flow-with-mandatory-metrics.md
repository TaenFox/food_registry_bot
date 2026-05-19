# ADR-010: Food Write Flow with Mandatory Nutrition Metrics

## Context

До этого момента food message мог сохраняться как journal entry отдельно от nutrition metrics, а часть food items вообще не попадала в nutrition estimation, если у них не было `quantity` и `unit`.

Это приводит к двум нежелательным эффектам:

- в журнале появляются food entries без nutrition metrics;
- пользователь не понимает, почему у части записей метрики есть, а у части нет.

Нужно зафиксировать новый целостный flow записи еды:

- после food message система должна сохранить и запись еды, и её nutrition metrics;
- ответ пользователю отправляется только после завершения этого flow;
- для воды nutrition metrics не считаются;
- если оценка неточная, это должно отражаться не пропуском метрики, а уровнем доверия.

## Decision

### 1. Новый порядок обработки food message

Для food message применяется порядок:

1. extraction;
2. сохранение journal entries и entry_items;
3. nutrition estimation для всех сохранённых food items;
4. сохранение nutrition metrics;
5. ответ пользователю.

Reply пользователю строится уже после того, как и journal data, и metrics успешно подготовлены и записаны.

### 2. Plain text fallback убирается

Обычное текстовое food message больше не сохраняется как минимальная raw food entry без extraction.

Если extraction provider не смог вернуть валидный extraction payload для food message, запись не создаётся и пользователь получает сообщение об ошибке.

Structured payload без участия LLM остаётся допустимым dev-сценарием, если он уже соответствует extraction contract.

### 3. Метрики для food items обязательны

Для всех `food` items nutrition layer должна возвращать метрики всегда, даже если:

- масса не была явно извлечена;
- размер порции оценён приблизительно;
- результат основан только на названии блюда и типовом предположении.

Отсутствие уверенности выражается не пропуском метрики, а полем `confidence`.

Для `water` entries nutrition estimation не вызывается.

### 4. Confidence хранится на уровне конкретной метрики

Каждая сохранённая метрика получает собственный уровень доверия.

На первом шаге используется дискретный confidence:

- `low`;
- `medium`;
- `high`.

### 5. Ошибки nutrition layer

Для food message сохранение journal entry и nutrition metrics считается одним логическим write flow.

Если nutrition layer вернула техническую ошибку или невалидный structured payload:

- запись food message не коммитится;
- пользователь получает сообщение об ошибке;
- partial write без metrics не допускается.

## Consequences

- Пользовательский контракт становится понятнее: запись еды либо появляется вместе с метриками, либо не появляется вовсе.
- Extraction становится обязательной частью записи еды.
- Nutrition contract должен поддерживать confidence и уметь считать все food items, а не только items с `quantity + unit`.
- Reply бота после записи еды может опираться на уже сохранённые nutrition metrics.

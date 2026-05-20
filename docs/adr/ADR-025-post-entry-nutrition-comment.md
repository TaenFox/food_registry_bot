# ADR-025: Post-Entry Nutrition Comment After Successful Food Logging

## Context

После Этапов 1 и 2 `nutrition coach` уже умеет:

- отвечать на conversational messages поверх factual context дня;
- держать короткую session memory для follow-up вопросов.

Следующий шаг - связать factual logging и coaching более непосредственно: после успешной записи еды бот может дать короткий nutrition-aware комментарий по новой записи и текущему состоянию дня.

При этом важно не размыть existing confirmation flow:

- запись еды по-прежнему должна сохраняться как factual факт;
- confirmation message не должен превращаться в длинний chat answer;
- ошибка LLM не должна ломать сохранение еды.

## Decision

### 1. Вводится отдельный post-entry comment contract

Post-entry comment не считается обычным conversational reply.

Это отдельный узкий контракт поверх successful food write flow:

- используется только после успешной записи food entries;
- не создаёт conversational session;
- не пишет conversational memory;
- может сохраняться в `entries.llm_comment` как комментарий к фактически сохранённой food entry;
- не влияет на routing следующих user messages.

### 2. Comment строится поверх factual state и delta

Для генерации post-entry comment используются:

- сохранённые позиции текущей записи;
- `metric_deltas`, рассчитанные по только что сохранённой записи;
- factual context текущего пищевого дня после сохранения записи.

Comment должен опираться именно на уже обновлённый factual state, а не на догадки о будущем поведении пользователя.

### 3. Comment остаётся коротким и предсказуемым

Post-entry comment:

- короткий, максимум 1-2 предложения;
- без списков, длинных объяснений и coaching dialogue;
- сфокусирован на самой заметной связи между записью и текущими дневными метриками;
- показывается пользователю с локализованным display label агента, а не с техническим именем сервиса;
- не должен утверждать, что изменил цели, настройки или historical entries.

### 4. Comment опционален и не ломает write flow

Если conversational LLM не настроен или comment generation завершилась ошибкой:

- запись еды всё равно сохраняется;
- пользователь получает обычный confirmation response;
- отдельное fallback-сообщение об ошибке coach не показывается.

### 5. Scope первого шага

На этом этапе post-entry comment:

- генерируется только для successful food write flow;
- не добавляется после water-only записи;
- может быть пропущен, если write flow охватывает больше одного summary date и короткий comment нельзя надёжно привязать к одному дню.

## Consequences

- Factual logging и coaching становятся связаннее без смешивания ролей.
- Пользователь получает короткий nutrition-aware сигнал сразу после записи еды.
- Краткий comment становится доступен и как часть persisted factual entry через `entries.llm_comment`.
- Поведение остаётся наблюдаемым и безопасным: save flow не зависит от успеха coach comment.

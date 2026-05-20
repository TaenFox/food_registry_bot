# ADR-022: Message Routing Boundary for Journal and Conversation

## Context

Текущий Telegram layer различает только два класса входа:

- команды, которые матчятся через `aiogram Command(...)`;
- все остальные сообщения, которые сразу отправляются в journal extraction flow.

Такой контракт был допустим, пока бот решал в первую очередь factual logging сценарий. Но на следующем этапе появляется отдельная цель: пользователь должен иметь возможность писать боту не только записи еды и воды, но и обычные сообщения с вопросами, просьбами о совете и обсуждением питания, целей и режима.

Если оставить текущую границу без изменений, возникают два риска:

- conversational message может тихо попасть в extraction/save flow и быть сохранён как ложный food entry;
- смысл factual logging размывается, потому что любой свободный текст трактуется как потенциальная запись журнала.

Нужно ввести отдельный routing contract между:

- command layer;
- journal ingestion layer;
- conversational assistant layer.

На этом шаге важно:

- не строить сложного coach-agent или memory layer;
- не передавать conversational messages в factual write flow;
- предпочесть наблюдаемое и безопасное поведение “умной магии”.

## Decision

### 1. Новая модель маршрутов сообщения

После command layer каждое пользовательское сообщение должно быть отнесено к одному из трёх маршрутов:

- `journal`;
- `conversation`;
- `ambiguous`.

Команды по-прежнему обрабатываются отдельно через `aiogram Command(...)` и не участвуют в этой классификации.

### 2. Что считается command message

`command message` - это сообщение, которое явно матчится через Telegram command handler, например:

- `/start`;
- `/health`;
- `/recent`;
- `/today`;
- `/settings`;
- `/goal`;
- admin-команды.

Такие сообщения не должны попадать ни в journal routing, ни в conversational routing.

### 3. Что считается journal message

`journal message` - это сообщение, которое пользователь отправляет как наблюдаемый факт для сохранения в дневник.

На первом шаге к journal route относятся:

- photo message с едой, потому что текущий поддержанный photo-сценарий относится к factual logging;
- structured payload, уже соответствующий extraction contract;
- text message с достаточно явными признаками записи еды или воды.

Признаки journal message должны быть консервативными и наблюдаемыми. Если уверенности в маршруте недостаточно, сообщение не должно по умолчанию сохраняться как запись.

### 4. Что считается conversational message

`conversational message` - это сообщение, в котором пользователь:

- задаёт вопрос;
- просит совета или объяснения;
- обсуждает питание, цели, режим, тренировки, самочувствие или интерпретацию прогресса;
- ожидает разговорного ответа, а не сохранения нового факта.

Такие сообщения должны идти только в conversational assistant layer и не должны вызывать extraction/save flow.

### 5. Что считается ambiguous message

`ambiguous message` - это сообщение, для которого недостаточно надёжно определить, хотел ли пользователь:

- сохранить новый факт в дневник;
- или начать разговор.

Для `ambiguous`-сообщения система обязана:

- не создавать journal entries;
- не вызывать nutrition estimation;
- не отправлять сообщение в conversational LLM как будто маршрут уже точно выбран;
- вернуть прозрачный ответ с просьбой переформулировать намерение.

### 6. Порядок маршрутизации

Порядок обработки сообщения фиксируется так:

1. command layer;
2. access check;
3. pre-routing classification;
4. один из трёх downstream paths:
   - `journal` -> extraction -> save entries -> nutrition flow -> factual reply;
   - `conversation` -> conversational assistant reply;
   - `ambiguous` -> clarification reply without save.

### 7. Граница ответственности factual logging

`factual logging` отвечает только за:

- извлечение journal payload;
- сохранение `food` и `water` entries;
- nutrition estimation для food items;
- factual summary и progress поверх уже сохранённых данных.

В factual logging не входят:

- советы по питанию;
- обсуждение целей;
- coaching;
- интерпретация ощущений и поведения;
- ответы на общие вопросы пользователя.

### 8. Граница ответственности conversational assistant

`conversational assistant layer` отвечает только за разговорный ответ на свободный текст пользователя.

На первом шаге этот слой:

- не сохраняет новые factual entries;
- не меняет goals;
- не корректирует historical data;
- не добавляет memory или profile modeling beyond current request;
- не подменяет собой journal ingestion.

### 9. Как вести себя в ambiguous cases

Если сообщение ambiguous, система должна предпочесть safe non-write behavior.

Это означает:

- лучше не сохранить потенциальную запись, чем тихо сохранить разговорное сообщение как food entry;
- пользователь должен получить короткий и понятный ответ, что бот не понял, нужно ли сохранить запись или ответить как ассистент;
- пользовательский ответ должен подсказывать, что запись еды/воды нужно сформулировать явно, а вопрос - задать как вопрос.

### 10. Техническое решение первого шага

На первом шаге вводится отдельный pre-routing layer с rule-based классификацией.

Причины такого выбора:

- он прозрачен и управляем;
- его легко тестировать;
- его можно заменить или усилить позже, не меняя downstream contracts.

LLM-based intent router на этом шаге не вводится.

Conversational assistant может использовать отдельный LLM boundary, но только после того, как сообщение уже было направлено в `conversation` route.

## Consequences

- Journal ingestion получает явную границу и больше не является default-путём для любого свободного текста.
- Появляется минимальный conversational mode без смешивания с factual save flow.
- В ambiguous cases пользовательский UX становится чуть более осторожным, но риск ложных записей заметно снижается.
- В дальнейшем можно отдельно улучшать:
  - правила journal/conversation classification;
  - conversational assistant capabilities;
  - UI-способы явного подтверждения маршрута,
  не ломая factual logging contract.

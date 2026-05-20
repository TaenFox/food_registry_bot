# ADR-005: Multimodal Journal Extraction Input

## Context

После запуска текстового `LLMExtractionService` следующий целостный шаг - поддержать фото еды без отдельного продуктового слоя и без новой схемы результата. При этом уже зафиксированный extraction result contract в `ADR-004` менять не нужно: фото должно приводиться к тому же `{"entries": [...]}` payload.

Нужно определить:

- как Telegram layer передаёт в extraction layer текст, caption и изображения;
- как не разъехаться на отдельные API для текста и фото;
- какой минимальный photo-flow поддерживается на первом шаге.

## Decision

### 1. Единый extraction input

В приложении вводится единый request object для extraction layer:

- `text` - опциональный текст сообщения или caption;
- `images` - массив бинарных изображений с media type.

Extraction service принимает именно этот multimodal request, а не отдельные методы для текста и фото.

### 2. Минимальный Telegram photo-flow

На первом шаге Telegram layer:

- принимает обычное текстовое сообщение как `text`;
- принимает сообщение с `photo` как один multimodal request;
- использует caption как `text`, если он есть;
- скачивает только самое крупное фото из массива `message.photo`;
- передаёт изображение в extraction layer как `image/jpeg`.

Поддержка нескольких фото в одном сообщении, документов, PDF и скриншотов тренировок в этот шаг не входит.

### 3. Граница ответственности

Telegram layer обязан:

- отличить текстовое сообщение от photo message;
- скачать изображение из Telegram;
- собрать multimodal extraction request;
- передать его в extraction service;
- сохранить валидный extraction result в ту же доменную модель журнала.

Extraction service обязан:

- принять единый request object;
- для text-only и image+text использовать тот же extraction result contract из `ADR-004`;
- вернуть либо валидный `ExtractedJournalPayload`, либо явную ошибку валидации/интеграции.

### 4. OpenAI adapter

Для прямой интеграции с OpenAI adapter использует Responses API с multimodal input:

- `input_text` для текста или caption;
- `input_image` для фото;
- один и тот же JSON output contract для journal extraction.

### 5. Ограничения первого шага

В этот инкремент не входят:

- несколько фото в одном сообщении как продуктовый сценарий;
- OCR-специализация и отдельная логика для чеков;
- confidence scoring;
- сохранение промежуточных vision-артефактов;
- КБЖУ и дневные итоги.

## Consequences

- Входной контракт extraction layer становится мультимодальным без изменения доменной модели и payload result contract.
- Поддержка фото добавляется инкрементно поверх уже работающего text extraction flow.
- Следующие шаги смогут расширять только входной слой и prompt/adapter, не переписывая Telegram handler заново.

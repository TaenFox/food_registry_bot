# ADR-026: Safe Boundary Between Journal Photo and Conversation Photo

## Context

До этого шага photo messages в routing трактовались только как journal input.

Это безопасно для factual logging, но не покрывает важный coaching-сценарий:

- пользователь фотографирует холодильник или доступные продукты;
- хочет спросить, что лучше приготовить или съесть;
- фото не должно быть сохранено как еда только потому, что это photo message.

При этом главный риск этапа очевиден: нельзя допустить, чтобы неявное `conversation_photo` размывало factual contract photo logging.

## Decision

### 1. Вводится отдельный `conversation_photo` сценарий

Photo message может идти не только в journal flow, но и в conversational coaching flow.

Этот сценарий предназначен для:

- фото холодильника;
- фото доступных продуктов;
- других product-based coaching photo messages.

### 2. Boundary делается консервативной

Photo message уходит в `conversation_photo` только если caption содержит явный conversational cue.

Достаточно сильными сигналами считаются:

- явный вопрос;
- явная просьба о совете;
- photo-specific coaching cues вроде `что приготовить`, `что съесть`, `из этого`, `в холодильнике`, `вот что есть`.

Если такого сигнала нет, photo message остаётся `journal_photo`.

### 3. No silent magic for captionless photos

Photo без caption не считается `conversation_photo`.

Такое сообщение по-прежнему трактуется как journal candidate, чтобы не ломать существующий photo food logging contract.

### 4. Multimodal coach uses the photo directly

Для `conversation_photo` conversational LLM получает:

- factual context дня;
- caption text;
- само изображение.

Coach должен интерпретировать фото как conversational input, а не как уже съеденную еду.

### 5. Scope первого шага

На этом этапе:

- поддерживается только один photo message за раз, как и в journal photo flow;
- `conversation_photo` не создаёт factual entries;
- `conversation_photo` не создаёт отдельную persistence-модель;
- сохраняется тот же 1-hour conversational session memory contract.

## Consequences

- Появляется первый безопасный мультимодальный coaching input.
- Photo coaching остаётся наблюдаемым и управляемым за счёт explicit-caption boundary.
- Existing photo food logging не ломается и остаётся default route для неявных photo messages.

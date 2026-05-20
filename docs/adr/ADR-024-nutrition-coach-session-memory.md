# ADR-024: Nutrition Coach Session Memory with One-Hour TTL

## Context

`nutrition coach` уже умеет отвечать на conversational messages поверх factual context дня. Следующий шаг - удерживать короткий рабочий контекст беседы, чтобы follow-up сообщения вроде `это будет сценарий А` не падали обратно в journal flow и не теряли смысл предыдущего ответа.

В обсуждении уже согласовано:

- память не должна быть “всем чатом”;
- активный coach context живёт 1 час после последнего conversational message;
- память не должна строиться как одна `active task`;
- нужно хранить и raw conversational messages, и краткое session summary.

Нужно зафиксировать первый технический контракт этой памяти.

## Decision

### 1. Вводится отдельная coach session

Для conversational nutrition coach вводится сущность `conversation_session`, привязанная к пользователю.

Session хранит:

- `user_id`;
- `summary_text`;
- `started_at`;
- `last_message_at`.

Сессия не хранит factual data дня. Factual context по-прежнему собирается отдельно из factual layer на каждый ответ.

### 2. Вводятся raw conversational messages

Для каждой coach session вводится таблица `conversation_messages`.

Каждое сообщение хранит:

- `session_id`;
- `role` (`user` / `assistant`);
- `content`;
- `created_at`.

Сохраняются только conversational turns `nutrition coach`. Journal messages и command messages туда не попадают.

### 3. Active session lifecycle

Сессия считается активной, если:

- она принадлежит пользователю;
- `last_message_at` не старше 1 часа относительно текущего conversational request.

Если активной сессии нет, создаётся новая.

### 4. Session memory contract

На каждом coach reply используются:

- текущий user message;
- factual context дня;
- `summary_text` активной сессии;
- несколько последних conversational turns из этой сессии.

После ответа session memory обновляется:

- в `conversation_messages` сохраняются текущие user и assistant messages;
- `summary_text` обновляется;
- `last_message_at` сдвигается на текущее время.

### 5. Coach LLM output

LLM для coach session должна возвращать не только `reply_text`, но и `updated_session_summary`.

Это позволяет:

- держать короткую рабочую память;
- не пересчитывать summary отдельным вторым LLM вызовом;
- не хранить в prompt слишком длинную raw history.

### 6. Routing with active session

Active coach session влияет на routing.

Если у пользователя есть активная conversational coach session:

- явные journal messages по-прежнему могут идти в factual logging;
- structured payload и photo journal flow сохраняются как journal;
- slash-like text по-прежнему не должен молча попадать в journal;
- неявные короткие follow-up text messages по умолчанию трактуются как continuation of conversation.

Это правило нужно, чтобы фразы вроде `это будет сценарий А` не сохранялись как food entries.

### 7. Reply to coach message

Reply на предыдущее conversational сообщение `nutrition coach` считается более сильным сигналом continuation, чем обычный поиск активной сессии по TTL.

При этом reply-link не отменяет явный journal intent:

- если reply-текст выглядит как явная запись факта, например `запиши кусок хлеба` или `съел яблоко`, сообщение должно идти в factual logging;
- если reply-текст не выглядит как явный journal, он может использоваться как continuation той же coach session.

### 8. Что не входит в этот этап

На этом этапе не вводятся:

- long-term memory;
- user profile extraction;
- photo-based coaching memory;
- post-entry coach comment;
- отдельный trainer coach.

## Consequences

- Nutrition coach получает короткую рабочую память на несколько сообщений.
- Follow-up сообщения перестают зависеть только от surface-form routing.
- Reply на coach-сообщение помогает точнее продолжать беседу, но не ломает явное логирование фактов.
- Память остаётся наблюдаемой и ограниченной по времени.
- Factual logging contract остаётся отделённым от conversational memory.

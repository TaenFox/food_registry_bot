# ADR-036: Telegram Callback State Storage

## Context

Проект уже использует inline-кнопки для:

- `/recent` c entry-level и item-level действиями;
- post-entry confirmation, который теперь тоже открывает entry-level детали;
- `/report`, `/settings`, admin и других callback-driven UX.

Для `recent` и post-entry flow пользовательский сценарий начал требовать больше технического контекста в callback:

- page/count для возврата в список;
- origin сценария (`recent` или `post_entry`);
- `parent_message_id` для дочернего item-level сообщения;
- `root_entry_id` для возврата на первый экран confirmation;
- дополнительные параметры item-level действий, например правка КБЖУ на 100 г.

Telegram ограничивает `callback_data` значением `64` байта.

На текущем шаге длинные callback payloads уже начинают превышать лимит и ломаются на уровне `aiogram.pack()`.

При этом in-memory state недостаточен:

- после рестарта процесса кнопки станут невалидными;
- такой state хуже наблюдается и дебажится;
- он плохо согласуется с уже принятым принципом, что БД является источником истины для runtime-состояния, которое должно переживать обычные перезапуски приложения.

## Decision

### 1. Для длинных Telegram callback flows вводится отдельное server-side state storage

В БД вводится отдельная таблица transient callback state.

Минимально она хранит:

- короткий стабильный `state_key`, который помещается в Telegram `callback_data`;
- `scope`, чтобы разные callback UX не смешивались неявно;
- сериализованный payload с нужным техническим контекстом;
- timestamps создания и обновления.

### 2. В `callback_data` передаётся только действие и короткий `state_key`

Для flows, которые могут выйти за Telegram `64` байта, inline-кнопки больше не тащат весь технический контекст в payload.

Вместо этого:

- в `callback_data` передаётся только `action`;
- и короткий `state_key`.

Все остальные данные восстанавливаются из БД по `state_key`.

### 3. Первый обязательный scope для state storage - `recent_action`

На текущем шаге новый storage обязателен как минимум для:

- `/recent` entry-level flow;
- `/recent` item-level flow;
- post-entry `Подробнее`, который переиспользует тот же action contract.

Другие callback flows могут продолжать хранить компактный payload inline, пока он реально остаётся коротким.

### 4. State payload считается техническим, а не пользовательским source-of-truth

Содержимое callback state:

- не считается доменной моделью питания;
- не участвует в nutrition summaries;
- не влияет на historical user data;
- не должно подменять проверки фактической доступности записи в БД.

Даже при наличии валидного `state_key` обработчик обязан повторно проверять:

- доступ пользователя;
- существование `Entry`;
- существование `EntryItem`;
- актуальность целевого сценария.

### 5. Stale callback state допустим

Callback state считается transient-слоем.

Если:

- запись уже удалена;
- item уже недоступен;
- state не найден;

пользователь получает понятный отказ уровня UX, а не внутреннюю ошибку.

На этом шаге не требуется:

- отдельная периодическая очистка state;
- TTL enforcement в runtime;
- автоматическое удаление всех старых state rows.

Если такие требования появятся, они обсуждаются отдельно.

## Consequences

- `recent` и post-entry кнопки перестают зависеть от Telegram payload limit.
- Кнопки остаются рабочими после обычного рестарта бота.
- Callback UX получает отдельный технический storage boundary вместо ad hoc разрастания `callback_data`.
- БД получает ещё один transient read/write слой, но без влияния на доменные nutrition-данные.

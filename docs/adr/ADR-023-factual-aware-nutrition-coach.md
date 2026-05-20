# ADR-023: Factual-Aware Nutrition Coach for Conversational Messages

## Context

В проекте уже введён safe conversational routing:

- команды обрабатываются отдельно;
- journal messages идут в factual logging flow;
- conversational messages не сохраняются как journal entries.

Следующий этап развития conversational слоя - сделать первый минимальный полезный `nutrition coach`.

На этом шаге важно:

- не строить ещё memory layer в БД;
- не вводить `trainer coach`;
- не смешивать coaching с factual save flow;
- не оставлять coach “в вакууме” без связи с реальными метриками дня.

В обсуждении уже согласовано:

- первый агент - только `nutrition coach`;
- он отвечает на nutrition / water / meal-planning вопросы и общие health topics;
- он всегда должен видеть factual context дня пользователя;
- short-term session memory будет отдельным следующим этапом.

Нужно зафиксировать архитектурную границу этого первого `nutrition coach`.

## Decision

### 1. На этом этапе вводится именно nutrition coach

Conversational layer первого содержательного шага реализуется как `nutrition coach`.

Он не является общим chat assistant и не является `trainer coach`.

Он отвечает на:

- питание;
- воду;
- завершение дня по текущим метрикам;
- meal-planning;
- выбор следующего приёма пищи;
- product-based suggestions по списку продуктов;
- питание в контексте тренировки;
- общие вопросы здоровья.

### 2. Coach всегда использует factual context дня

Каждый conversational reply строится не только по user message, но и по factual context текущего nutrition-day пользователя.

Минимальный factual context этого этапа:

- `summary_date`;
- `timezone`;
- `nutrition_day_start_hour`;
- day totals по `calories`, `protein`, `fat`, `carbs`, `fiber`;
- water total;
- progress against daily goals;
- recent entries.

Этот context должен быть явным техническим входом coach service, а не неявным знанием внутри Telegram handler.

### 3. Новый service boundary

На этом этапе conversational-ответ больше не строится как `user_message -> plain LLM reply`.

Вводится отдельная boundary:

- `nutrition coach context builder`;
- `nutrition coach service`.

Ожидаемый вход `nutrition coach service`:

- текущее пользовательское сообщение;
- factual coach context.

Ожидаемый выход:

- coach reply;
- provider / model trace.

### 4. Factual context не равен memory

На этом этапе `nutrition coach` ещё не получает DB-backed conversational memory.

Текущий шаг не включает:

- session summary;
- recent conversational turns;
- сохранение conversational messages в БД.

Factual context дня и conversational memory - это разные уровни системы и они будут развиваться отдельно.

### 5. Что coach не делает на этом этапе

`nutrition coach` на этом этапе не должен:

- создавать journal entries;
- менять goals;
- изменять historical data;
- выдавать post-entry comment внутри food write flow;
- работать по фото холодильника;
- становиться отдельным `trainer coach`.

### 6. Взаимодействие с вопросами вне домена

Если сообщение conversational, но по смыслу заметно уходит слишком далеко от nutrition / hydration / meal-planning / health domain, coach не должен притворяться универсальным ассистентом.

В таких случаях он может:

- ответить кратко;
- мягко вернуть разговор к своему назначению.

На этом этапе жёсткая policy-матрица отказов не вводится beyond already existing platform safeguards.

## Consequences

- Conversational layer становится полезным за счёт связи с реальными данными дня, а не только за счёт generic LLM reply.
- Появляется явная service boundary для следующего этапа с session memory.
- Проект остаётся обратимым:
  - factual context builder можно расширять;
  - DB memory можно добавить позже без слома текущего coach contract.
- Multimodal coaching и post-entry coach comment остаются отдельными следующими этапами.

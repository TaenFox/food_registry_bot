# Проект: Nutrition Coach поверх factual food/water bot

Этот документ фиксирует согласованное направление развития conversational слоя как отдельной функциональности `nutrition coach`.

Цель документа:

- сохранить результат обсуждения в репозитории;
- дать следующий понятный шаг для новой сессии Codex;
- позволить двигаться короткими инкрементами и проверять завершённость этапов;
- не смешивать план коучинга с factual logging contract.

Документ не заменяет ADR. Если очередной этап меняет routing contract, модель данных, interaction contract или архитектурную границу, перед реализацией нужно зафиксировать это в отдельном ADR.

## 1. Целевой результат

В проекте появляется отдельный `nutrition coach`, который работает поверх уже существующего factual слоя еды и воды.

`nutrition coach`:

- отвечает на вопросы о питании, воде, meal-planning и общих вопросах здоровья;
- всегда использует актуальный factual context дня пользователя;
- поддерживает короткую рабочую память беседы;
- не подменяет factual logging;
- не сохраняет conversational message как journal entry;
- не меняет goals и historical data без отдельной целевой функциональности.

На текущем направлении `trainer coach` не вводится. Вопросы, связанные с тренировками, допускаются только в той части, где они влияют на питание, воду и планирование приёмов пищи.

## 2. Уже принято

Ниже перечислены решения, уже согласованные в обсуждении и используемые как вход для следующих этапов.

### 2.1. Роль первого агента

На ближайших этапах развивается только один агент:

- `nutrition coach`

Отдельный `trainer coach` откладывается до появления целостного factual workout layer.

### 2.2. Тематика ответов

`nutrition coach` отвечает на:

- nutrition questions;
- water / hydration questions;
- meal-planning;
- общие вопросы здоровья;
- питание вокруг тренировки;
- выбор блюда из доступных продуктов.

Если вопрос уходит слишком далеко от предназначения коуча, агент не должен пытаться стать универсальным ассистентом. В таких случаях он может ответить кратко и мягко вернуть разговор в домен питания, воды, самочувствия, режима и планирования еды.

### 2.3. Factual context

`nutrition coach` всегда получает factual context дня. Это правило не зависит от типа вопроса.

Минимальный factual context:

- today summary;
- today goal progress;
- water progress;
- recent entries;
- при наличии в системе позже: relevant workout-related factual context, если он появится в отдельном слое.

### 2.4. Память беседы

Контекст беседы должен жить ограниченно и не быть “памятью всего чата”.

Согласованная модель:

- активный conversational context живёт 1 час после последнего conversational message;
- после этого начинается новая сессия;
- память не строится как одна `active task`;
- память строится как:
  - `session summary`;
  - `recent turns`;
  - сохранённые raw conversational messages в БД.

### 2.5. Почему не `active task`

Идея отдельной активной задачи обсуждалась и была отклонена на текущем этапе, потому что:

- пользователь может быстро переключаться между несколькими темами;
- явная state machine “какая задача сейчас активна” избыточна;
- она усложнит агент раньше, чем принесёт явную пользу;
- при этом агенту всё равно нужна полезная память нескольких последних сообщений.

Итог: на текущем этапе используется общая session memory, а не task state.

### 2.6. Работа с фото

Сценарий `photo` в coaching признан важным, но не должен автоматически смешиваться с photo journal ingestion.

Важный будущий сценарий:

- пользователь фотографирует содержимое холодильника и спрашивает, что приготовить.

Этот сценарий признаётся полезным, но не является первым следующим этапом. Сначала нужен text-first nutrition coach с memory. Photo coaching лучше вводить позже отдельным подэтапом.

## 3. Границы ответственности

### 3.1. Что остаётся factual logging

`factual logging` отвечает за:

- command layer;
- journal routing;
- extraction;
- save flow для food/water entries;
- nutrition metrics;
- factual day summary;
- goals/progress поверх сохранённых данных.

### 3.2. Что относится к nutrition coach

`nutrition coach` отвечает за:

- интерпретацию уже сохранённого factual state;
- советы по завершению дня;
- meal suggestions;
- product-based suggestions;
- meal-planning;
- nutrition guidance around training;
- conversational follow-up на несколько сообщений.

### 3.3. Что nutrition coach не делает на этом этапе

На ближайших этапах `nutrition coach` не должен:

- создавать journal entries;
- менять factual metrics;
- менять goals;
- исправлять historical entries;
- становиться отдельной long-term memory system;
- становиться `trainer coach`;
- строить полноценный multi-day adaptive meal planning engine.

## 4. Основные пользовательские сценарии

Ниже приведены сценарии, которые считаются продуктово важными для ближайшего развития.

### 4.1. Завершение дня по текущим метрикам

Примеры:

- как лучше завершить питание сегодня;
- что съесть на ужин по уже съеденному;
- как добрать белок / клетчатку / воду;
- как не перебрать калории или жиры.

### 4.2. Питание вокруг тренировки

Примеры:

- как организовать питание перед тренировкой;
- что съесть после тренировки;
- как сегодня и завтра распределить еду, если планируется нагрузка.

На этом этапе это считается nutrition-сценарием, а не trainer-сценарием.

### 4.3. Что приготовить из доступных продуктов

Примеры:

- перечислить продукты и спросить, что лучше приготовить;
- получить 2-4 варианта еды с объяснением, почему они подходят под текущий factual state.

Первый подэтап этого сценария:

- text-only список продуктов.

Отдельный будущий подэтап:

- фото холодильника / фото доступных продуктов.

## 5. Рекомендуемая техническая модель

### 5.1. Первый целевой service boundary

Следующий разговорный слой стоит строить как отдельный `nutrition coach service`, а не как ad hoc prompt внутри handler.

Ожидаемый вход сервиса:

- текущее пользовательское сообщение;
- factual context дня;

Ожидаемый выход:

- coach reply;
- provider / model trace.

### 5.2. Память

Рекомендуемая минимальная memory model:

- хранить raw conversational messages в БД;
- хранить session summary в БД;
- при ответе использовать summary + recent turns;
- factual data всегда брать из factual layer, а не из conversational memory.

Почему не только summary:

- summary теряет трассируемость;
- без raw messages тяжелее отлаживать поведение;
- summary может потерять важный follow-up контекст.

Почему не только raw history:

- шумит prompt;
- плохо масштабируется по длине;
- даёт менее управляемую memory model.

## 6. Поэтапный план

Ниже зафиксирован рекомендуемый порядок этапов. Каждый этап должен оставлять проект в проверяемом состоянии и иметь собственные тесты и документацию.

### Этап 0. Safe conversational routing

Статус:

- `[x]` выполнен в текущей ветке

Содержимое:

- отдельная граница между `journal`, `conversation`, `ambiguous`;
- conversational entrypoint;
- защита от случайного сохранения разговорных сообщений как journal entries.

Связанный ADR:

- [ADR-022](/Users/pmokeev/Desktop/Projects/food_registry_bot/docs/adr/ADR-022-message-routing-boundary-for-journal-and-conversation.md:1)

### Этап 1. Text-first nutrition coach с factual context

Статус:

- `[x]` выполнен

Цель:

- сделать полезный `nutrition coach`, который отвечает на text conversational messages, всегда видит factual state дня, но ещё не имеет session memory в БД.

Входит в этап:

- отдельный coach boundary;
- явный factual context builder для coach;
- prompt / contract для ответов nutrition coach;
- поддержка сценариев:
  - завершение дня;
  - meal-planning;
  - питание вокруг тренировки;
  - советы по списку продуктов в тексте.

Не входит в этап:

- DB memory;
- trainer coach;
- photo coaching;
- post-entry coach comment.

Критерий завершения:

- conversational nutrition replies строятся не “в вакууме”, а поверх factual context дня;
- это покрыто тестами и задокументировано.

Связанный ADR:

- [ADR-023](/Users/pmokeev/Desktop/Projects/food_registry_bot/docs/adr/ADR-023-factual-aware-nutrition-coach.md:1)

### Этап 2. Session memory на 1 час

Статус:

- `[x]` выполнен

Цель:

- добавить короткую memory model для нескольких conversational turns.

Входит в этап:

- хранение conversational messages в БД;
- хранение session summary;
- lifecycle active session = 1 hour after last message;
- использование summary + recent turns в coach service.

Не входит в этап:

- long-term user profiling;
- persistent preference extraction;
- retrieval по всей истории пользователя;
- photo coaching.

Критерий завершения:

- агент держит несколько follow-up сообщений без потери краткого контекста;
- память ограничена по времени и не смешивается с factual data.

Связанный ADR:

- [ADR-024](/Users/pmokeev/Desktop/Projects/food_registry_bot/docs/adr/ADR-024-nutrition-coach-session-memory.md:1)

### Этап 3. Post-entry nutrition comment

Статус:

- `[x]` выполнен

Цель:

- связать factual logging и coaching коротким комментарием после записи еды.

Входит в этап:

- короткий coach-comment после successful food write flow;
- комментарий строится по delta и новому состоянию дня;
- комментарий остаётся коротким и предсказуемым.

Не входит в этап:

- длинный conversational answer после каждой записи;
- скрытая переоценка factual entries;
- отдельный trainer coach.

Критерий завершения:

- после сохранения еды бот умеет дать лаконичный nutrition-aware комментарий, не ломая existing confirmation flow.

Связанный ADR:

- [ADR-025](/Users/pmokeev/Desktop/Projects/food_registry_bot/docs/adr/ADR-025-post-entry-nutrition-comment.md:1)

### Этап 4. Multimodal coaching input

Статус:

- `[ ]` позже, отдельным шагом

Цель:

- добавить coaching-сценарии с мультимодальным conversational input.

Входит в этап:

- фото холодильника;
- фото доступных продуктов;
- чёткая граница между `journal_photo` и `conversation_photo`.

Главный риск этапа:

- не допустить, чтобы coaching photo по ошибке шёл в factual save flow.

## 7. Что нужно фиксировать ADR перед следующими этапами

Перед реализацией следующего значимого шага нужно проверить, не требуется ли новый ADR.

С высокой вероятностью ADR понадобится, если этап добавляет:

- новую DB model для conversational memory;
- новый coach contract;
- новую message classification boundary для photo coaching;
- новый post-entry interaction contract;
- значимую архитектурную границу между factual и coach layers.

## 8. Как использовать этот документ в новом чате Codex

Если работа продолжается в новом чате, удобно начинать с этого файла и брать следующий незавершённый этап.

Практический порядок:

1. Открыть этот документ.
2. Выбрать следующий этап с незавершённым статусом `[ ]`.
3. Отдельно попросить:
   - изучить текущее состояние кода;
   - подтвердить, нужен ли ADR;
   - предложить минимальную реализацию именно этого этапа.
4. После завершения этапа:
   - обновить этот документ;
   - обновить `docs/user-functions.md`, если меняется пользовательский контракт;
   - добавить ADR, если меняется архитектурная граница или модель.

Удобный шаблон запроса для нового чата:

```text
Открой docs/projects/01-nutrition-coach-project.md и возьми следующий незавершённый этап.
Сначала изучи текущее состояние кода и скажи:
- нужен ли для этого этапа новый ADR;
- какой минимальный инкремент реализации ты рекомендуешь;
- какие тесты и документы нужно обновить.
Не начинай код до явной формулировки решения.
```

## 9. Текущее рекомендуемое следующее действие

Если продолжать развитие прямо сейчас, следующий шаг:

- Этап 3: `post-entry nutrition comment`

Почему именно он:

- factual-aware coach и краткая memory уже работают вместе;
- следующий полезный мост между logging и coaching - короткий комментарий после записи еды;
- это можно строить поверх уже существующих delta и day-progress данных;
- photo coaching по-прежнему лучше оставить отдельным следующим шагом после этого.

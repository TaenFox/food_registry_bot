# Чек-лист: добавление новой поддержанной диеты

Этот документ нужен разработчику или агенту, который добавляет в проект новую поддержанную диету.

Цель:

- добавить диету как полноценную продуктовую функцию, а не только новый `code` в справочник;
- не сломать существующие summary, `/today`, `/report`, `/recent`, post-entry flow и Docker-сценарий запуска;
- оставить проект в проверяемом состоянии.

## 1. Сначала уточнить продуктовую модель

- Зафиксировать `diet code`, `display name` и краткое русское название для UI.
- Понять, должен ли `diet score` оцениваться только для `food items` или ещё и для `water items`.
- Если для воды нужен фиксированный score, определить его явно и заранее.
- Определить семантику шкалы `1..10` именно для этой диеты:
  - что считается явным конфликтом;
  - что считается нейтральным случаем;
  - что считается хорошим соответствием;
  - может ли вода улучшать score и насколько сильно.
- Проверить, не меняет ли новая диета архитектурные договорённости из [ADR-035](/Users/pmokeev/Desktop/Projects/food_registry_bot__codex-2/docs/adr/ADR-035-diet-tracking-with-item-level-scores.md:1).
- Если меняет, сначала обновить существующий ADR или добавить новый ADR.

## 2. Добавить diet definition в код

- Обновить [registry.py](/Users/pmokeev/Desktop/Projects/food_registry_bot__codex-2/src/food_registry_bot/diet/registry.py:1):
  - добавить новую `SupportedDietDefinition`;
  - задать стабильный `code`;
  - задать `metric_code`;
  - заполнить `description`;
  - заполнить `scoring_guidance`;
  - если нужно, задать `water_score`.
- Проверить, что:
  - `metric_code` уникален;
  - UI-имя диеты читается на русском;
  - bar-label не конфликтует с уже существующими сокращениями.

## 3. Добавить метрику и справочник диет в БД

- Добавить новую метрику в `supported_metrics` через миграцию.
- Если диеты ещё нет в `supported_diets`, добавить её в seed/миграцию.
- Проверить обратимость миграции.
- Проверить, что исторические данные без новой diet metric остаются допустимыми.

## 4. Протянуть диету через runtime

- Проверить, что новая диета попадает в `get_supported_diet_metric_codes()`.
- Если diet score для воды фиксированный:
  - убедиться, что вода не уходит в LLM для этой диеты;
  - убедиться, что фиксированное значение сохраняется через обычный `entry_item_metrics`.
- Если diet score идёт через LLM:
  - убедиться, что diet request строится только для нужных item;
  - убедиться, что prompt описывает именно эту диету, а не абстрактную “здоровость”.
- Проверить, что сохранение diet score не ломает nutrition summary и не делает записи “неполными”.

## 5. Обновить read-side и UI

- Проверить `/settings`:
  - новая диета отображается отдельным toggle;
  - русское имя единообразно;
  - включение/выключение не переписывает старую историю.
- Проверить `/today` и post-entry day-report:
  - диета входит в `Диеты за день`;
  - средний score считается корректно;
  - в `bars` mode bar-line читается и не разваливается по ширине;
  - если включена `Дельта записи`, delta по среднему score показывается корректно.
- Проверить `/report`:
  - диета появляется в отдельном diet block;
  - пометки о неполных данных остаются честными;
  - нет английских формулировок.
- Проверить `/recent`:
  - entry-level средний score по записи считается корректно;
  - item-level экран не показывает лишнюю diet-аналитику, если это не согласовано отдельно.

## 6. Обновить prompts и комментарии LLM

- Если диета использует LLM scoring, обновить diet client prompt'ы для всех используемых провайдеров.
- Проверить, что prompt:
  - объясняет шкалу `1..10`;
  - учитывает `quantity` и `unit`, а не только название продукта;
  - не смешивает диетический score с nutrition metrics.
- Проверить, что `nutrition coach` получает активные диеты в factual context и может упомянуть новую диету в post-entry comment.

## 7. Добавить или обновить тесты

- Тесты на миграцию/seed при необходимости.
- Тесты на сохранение новой diet metric в `entry_item_metrics`.
- Тесты на water-flow, если у диеты есть фиксированный `water_score`.
- Тесты на `/today`:
  - text mode;
  - bars mode.
- Тесты на post-entry confirmation:
  - diet block появляется;
  - delta по среднему score корректна;
  - в bars mode delta подсвечивается в bar.
- Тесты на `/report` и `/recent`, если меняется наблюдаемое поведение.
- Отдельно проверить, что новая diet metric не ломает daily nutrition summary.

## 8. Обновить документацию

- Обновить [README.md](/Users/pmokeev/Desktop/Projects/food_registry_bot__codex-2/README.md:1), если меняется пользовательское обещание проекта.
- Обновить [docs/user-functions.md](/Users/pmokeev/Desktop/Projects/food_registry_bot__codex-2/docs/user-functions.md:1), если меняется наблюдаемое поведение.
- Обновить [ADR-035](/Users/pmokeev/Desktop/Projects/food_registry_bot__codex-2/docs/adr/ADR-035-diet-tracking-with-item-level-scores.md:1), если меняются правила diet tracking.
- Если новая диета вводит отдельный нетривиальный компромисс, оформить новый ADR.

## 9. Перед завершением шага проверить рабочий цикл

- Убедиться, что рабочее дерево чистое до начала следующего этапа.
- Прогнать `pytest`.
- Прогнать `ruff check` по изменённым файлам или шире, если затронута общая логика.
- При необходимости прогнать `compileall` с writable `pycache_prefix`.
- Поднять контейнеры через `./scripts/docker-compose.sh up -d --build`.
- Проверить `./scripts/docker-compose.sh ps`.

## 10. Минимальный practical smoke-check

- Включить новую диету в `/settings`.
- Добавить food entry, который должен получить высокий score.
- Добавить food entry, который должен заметно ухудшить средний score.
- Если применимо, добавить воду и проверить её влияние.
- Проверить:
  - confirmation после записи;
  - `/today`;
  - `/report`;
  - `/recent`.

## Короткая памятка по текущей реализации

- source of truth для diet scores - `entry_item_metrics`;
- новые diet metric codes developer-managed;
- `food items` оцениваются через LLM;
- `water items` могут оцениваться фиксированным значением из registry;
- `/today` и post-entry report показывают quantity-weighted средний score за день;
- `/report` показывает period aggregate отдельно от goal-oriented nutrition metrics;
- добавление новой diet metric не должно ломать nutrition completeness logic.

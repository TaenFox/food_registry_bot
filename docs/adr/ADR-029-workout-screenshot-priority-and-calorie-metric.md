# ADR-029: Single-Screenshot Workout Input and Calorie-Only Metric

## Context

Следующий шаг развития workout layer - принимать screenshot тренировки как journal input.

Нужно зафиксировать первый multimodal contract так, чтобы:

- не пытаться строить полный OCR всех экранов;
- использовать одиночный screenshot как factual workout input;
- не потерять возможность позже расширить модель;
- не смешивать в одном шаге и calories, и exercise breakdown, и подробную силовую аналитику;
- не тащить в текущий scope multi-image merge и source-priority логику.

## Decision

### 1. Первый screenshot-based workout input остаётся workout journal flow

Workout screenshots на этом этапе относятся к factual journal functionality.

Это означает:

- screenshots проходят через workout journal routing, а не через `nutrition coach`;
- результатом является сохранённый `workout entry`;
- conversational interpretation делается только после того, как факт уже извлечён и сохранён.

### 2. Первый шаг поддерживает только один screenshot

На текущем этапе workout screenshot flow поддерживает только одиночный screenshot.

Это означает:

- бот не пытается объединять несколько изображений одного workout;
- bot не строит source priority между приложениями;
- если пользователь прислал media group или несколько изображений как одну группу, screenshot extraction не выполняется;
- пользователь получает явную просьбу прислать один основной screenshot с итогами тренировки.

### 3. На первом screenshot workout шаге сохраняется только calorie metric

На этом этапе screenshot extraction не должен пытаться сохранять:

- список упражнений;
- breakdown по подходам, повторениям и весам;
- muscle map;
- визуальные зоны нагрузки;
- детальные heart-rate series;
- computed training load.

Сохраняемый новый факт этого шага:

- factual workout calorie metric, если он явно присутствует на screenshot и достаточно надёжен.

Этот metric должен рассматриваться как factual source data, а не как расчёт приложения бота.

### 4. Дополнительные screenshot facts допустимы только как контекст записи, а не как exercise log

Если screenshots позволяют понять:

- тип тренировки;
- длительность;
- общую характеристику нагрузки;

эти факты могут использоваться для общего workout interpretation на уровне записи, но не должны приводить к сохранению exercise-by-exercise состава на текущем шаге.

### 5. Workout screenshot extraction должен оставаться консервативным

Если по screenshot нельзя достаточно надёжно понять базовые факты тренировки или calorie metric:

- система не должна сохранять сомнительную calorie metric;
- можно сохранить workout entry без calorie metric, если остальные базовые факты понятны;
- ambiguous parsing должен быть предпочтительнее ложного точного факта.

### 6. Модель должна оставаться расширяемой к будущим workout metrics

Хотя на этом шаге сохраняется только calorie metric, contract не должен запрещать позже добавить:

- pulse-related factual metrics;
- duration-related normalized fields;
- calories active vs total, если это понадобится как отдельное решение;
- exercise composition отдельным поздним этапом.

Но эти расширения не входят в текущий шаг и потребуют отдельного обсуждения.

## Consequences

- Первый screenshot-based workout flow остаётся узким и полезным.
- Бот работает только с одним screenshot и не пытается угадывать связь между несколькими изображениями.
- Система не уходит в premature OCR полной страницы упражнений.
- Позже можно расширить workout model дальше без переписывания базового journal подхода.

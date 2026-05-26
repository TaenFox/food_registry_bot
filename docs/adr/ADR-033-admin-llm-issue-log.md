# ADR-033: Admin LLM Issue Log for Extraction and Nutrition Failures

## Context

После перехода на LLM-based extraction и nutrition в системе уже возникают технические сбои вида:

- provider error;
- empty structured payload;
- invalid structured payload;
- coverage mismatch между request и nutrition result.

Пока такие случаи видны пользователю как короткое сообщение об ошибке, а администратор может узнать о проблеме только из ручного воспроизведения или серверных логов. Этого недостаточно для повседневой эксплуатации локального и серверного бота.

Нужно добавить минимальную наблюдаемость, которая:

- показывает администратору, есть ли свежие LLM-сбои;
- позволяет увидеть последние проблемные кейсы без доступа к stdout/stderr контейнера;
- не тащит тяжёлую observability-инфраструктуру.

## Decision

### 1. Вводится отдельная таблица `llm_issue_logs`

В БД появляется отдельный технический журнал LLM-сбоев с полями:

- `stage`: `extraction` или `nutrition`;
- `error_code`: краткий технический код вроде `client_error`, `empty_payload`, `invalid_payload`;
- `provider`, `model`;
- `telegram_user_id`, `username`, если они доступны;
- `request_text`, если он был;
- `raw_payload`, если provider уже что-то вернул;
- `technical_message` с точной причиной валидации или provider error;
- `created_at`.

Это отдельная сущность, а не расширение `entries`, потому что:

- issue может возникнуть ещё до создания `entry`;
- не каждая ошибка привязана к успешно сохранённой записи;
- админский обзор должен видеть и failed attempts.

### 2. Логируются только LLM-related issues

На текущем шаге в журнал пишутся только ошибки, пришедшие именно из LLM-based provider flows:

- extraction LLM errors;
- nutrition LLM errors.

Dev-only structured payload mode и static nutrition stub не считаются production LLM issues и в этот журнал не попадают.

### 3. `/admin` показывает агрегаты за последние 24 часа

В overview админа добавляются два счётчика:

- `LLM extraction issues за 24ч`;
- `LLM nutrition issues за 24ч`.

Этого достаточно, чтобы быстро понять, есть ли системная деградация.

### 4. Отдельный admin-экран показывает последние кейсы

В admin panel добавляется отдельный экран `LLM-ошибки`, доступный по inline-кнопке из `/admin`.

Он показывает последние issue records с краткими полями:

- время;
- stage;
- error code;
- user id;
- provider/model;
- сокращённый technical message;
- сокращённый request text;
- сокращённый raw payload.

## Consequences

- Админ получает минимальный эксплуатационный обзор LLM-сбоев прямо из бота.
- LLM issue review остаётся частью одного admin-flow, а не отдельным командным интерфейсом.
- Диагностика invalid payload и provider errors становится возможной без ручного доступа к логам контейнера.
- В БД появляется новая техническая таблица, но без влияния на пользовательские factual данные.
- Если позже понадобится richer telemetry, rate-based alerts или request/response correlation, текущий журнал можно расширить без ломки admin-контракта.

"use strict";

function createReportView({ request, handleAccessError, expire }) {
    const root = document.querySelector("#report");
    const periodButtons = [...root.querySelectorAll("[data-groups]")];
    const range = root.querySelector(".report-dates");
    const results = root.querySelector(".report-results");
    const status = root.querySelector(".report-status p");
    const retry = root.querySelector(".report-retry");
    const metricsRoot = root.querySelector(".report-metrics");
    const coverage = root.querySelector(".report-coverage");
    const title = root.querySelector("#report-dynamics-title");
    const axis = root.querySelector(".report-axis");
    const grid = root.querySelector(".report-gridlines");
    const bars = root.querySelector(".report-bars");
    const goalKey = root.querySelector(".report-goal-key");
    const toleranceKey = root.querySelector(".report-tolerance-key");
    const missingKey = root.querySelector(".report-missing-key");
    const detail = root.querySelector(".report-detail");
    const metrics = {
        calories: { name: "Калории", unit: "ккал", color: "var(--kcal)" },
        protein: { name: "Белки", unit: "г", color: "var(--protein)" },
        fat: { name: "Жиры", unit: "г", color: "var(--fat)" },
        carbs: { name: "Углеводы", unit: "г", color: "var(--carbs)" },
        fiber: { name: "Клетчатка", unit: "г", color: "var(--fiber)" },
        water: { name: "Вода", unit: "мл", color: "var(--water)" },
    };
    const shortDate = new Intl.DateTimeFormat("ru", { day: "2-digit", month: "2-digit", timeZone: "UTC" });
    const dayWords = new Intl.PluralRules("ru");
    const daySuffix = { one: "день", few: "дня", many: "дней", other: "дня" };
    let groupCount = Number(periodButtons[0].dataset.groups);
    let metricCode;
    let selectedGroup;
    let payload;
    let pending;

    function dateRange(from, to, showYear = false) {
        const start = new Date(from);
        const end = new Date(to);
        const endYear = showYear ? " " + end.getUTCFullYear() : "";
        if (from === to) return fullDate.format(end) + endYear;
        if (from.slice(0, 7) === to.slice(0, 7)) {
            return start.getUTCDate() + "–" + fullDate.format(end) + endYear;
        }
        const startYear = start.getUTCFullYear() !== end.getUTCFullYear()
            ? " " + start.getUTCFullYear() : "";
        return fullDate.format(start) + startYear + " — " + fullDate.format(end) + endYear;
    }

    function amount(value, unit) {
        return value === null ? "—" : formatter.format(value) + " " + unit;
    }

    function selectGroup(index, metric) {
        const group = metric.groups[index];
        const { unit } = metrics[metric.code];
        const start = group.summary_date_from;
        const end = group.summary_date_to;
        const current = end >= payload.date_to;
        const days = (Date.parse(end) - Date.parse(start)) / 86_400_000 + 1;
        selectedGroup = start;
        for (const [position, button] of [...bars.children].entries()) {
            button.setAttribute("aria-pressed", String(position === index));
        }
        const label = dateRange(start, end) + (current ? " · текущая группа" : "");
        const note = "Дней с данными: " + group.data_day_count + " из " + days
            + (current ? " · по " + fullDate.format(new Date(payload.date_to)) : "");
        detail.replaceChildren(
            element("strong", label),
            element("p", group.average_value === null
                ? "Нет данных"
                : "Факт в среднем " + amount(group.average_value, unit)
                    + " · цель в среднем " + amount(group.average_goal_value, unit))
        );
        if (group.min_goal_value !== group.max_goal_value) {
            detail.append(element("p", "Цели по дням: "
                + formatter.format(group.min_goal_value) + "–" + amount(group.max_goal_value, unit), "report-note"));
        }
        detail.append(element("p", note, "report-note"));
    }

    function renderChart(metric) {
        const { name, unit, color } = metrics[metric.code];
        const tolerance = payload.tolerance / 100;
        const observationMax = Math.max(1, ...metric.groups.flatMap(group => [
            group.average_value ?? 0, (group.average_goal_value ?? 0) * (1 + tolerance),
        ]));
        const rawStep = observationMax / 3;
        const magnitude = 10 ** Math.floor(Math.log10(rawStep));
        const step = [1, 2, 2.5, 5, 10].find(value => value * magnitude >= rawStep) * magnitude;
        const scale = value => value / (step * 3) * 100 + "%";
        title.textContent = "Динамика · " + name.toLowerCase() + ", " + unit;
        coverage.textContent = "Дней с данными: " + metric.data_days + " из " + payload.days
            + (metric.incomplete_days ? " · неполных: " + metric.incomplete_days : "");
        bars.style.setProperty("--group-count", metric.groups.length);
        root.style.setProperty("--series-color", color);
        goalKey.hidden = !metric.groups.some(group => group.average_goal_value !== null);
        toleranceKey.hidden = goalKey.hidden;
        toleranceKey.lastElementChild.textContent = "Допуск ±" + payload.tolerance + "%";
        missingKey.hidden = metric.groups.every(group => group.average_value !== null);

        axis.replaceChildren();
        grid.replaceChildren();
        if (metric.data_days) {
            for (let index = 0; index <= 3; index++) {
                const label = element("span", formatter.format((3 - index) * step));
                const line = element("span");
                const position = index / 3 * 100 + "%";
                label.style.setProperty("--position", position);
                line.style.setProperty("--position", position);
                axis.append(label);
                grid.append(line);
            }
        }

        bars.replaceChildren(...metric.groups.map((group, index) => {
            const current = group.summary_date_to >= payload.date_to;
            const button = actionButton("", () => {
                if (!expire()) selectGroup(index, metric);
            }, current ? "report-group report-current" : "report-group");
            const value = group.average_value;
            const goal = group.average_goal_value;
            const label = dateRange(group.summary_date_from, group.summary_date_to)
                + (current ? ", текущая группа" : "");
            button.setAttribute("aria-label", value === null ? label + ". Нет данных"
                : label + ". Среднее " + amount(value, unit) + ". Цель в среднем " + amount(goal, unit));
            const track = element("span", undefined, "report-track");
            track.setAttribute("aria-hidden", "true");
            if (value === null) {
                track.append(element("span", "—", "report-no-data"));
            } else {
                const bar = element("span", undefined, "report-bar");
                bar.style.setProperty("--fact", scale(value));
                if (goal !== null) {
                    const band = element("span", undefined, "report-band");
                    band.style.setProperty("--lower", scale(goal * (1 - tolerance)));
                    band.style.setProperty("--band-height", scale(goal * 2 * tolerance));
                    const target = element("span", undefined, "report-goal");
                    target.style.setProperty("--target", scale(goal));
                    track.append(band, bar, target);
                } else {
                    track.append(bar);
                }
            }
            const tick = metric.groups.length > 4
                ? group.summary_date_from.slice(-2)
                : shortDate.format(new Date(group.summary_date_from));
            button.append(track, element("span", tick, "report-tick"));
            return button;
        }));
        const index = metric.groups.findIndex(group => group.summary_date_from === selectedGroup);
        selectGroup(index < 0 ? metric.groups.length - 1 : index, metric);
    }

    function selectMetric(metric) {
        metricCode = metric.code;
        for (const button of metricsRoot.children) {
            button.setAttribute("aria-pressed", String(button.dataset.metric === metricCode));
        }
        renderChart(metric);
    }

    function render() {
        range.textContent = dateRange(payload.date_from, payload.date_to, true);
        for (const period of payload.periods) {
            const button = periodButtons.find(button => Number(button.dataset.groups) === period.groups);
            button.lastElementChild.textContent = period.days + " " + daySuffix[dayWords.select(period.days)];
        }
        if (!payload.metrics.length) {
            status.textContent = "Включите хотя бы одну метрику в настройках бота.";
            return;
        }

        metricsRoot.replaceChildren(...payload.metrics.map(metric => {
            const { name, unit } = metrics[metric.code];
            const average = amount(metric.average, unit);
            const goals = metric.goal_days ? metric.goal_hits + " из " + metric.goal_days : "—";
            const button = actionButton("", () => {
                if (!expire()) selectMetric(metric);
            });
            button.dataset.metric = metric.code;
            button.setAttribute("aria-label", name + ". Среднее " + average + ". Дней с данными: "
                + metric.data_days + ". Дней в цели: " + goals);
            button.append(element("span", name), element("span", average), element("span", goals));
            return button;
        }));
        const metric = payload.metrics.find(metric => metric.code === metricCode)
            ?? payload.metrics.find(metric => metric.data_days > 0) ?? payload.metrics[0];
        selectMetric(metric);
        results.hidden = false;
    }

    async function load() {
        if (expire()) return;
        pending?.abort();
        const controller = new AbortController();
        pending = controller;
        status.textContent = "Загрузка отчёта…";
        retry.hidden = true;
        range.textContent = "";
        results.hidden = true;
        for (const button of periodButtons) {
            button.setAttribute("aria-pressed", String(Number(button.dataset.groups) === groupCount));
        }
        try {
            const result = await request(groupCount, controller.signal);
            if (controller.signal.aborted || expire()) return;
            if (handleAccessError(result)) return;
            if (!result.response.ok) throw new Error("Report request failed");
            payload = result.payload;
            status.textContent = "";
            render();
        } catch {
            if (controller.signal.aborted) return;
            status.textContent = "Не удалось загрузить отчёт.";
            retry.hidden = false;
        } finally {
            if (pending === controller) pending = undefined;
        }
    }

    for (const button of periodButtons) {
        button.addEventListener("click", () => {
            const next = Number(button.dataset.groups);
            if (next === groupCount) return;
            groupCount = next;
            load();
        });
    }
    retry.addEventListener("click", load);

    return {
        open() {
            root.hidden = false;
            load();
        },
        close() {
            pending?.abort();
            pending = undefined;
            root.hidden = true;
            payload = undefined;
            range.textContent = "";
            status.textContent = "";
            results.hidden = true;
            retry.hidden = true;
            metricsRoot.replaceChildren();
            detail.replaceChildren();
            bars.replaceChildren();
        },
    };
}

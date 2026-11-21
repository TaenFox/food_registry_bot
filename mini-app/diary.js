"use strict";

const telegram = window.Telegram?.WebApp;
const diary = document.querySelector("#diary");
const status = document.querySelector("#status");
const date = document.querySelector("#diary-date time");
const scale = document.querySelector(".scale");
const plot = document.querySelector(".plot");
const chart = plot.querySelector("svg");
const dates = document.querySelector(".dates");
const legend = document.querySelector(".legend");
const mealList = document.querySelector(".meals");
const detail = document.querySelector("#metric-detail");
const chartEmpty = document.querySelector("#plot-empty");
const thresholdKey = document.querySelector("#threshold-key");
const metricLabels = {
    calories: "Калории",
    protein: "Белки",
    fat: "Жиры",
    carbs: "Углеводы",
};
const formatter = new Intl.NumberFormat("ru", { maximumFractionDigits: 1 });
const fullDate = new Intl.DateTimeFormat("ru", { day: "numeric", month: "long", timeZone: "UTC" });
let model;
let selectedDay;
let selectedMetric;
let expiresAt;

function element(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
}

function draw(tag, attributes, className) {
    const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (const [name, value] of Object.entries(attributes)) node.setAttribute(name, value);
    if (className) node.setAttribute("class", className);
    chart.append(node);
}

function x(index) {
    return 40 + index * 80;
}

function isWeekend(day) {
    const weekday = new Date(day.date).getUTCDay();
    return weekday === 6 || weekday === 0;
}

function seriesClass(code) {
    return code === "calories" ? "kcal" : code;
}

function renderChart() {
    chart.replaceChildren();
    scale.replaceChildren();
    const thresholdLow = 100 - model.tolerance;
    const thresholdHigh = 100 + model.tolerance;
    const values = model.days.flatMap(day =>
        model.metrics.map(code => day.metrics[code].percent)
    ).filter(Number.isFinite);
    const minimum = Math.max(0, Math.floor(Math.min(thresholdLow - 10, ...values) / 10) * 10);
    const maximum = Math.ceil(Math.max(thresholdHigh + 10, ...values) / 10) * 10;
    const y = value => 12 + (maximum - value) / (maximum - minimum) * 176;

    for (const [index, day] of model.days.entries()) {
        if (isWeekend(day)) {
            draw("rect", { x: x(index) - 40, y: 0, width: 80, height: 200 }, "weekend-zone");
        }
        if (model.metrics.some(code => day.metrics[code].goal !== null)) {
            const left = x(index) - 34;
            draw("rect", {
                x: left, y: y(thresholdHigh), width: 68, height: y(thresholdLow) - y(thresholdHigh),
            }, "range");
            for (const threshold of [thresholdLow, thresholdHigh]) {
                draw("line", { x1: left, x2: left + 68, y1: y(threshold), y2: y(threshold) }, "limit-line");
            }
        }
    }

    const ticks = [maximum, thresholdHigh, thresholdLow, minimum];
    let previousLabelY = -Infinity;
    for (const tick of ticks) {
        const isLimit = tick === thresholdHigh || tick === thresholdLow;
        const position = y(tick);
        if (!isLimit) {
            draw("line", { x1: 0, x2: 400, y1: position, y2: position }, "axis");
        }
        if (position - previousLabelY >= 18) {
            const label = element("span", tick + "%", isLimit ? "limit" : "");
            label.style.setProperty("--y", position / 2 + "%");
            scale.append(label);
            previousLabelY = position;
        }
    }

    for (const code of model.metrics) {
        const style = seriesClass(code);
        let previous = null;
        for (const [index, day] of model.days.entries()) {
            const value = day.metrics[code].percent;
            if (!Number.isFinite(value)) {
                previous = null;
                continue;
            }
            const point = { x: x(index), y: y(value) };
            if (previous) {
                draw("line", {
                    x1: previous.x, y1: previous.y, x2: point.x, y2: point.y,
                }, "series " + style + (day.current ? " partial" : ""));
            }
            draw("circle", { cx: point.x, cy: point.y, r: 3 },
                "point " + style + (day.current ? " current" : ""));
            previous = point;
        }
    }

    chartEmpty.hidden = values.length > 0;
    chartEmpty.textContent = model.metrics.length
        ? "Пока нет данных для сравнения с целями."
        : "Включите показатели КБЖУ в настройках бота.";
    thresholdKey.hidden = !model.metrics.length;
    thresholdKey.lastElementChild.textContent = `Допуск ${thresholdLow}–${thresholdHigh}%`;
}

function renderMetric() {
    for (const button of legend.querySelectorAll("button")) {
        button.setAttribute("aria-pressed", String(button.dataset.metric === selectedMetric));
    }
    detail.hidden = !selectedMetric;
    detail.textContent = "";
    if (!selectedMetric) return;

    const observation = model.days[selectedDay].metrics[selectedMetric];
    const unit = selectedMetric === "calories" ? "ккал" : "г";
    const amount = value => value === null ? "—" : formatter.format(value) + " " + unit;
    let text = `${metricLabels[selectedMetric]} · ${amount(observation.value)} из ${amount(observation.goal)}`;
    if (observation.goal !== null) {
        text += ` · пороги ${formatter.format(observation.lower)}–${amount(observation.upper)}`;
    }
    detail.textContent = text;
}

function calories(value) {
    return value === null ? "—" : formatter.format(value) + " ккал";
}

function renderMeals(day) {
    mealList.replaceChildren();
    if (!day.complete) {
        mealList.append(element("p", "У части записей неполные данные о питании.", "meal-note"));
    }
    if (!day.meals.length) {
        mealList.append(element("p", "За этот пищевой день пока нет записей о еде.", "meal-note"));
    }
    for (const meal of day.meals) {
        const group = element("article", undefined, "meal");
        const header = element("header");
        const heading = element("h2");
        const time = element("time", meal.time);
        time.dateTime = meal.time;
        const total = calories(meal.calories);
        heading.append(time, element("span", meal.complete ? total : total + " · неполные данные"));
        header.append(heading);
        const list = element("ul");
        for (const dish of meal.dishes) {
            const row = element("li");
            row.append(element("span", dish.name), element("span", calories(dish.calories), "amount"));
            list.append(row);
        }
        group.append(header, list);
        mealList.append(group);
    }
}

function selectDay(index) {
    if (expire()) return;
    selectedDay = index;
    const day = model.days[index];
    date.dateTime = day.date;
    date.textContent = fullDate.format(new Date(day.date));
    plot.style.setProperty("--selected", x(index) / 4 + "%");
    for (const button of dates.querySelectorAll("button")) {
        button.setAttribute("aria-pressed", String(Number(button.dataset.index) === index));
    }
    renderMetric();
    renderMeals(day);
}

function renderControls() {
    dates.replaceChildren();
    for (const [index, day] of model.days.entries()) {
        const weekday = new Date(day.date).getUTCDay();
        const suffix = weekday === 6 ? " сб" : weekday === 0 ? " вс" : "";
        const button = element("button", undefined, isWeekend(day) ? "weekend" : "");
        button.type = "button";
        button.dataset.index = index;
        button.style.setProperty("--x", x(index) / 4 + "%");
        button.setAttribute("aria-label", fullDate.format(new Date(day.date)) + (day.current ? ", сегодня" : ""));
        button.append(element("span", Number(day.date.slice(-2)) + suffix));
        button.addEventListener("click", () => selectDay(index));
        dates.append(button);
    }

    legend.replaceChildren();
    for (const code of model.metrics) {
        const item = element("li", undefined, seriesClass(code));
        const button = element("button");
        button.type = "button";
        button.dataset.metric = code;
        const swatch = element("i");
        swatch.setAttribute("aria-hidden", "true");
        button.append(swatch, document.createTextNode(metricLabels[code]));
        button.addEventListener("click", () => {
            if (expire()) return;
            selectedMetric = selectedMetric === code ? undefined : code;
            renderMetric();
        });
        item.append(button);
        legend.append(item);
    }
}

function showStatus(message) {
    diary.hidden = true;
    status.hidden = false;
    status.textContent = message;
}

function expire() {
    if (!expiresAt || Date.now() < expiresAt) return false;
    model = undefined;
    mealList.replaceChildren();
    chart.replaceChildren();
    detail.textContent = "";
    date.textContent = "";
    date.removeAttribute("datetime");
    dates.replaceChildren();
    legend.replaceChildren();
    showStatus("Срок входа истёк. Откройте дневник заново из Telegram.");
    return true;
}

async function openDiary() {
    if (telegram?.colorScheme) document.documentElement.style.colorScheme = telegram.colorScheme;
    telegram?.ready();
    if (!telegram?.initData) {
        showStatus("Откройте дневник из Telegram.");
        return;
    }

    const requestedAt = Date.now();
    try {
        const response = await fetch("/api/diary", {
            headers: { Authorization: "tma " + telegram.initData },
            cache: "no-store",
            credentials: "omit",
        });
        if (response.status === 401) {
            showStatus("Не удалось подтвердить вход. Откройте дневник заново из Telegram.");
            return;
        }
        if (response.status === 403) {
            showStatus("Доступ к дневнику не разрешён.");
            return;
        }
        if (response.status === 404 && (await response.json()).error === "profile_missing") {
            showStatus("Сначала отправьте /start боту, затем откройте дневник снова.");
            return;
        }
        if (!response.ok) throw new Error("Diary request failed");

        model = await response.json();
        if (!Number.isFinite(model.expires_in_ms)) throw new Error("Missing authorization expiry");
        expiresAt = requestedAt + model.expires_in_ms;
        if (expire()) return;
        setTimeout(expire, expiresAt - Date.now());
        renderChart();
        renderControls();
        selectDay(model.days.findIndex(day => day.current));
        if (expire()) return;
        status.hidden = true;
        diary.hidden = false;
    } catch {
        showStatus("Не удалось загрузить дневник. Попробуйте открыть его ещё раз.");
    }
}

document.addEventListener("visibilitychange", expire);
window.addEventListener("pageshow", expire);
telegram?.onEvent("themeChanged", () => {
    document.documentElement.style.colorScheme = telegram.colorScheme;
});
openDiary();

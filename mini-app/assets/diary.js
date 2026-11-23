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
const actionStatus = document.querySelector("#action-status");
const screenNavigation = document.querySelector("#screen-navigation");
const screenPicker = screenNavigation.querySelector(".screen-picker");
const screenOptions = screenNavigation.querySelector("#screen-options");
const metricLabels = {
    calories: "Калории",
    protein: "Белки",
    fat: "Жиры",
    carbs: "Углеводы",
};
const formatter = new Intl.NumberFormat("ru", { maximumFractionDigits: 1 });
const fullDate = new Intl.DateTimeFormat("ru", { day: "numeric", month: "long", timeZone: "UTC" });
const shortWeekday = new Intl.DateTimeFormat("ru", { weekday: "short", timeZone: "UTC" });
const longWeekday = new Intl.DateTimeFormat("ru", { weekday: "long", timeZone: "UTC" });
let model;
let selectedDay;
let selectedMetric;
let expiresAt;
let expiryTimer;
let openedDish;
let savingDish = false;
let activeScreen = "diary";

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

    const series = selectedMetric
        ? [...model.metrics.filter(code => code !== selectedMetric), selectedMetric]
        : model.metrics;
    for (const code of series) {
        const style = seriesClass(code);
        const emphasis = !selectedMetric ? "" : code === selectedMetric ? " highlighted" : " subdued";
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
                }, "series " + style + emphasis + (day.current ? " partial" : ""));
            }
            draw("circle", { cx: point.x, cy: point.y, r: 3 },
                "point " + style + emphasis + (day.current ? " current" : ""));
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
    if (observation.value !== null && !observation.complete) text += " · неполные данные";
    detail.textContent = text;
}

function formatAmount(value, code) {
    const unit = code === "calories" ? "ккал" : "г";
    return value === null ? "—" : formatter.format(value) + " " + unit;
}

function actionButton(label, handler, className) {
    const button = element("button", label, className);
    button.type = "button";
    button.addEventListener("click", handler);
    return button;
}

function setActionStatus(message) {
    actionStatus.textContent = message;
    actionStatus.hidden = !message;
}

function focusDish(itemId, selector = ".dish") {
    mealList.querySelector(`[data-dish-id="${itemId}"] ${selector}`)?.focus({ preventScroll: true });
}

function closeDish(itemId) {
    openedDish = undefined;
    renderMeals(model.days[selectedDay]);
    focusDish(itemId);
}

function numberField(labelText, name, value) {
    const label = element("label", labelText);
    const input = element("input");
    input.type = "number";
    input.name = name;
    input.min = name === "weight" ? "1" : "0";
    input.step = name === "weight" ? "1" : "any";
    input.required = true;
    if (value !== undefined) input.value = value;
    label.append(input);
    return label;
}

function formActions(itemId, label) {
    const actions = element("div", undefined, "dish-actions");
    const cancel = actionButton("Отмена", () => closeDish(itemId));
    const submit = element("button", label, "primary");
    submit.type = "submit";
    actions.append(cancel, submit);
    return actions;
}

function editDishForm(meal, dish) {
    const form = element("form");
    const grams = ["g", "г", "гр"].includes(dish.unit?.trim().toLowerCase()) && dish.quantity > 0
        ? dish.quantity : undefined;
    form.append(numberField("Вес, г", "weight", grams));
    if (!grams && dish.quantity != null && dish.unit) {
        form.append(element("p", `Сохранено: ${dish.quantity} ${dish.unit === "ml" ? "мл" : dish.unit}`, "meal-note"));
    }
    const fieldset = element("fieldset");
    fieldset.append(element("legend", "КБЖУ на 100 г"));
    const fields = element("div", undefined, "dish-fields");
    const initial = {};
    for (const code of Object.keys(metricLabels)) {
        const value = grams && dish.metrics[code] !== null
            ? Number((dish.metrics[code] * 100 / grams).toFixed(4))
            : undefined;
        initial[code] = value;
        const unit = code === "calories" ? "ккал" : "г";
        fields.append(numberField(`${metricLabels[code]}, ${unit}`, code, value));
    }
    fieldset.append(fields);
    form.append(fieldset, formActions(dish.id, "Сохранить"));
    form.addEventListener("submit", event => {
        event.preventDefault();
        const data = new FormData(form);
        const per100g = {};
        for (const code of Object.keys(metricLabels)) {
            const value = Number(data.get(code));
            if (initial[code] !== value) per100g[code] = value;
        }
        saveDish(form, {
            action: "edit", entry_id: meal.id, item_id: dish.id,
            revision: dish.revision, weight: Number(data.get("weight")), per_100g: per100g,
        }, "Блюдо обновлено.");
    });
    return form;
}

function moveDishForm(day, meal, dish) {
    const form = element("form");
    const label = element("label", "Перенести в запись");
    const select = element("select");
    for (const candidate of day.meals) {
        if (candidate.id === meal.id) continue;
        const firstDish = candidate.dishes[0]?.name;
        const option = element("option", firstDish ? `${candidate.time} · ${firstDish}` : candidate.time);
        option.value = candidate.id;
        select.append(option);
    }
    label.append(select);
    form.append(label);
    const lastDish = meal.dishes.length === 1;
    if (lastDish) {
        form.append(element("p", `После переноса запись в ${meal.time} будет удалена.`, "dish-warning"));
    }
    form.append(formActions(dish.id, lastDish ? "Перенести и удалить" : "Перенести"));
    form.addEventListener("submit", event => {
        event.preventDefault();
        saveDish(form, {
            action: "move", entry_id: meal.id, item_id: dish.id,
            target_entry_id: Number(select.value), confirm_delete: lastDish,
        }, lastDish ? "Блюдо перенесено, пустая запись удалена." : "Блюдо перенесено.");
    });
    return form;
}

function dishPanel(day, meal, dish) {
    const panel = element("div", undefined, "dish-panel");
    if (openedDish.mode === "edit") {
        panel.append(editDishForm(meal, dish));
    } else if (openedDish.mode === "move") {
        panel.append(moveDishForm(day, meal, dish));
    } else {
        const actions = element("div", undefined, "dish-actions");
        const open = (mode, selector) => {
            openedDish.mode = mode;
            renderMeals(day);
            focusDish(dish.id, selector);
        };
        const edit = actionButton("Изменить", () => open("edit", "input"));
        const move = actionButton("Перенести", () => open("move", "select"));
        move.disabled = day.meals.length < 2;
        actions.append(edit, move);
        panel.append(actions);
    }
    return panel;
}

function renderMeals(day) {
    const code = selectedMetric || "calories";
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
        const summary = meal.totals[code];
        const total = formatAmount(summary.value, code);
        heading.append(time, element("span", summary.complete || summary.value === null
            ? total : total + " · неполные данные"));
        header.append(heading);
        const list = element("ul");
        for (const dish of meal.dishes) {
            const row = element("li");
            row.dataset.dishId = dish.id;
            const expanded = openedDish?.id === dish.id;
            const control = actionButton(undefined, () => {
                if (expire()) return;
                openedDish = expanded ? undefined : { id: dish.id, mode: "actions" };
                setActionStatus("");
                renderMeals(day);
                focusDish(dish.id, expanded ? ".dish" : ".dish-actions button");
            }, "dish");
            control.setAttribute("aria-expanded", String(expanded));
            control.append(element("span", dish.name), element("span", formatAmount(dish.metrics[code], code), "amount"));
            row.append(control);
            if (expanded) row.append(dishPanel(day, meal, dish));
            list.append(row);
        }
        group.append(header, list);
        mealList.append(group);
    }
}

function selectDay(index) {
    if (expire()) return;
    selectedDay = index;
    openedDish = undefined;
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
        const calendarDate = new Date(day.date);
        const button = element("button", undefined, isWeekend(day) ? "weekend" : "");
        button.type = "button";
        button.dataset.index = index;
        button.style.setProperty("--x", x(index) / 4 + "%");
        button.setAttribute("aria-label", fullDate.format(calendarDate) + ", " + longWeekday.format(calendarDate)
            + (day.current ? ", сегодня" : ""));
        button.append(element("span", Number(day.date.slice(-2)) + " " + shortWeekday.format(calendarDate)));
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
            openedDish = undefined;
            renderChart();
            renderMetric();
            renderMeals(model.days[selectedDay]);
        });
        item.append(button);
        legend.append(item);
    }
}

function showStatus(message) {
    diary.hidden = true;
    screenNavigation.hidden = true;
    status.hidden = false;
    status.textContent = message;
}

function clearDiary(message) {
    model = undefined;
    openedDish = undefined;
    clearTimeout(expiryTimer);
    reportView.close();
    showScreenOptions(false);
    mealList.replaceChildren();
    chart.replaceChildren();
    scale.replaceChildren();
    detail.textContent = "";
    date.textContent = "";
    date.removeAttribute("datetime");
    dates.replaceChildren();
    legend.replaceChildren();
    setActionStatus("");
    showStatus(message);
}

function expire() {
    if (!expiresAt || Date.now() < expiresAt) return false;
    clearDiary("Срок входа истёк. Откройте дневник заново из Telegram.");
    return true;
}

async function miniAppRequest(path, { command, signal } = {}) {
    const requestedAt = Date.now();
    const headers = { Authorization: "tma " + telegram.initData };
    const options = { headers, signal, cache: "no-store", credentials: "omit" };
    if (command) {
        headers["Content-Type"] = "application/json";
        options.method = "POST";
        options.body = JSON.stringify(command);
    }
    const response = await fetch(path, options);
    const payload = await response.json().catch(() => ({}));
    return { response, payload, requestedAt };
}

function diaryRequest(command) {
    return miniAppRequest("/api/diary", { command });
}

function handleAccessError({ response, payload }) {
    if (response.status === 401) {
        clearDiary("Не удалось подтвердить вход. Откройте дневник заново из Telegram.");
    } else if (response.status === 403) {
        clearDiary("Доступ к дневнику не разрешён.");
    } else if (response.status === 404 && payload.error === "profile_missing") {
        clearDiary("Сначала отправьте /start боту, затем откройте дневник снова.");
    } else {
        return false;
    }
    return true;
}

function installDiary({ payload, requestedAt }) {
    if (!Number.isFinite(payload.expires_in_ms)) throw new Error("Missing authorization expiry");
    const previousDate = model?.days[selectedDay]?.date;
    model = payload;
    expiresAt = requestedAt + model.expires_in_ms;
    if (expire()) return;
    clearTimeout(expiryTimer);
    expiryTimer = setTimeout(expire, expiresAt - Date.now());
    if (!model.metrics.includes(selectedMetric)) selectedMetric = undefined;
    const previousIndex = model.days.findIndex(day => day.date === previousDate);
    renderChart();
    renderControls();
    selectDay(previousIndex >= 0 ? previousIndex : model.days.findIndex(day => day.current));
    if (expire()) return;
    status.hidden = true;
    diary.hidden = activeScreen !== "diary";
    screenNavigation.hidden = false;
}

async function saveDish(form, command, successMessage) {
    if (savingDish || expire()) return;
    savingDish = true;
    setActionStatus("Сохраняю…");
    const controls = [...form.elements];
    controls.forEach(control => { control.disabled = true; });
    try {
        const result = await diaryRequest(command);
        if (handleAccessError(result)) return;
        if (result.response.ok) {
            installDiary(result);
            if (!model) return;
            setActionStatus(successMessage);
            focusDish(command.item_id);
        } else if (result.response.status === 409 || result.response.status === 404) {
            const latest = await diaryRequest();
            if (handleAccessError(latest)) return;
            if (!latest.response.ok) throw new Error("Diary refresh failed");
            installDiary(latest);
            if (model) setActionStatus("Запись изменилась. Дневник обновлён, повторите действие.");
        } else if (result.payload.error === "missing_per_100g") {
            setActionStatus("Укажите все значения КБЖУ на 100 г.");
        } else if (result.payload.error === "different_food_day") {
            setActionStatus("Перенос доступен только внутри одного пищевого дня.");
        } else {
            setActionStatus("Не удалось сохранить изменения. Проверьте введённые данные.");
        }
    } catch {
        if (model) setActionStatus("Не удалось подтвердить сохранение. Попробуйте ещё раз.");
    } finally {
        savingDish = false;
        controls.forEach(control => { control.disabled = false; });
    }
}

async function openDiary() {
    if (telegram?.colorScheme) document.documentElement.style.colorScheme = telegram.colorScheme;
    telegram?.ready();
    if (!telegram?.initData) {
        showStatus("Откройте дневник из Telegram.");
        return;
    }

    try {
        const result = await diaryRequest();
        if (handleAccessError(result)) return;
        if (!result.response.ok) throw new Error("Diary request failed");
        installDiary(result);
    } catch {
        clearDiary("Не удалось загрузить дневник. Попробуйте открыть его ещё раз.");
    }
}

function showScreenOptions(open) {
    screenOptions.hidden = !open;
    screenPicker.setAttribute("aria-expanded", String(open));
}

const reportView = createReportView({
    request: (groups, signal) => miniAppRequest("/api/report?groups=" + groups, { signal }),
    handleAccessError,
    expire,
});

screenPicker.addEventListener("click", () => {
    if (!expire()) showScreenOptions(screenOptions.hidden);
});
screenOptions.addEventListener("click", event => {
    const option = event.target.closest("[data-screen]");
    if (!option || expire()) return;
    showScreenOptions(false);
    screenPicker.focus();
    if (activeScreen === option.dataset.screen) return;

    activeScreen = option.dataset.screen;
    screenOptions.querySelector('[aria-current="page"]').removeAttribute("aria-current");
    option.setAttribute("aria-current", "page");
    screenPicker.firstElementChild.textContent = option.textContent;
    diary.hidden = activeScreen !== "diary";
    if (diary.hidden) reportView.open();
    else reportView.close();
    window.scrollTo(0, 0);
});
document.addEventListener("click", event => {
    if (!screenOptions.hidden && !screenNavigation.contains(event.target)) showScreenOptions(false);
});
document.addEventListener("keydown", event => {
    if (event.key !== "Escape" || screenOptions.hidden) return;
    showScreenOptions(false);
    screenPicker.focus();
});

document.addEventListener("visibilitychange", expire);
window.addEventListener("pageshow", expire);
telegram?.onEvent("themeChanged", () => {
    document.documentElement.style.colorScheme = telegram.colorScheme;
});
openDiary();

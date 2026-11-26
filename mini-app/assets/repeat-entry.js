"use strict";

function createRepeatEntryView({ request, findHistory, handleAccessError, expire, defaults, onSaved }) {
    const dialog = document.querySelector("#repeat-composer");
    const title = dialog.querySelector("h2");
    const close = dialog.querySelector(".entry-close");
    const form = dialog.querySelector("form");
    const items = form.querySelector(".repeat-items");
    const date = form.elements.local_date;
    const time = form.elements.local_time;
    const status = form.querySelector(".entry-status");
    const submit = form.querySelector(".entry-submit");
    const history = dialog.querySelector(".repeat-history");
    const search = history.querySelector("input");
    const historyItems = history.querySelector(".history-items");
    const historyStatus = history.querySelector(".history-status");
    const apply = history.querySelector(".history-apply");
    const maxItems = 24;
    const draft = new Map();
    const selected = new Map();
    const errors = {
        invalid_entry: "Проверьте состав, дату и время записи.",
        invalid_entry_time: "Такого местного времени нет. Выберите другое.",
        entry_request_conflict: "Запрос изменился. Нажмите «Сохранить запись» ещё раз.",
        entry_missing: "Исходная запись исчезла. Уберите блюдо и выберите другое.",
        dish_changed: "Одно из исходных блюд изменилось. Уберите его и выберите заново.",
        portion_not_scalable: "Для этой порции нет исходного веса или объёма.",
    };
    let searchTimer;
    let pendingSearch;
    let onHistoryBack;
    let attempt;
    let submitting = false;

    function addDish(dish) {
        const unit = portionUnit(dish.unit);
        draft.set(dish.id, { dish, unit, quantity: unit && dish.quantity > 0 ? dish.quantity : null });
    }

    function scaledValue({ dish, quantity }, code) {
        const value = dish.metrics[code];
        if (!Number.isFinite(value)) return null;
        return quantity === null ? value : value * (quantity / dish.quantity);
    }

    function updateTotals() {
        const entries = [...draft.values()];
        const valid = entries.length > 0 && entries.every(({ quantity }) =>
            quantity === null || (Number.isInteger(quantity) && quantity > 0 && quantity <= 2_147_483_647));
        submit.disabled = submitting || !valid || draft.size > maxItems;
        const totals = Object.fromEntries([...Object.keys(editableMetricLabels), "fiber"].map(code => {
            const known = entries.map(item => scaledValue(item, code)).filter(Number.isFinite);
            return [code, { value: known.length && valid ? known.reduce((sum, value) => sum + value, 0) : null,
                complete: entries.every(item => item.dish.water || Number.isFinite(scaledValue(item, code))) }];
        }));
        for (const item of entries) {
            const value = scaledValue(item, "calories");
            items.querySelector(`[data-repeat-calories="${item.dish.id}"]`).textContent =
                Number.isFinite(value) ? formatAmount(value, "calories") : "Калории неизвестны";
        }
        form.querySelector(".repeat-calories").textContent = formatAmount(totals.calories.value, "calories");
        const macros = [["Б", "protein"], ["Ж", "fat"], ["У", "carbs"], ["Клетчатка", "fiber"]];
        const incomplete = entries.length && Object.values(totals).some(total => !total.complete);
        form.querySelector(".repeat-macros").textContent = entries.length
            ? macros.map(([label, code]) => label + " " + formatAmount(totals[code].value, code)).join(" · ")
                + (incomplete ? " · неполные данные" : "")
            : "";
    }

    function portion(dish) {
        const unit = portionUnit(dish.unit);
        return dish.quantity > 0
            ? `${formatter.format(dish.quantity)} ${portionUnits[unit] || dish.unit || ""}`.trim()
            : "Порция неизвестна";
    }

    function renderDraft() {
        items.replaceChildren();
        if (!draft.size) items.append(element("p", "Выберите блюда из истории.", "repeat-meta"));
        for (const entry of draft.values()) {
            const { dish, unit, quantity } = entry;
            const row = element("div", undefined, "repeat-row");
            const label = element("div");
            const amount = element("span", undefined, "repeat-meta");
            amount.dataset.repeatCalories = dish.id;
            label.append(element("strong", dish.name), amount);
            const remove = actionButton("×", () => {
                draft.delete(dish.id);
                status.textContent = "";
                renderDraft();
                form.querySelector(".entry-history").focus({ preventScroll: true });
            }, "repeat-remove");
            remove.setAttribute("aria-label", `Убрать «${dish.name}»`);
            const amountControl = element("label", undefined, "repeat-quantity");
            if (quantity !== null) {
                const input = element("input");
                input.type = "number";
                input.inputMode = "numeric";
                input.min = 1;
                input.max = 2_147_483_647;
                input.required = true;
                input.value = Number.isFinite(quantity) ? quantity : "";
                input.setAttribute("aria-label", `Порция · ${dish.name}, ${portionUnits[unit]}`);
                input.addEventListener("input", () => {
                    entry.quantity = input.valueAsNumber;
                    status.textContent = "";
                    updateTotals();
                });
                amountControl.append(input, element("span", portionUnits[unit], "repeat-meta"));
            } else {
                amountControl.append(element("span", portion(dish), "repeat-meta"));
            }
            row.append(label, amountControl, remove);
            items.append(row);
        }
        updateTotals();
    }

    function updateSelection() {
        const count = selected.size;
        apply.textContent = count ? `Добавить выбранные · ${count}` : "Добавить выбранные";
        apply.disabled = !count;
        const full = draft.size + count >= maxItems;
        historyItems.querySelectorAll("button").forEach(button => {
            const chosen = selected.has(Number(button.dataset.dish));
            button.disabled = full && !chosen;
            button.setAttribute("aria-pressed", String(chosen));
            button.lastElementChild.textContent = chosen ? "✓" : "";
        });
    }

    function renderHistory(results) {
        historyItems.replaceChildren();
        for (const dish of results) {
            if (draft.has(dish.id)) continue;
            const row = element("li");
            const choice = actionButton(undefined, () => {
                if (selected.has(dish.id)) selected.delete(dish.id);
                else selected.set(dish.id, dish);
                updateSelection();
            }, "history-choice");
            choice.dataset.dish = dish.id;
            const label = element("div");
            const sourceDate = fullDate.format(new Date(dish.occurred_at.slice(0, 10)));
            label.append(element("strong", `${dish.name} · ${portion(dish)}`),
                element("span", `Последний раз ${sourceDate} · ${dish.occurred_at.slice(11, 16)}`, "repeat-meta"));
            const macros = Object.entries({ calories: "", protein: "Б ", fat: "Ж ", carbs: "У ", fiber: "Клетчатка " })
                .filter(([code]) => Number.isFinite(dish.metrics[code]))
                .map(([code, title]) => title + formatAmount(dish.metrics[code], code));
            if (macros.length) label.append(element("span", macros.join(" · "), "repeat-meta"));
            const mark = element("span", "", "history-mark");
            mark.setAttribute("aria-hidden", "true");
            choice.append(label, mark);
            row.append(choice);
            historyItems.append(row);
        }
        historyStatus.textContent = historyItems.childElementCount ? "" : "Подходящих блюд нет.";
        updateSelection();
    }

    function stopSearch() {
        clearTimeout(searchTimer);
        pendingSearch?.abort();
        pendingSearch = undefined;
    }

    async function loadHistory() {
        if (expire() || !dialog.open || history.hidden) return;
        stopSearch();
        const controller = new AbortController();
        pendingSearch = controller;
        historyStatus.textContent = "Ищу блюда…";
        historyItems.replaceChildren();
        try {
            const result = await findHistory(search.value.trim(), controller.signal);
            if (pendingSearch !== controller || expire() || handleAccessError(result)) return;
            if (!result.response.ok) throw new Error("History request failed");
            renderHistory(result.payload.items);
        } catch {
            if (pendingSearch === controller) {
                historyStatus.textContent = "Не удалось загрузить историю.";
                historyStatus.append(" ", actionButton("Повторить", loadHistory, "entry-history"));
            }
        } finally {
            if (pendingSearch === controller) pendingSearch = undefined;
        }
    }

    function showHistory() {
        selected.clear();
        form.hidden = true;
        history.hidden = false;
        title.textContent = "Из истории";
        close.setAttribute("aria-label", "Назад к записи");
        search.value = "";
        updateSelection();
        loadHistory();
    }

    function showDraft() {
        stopSearch();
        onHistoryBack = undefined;
        history.hidden = true;
        form.hidden = false;
        title.textContent = "Новая запись";
        close.setAttribute("aria-label", "Закрыть форму");
        renderDraft();
        status.textContent = draft.size > maxItems ? `Оставьте до ${maxItems} блюд в записи.` : "";
    }

    function clear() {
        stopSearch();
        if (dialog.open) dialog.close();
        form.reset();
        draft.clear();
        selected.clear();
        items.replaceChildren();
        historyItems.replaceChildren();
        status.textContent = "";
        attempt = undefined;
        onHistoryBack = undefined;
    }

    function start(initial) {
        if (expire() || dialog.open || submitting) return false;
        clear();
        date.value = initial.date;
        time.value = initial.time;
        return true;
    }

    function openMeal(meal) {
        if (!start(defaults(true))) return;
        meal.dishes.forEach(dish => addDish({ ...dish, entry_id: meal.id }));
        showDraft();
        dialog.showModal();
    }

    function openHistory(initial, back) {
        if (!start(initial)) return;
        onHistoryBack = back;
        dialog.showModal();
        showHistory();
    }

    close.addEventListener("click", () => {
        if (submitting) return;
        if (history.hidden) clear();
        else if (onHistoryBack) {
            const back = onHistoryBack;
            clear();
            back();
        } else showDraft();
    });
    dialog.addEventListener("cancel", event => {
        event.preventDefault();
        close.click();
    });
    form.querySelector(".entry-history").addEventListener("click", showHistory);
    search.addEventListener("input", () => {
        stopSearch();
        historyItems.replaceChildren();
        historyStatus.textContent = "Ищу блюда…";
        searchTimer = setTimeout(loadHistory, 200);
    });
    apply.addEventListener("click", () => {
        selected.forEach(addDish);
        showDraft();
        form.querySelector(".entry-history").focus({ preventScroll: true });
    });

    form.addEventListener("submit", async event => {
        event.preventDefault();
        if (submitting || expire() || submit.disabled) return;
        const command = { local_date: date.value, local_time: time.value, items: [...draft.values()].map(
            ({ dish, quantity }) => ({ item_id: dish.id, entry_id: dish.entry_id, revision: dish.revision,
                ...(quantity !== null && quantity !== dish.quantity ? { quantity } : {}) })
        ) };
        const signature = JSON.stringify(command);
        if (attempt?.signature !== signature) attempt = { signature, id: crypto.randomUUID() };
        submitting = true;
        const controls = [close, ...form.elements];
        controls.forEach(control => { control.disabled = true; });
        status.textContent = "Сохраняю…";
        try {
            const result = await request({ ...command, request_id: attempt.id });
            if (expire() || handleAccessError(result)) return;
            if (result.response.ok) {
                await onSaved(result.payload.summary_date);
                clear();
            } else {
                if (result.payload.error === "entry_request_conflict") attempt = undefined;
                status.textContent = errors[result.payload.error] || "Не удалось сохранить запись.";
            }
        } catch {
            if (dialog.open) status.textContent = "Не удалось подтвердить сохранение. Попробуйте ещё раз.";
        } finally {
            submitting = false;
            controls.forEach(control => { control.disabled = false; });
        }
    });

    return { openMeal, openHistory, clear };
}

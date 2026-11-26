"use strict";

function createSettingsView({ request, handleAccessError, expire, setBusy, refreshDiary }) {
    const root = document.querySelector("#settings");
    const form = root.querySelector("form");
    const sections = form.querySelector(".settings-sections");
    const loading = root.querySelector(".settings-load");
    const loadStatus = loading.querySelector("p");
    const retry = loading.querySelector(".settings-retry");
    const status = form.querySelector(".settings-status");
    const save = form.querySelector(".settings-save");
    const goalMetrics = { ...metricLabels, water: "Вода" };
    const dietSection = form.querySelector('[data-section="diets"]');
    const rows = Object.fromEntries(
        [...sections.querySelectorAll(".settings-rows")].map(container =>
            [container.closest("details").dataset.section, container])
    );
    let current;
    let draft;
    let pending;

    function patch() {
        const result = {};
        for (const [key, value] of Object.entries(draft)) {
            if (typeof value === "object") {
                const changed = Object.entries(value).filter(([code, item]) => item !== current[key][code]);
                if (changed.length) result[key] = Object.fromEntries(changed);
            } else if (value !== current[key]) {
                result[key] = value;
            }
        }
        return result;
    }

    function updateDraft() {
        save.disabled = !Object.keys(patch()).length;
        status.textContent = "";
    }

    function goalRow(code, value, step) {
        const name = goalMetrics[code];
        const unit = code === "calories" ? "ккал" : code === "water" ? "мл" : "г";
        const row = element("div", undefined, "settings-row");
        const stepper = element("div", undefined, "settings-stepper");
        const amount = element("output");
        amount.setAttribute("aria-label", name);
        const decrease = actionButton("−", () => adjust(-step));
        const increase = actionButton("+", () => adjust(step));
        decrease.setAttribute("aria-label", "Уменьшить цель · " + name);
        increase.setAttribute("aria-label", "Увеличить цель · " + name);
        const maximum = 2_147_483_647;
        function render() {
            amount.value = formatter.format(value) + " " + unit;
            decrease.disabled = value <= step;
            increase.disabled = value > maximum - step;
        }
        function adjust(delta) {
            value = delta < 0 ? Math.max(step, value + delta) : value + delta;
            draft.goals[code] = value;
            render();
            updateDraft();
        }
        render();
        stepper.append(decrease, amount, increase);
        row.append(element("span", name), stepper);
        return row;
    }

    function toggle(label, checked, change) {
        const row = element("label", label, "settings-row");
        const input = element("input");
        input.type = "checkbox";
        input.setAttribute("role", "switch");
        input.checked = checked;
        input.addEventListener("change", () => {
            change(input.checked);
            updateDraft();
        });
        row.append(input);
        return row;
    }

    function choice(payload, key, label, display = String) {
        const row = element("label", label, "settings-row");
        const select = element("select");
        for (const value of payload.choices[key]) {
            const option = element("option", display(value));
            option.value = value;
            option.selected = value === draft[key];
            select.append(option);
        }
        select.addEventListener("change", () => {
            draft[key] = typeof current[key] === "number" ? Number(select.value) : select.value;
            updateDraft();
        });
        row.append(select);
        return row;
    }

    function timezoneField(zones) {
        const fields = element("div", undefined, "settings-stack");
        const searchLabel = element("label", "Поиск по городу или часовому поясу");
        const search = element("input");
        search.type = "search";
        search.autocomplete = "off";
        searchLabel.append(search);
        const zoneLabel = element("label", "Часовой пояс");
        const select = element("select");
        zoneLabel.append(select);
        const device = Intl.DateTimeFormat().resolvedOptions().timeZone;

        function showZones() {
            const query = search.value.trim().toLowerCase().replaceAll(" ", "_");
            const matches = zones.filter(zone => zone !== draft.timezone && zone.toLowerCase().includes(query));
            select.replaceChildren(...[draft.timezone, ...matches].map(zone => {
                const option = element("option", zone.replaceAll("_", " "));
                option.value = zone;
                return option;
            }));
            select.value = draft.timezone;
        }
        function choose(zone) {
            draft.timezone = zone;
            search.value = "";
            showZones();
            updateDraft();
        }
        search.addEventListener("input", showZones);
        search.addEventListener("keydown", event => {
            if (event.key === "Enter") {
                event.preventDefault();
                select.focus();
            }
        });
        select.addEventListener("change", () => choose(select.value));
        fields.append(searchLabel, zoneLabel);
        if (zones.includes(device)) {
            fields.append(actionButton("Использовать часовой пояс устройства", () => choose(device)));
        }
        showZones();
        return fields;
    }

    function contextField(available, maxLength) {
        if (!available) return element("p", "Сейчас пользовательский контекст недоступен.", "settings-note");
        const label = element("label", "Контекст для ИИ", "settings-stack");
        const input = element("textarea");
        input.rows = 4;
        input.maxLength = maxLength;
        input.value = draft.user_context_comment;
        input.addEventListener("input", () => {
            draft.user_context_comment = input.value.trim();
            updateDraft();
        });
        label.append(input);
        return label;
    }

    function install(payload) {
        const { goal_steps, choices, diets, expires_in_ms, context_available, context_max_length, ...values } = payload;
        current = { ...values, diets: Object.fromEntries(diets.map(diet => [diet.code, diet.selected])) };
        draft = { ...current, goals: { ...current.goals }, metrics: { ...current.metrics },
            diets: { ...current.diets } };

        const settingToggle = (key, label) => toggle(label, draft[key], value => { draft[key] = value; });
        rows.goals.replaceChildren(...Object.entries(draft.goals).map(([code, value]) =>
            goalRow(code, value, goal_steps[code])));
        rows.metrics.replaceChildren(...Object.entries(draft.metrics).map(([code, value]) =>
            toggle(goalMetrics[code], value, checked => { draft.metrics[code] = checked; })),
            settingToggle("workout_logging_enabled", "Тренировки"));
        rows.reports.replaceChildren(
            timezoneField(choices.timezone),
            choice(payload, "nutrition_day_start_hour", "Начало пищевого дня",
                hour => String(hour).padStart(2, "0") + ":00"),
            choice(payload, "report_goal_tolerance_percent", "Допуск к цели", value => "±" + value + "%"),
            choice(payload, "report_noticeable_entry_percentile", "Порог заметных записей", value => value + "%")
        );
        rows.display.replaceChildren(
            choice(payload, "summary_display_mode", "Формат итогов", mode => mode === "text" ? "Текст" : "Бары"),
            settingToggle("show_day_progress_bar", "Прогресс дня"),
            settingToggle("show_post_entry_delta_suffix", "Дельта после записи")
        );
        dietSection.hidden = !diets.length;
        rows.diets.replaceChildren(...diets.map(diet =>
            toggle(diet.name, diet.selected, checked => { draft.diets[diet.code] = checked; })));
        rows.context.replaceChildren(contextField(context_available, context_max_length));
        loading.hidden = true;
        form.hidden = false;
        updateDraft();
    }

    function close() {
        root.hidden = true;
        pending?.abort();
        pending = undefined;
    }

    function clear() {
        close();
        current = undefined;
        draft = undefined;
        for (const container of Object.values(rows)) container.replaceChildren();
        form.hidden = true;
        status.textContent = "";
    }

    async function open() {
        root.hidden = false;
        if (current && Object.keys(patch()).length) return;
        pending?.abort();
        const controller = new AbortController();
        pending = controller;
        loading.hidden = false;
        form.hidden = true;
        loadStatus.textContent = "Загрузка настроек…";
        retry.hidden = true;
        try {
            const result = await request({ signal: controller.signal });
            if (pending !== controller || expire() || handleAccessError(result)) return;
            if (!result.response.ok) throw new Error("Settings request failed");
            install(result.payload);
        } catch {
            if (pending !== controller) return;
            loadStatus.textContent = "Не удалось загрузить настройки.";
            retry.hidden = false;
        } finally {
            if (pending === controller) pending = undefined;
        }
    }

    retry.addEventListener("click", open);
    form.addEventListener("submit", async event => {
        event.preventDefault();
        if (save.disabled || expire()) return;
        const command = patch();
        setBusy(true);
        save.disabled = true;
        sections.disabled = true;
        status.textContent = "Сохраняю…";
        try {
            const result = await request({ command });
            if (expire() || handleAccessError(result)) return;
            if (!result.response.ok) throw new Error("Settings update failed");
            install(result.payload);
            status.textContent = "Изменения сохранены.";
            await refreshDiary();
        } catch {
            if (draft) {
                updateDraft();
                status.textContent = "Не удалось подтвердить сохранение. Попробуйте ещё раз.";
            }
        } finally {
            sections.disabled = false;
            setBusy(false);
        }
    });

    return { open, close, clear };
}

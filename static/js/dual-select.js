/**
 * dual-select.js
 * ---------------
 * Powers the searchable dual-list ("transfer list") multi-select widget
 * used for assigning multiple interns to a project
 * (components/dual_select.html).
 *
 * The widget is backed by a real <select multiple>, kept perfectly in
 * sync with the visible UI. Nothing about form submission changes --
 * the backend still reads request.form.getlist("assigned_intern_ids")
 * exactly as before.
 */
(function () {
    "use strict";

    function initDualSelect(root) {
        const select = root.querySelector("select");
        if (!select) return;

        const availableList = root.querySelector('[data-role="available-list"]');
        const selectedList = root.querySelector('[data-role="selected-list"]');
        const selectedEmpty = root.querySelector('[data-role="selected-empty"]');
        const availableCount = root.querySelector('[data-role="available-count"]');
        const selectedCount = root.querySelector('[data-role="selected-count"]');
        const searchInput = root.querySelector(".pia-dual-search");
        if (!availableList || !selectedList) return;

        // Pull items out of the native <select> once; this is our
        // single source of truth for labels/ids.
        const items = Array.from(select.options).map((opt) => ({
            id: opt.value,
            label: opt.textContent,
            city: opt.dataset.city || "",
            departmentId: opt.dataset.departmentId || "",
            option: opt,
        }));

        // Optional external eligibility filter (e.g. City -> Department
        // -> Project Manager cascading on the Project Assignment form).
        // When set, only items matching the predicate are selectable/
        // visible in "Available", and any already-"Selected" item that
        // no longer matches is automatically deselected -- this is how
        // "Prevent selecting unrelated ... interns" is enforced in the UI.
        let filterPredicate = null;

        function isSelected(item) {
            return item.option.selected;
        }

        function setSelected(item, selected) {
            item.option.selected = selected;
        }

        function matchesFilter(item) {
            return !filterPredicate || filterPredicate(item);
        }

        function render() {
            const query = (searchInput ? searchInput.value : "").trim().toLowerCase();

            // Drop any currently-selected item that no longer satisfies
            // the active eligibility filter (e.g. department/PM changed).
            items.forEach((it) => {
                if (isSelected(it) && !matchesFilter(it)) {
                    setSelected(it, false);
                }
            });

            const available = items.filter((it) => !isSelected(it) && matchesFilter(it));
            const selected = items.filter((it) => isSelected(it));

            const filteredAvailable = query
                ? available.filter((it) => it.label.toLowerCase().includes(query))
                : available;

            availableList.innerHTML = "";
            if (filteredAvailable.length === 0) {
                const empty = document.createElement("div");
                empty.className = "pia-dual-empty";
                if (query) {
                    empty.textContent = "No interns match your search.";
                } else if (filterPredicate) {
                    empty.textContent = "No eligible interns for the selected City/Department/Project Manager.";
                } else {
                    empty.textContent = "All interns have been selected.";
                }
                availableList.appendChild(empty);
            } else {
                filteredAvailable.forEach((it) => {
                    availableList.appendChild(buildRow(it, "add"));
                });
            }

            selectedList.innerHTML = "";
            if (selected.length === 0) {
                selectedList.appendChild(selectedEmpty || buildEmptySelected());
            } else {
                selected.forEach((it) => {
                    selectedList.appendChild(buildRow(it, "remove"));
                });
            }

            if (availableCount) availableCount.textContent = String(available.length);
            if (selectedCount) selectedCount.textContent = String(selected.length);
        }

        function buildEmptySelected() {
            const empty = document.createElement("div");
            empty.className = "pia-dual-empty";
            empty.setAttribute("data-role", "selected-empty");
            empty.textContent = "No interns selected yet. Click an intern on the left to add them.";
            return empty;
        }

        function buildRow(item, action) {
            const row = document.createElement("button");
            row.type = "button";
            row.className = "pia-dual-row";
            row.setAttribute("data-id", item.id);

            const label = document.createElement("span");
            label.className = "pia-dual-row-label";
            label.textContent = item.label;
            row.appendChild(label);

            const icon = document.createElement("i");
            icon.className = action === "add" ? "bi bi-plus-circle" : "bi bi-dash-circle";
            row.appendChild(icon);

            row.addEventListener("click", function () {
                setSelected(item, action === "add");
                render();
            });

            return row;
        }

        if (searchInput) {
            searchInput.addEventListener("input", render);
        }

        render();

        // Expose a small API on the root element so other scripts (the
        // Project Assignment City -> Department -> PM cascade) can push
        // an eligibility filter in without knowing anything about this
        // widget's internals.
        root._piaDualSelect = {
            render,
            applyFilter(predicateFn) {
                filterPredicate = typeof predicateFn === "function" ? predicateFn : null;
                render();
            },
        };
    }

    function init() {
        document.querySelectorAll(".pia-dual-select").forEach(initDualSelect);
    }

    // Global helper: safe to call even before/without this widget being
    // present -- e.g. window.piaDualSelectApplyFilter(root, fn).
    window.piaDualSelectApplyFilter = function (root, predicateFn) {
        if (root && root._piaDualSelect) {
            root._piaDualSelect.applyFilter(predicateFn);
        }
    };

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();

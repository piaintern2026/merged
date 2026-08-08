/**
 * department-cascade.js
 * ----------------------
 * Powers every Department -> Division/Section cascading dropdown in the
 * system (components/department_select.html). Two things happen here:
 *
 *  1. Any <select class="pia-searchable-select"> is enhanced with a
 *     type-to-filter search box (a lightweight combobox), while
 *     staying backed by the real <select> so existing form-reading
 *     backend code needs zero changes.
 *
 *  2. Inside a `.pia-dept-cascade` widget, changing the Department
 *     select fetches that department's Divisions/Sections from
 *     /departments/api/sub-departments?department_id=<id> and
 *     repopulates the Division/Section select -- dynamically, with no
 *     page reload.
 */
(function () {
    "use strict";

    // ------------------------------------------------------------------
    // Searchable select (combobox) enhancement
    // ------------------------------------------------------------------
    function enhanceSearchableSelect(select) {
        if (!select || select.dataset.piaEnhanced === "1") return;
        select.dataset.piaEnhanced = "1";

        const wrapper = document.createElement("div");
        wrapper.className = "pia-searchable-select-wrapper";
        select.parentNode.insertBefore(wrapper, select);

        const input = document.createElement("input");
        input.type = "text";
        input.className = "form-control pia-searchable-select-input";
        input.autocomplete = "off";
        input.placeholder = "Type to search…";

        const menu = document.createElement("div");
        menu.className = "pia-searchable-select-menu d-none";

        wrapper.appendChild(input);
        wrapper.appendChild(menu);
        wrapper.appendChild(select);
        select.classList.add("d-none");

        function currentLabel() {
            const opt = select.options[select.selectedIndex];
            return opt && opt.value ? opt.textContent : "";
        }

        function buildMenu(filterText) {
            menu.innerHTML = "";
            const filter = (filterText || "").trim().toLowerCase();
            let anyVisible = false;
            Array.from(select.options).forEach((opt) => {
                if (opt.hidden) return; // excluded by an active cascade filter (e.g. City)
                if (!opt.value && !filter) return; // hide blank placeholder from the list
                if (filter && opt.textContent.toLowerCase().indexOf(filter) === -1) return;
                anyVisible = true;
                const item = document.createElement("div");
                item.className = "pia-searchable-select-item";
                item.textContent = opt.textContent;
                item.dataset.value = opt.value;
                if (opt.value === select.value) item.classList.add("active");
                item.addEventListener("mousedown", (e) => {
                    e.preventDefault();
                    select.value = opt.value;
                    input.value = opt.value ? opt.textContent : "";
                    menu.classList.add("d-none");
                    select.dispatchEvent(new Event("change", { bubbles: true }));
                });
                menu.appendChild(item);
            });
            if (!anyVisible) {
                const empty = document.createElement("div");
                empty.className = "pia-searchable-select-empty";
                empty.textContent = "No matches.";
                menu.appendChild(empty);
            }
        }

        input.value = currentLabel();

        input.addEventListener("focus", () => {
            buildMenu(select.value ? "" : input.value);
            menu.classList.remove("d-none");
        });
        input.addEventListener("input", () => {
            buildMenu(input.value);
            menu.classList.remove("d-none");
        });
        input.addEventListener("blur", () => {
            // Delay so a mousedown on a menu item registers first.
            setTimeout(() => {
                menu.classList.add("d-none");
                input.value = currentLabel();
            }, 150);
        });

        // Keep the visible text box synced if something else (e.g. our
        // own cascade refresh) changes the underlying select's value.
        select.addEventListener("pia:refresh-label", () => {
            input.value = currentLabel();
        });
    }

    function enhanceAllSearchableSelects(root) {
        (root || document)
            .querySelectorAll("select.pia-searchable-select")
            .forEach(enhanceSearchableSelect);
    }

    // ------------------------------------------------------------------
    // Department -> Division/Section cascade
    // ------------------------------------------------------------------
    function populateSubDepartments(subSelect, options, preserveValue) {
        const previousValue = preserveValue != null ? String(preserveValue) : subSelect.value;
        subSelect.innerHTML = "";

        const blank = document.createElement("option");
        blank.value = "";
        blank.textContent = options.length ? "Select a division/section…" : "No divisions/sections";
        subSelect.appendChild(blank);

        options.forEach((sd) => {
            const opt = document.createElement("option");
            opt.value = sd.id;
            opt.textContent = sd.name;
            subSelect.appendChild(opt);
        });

        const stillValid = options.some((sd) => String(sd.id) === previousValue);
        subSelect.value = stillValid ? previousValue : "";
        subSelect.dispatchEvent(new Event("pia:refresh-label"));
    }

    function loadSubDepartments(widget, deptId, preserveValue) {
        const subSelect = widget.querySelector('[data-role="subdepartment-select"]');
        if (!subSelect) return;

        if (!deptId) {
            populateSubDepartments(subSelect, [], preserveValue);
            return;
        }

        const url = widget.dataset.subdeptUrl + "?department_id=" + encodeURIComponent(deptId);
        fetch(url, { headers: { "X-Requested-With": "XMLHttpRequest" } })
            .then((res) => (res.ok ? res.json() : { sub_departments: [] }))
            .then((data) => {
                populateSubDepartments(subSelect, data.sub_departments || [], preserveValue);
            })
            .catch(() => {
                populateSubDepartments(subSelect, [], preserveValue);
            });
    }

    function initCascade(widget) {
        if (widget.dataset.piaCascadeInit === "1") return;
        widget.dataset.piaCascadeInit = "1";

        const deptSelect = widget.querySelector('[data-role="department-select"]');
        const subSelect = widget.querySelector('[data-role="subdepartment-select"]');
        if (!deptSelect || !subSelect) return;

        const initialSubId = subSelect.dataset.selectedId || "";

        deptSelect.addEventListener("change", () => {
            loadSubDepartments(widget, deptSelect.value, null);
        });

        // On first load, if a department is already selected (edit
        // forms, filters restored from the query string), fetch its
        // divisions/sections and pre-select the saved value.
        if (deptSelect.value) {
            loadSubDepartments(widget, deptSelect.value, initialSubId);
        }
    }

    function initAll(root) {
        enhanceAllSearchableSelects(root);
        (root || document).querySelectorAll(".pia-dept-cascade").forEach(initCascade);
    }

    document.addEventListener("DOMContentLoaded", () => initAll(document));

    // Expose for any page that injects a widget dynamically (e.g. a
    // modal loaded after DOMContentLoaded already fired).
    window.piaInitDepartmentCascade = initAll;
})();

/**
 * project-assignment-cascade.js
 * ------------------------------
 * Powers the cascading Project Assignment flow on the Create/Edit
 * Project form (templates/projects/form.html), Station HR / Super
 * Admin branch only:
 *
 *      City  ->  Department  ->  Project Manager  ->  Interns
 *
 * Each step narrows the next: picking a City filters the Department
 * dropdown to departments in that city; picking a Department filters
 * the Project Manager dropdown to PMs in that city/department; picking
 * a Project Manager (or leaving it on a department with no PM chosen
 * yet) filters the Interns dual-select to interns in that city/
 * department. Nothing about existing markup, styling, or other
 * functionality (the Department -> Division/Section cascade, the
 * searchable-select combobox, the dual-select transfer list) changes
 * -- this only adds an extra filtering layer on top of them, driven by
 * `data-city` / `data-department-id` attributes already present on the
 * relevant <option> elements.
 *
 * This is a *client-side UX* layer only. The actual "prevent selecting
 * unrelated departments, PMs, or interns" guarantee is enforced again
 * on the server in routes/project.py, so a tampered/bypassed request
 * still gets rejected.
 */
(function () {
    "use strict";

    function fireChange(select) {
        select.dispatchEvent(new Event("change", { bubbles: true }));
        select.dispatchEvent(new Event("pia:refresh-label"));
    }

    /**
     * Hide/show <option>s in `select` based on a predicate over each
     * option's dataset. If the currently selected option no longer
     * matches, the select is reset to blank and a real `change` event
     * is fired so any other cascade listening on it (e.g. the existing
     * Department -> Division/Section fetch) reacts too.
     */
    function filterOptions(select, predicate) {
        if (!select) return;
        let currentStillValid = false;
        Array.from(select.options).forEach((opt) => {
            if (!opt.value) {
                opt.hidden = false; // always keep the blank placeholder visible
                return;
            }
            const matches = predicate(opt);
            opt.hidden = !matches;
            if (matches && opt.value === select.value) currentStillValid = true;
        });
        if (!currentStillValid && select.value !== "") {
            select.value = "";
            fireChange(select);
        }
    }

    function initProjectAssignmentCascade(root) {
        if (!root || root.dataset.piaCascadeInit === "1") return;
        root.dataset.piaCascadeInit = "1";

        const citySelect = root.querySelector('[data-role="city-select"]');
        const deptSelect = root.querySelector('[data-role="department-select"]');
        const managerSelect = root.querySelector('[data-role="manager-select"]');
        const dualSelectRoot = root.querySelector(".pia-dual-select");

        // Nothing to cascade without at least a Department dropdown.
        if (!deptSelect) return;

        function refreshManagerFilter() {
            if (!managerSelect) return;
            const city = citySelect ? citySelect.value : "";
            const deptId = deptSelect.value;
            filterOptions(managerSelect, (opt) => {
                const cityOk = !city || opt.dataset.city === city;
                const deptOk = !deptId || opt.dataset.departmentId === String(deptId);
                return cityOk && deptOk;
            });
            managerSelect.disabled = !deptId;
        }

        function refreshInternFilter() {
            if (!dualSelectRoot || !window.piaDualSelectApplyFilter) return;

            const deptId = deptSelect.value;
            let eligibleCity = citySelect ? citySelect.value : "";
            let eligibleDeptId = deptId;

            // Once a specific Project Manager is chosen, interns are
            // further narrowed to exactly that PM's city/department
            // (their real assignment scope), not just whatever the
            // dropdowns above happen to say.
            if (managerSelect && managerSelect.value) {
                const opt = managerSelect.options[managerSelect.selectedIndex];
                eligibleCity = opt.dataset.city || eligibleCity;
                eligibleDeptId = opt.dataset.departmentId || eligibleDeptId;
            }

            window.piaDualSelectApplyFilter(dualSelectRoot, (item) => {
                // No Department chosen yet -> nothing is eligible.
                if (!deptId) return false;
                const cityOk = !eligibleCity || item.city === eligibleCity;
                const deptOk = !eligibleDeptId || item.departmentId === String(eligibleDeptId);
                return cityOk && deptOk;
            });
        }

        function refreshDeptFilter() {
            if (citySelect) {
                const city = citySelect.value;
                filterOptions(deptSelect, (opt) => !city || opt.dataset.city === city);
                deptSelect.disabled = !city;
            }
            refreshManagerFilter();
            refreshInternFilter();
        }

        if (citySelect) {
            citySelect.addEventListener("change", refreshDeptFilter);
        }

        deptSelect.addEventListener("change", () => {
            refreshManagerFilter();
            refreshInternFilter();
        });

        if (managerSelect) {
            managerSelect.addEventListener("change", refreshInternFilter);
        }

        // Apply the initial state on load -- covers both the Edit form
        // (City/Department/PM/Interns already have saved values) and a
        // validation-error resubmit (values restored from the posted
        // form), so previously-valid selections aren't wiped out.
        refreshDeptFilter();
    }

    function initAll(rootDoc) {
        (rootDoc || document)
            .querySelectorAll('[data-cascade="project-assignment"]')
            .forEach(initProjectAssignmentCascade);
    }

    document.addEventListener("DOMContentLoaded", () => initAll(document));

    window.piaInitProjectAssignmentCascade = initAll;
})();

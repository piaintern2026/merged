/**
 * table-enhance.js
 * ----------------
 * Generic, reusable enhancer for PIA tables. For every <table
 * data-page-size="N"> on the page it wires up:
 *   - live, case-insensitive search (via an input with
 *     data-table-search="<table id>"), matched against each row's
 *     data-search attribute (rendered server-side with the most
 *     relevant unique field(s), already lower-cased),
 *   - lightweight client-side pagination (rendered into an element
 *     with data-pagination-for="<table id>"),
 *   - a clear "no results" empty state (an element with
 *     data-empty-for="<table id>"),
 *   - an optional live result-count chip (data-count-for="<table id>").
 *
 * Markup contract (see any templates list.html file for real examples):
 *
 *   <input data-table-search="myTable" placeholder="Search...">
 *   <table id="myTable" data-page-size="10">
 *     <tbody>
 *       <tr data-search="jane doe cnic12345">...</tr>
 *     </tbody>
 *   </table>
 *   <div data-empty-for="myTable" class="pia-table-empty d-none">...</div>
 *   <div data-pagination-for="myTable"></div>
 *
 * This never touches rows without a data-search attribute, so tables
 * that opt out simply keep working exactly as before.
 *
 * Optional exact-match filter dropdowns (e.g. "Filter by City") can be
 * added alongside the free-text search box:
 *
 *   <select data-table-filter="myTable" data-filter-field="city">
 *     <option value="">All Cities</option>
 *     <option value="Lahore">Lahore</option>
 *   </select>
 *   <tr data-search="..." data-city="Lahore">...</tr>
 *
 * The select's data-filter-field must match a data-<field> attribute
 * on each row. Multiple filters can be combined on the same table
 * (they're AND-ed together with the search box). Rows without the
 * matching data attribute simply never match a non-empty filter.
 */

(function () {
    function debounce(fn, delay) {
        let t;
        return function (...args) {
            clearTimeout(t);
            t = setTimeout(() => fn.apply(this, args), delay);
        };
    }

    // Columns whose header text matches these are never sortable
    // (avatars/photos and action buttons have no meaningful order).
    const UNSORTABLE_HEADINGS = /^(photo|actions?|#)$/i;

    function cellSortValue(td) {
        if (!td) return "";
        // Prefer an explicit override, e.g. <td data-sort-value="2026-01-05">5 Jan 2026</td>
        if (td.dataset.sortValue !== undefined) return td.dataset.sortValue;
        return td.textContent.trim();
    }

    function compareValues(a, b) {
        const na = parseFloat(a.replace(/[,%]/g, ""));
        const nb = parseFloat(b.replace(/[,%]/g, ""));
        const bothNumeric = a !== "" && b !== "" && !isNaN(na) && !isNaN(nb) &&
            /^-?[\d.,%]+$/.test(a) && /^-?[\d.,%]+$/.test(b);
        if (bothNumeric) return na - nb;

        const da = Date.parse(a);
        const db = Date.parse(b);
        if (a !== "" && b !== "" && !isNaN(da) && !isNaN(db) && /\d{4}/.test(a) && /\d{4}/.test(b)) {
            return da - db;
        }
        return a.localeCompare(b, undefined, { sensitivity: "base", numeric: true });
    }

    function enableSorting(table, onSorted) {
        const headerRow = table.querySelector("thead tr");
        if (!headerRow) return;

        Array.from(headerRow.children).forEach((th, index) => {
            const label = th.textContent.trim();
            if (!label || UNSORTABLE_HEADINGS.test(label) || th.dataset.noSort !== undefined) return;

            th.classList.add("pia-th-sortable");
            th.setAttribute("role", "button");
            th.setAttribute("tabindex", "0");
            th.setAttribute("aria-sort", "none");
            th.title = "Sort by " + label;
            th.insertAdjacentHTML("beforeend", ' <i class="bi bi-arrow-down-up pia-sort-icon"></i>');

            const doSort = function () {
                const currentDir = th.dataset.sortDir === "asc" ? "desc" : "asc";
                Array.from(headerRow.children).forEach((sib) => {
                    sib.dataset.sortDir = "";
                    sib.setAttribute("aria-sort", "none");
                    const icon = sib.querySelector(".pia-sort-icon");
                    if (icon) icon.className = "bi bi-arrow-down-up pia-sort-icon";
                });
                th.dataset.sortDir = currentDir;
                th.setAttribute("aria-sort", currentDir === "asc" ? "ascending" : "descending");
                const icon = th.querySelector(".pia-sort-icon");
                if (icon) icon.className = "bi bi-arrow-" + (currentDir === "asc" ? "up" : "down") + " pia-sort-icon active";

                onSorted(index, currentDir);
            };

            th.addEventListener("click", doSort);
            th.addEventListener("keydown", function (e) {
                if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    doSort();
                }
            });
        });
    }

    function enhanceTable(table) {
        const tbody = table.querySelector("tbody");
        if (!tbody) return;

        const allRows = Array.from(tbody.querySelectorAll("tr[data-search]"));
        if (allRows.length === 0) return; // nothing to search/paginate

        const pageSize = parseInt(table.dataset.pageSize, 10) || 10;
        const tableId = table.id;

        const wrap = table.closest(".table-responsive");
        const searchInput = document.querySelector('[data-table-search="' + tableId + '"]');
        const filterSelects = Array.from(
            document.querySelectorAll('[data-table-filter="' + tableId + '"]')
        );
        const emptyEl = document.querySelector('[data-empty-for="' + tableId + '"]');
        const paginationEl = document.querySelector('[data-pagination-for="' + tableId + '"]');
        const countEl = document.querySelector('[data-count-for="' + tableId + '"]');

        let filteredRows = allRows.slice();
        let currentPage = 1;

        function applyFilters() {
            const q = searchInput ? searchInput.value.trim().toLowerCase() : "";
            filteredRows = allRows.filter((r) => {
                if (q && (r.dataset.search || "").indexOf(q) === -1) return false;
                for (const sel of filterSelects) {
                    const field = sel.dataset.filterField;
                    const val = sel.value;
                    if (!field || !val) continue;
                    if ((r.dataset[field] || "") !== val) return false;
                }
                return true;
            });
        }

        function renderPagination(totalPages) {
            if (!paginationEl) return;
            if (totalPages <= 1) {
                paginationEl.innerHTML = "";
                return;
            }
            const items = [];
            items.push(
                '<li class="page-item ' + (currentPage === 1 ? "disabled" : "") + '">' +
                '<a class="page-link" href="#" data-page="' + (currentPage - 1) + '"><i class="bi bi-chevron-left"></i></a></li>'
            );

            const left = Math.max(1, currentPage - 2);
            const right = Math.min(totalPages, currentPage + 2);
            if (left > 1) {
                items.push('<li class="page-item"><a class="page-link" href="#" data-page="1">1</a></li>');
                if (left > 2) items.push('<li class="page-item disabled"><span class="page-link">&hellip;</span></li>');
            }
            for (let p = left; p <= right; p++) {
                items.push(
                    '<li class="page-item ' + (p === currentPage ? "active" : "") + '">' +
                    '<a class="page-link" href="#" data-page="' + p + '">' + p + "</a></li>"
                );
            }
            if (right < totalPages) {
                if (right < totalPages - 1) items.push('<li class="page-item disabled"><span class="page-link">&hellip;</span></li>');
                items.push('<li class="page-item"><a class="page-link" href="#" data-page="' + totalPages + '">' + totalPages + "</a></li>");
            }

            items.push(
                '<li class="page-item ' + (currentPage === totalPages ? "disabled" : "") + '">' +
                '<a class="page-link" href="#" data-page="' + (currentPage + 1) + '"><i class="bi bi-chevron-right"></i></a></li>'
            );

            const start = (currentPage - 1) * pageSize + 1;
            const end = Math.min(filteredRows.length, currentPage * pageSize);

            paginationEl.innerHTML =
                '<nav aria-label="Page navigation" class="mt-3">' +
                '<ul class="pagination justify-content-center mb-0">' + items.join("") + "</ul>" +
                '<p class="text-center text-muted small mt-2 mb-0">Showing ' + start + '&ndash;' + end +
                " of " + filteredRows.length + " result(s) &bull; Page " + currentPage + " of " + totalPages + "</p>" +
                "</nav>";

            paginationEl.querySelectorAll("a.page-link[data-page]").forEach((a) => {
                a.addEventListener("click", function (e) {
                    e.preventDefault();
                    const p = parseInt(this.dataset.page, 10);
                    if (p >= 1 && p <= totalPages && p !== currentPage) {
                        currentPage = p;
                        render();
                    }
                });
            });
        }

        function render() {
            allRows.forEach((r) => (r.style.display = "none"));

            // Reflect the current sort/filter order in the actual DOM (not just
            // visibility), so sorting is visible whether or not this table uses
            // client-side pagination.
            const frag = document.createDocumentFragment();
            filteredRows.forEach((r) => frag.appendChild(r));
            allRows.forEach((r) => {
                if (!filteredRows.includes(r)) frag.appendChild(r);
            });
            tbody.appendChild(frag);

            if (paginationEl) {
                // Client-side paginated table
                const totalPages = Math.max(1, Math.ceil(filteredRows.length / pageSize));
                if (currentPage > totalPages) currentPage = totalPages;
                const start = (currentPage - 1) * pageSize;
                filteredRows.slice(start, start + pageSize).forEach((r) => (r.style.display = ""));
                renderPagination(totalPages);
            } else {
                // No client pagination target (e.g. page already has server-side
                // pagination) - just show every row that matches the search.
                filteredRows.forEach((r) => (r.style.display = ""));
            }

            const hasResults = filteredRows.length > 0;
            if (emptyEl) emptyEl.classList.toggle("d-none", hasResults);
            if (wrap) wrap.classList.toggle("d-none", !hasResults);
            if (countEl) countEl.textContent = filteredRows.length + " of " + allRows.length;
        }

        enableSorting(table, function (colIndex, dir) {
            filteredRows.sort(function (r1, r2) {
                const v1 = cellSortValue(r1.children[colIndex]);
                const v2 = cellSortValue(r2.children[colIndex]);
                const cmp = compareValues(v1, v2);
                return dir === "asc" ? cmp : -cmp;
            });
            currentPage = 1;
            render();
        });

        if (searchInput) {
            const searchWrap = searchInput.closest(".pia-table-search");
            const clearBtn = searchWrap ? searchWrap.querySelector(".pia-search-clear") : null;

            const doSearch = debounce(function () {
                const q = searchInput.value.trim().toLowerCase();
                if (searchWrap) searchWrap.classList.toggle("has-value", q.length > 0);
                applyFilters();
                currentPage = 1;
                render();
            }, 120);

            searchInput.addEventListener("input", doSearch);

            if (clearBtn) {
                clearBtn.addEventListener("click", function () {
                    searchInput.value = "";
                    searchInput.dispatchEvent(new Event("input"));
                    searchInput.focus();
                });
            }
        }

        filterSelects.forEach((sel) => {
            sel.addEventListener("change", function () {
                applyFilters();
                currentPage = 1;
                render();
            });
        });

        render();
    }

    document.addEventListener("DOMContentLoaded", function () {
        document.querySelectorAll("table[data-page-size]").forEach(enhanceTable);
    });
})();

/**
 * script.js
 * ---------
 * Shared client-side behaviour: mobile sidebar toggle, desktop sidebar
 * collapse, dark-mode toggle, live date display, and auto-dismissing
 * alerts. (Password visibility toggle for the login page lives in
 * templates/auth/login.html, not here -- see the note below.)
 */

document.addEventListener("DOMContentLoaded", function () {
    // ---- Mobile sidebar toggle ----
    const sidebar = document.getElementById("pia-sidebar");
    const backdrop = document.getElementById("pia-sidebar-backdrop");
    const toggleBtn = document.getElementById("sidebarToggle");

    function closeSidebar() {
        if (sidebar) sidebar.classList.remove("show");
        if (backdrop) backdrop.classList.remove("show");
    }

    if (toggleBtn && sidebar && backdrop) {
        toggleBtn.addEventListener("click", function () {
            sidebar.classList.toggle("show");
            backdrop.classList.toggle("show");
        });
        backdrop.addEventListener("click", closeSidebar);
    }

    // ---- Desktop sidebar collapse (icon-only mode) ----
    const collapseBtn = document.getElementById("sidebarCollapseToggle");
    if (collapseBtn && sidebar) {
        if (localStorage.getItem("pia-sidebar-collapsed") === "1") {
            sidebar.classList.add("collapsed");
        }
        collapseBtn.addEventListener("click", function () {
            sidebar.classList.toggle("collapsed");
            localStorage.setItem(
                "pia-sidebar-collapsed",
                sidebar.classList.contains("collapsed") ? "1" : "0"
            );
        });
    }

    // ---- Dark mode toggle ----
    const themeToggle = document.getElementById("themeToggle");
    const themeIcon = document.getElementById("themeToggleIcon");

    function applyThemeIcon() {
        if (!themeIcon) return;
        const isDark = document.documentElement.getAttribute("data-theme") === "dark";
        themeIcon.className = isDark ? "bi bi-sun" : "bi bi-moon-stars";
    }
    applyThemeIcon();

    if (themeToggle) {
        themeToggle.addEventListener("click", function () {
            const html = document.documentElement;
            const isDark = html.getAttribute("data-theme") === "dark";
            if (isDark) {
                html.removeAttribute("data-theme");
                localStorage.setItem("pia-theme", "light");
            } else {
                html.setAttribute("data-theme", "dark");
                localStorage.setItem("pia-theme", "dark");
            }
            applyThemeIcon();
        });
    }

    // ---- Live current date in navbar ----
    const dateEl = document.getElementById("pia-current-date-text");
    if (dateEl) {
        dateEl.textContent = new Date().toLocaleDateString(undefined, {
            weekday: "short",
            year: "numeric",
            month: "short",
            day: "numeric",
        });
    }

    // NOTE: password visibility toggle for the login page lives in
    // templates/auth/login.html itself (it needs to run whether or not
    // this shared script.js file is present, and having a second
    // listener bound here to the same #togglePassword button was the
    // bug: every click fired BOTH handlers, so the type flipped
    // password -> text -> password in the same click and the field
    // never visibly changed).

    // ---- Auto-dismiss flash alerts after 5 seconds ----
    document.querySelectorAll(".flash-container .alert").forEach(function (alertEl) {
        setTimeout(function () {
            const bsAlert = bootstrap.Alert.getOrCreateInstance(alertEl);
            bsAlert.close();
        }, 5000);
    });

    // ---- Modal robustness ----
    // Modals throughout the app are declared inline inside table rows
    // and cards. Bootstrap positions modals with `position: fixed`,
    // which is only reliable when every ancestor is a normal
    // (non-transformed, non-animated) element - one bad ancestor style
    // is enough to make the modal render off-screen/clipped while its
    // full-viewport backdrop still shows, which looks like the whole
    // page has frozen behind a grey overlay.
    //
    // Reparenting the modal to a direct child of <body> right before
    // it opens sidesteps that class of bug entirely, and the cleanup
    // below guarantees a modal that fails to show for any reason never
    // leaves a stray backdrop blocking the page.
    document.addEventListener("show.bs.modal", function (e) {
        const modalEl = e.target;
        if (modalEl && modalEl.parentElement !== document.body) {
            document.body.appendChild(modalEl);
        }
    });

    document.addEventListener("hidden.bs.modal", function () {
        // If no modal is left open, make sure nothing is still locking
        // the page (stray backdrop and/or the body's modal-open class).
        const anyOpen = document.querySelector(".modal.show");
        if (!anyOpen) {
            document.body.classList.remove("modal-open");
            document.body.style.removeProperty("overflow");
            document.body.style.removeProperty("padding-right");
            document.querySelectorAll(".modal-backdrop").forEach((el) => el.remove());
        }
    });

    // ------------------------------------------------------------------
    // Clickable dashboard navigation cards (elements with [data-nav-url]).
    // Used so the whole card acts as a link to an existing route without
    // changing any backend logic. Clicks on a real <a>/<button>/<input>
    // inside the card (e.g. the "View" or "Review" buttons some cards
    // already have) are left alone so their own behaviour still works.
    // ------------------------------------------------------------------
    document.addEventListener("click", function (e) {
        const card = e.target.closest("[data-nav-url]");
        if (!card) return;
        if (e.target.closest("a, button, input, select, textarea")) return;
        const url = card.getAttribute("data-nav-url");
        if (url) window.location.href = url;
    });

    document.querySelectorAll("[data-nav-url]").forEach(function (card) {
        if (!card.hasAttribute("tabindex")) card.setAttribute("tabindex", "0");
        if (!card.hasAttribute("role")) card.setAttribute("role", "link");
        card.addEventListener("keydown", function (e) {
            if (e.key === "Enter" || e.key === " ") {
                if (e.target.closest("a, button, input, select, textarea")) return;
                e.preventDefault();
                const url = card.getAttribute("data-nav-url");
                if (url) window.location.href = url;
            }
        });
    });
});

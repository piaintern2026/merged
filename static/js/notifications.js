/**
 * notifications.js
 * -----------------
 * Powers the navbar notification bell: fetches recent notifications
 * via /notifications/api/recent, renders them with read/unread state,
 * relative timestamps, and smooth fade-in, and lets the user mark a
 * single notification (or all of them) as read without leaving the
 * page. Falls back gracefully (bell still links to the full page via
 * "View all notifications") if anything fails.
 */

(function () {
    const toggleBtn = document.getElementById("pia-notif-toggle");
    const menu = document.getElementById("pia-notif-menu");
    const body = document.getElementById("pia-notif-body");
    const badge = document.getElementById("pia-notif-badge");
    const markAllBtn = document.getElementById("pia-notif-mark-all");

    if (!toggleBtn || !menu || !body) return;

    const ICONS = {
        "Project Assigned": "bi-kanban",
        "Attendance Reminder": "bi-calendar-check",
        "Project Deadline": "bi-alarm",
        "Evaluation Complete": "bi-clipboard-check",
        General: "bi-bell",
    };

    function updateBadge(count) {
        if (!badge) return;
        if (count > 0) {
            badge.textContent = count > 9 ? "9+" : String(count);
            badge.classList.remove("d-none");
        } else {
            badge.classList.add("d-none");
        }
    }

    function escapeHtml(str) {
        const div = document.createElement("div");
        div.textContent = str;
        return div.innerHTML;
    }

    function renderNotifications(data) {
        const items = data.notifications || [];
        updateBadge(data.unread_count || 0);

        if (items.length === 0) {
            body.innerHTML =
                '<div class="pia-notif-empty"><i class="bi bi-bell-slash"></i>You\'re all caught up. No notifications yet.</div>';
            return;
        }

        body.innerHTML = items
            .map(function (n) {
                const icon = n.icon || ICONS[n.notification_type] || "bi-bell";
                const unreadClass = n.is_read ? "" : " unread";
                const dot = n.is_read ? "" : '<span class="pia-notif-dot" title="Unread"></span>';
                return (
                    '<div class="pia-notif-item' + unreadClass + '" data-id="' + n.id + '">' +
                    '<div class="pia-notif-icon"><i class="bi ' + icon + '"></i></div>' +
                    '<div class="pia-notif-text">' +
                    '<div class="pia-notif-msg">' + escapeHtml(n.message) + "</div>" +
                    '<div class="pia-notif-time">' + escapeHtml(n.time_ago) + "</div>" +
                    "</div>" +
                    dot +
                    "</div>"
                );
            })
            .join("");

        body.querySelectorAll(".pia-notif-item.unread").forEach(function (el) {
            el.addEventListener("click", function () {
                const id = this.dataset.id;
                markRead(id, this);
            });
        });
    }

    function loadNotifications() {
        body.innerHTML =
            '<div class="pia-notif-loading"><div class="spinner-border spinner-border-sm text-success me-2" role="status"></div>Loading notifications&hellip;</div>';

        fetch("/notifications/api/recent", { headers: { "X-Requested-With": "XMLHttpRequest" } })
            .then((res) => {
                if (!res.ok) throw new Error("Request failed");
                return res.json();
            })
            .then(renderNotifications)
            .catch(function () {
                body.innerHTML =
                    '<div class="pia-notif-empty"><i class="bi bi-exclamation-triangle"></i>Couldn\'t load notifications. Try the full page.</div>';
            });
    }

    function markRead(id, el) {
        fetch("/notifications/api/mark-read/" + id, {
            method: "POST",
            headers: { "X-Requested-With": "XMLHttpRequest" },
        })
            .then((res) => res.json())
            .then(function (data) {
                if (data.success) {
                    if (el) {
                        el.classList.remove("unread");
                        const dot = el.querySelector(".pia-notif-dot");
                        if (dot) dot.remove();
                    }
                    updateBadge(data.unread_count || 0);
                }
            })
            .catch(function () {
                /* fail silently; the full notifications page remains authoritative */
            });
    }

    let loadedOnce = false;
    menu.addEventListener("show.bs.dropdown", function () {
        // Refresh every time it's opened, cheap single request
        loadNotifications();
        loadedOnce = true;
    });

    if (markAllBtn) {
        markAllBtn.addEventListener("click", function () {
            fetch("/notifications/mark-all-read", {
                method: "POST",
                headers: { "X-Requested-With": "XMLHttpRequest" },
            })
                .then(function () {
                    loadNotifications();
                })
                .catch(function () {
                    /* fail silently; user can still use the full notifications page */
                });
        });
    }
})();

/**
 * username-from-email.js
 * -----------------------
 * Module 2: the email entered during manual user creation is
 * automatically used as the username.
 *
 * Any pair of <input name="email"> / <input name="username"> inside a
 * form marked with [data-username-sync] gets wired up so that the
 * username field mirrors the email field as the admin/HR types it.
 *
 * Rules:
 *   - Only auto-fills while the user hasn't manually typed their own
 *     username (tracked via a "touched" flag on the username field).
 *     If someone clears the auto-filled value and types something
 *     different, we stop overwriting it.
 *   - Only active on "create" forms - if the username field already
 *     has a value when the page loads (e.g. editing an existing user),
 *     syncing is skipped entirely so we never clobber a real account's
 *     existing username.
 *   - The username field stays a normal editable input - the admin can
 *     still override it before submitting, this just removes the need
 *     to type the same thing twice.
 */
(function () {
    function wireForm(form) {
        var emailInput = form.querySelector('input[name="email"]');
        var usernameInput = form.querySelector('input[name="username"]');
        if (!emailInput || !usernameInput) return;

        // Don't touch edit forms that already have a username pre-filled.
        if (usernameInput.value.trim() !== "") return;

        var userEdited = false;
        usernameInput.addEventListener("input", function () {
            // Any manual edit that doesn't match the current email value
            // means the admin wants a different username - stop syncing.
            userEdited = usernameInput.value !== emailInput.value.trim();
        });

        function sync() {
            if (userEdited) return;
            usernameInput.value = emailInput.value.trim();
        }

        emailInput.addEventListener("input", sync);
        emailInput.addEventListener("blur", sync);
    }

    function init() {
        document.querySelectorAll("form[data-username-sync]").forEach(wireForm);
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();

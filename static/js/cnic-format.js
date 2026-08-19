/**
 * cnic-format.js
 * ---------------
 * Module 1: Automatic CNIC Formatting.
 *
 * Any <input> with name="cnic" (or an explicit data-cnic-input
 * attribute, for any future field named differently) is automatically
 * formatted as the user types into the standard Pakistani CNIC layout:
 *
 *      Raw digits typed: 3520212345671
 *      Displayed as:     35202-1234567-1
 *
 * Rules:
 *  - Only numeric characters are accepted; everything else is stripped
 *    as the user types (paste included).
 *  - A "-" is inserted after the first 5 digits.
 *  - A "-" is inserted after the next 7 digits.
 *  - Input is capped at 13 digits total, i.e. the fully formatted
 *    value never exceeds "#####-#######-#" (15 characters).
 *
 * This only changes the on-screen presentation. The backend already
 * accepts CNICs with or without dashes (see utils.CNIC_RE /
 * utils.normalize_cnic), so submitted values keep working exactly as
 * before -- this is a pure UX enhancement, no backend change needed.
 */
(function () {
    "use strict";

    function formatCnicDigits(digits) {
        digits = digits.slice(0, 13);
        if (digits.length > 12) {
            return digits.slice(0, 5) + "-" + digits.slice(5, 12) + "-" + digits.slice(12);
        }
        if (digits.length > 5) {
            return digits.slice(0, 5) + "-" + digits.slice(5);
        }
        return digits;
    }

    function digitsBeforeCaret(value, caret) {
        // Count how many digit characters sit before the caret, so we
        // can restore the caret to the right spot after reformatting
        // (otherwise the cursor would jump to the end on every keystroke).
        let count = 0;
        for (let i = 0; i < caret && i < value.length; i++) {
            if (/\d/.test(value[i])) count++;
        }
        return count;
    }

    function caretFromDigitCount(formatted, digitCount) {
        if (digitCount <= 0) return 0;
        let seen = 0;
        for (let i = 0; i < formatted.length; i++) {
            if (/\d/.test(formatted[i])) {
                seen++;
                if (seen === digitCount) return i + 1;
            }
        }
        return formatted.length;
    }

    function handleInput(e) {
        const input = e.target;
        const caret = input.selectionStart || 0;
        const digitCountBeforeCaret = digitsBeforeCaret(input.value, caret);

        const digitsOnly = input.value.replace(/\D/g, "").slice(0, 13);
        const formatted = formatCnicDigits(digitsOnly);

        if (formatted !== input.value) {
            input.value = formatted;
            const newCaret = caretFromDigitCount(formatted, digitCountBeforeCaret);
            input.setSelectionRange(newCaret, newCaret);
        }
    }

    function enhanceCnicInput(input) {
        if (!input || input.dataset.piaCnicEnhanced === "1") return;
        input.dataset.piaCnicEnhanced = "1";

        input.setAttribute("inputmode", "numeric");
        input.setAttribute("maxlength", "15"); // "#####-#######-#"
        if (!input.getAttribute("placeholder")) {
            input.setAttribute("placeholder", "#####-#######-#");
        }

        // Format any pre-filled value immediately (e.g. edit forms).
        if (input.value) {
            input.value = formatCnicDigits(input.value.replace(/\D/g, "").slice(0, 13));
        }

        input.addEventListener("input", handleInput);
    }

    function enhanceAll(root) {
        (root || document)
            .querySelectorAll('input[name="cnic"], input[data-cnic-input]')
            .forEach(enhanceCnicInput);
    }

    document.addEventListener("DOMContentLoaded", function () {
        enhanceAll(document);
    });

    // Exposed in case a page injects a CNIC field dynamically later.
    window.piaEnhanceCnicInputs = enhanceAll;
})();

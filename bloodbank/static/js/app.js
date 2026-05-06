(function () {
    function closeFlashButtons() {
        document.querySelectorAll(".flash-close").forEach(function (button) {
            button.addEventListener("click", function () {
                button.closest(".flash").remove();
            });
        });
    }

    function roleFieldToggle() {
        var roleSelect = document.getElementById("roleSelect");
        var donorFields = document.querySelector('[data-role-fields="donor"]');
        if (!roleSelect || !donorFields) {
            return;
        }

        function sync() {
            var isDonor = roleSelect.value === "donor";
            donorFields.style.display = isDonor ? "grid" : "none";
            donorFields.querySelectorAll("input, select").forEach(function (field) {
                if (field.name === "blood_group" || field.name === "phone") {
                    field.required = isDonor;
                }
            });
        }

        roleSelect.addEventListener("change", sync);
        sync();
    }

    function confirmForms() {
        document.querySelectorAll("form[data-confirm]").forEach(function (form) {
            form.addEventListener("submit", function (event) {
                var message = form.getAttribute("data-confirm");
                if (message && !window.confirm(message)) {
                    event.preventDefault();
                }
            });
        });
    }

    function passwordTools() {
        document.querySelectorAll("[data-toggle-password]").forEach(function (button) {
            button.addEventListener("click", function () {
                var input = button.parentElement.querySelector("input");
                if (!input) {
                    return;
                }
                var show = input.type === "password";
                input.type = show ? "text" : "password";
                button.textContent = show ? "Hide" : "Show";
            });
        });

        document.querySelectorAll("[data-password-strength]").forEach(function (input) {
            var label = input.closest("label").querySelector("[data-strength-label]");
            function syncStrength() {
                var value = input.value;
                var score = 0;
                if (value.length >= 6) score += 1;
                if (value.length >= 10) score += 1;
                if (/[A-Z]/.test(value)) score += 1;
                if (/[0-9]/.test(value)) score += 1;
                if (/[^A-Za-z0-9]/.test(value)) score += 1;

                label.classList.remove("is-weak", "is-medium", "is-good");
                if (!value) {
                    label.textContent = "Strength: waiting";
                    return;
                }
                if (score <= 2) {
                    label.textContent = "Strength: weak";
                    label.classList.add("is-weak");
                } else if (score <= 4) {
                    label.textContent = "Strength: medium";
                    label.classList.add("is-medium");
                } else {
                    label.textContent = "Strength: strong";
                    label.classList.add("is-good");
                }
            }
            input.addEventListener("input", syncStrength);
            syncStrength();
        });

        var source = document.querySelector("[data-match-source]");
        var target = document.querySelector("[data-match-target]");
        var matchLabel = document.querySelector("[data-match-label]");
        if (source && target && matchLabel) {
            function syncMatch() {
                matchLabel.classList.remove("is-bad", "is-good");
                if (!target.value) {
                    matchLabel.textContent = "Passwords must match.";
                    return;
                }
                if (source.value === target.value) {
                    matchLabel.textContent = "Passwords match.";
                    matchLabel.classList.add("is-good");
                } else {
                    matchLabel.textContent = "Passwords do not match.";
                    matchLabel.classList.add("is-bad");
                }
            }
            source.addEventListener("input", syncMatch);
            target.addEventListener("input", syncMatch);
            syncMatch();
        }
    }

    function fitCanvas(canvas) {
        var ratio = window.devicePixelRatio || 1;
        var width = Math.max(320, canvas.clientWidth || canvas.parentElement.clientWidth || 320);
        var height = Number(canvas.getAttribute("height")) || 220;
        canvas.width = Math.floor(width * ratio);
        canvas.height = Math.floor(height * ratio);
        var context = canvas.getContext("2d");
        context.setTransform(ratio, 0, 0, ratio, 0, 0);
        return { context: context, width: width, height: height };
    }

    function clear(context, width, height) {
        context.clearRect(0, 0, width, height);
    }

    function drawBarChart(canvas, labels, values, color) {
        if (!canvas) {
            return;
        }
        var fitted = fitCanvas(canvas);
        var context = fitted.context;
        var width = fitted.width;
        var height = fitted.height;
        clear(context, width, height);

        var maxValue = Math.max.apply(null, values.concat([1]));
        var padding = { top: 16, right: 14, bottom: 44, left: 34 };
        var chartWidth = width - padding.left - padding.right;
        var chartHeight = height - padding.top - padding.bottom;
        var barGap = 10;
        var barWidth = Math.max(18, (chartWidth - barGap * Math.max(0, values.length - 1)) / Math.max(1, values.length));

        context.strokeStyle = "#dde1ea";
        context.lineWidth = 1;
        context.beginPath();
        context.moveTo(padding.left, padding.top);
        context.lineTo(padding.left, padding.top + chartHeight);
        context.lineTo(width - padding.right, padding.top + chartHeight);
        context.stroke();

        values.forEach(function (value, index) {
            var x = padding.left + index * (barWidth + barGap);
            var barHeight = (value / maxValue) * chartHeight;
            var y = padding.top + chartHeight - barHeight;
            context.fillStyle = color;
            context.fillRect(x, y, barWidth, barHeight);

            context.fillStyle = "#20212a";
            context.font = "700 12px system-ui";
            context.textAlign = "center";
            context.fillText(String(value), x + barWidth / 2, y - 6);
            context.fillStyle = "#6f7482";
            context.font = "700 11px system-ui";
            context.fillText(labels[index] || "", x + barWidth / 2, height - 18);
        });

        if (!values.length) {
            context.fillStyle = "#6f7482";
            context.font = "700 14px system-ui";
            context.textAlign = "center";
            context.fillText("No data", width / 2, height / 2);
        }
    }

    function drawDonutChart(canvas, labels, values) {
        if (!canvas) {
            return;
        }
        var fitted = fitCanvas(canvas);
        var context = fitted.context;
        var width = fitted.width;
        var height = fitted.height;
        clear(context, width, height);

        var palette = ["#c92a3f", "#0f766e", "#2563eb", "#b7791f", "#7c3aed", "#dc2626", "#0891b2", "#475569"];
        var total = values.reduce(function (sum, value) { return sum + value; }, 0);
        var centerX = width * 0.38;
        var centerY = height / 2;
        var radius = Math.min(width, height) * 0.32;
        var start = -Math.PI / 2;

        if (!total) {
            context.fillStyle = "#6f7482";
            context.font = "700 14px system-ui";
            context.textAlign = "center";
            context.fillText("No data", width / 2, height / 2);
            return;
        }

        values.forEach(function (value, index) {
            var angle = (value / total) * Math.PI * 2;
            context.beginPath();
            context.moveTo(centerX, centerY);
            context.arc(centerX, centerY, radius, start, start + angle);
            context.closePath();
            context.fillStyle = palette[index % palette.length];
            context.fill();
            start += angle;
        });

        context.globalCompositeOperation = "destination-out";
        context.beginPath();
        context.arc(centerX, centerY, radius * 0.56, 0, Math.PI * 2);
        context.fill();
        context.globalCompositeOperation = "source-over";

        context.fillStyle = "#20212a";
        context.font = "900 20px system-ui";
        context.textAlign = "center";
        context.fillText(String(total), centerX, centerY + 7);

        context.textAlign = "left";
        context.font = "700 12px system-ui";
        labels.forEach(function (label, index) {
            var y = 24 + index * 22;
            context.fillStyle = palette[index % palette.length];
            context.fillRect(width * 0.68, y - 10, 10, 10);
            context.fillStyle = "#20212a";
            context.fillText(label + " " + values[index], width * 0.68 + 16, y);
        });
    }

    function initCharts() {
        var dataNode = document.getElementById("adminChartData");
        if (!dataNode) {
            return;
        }
        var payload = JSON.parse(dataNode.textContent || "{}");
        drawBarChart(
            document.getElementById("monthlyDonationsChart"),
            payload.monthlyDonations.labels || [],
            payload.monthlyDonations.values || [],
            "#c92a3f"
        );
        drawDonutChart(
            document.getElementById("bloodDistributionChart"),
            payload.bloodDistribution.labels || [],
            payload.bloodDistribution.values || []
        );
        drawBarChart(
            document.getElementById("requestStatusChart"),
            payload.requestStatus.labels || [],
            payload.requestStatus.values || [],
            "#0f766e"
        );
    }

    document.addEventListener("DOMContentLoaded", function () {
        closeFlashButtons();
        roleFieldToggle();
        confirmForms();
        passwordTools();
        initCharts();
    });

    window.addEventListener("resize", function () {
        clearTimeout(window.__lifeDropResizeTimer);
        window.__lifeDropResizeTimer = setTimeout(initCharts, 120);
    });
})();

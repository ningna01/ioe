(function () {
    function isObject(value) {
        return value !== null && typeof value === 'object' && !Array.isArray(value);
    }

    function mergeDeep(target, source) {
        var output = Object.assign({}, target);
        if (!isObject(source)) {
            return output;
        }
        Object.keys(source).forEach(function (key) {
            if (isObject(source[key]) && isObject(output[key])) {
                output[key] = mergeDeep(output[key], source[key]);
            } else {
                output[key] = source[key];
            }
        });
        return output;
    }

    var palette = {
        blue: '#3a78c0',
        green: '#2f9b74',
        red: '#d4635a',
        orange: '#e2943b',
        teal: '#2f9e97',
        gray: '#6f8092',
    };

    var baseOptions = {
        responsive: true,
        maintainAspectRatio: true,
        layout: { padding: { top: 12, right: 16, bottom: 12, left: 16 } },
        plugins: {
            legend: { position: 'top', align: 'end' },
            tooltip: {
                backgroundColor: 'rgba(22, 36, 54, 0.92)',
                titleColor: '#ffffff',
                bodyColor: '#e9f2fb',
                padding: 10,
            },
        },
        interaction: {
            mode: 'index',
            intersect: false,
        },
    };

    if (typeof Chart !== 'undefined') {
        Chart.defaults.color = '#35506a';
        Chart.defaults.font.family = "'Noto Sans SC', 'PingFang SC', 'Microsoft YaHei', sans-serif";
        Chart.defaults.plugins.legend.position = 'top';
        Chart.defaults.plugins.legend.align = 'end';
        Chart.defaults.responsive = true;
        Chart.defaults.maintainAspectRatio = true;
    }

    function createChart(canvasId, config) {
        var chartCanvas = document.getElementById(canvasId);
        if (!chartCanvas || typeof Chart === 'undefined') {
            return null;
        }
        var finalConfig = mergeDeep(config || {}, {
            options: mergeDeep(baseOptions, (config && config.options) || {}),
        });
        return new Chart(chartCanvas.getContext('2d'), finalConfig);
    }

    function getDatasetVisible(chart, datasetIndex) {
        if (!chart || !chart.data || !chart.data.datasets[datasetIndex]) {
            return false;
        }

        if (typeof chart.isDatasetVisible === 'function') {
            try {
                return chart.isDatasetVisible(datasetIndex);
            } catch (error) {
                // Fall through to metadata-based visibility check.
            }
        }

        if (typeof chart.getDatasetMeta === 'function') {
            var meta = chart.getDatasetMeta(datasetIndex);
            if (meta && meta.hidden !== null && typeof meta.hidden !== 'undefined') {
                return !meta.hidden;
            }
        }

        return !chart.data.datasets[datasetIndex].hidden;
    }

    function setDatasetVisible(chart, datasetIndex, visible) {
        if (!chart || !chart.data || !chart.data.datasets[datasetIndex]) {
            return;
        }

        if (typeof chart.setDatasetVisibility === 'function') {
            chart.setDatasetVisibility(datasetIndex, visible);
            return;
        }

        chart.data.datasets[datasetIndex].hidden = !visible;
        if (typeof chart.getDatasetMeta === 'function') {
            var meta = chart.getDatasetMeta(datasetIndex);
            if (meta) {
                meta.hidden = visible ? null : true;
            }
        }
    }

    function bindDatasetToggle(chart, controlsSelector) {
        if (!chart) {
            return;
        }
        var controls = document.querySelector(controlsSelector);
        if (!controls) {
            return;
        }
        var buttons = controls.querySelectorAll('[data-dataset-index]');
        if (!buttons.length) {
            return;
        }

        function syncButtonStates() {
            buttons.forEach(function (button) {
                var datasetIndex = parseInt(button.getAttribute('data-dataset-index'), 10);
                if (Number.isNaN(datasetIndex) || !chart.data.datasets[datasetIndex]) {
                    return;
                }
                var visible = getDatasetVisible(chart, datasetIndex);
                button.classList.toggle('active', visible);
                button.setAttribute('aria-pressed', visible ? 'true' : 'false');
            });
        }

        function countVisibleDatasets() {
            var visibleCount = 0;
            chart.data.datasets.forEach(function (_, datasetIndex) {
                if (getDatasetVisible(chart, datasetIndex)) {
                    visibleCount += 1;
                }
            });
            return visibleCount;
        }

        buttons.forEach(function (button) {
            button.addEventListener('click', function () {
                var datasetIndex = parseInt(button.getAttribute('data-dataset-index'), 10);
                if (Number.isNaN(datasetIndex) || !chart.data.datasets[datasetIndex]) {
                    return;
                }
                var visible = getDatasetVisible(chart, datasetIndex);
                // Keep at least one dataset visible to avoid an empty chart state.
                if (visible && countVisibleDatasets() <= 1) {
                    return;
                }
                setDatasetVisible(chart, datasetIndex, !visible);
                chart.update();
                syncButtonStates();
            });
        });

        syncButtonStates();
    }

    window.ReportCharts = {
        palette: palette,
        baseOptions: baseOptions,
        create: createChart,
        bindDatasetToggle: bindDatasetToggle,
    };
})();

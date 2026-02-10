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

    window.ReportCharts = {
        palette: palette,
        baseOptions: baseOptions,
        create: createChart,
    };
})();

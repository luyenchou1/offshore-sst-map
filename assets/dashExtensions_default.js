window.dashExtensions = Object.assign({}, window.dashExtensions, {
    default: {
        function0: function(feature, latlng) {
            return L.circleMarker(latlng, {
                radius: 5,
                stroke: false,
                fill: true,
                fillOpacity: 0.01
            });
        },
        function1: function(feature, layer) {
            layer.bindTooltip(feature.properties.temp_f + ' °F', {
                sticky: true
            });
        }
    }
});
window.dashExtensions = Object.assign({}, window.dashExtensions, {
    default: {
        function0: function(feature, latlng) {
            const p = feature.properties || {};
            if (p.heat) {
                return L.circleMarker(latlng, {
                    radius: 14, // bigger circles merge cleanly with less blur
                    stroke: false,
                    fillColor: p.color || "#ff4400",
                    fillOpacity: p.alpha != null ? p.alpha : 0.3, // density-scaled
                    interactive: false,
                });
            }
            const color = p.color || "#9aa0a6";
            const marker = L.circleMarker(latlng, {
                radius: 5,
                color: "#ffffff", // white halo -> pops on any background
                weight: 1.6,
                fillColor: color,
                fillOpacity: 0.95,
                opacity: 1,
            });
            const len = (p.length != null && p.length !== "") ? (" · " + p.length + "\"") : "";
            const temp = (p.temp != null && p.temp !== "") ? (" · " + p.temp + "°F") : "";
            const date = p.date ? (" · " + p.date) : "";
            marker.bindTooltip((p.species || "") + len + temp + date, {
                direction: "top"
            });
            return marker;
        }
    }
});
/**
 * Crush Cache — coach dashboard.
 *
 * Renders the Leaflet map with stations + live team markers, and follows
 * hunt events over the native WebSocket API (positions, leaderboard,
 * status) with a 30s polling fallback when the socket is down.
 */
document.addEventListener("DOMContentLoaded", function () {
    var root = document.getElementById("cache-coach-root");
    if (!root) return;

    var huntId = root.dataset.huntId;
    var stateUrl = root.dataset.stateUrl;
    var mapData;
    try {
        mapData = JSON.parse(root.dataset.mapData || "{}");
    } catch (e) {
        mapData = { stations: [], teams: [] };
    }

    var teamMarkers = {};
    var map = null;

    // --- Map ---
    if (document.getElementById("coach-map") && window.L) {
        var points = (mapData.stations || [])
            .filter(function (s) { return s.lat !== null; })
            .map(function (s) { return [s.lat, s.lng]; });

        map = L.map("coach-map");
        L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
            maxZoom: 19,
            attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
        }).addTo(map);

        if (points.length) {
            map.fitBounds(points, { padding: [30, 30], maxZoom: 16 });
        } else {
            map.setView([49.6116, 6.1319], 13); // Luxembourg City fallback
        }

        (mapData.stations || []).forEach(function (s) {
            if (s.lat === null) return;
            L.marker([s.lat, s.lng]).bindPopup(s.order + ". " + s.name).addTo(map);
            if (s.radius) {
                L.circle([s.lat, s.lng], {
                    radius: s.radius, color: "#8b5cf6", weight: 1, fillOpacity: 0.05,
                }).addTo(map);
            }
        });

        (mapData.teams || []).forEach(function (t) {
            if (t.lat === null || t.lat === undefined) return;
            upsertTeamMarker(t.id, t.name, t.color, t.lat, t.lng);
        });
    }

    function upsertTeamMarker(id, name, color, lat, lng) {
        if (!map) return;
        if (teamMarkers[id]) {
            teamMarkers[id].setLatLng([lat, lng]);
        } else {
            teamMarkers[id] = L.circleMarker([lat, lng], {
                radius: 9, color: "#fff", weight: 2, fillColor: color, fillOpacity: 1,
            }).bindTooltip(name, { permanent: false }).addTo(map);
        }
    }

    // --- Progress feed ---
    var feed = document.getElementById("progress-feed");
    function addFeedLine(text) {
        if (!feed) return;
        var line = document.createElement("div");
        line.textContent = new Date().toLocaleTimeString() + " — " + text;
        feed.prepend(line);
        while (feed.children.length > 12) feed.removeChild(feed.lastChild);
    }

    // --- WebSocket ---
    var ws = null;
    var wsRetry = 0;
    var wsHealthy = false;

    function connect() {
        var protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
        ws = new WebSocket(protocol + "//" + window.location.host + "/ws/cache/" + huntId + "/");

        ws.onopen = function () { wsHealthy = true; wsRetry = 0; };
        ws.onmessage = function (event) {
            var msg;
            try { msg = JSON.parse(event.data); } catch (e) { return; }
            var data = msg.data || {};
            if (msg.type === "position" && data.team_id) {
                upsertTeamMarker(data.team_id, data.team_name, data.team_color, data.lat, data.lng);
            } else if (msg.type === "progress") {
                addFeedLine(
                    data.team_name + (data.is_finished
                        ? " finished the hunt! 🏁"
                        : " completed station " + data.station_order + " (" + data.points + " pts)")
                );
            } else if (msg.type === "leaderboard" || msg.type === "status") {
                // Leaderboard/status are server-rendered — refresh to stay simple
                // and correct. Positions keep flowing over the socket meanwhile.
                if (msg.type === "status") window.location.reload();
                else refreshState();
            }
        };
        ws.onclose = function () {
            wsHealthy = false;
            if (wsRetry < 8) {
                wsRetry += 1;
                setTimeout(connect, 2000 * wsRetry);
            }
        };
    }
    connect();

    // --- Polling fallback + leaderboard refresh ---
    function refreshState() {
        fetch(stateUrl, { headers: { "Accept": "application/json" } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (data) {
                if (!data || !data.ok) return;
                (data.positions || []).forEach(function (p) {
                    if (p.lat === null) return;
                    upsertTeamMarker(p.team_id, p.team_name, p.team_color, p.lat, p.lng);
                });
                renderLeaderboard(data.leaderboard || []);
            })
            .catch(function () {});
    }

    function renderLeaderboard(entries) {
        var container = document.querySelector("#cache-leaderboard .space-y-2");
        if (!container) return;
        container.textContent = "";
        entries.forEach(function (e) {
            var row = document.createElement("div");
            row.className = "flex items-center justify-between rounded-lg border border-gray-200 dark:border-gray-700 px-4 py-2";

            var left = document.createElement("div");
            left.className = "flex items-center gap-3";

            var rank = document.createElement("span");
            rank.className = "w-6 text-center font-bold " + (e.rank === 1 ? "text-amber-500" : "text-gray-400");
            rank.textContent = e.rank === 1 ? "🏆" : e.rank;

            var dot = document.createElement("span");
            dot.className = "inline-block h-3 w-3 rounded-full";
            dot.style.background = e.team_color;

            var name = document.createElement("span");
            name.className = "font-medium";
            name.textContent = e.team_name;

            left.appendChild(rank);
            left.appendChild(dot);
            left.appendChild(name);

            if (e.is_finished) {
                var flag = document.createElement("span");
                flag.className = "text-xs text-green-600 dark:text-green-400";
                flag.textContent = "🏁";
                left.appendChild(flag);
            } else if (e.station_order) {
                var st = document.createElement("span");
                st.className = "text-xs text-gray-400";
                st.textContent = "station " + e.station_order;
                left.appendChild(st);
            }

            var pts = document.createElement("span");
            pts.className = "font-bold";
            pts.textContent = e.points;

            row.appendChild(left);
            row.appendChild(pts);
            container.appendChild(row);
        });
    }

    setInterval(function () {
        if (!wsHealthy) refreshState();
    }, 30000);
});

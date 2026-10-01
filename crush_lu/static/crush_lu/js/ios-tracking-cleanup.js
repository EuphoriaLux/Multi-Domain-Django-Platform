// iOS app shell only (served under `suppress_ios_tracking`): the app no longer
// loads GA4, the Meta Pixel or Application Insights, and hides the consent
// controls. Members who accepted those cookies in an earlier version still
// have the identifiers in the WebView's cookie jar, so expire them here.
// Mirrors clearAnalyticsCookies() in core/templates/includes/cookie_banner.html,
// plus the Meta identifiers. Expiring on every parent domain matters: gtag
// writes _ga* on the widest domain the browser accepts (Domain=crush.lu).
(function () {
    "use strict";
    var expired =
        "=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/; SameSite=Lax";
    var names = ["ai_user", "ai_session", "_fbp", "_fbc"];
    document.cookie.split(";").forEach(function (pair) {
        var name = pair.split("=")[0].trim();
        if (
            /^(_g(a|id|at)(_|$)|_fb[pc]$)/.test(name) &&
            names.indexOf(name) === -1
        ) {
            names.push(name);
        }
    });
    var labels = location.hostname.split(".");
    var scopes = [""];
    for (var i = 0; i < labels.length - 1; i++) {
        scopes.push("; domain=" + labels.slice(i).join("."));
    }
    names.forEach(function (name) {
        scopes.forEach(function (scope) {
            document.cookie = name + expired + scope;
        });
    });
})();

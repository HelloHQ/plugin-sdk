// Verbatim copy of the HQBridge shim the HelloHQ app injects into every plugin
// WebView: `pluginWebviewInitScript` in hellohq
// lib/app/utils/service/plugin_webview_init_script.dart (origin/main 3b085127e).
// The SDK tests run HQHost against it so the wire contract cannot drift
// silently. Update it from the app when the shim changes; do not edit by hand.
(function () {
  "use strict";
  var pending = new Map();
  var nextId = 1;

  // Settle an id-correlated Promise from the host's response object.
  // response = { id, ok, data } | { id, ok:false, error:{ code, message } }.
  window.__hqResolve = function (response) {
    if (!response || typeof response.id === "undefined") return;
    var p = pending.get(response.id);
    if (!p) return;
    pending.delete(response.id);
    if (response.ok) {
      p.resolve(response.data);
    } else {
      var err = response.error || {};
      var e = new Error(err.message || "plugin bridge error");
      e.code = err.code || "internal";
      p.reject(e);
    }
  };

  function call(action, payload) {
    return new Promise(function (resolve, reject) {
      var id = nextId++;
      pending.set(id, { resolve: resolve, reject: reject });
      window.ipc.postMessage(
        JSON.stringify({ id: id, action: action, payload: payload || {} })
      );
    });
  }

  window.HQBridge = {
    // read(resource, args) -> { action:"read", payload:{ resource, args } }
    read: function (resource, args) {
      return call("read", { resource: resource, args: args || {} });
    },
    // compute(fn, args) -> { action:"compute", payload:{ function, args } }
    compute: function (fn, args) {
      return call("compute", { function: fn, args: args || {} });
    },
    // writeExternal(name, base64) ->
    //   { action:"write_external", payload:{ suggested_filename, content_base64 } }
    writeExternal: function (suggestedFilename, contentBase64) {
      return call("write_external", {
        suggested_filename: suggestedFilename,
        content_base64: contentBase64,
      });
    },
    // log(level, message) — fire-and-forget, no id, no response.
    log: function (level, message) {
      window.ipc.postMessage(
        JSON.stringify({
          action: "log",
          payload: { level: level, message: message },
        })
      );
    },
  };
})();

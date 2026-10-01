/**
 * AI Gateway admin console (Phase 6).
 *
 * Renders the provider / model / quota / health panels from the admin JSON API
 * and wires edits back. A rejected request reverts the control and surfaces a
 * toast, so the page can never show a toggle as "on" when the server refused
 * it. All rendering is done with textContent-based DOM building: provider and
 * model names are data, never markup.
 */
(function () {
    "use strict";

    var BASE = "/api/ai-gateway/admin";
    var toast = window.StudyPlannerToast || {
        success: function () {}, error: function () {}, info: function () {}
    };

    var state = { providers: [], models: [], quotas: [], health: [] };

    // ------------------------------------------------------------- utilities

    function el(tag, props, children) {
        var node = document.createElement(tag);
        if (props) {
            Object.keys(props).forEach(function (key) {
                if (key === "class") { node.className = props[key]; }
                else if (key === "text") { node.textContent = props[key]; }
                else if (key === "dataset") {
                    Object.keys(props.dataset).forEach(function (d) { node.dataset[d] = props.dataset[d]; });
                } else { node.setAttribute(key, props[key]); }
            });
        }
        (children || []).forEach(function (child) { if (child) { node.appendChild(child); } });
        return node;
    }

    function api(path, options) {
        var opts = options || {};
        opts.headers = Object.assign({ "Accept": "application/json" }, opts.headers || {});
        if (opts.body) {
            opts.headers["Content-Type"] = "application/json";
            opts.body = JSON.stringify(opts.body);
        }
        return fetch(BASE + path, opts).then(function (res) {
            return res.json().catch(function () { return {}; }).then(function (data) {
                if (!res.ok) {
                    var err = new Error(data.error || ("HTTP " + res.status));
                    err.status = res.status;
                    throw err;
                }
                return data;
            });
        });
    }

    function failure(prefix) {
        return function (err) {
            if (err.status === 403) {
                setStatus("You do not have access to the gateway admin console.");
            } else if (err.status === 401) {
                setStatus("Your session expired. Reload and sign in again.");
            }
            toast.error(prefix + ": " + err.message);
            throw err;
        };
    }

    function setStatus(message) {
        var host = document.querySelector("[data-admin-status]");
        if (host) { host.textContent = message || ""; }
    }

    function clear(host) { while (host.firstChild) { host.removeChild(host.firstChild); } }

    function toggle(label, checked, onCommit) {
        var input = el("input", { type: "checkbox" });
        input.checked = !!checked;
        input.addEventListener("change", function () {
            var desired = input.checked;
            input.disabled = true;
            Promise.resolve(onCommit(desired)).catch(function () {
                input.checked = !desired;
            }).then(function () { input.disabled = false; });
        });
        return el("label", { class: "admin-toggle" }, [input, el("span", { text: label })]);
    }

    function badge(text, modifier) {
        return el("span", { class: "admin-badge" + (modifier ? " admin-badge--" + modifier : ""), text: text });
    }

    function providerPath(slug) { return "/providers/" + encodeURIComponent(slug); }
    function modelPath(provider, modelId) {
        return "/models/" + encodeURIComponent(provider) + "/" +
            modelId.split("/").map(encodeURIComponent).join("/");
    }

    // ------------------------------------------------------------- providers

    function renderProviders() {
        var host = document.querySelector("[data-admin-providers]");
        if (!host) { return; }
        clear(host);
        state.providers.forEach(function (provider) {
            var head = el("div", { class: "admin-card__head" }, [
                el("h3", { class: "admin-card__title", text: provider.name + " (" + provider.slug + ")" }),
                el("span", {
                    class: "admin-card__meta",
                    text: provider.modelCount + " model" + (provider.modelCount === 1 ? "" : "s") +
                        " · " + (provider.hasServerKey ? "server key set" : "no server key") +
                        " · " + provider.adapter
                })
            ]);

            function commit(patch) {
                return api(providerPath(provider.slug), { method: "PATCH", body: patch })
                    .then(function (data) {
                        if (data.provider) {
                            state.providers = state.providers.map(function (p) {
                                return p.slug === data.provider.slug ? data.provider : p;
                            });
                            renderProviders();
                            renderHealth();
                        }
                        toast.success("Saved " + provider.name + ".");
                    })
                    .catch(failure("Could not save " + provider.name));
            }

            var toggles = el("div", { class: "admin-toggles" }, [
                toggle("Enabled", provider.isEnabled, function (v) { return commit({ isEnabled: v }); }),
                toggle("May train on data", provider.mayTrainOnData, function (v) { return commit({ mayTrainOnData: v }); }),
                toggle("Allowed for minors", provider.allowedForMinors, function (v) { return commit({ allowedForMinors: v }); })
            ]);

            var warnings = el("ul", { class: "admin-warnings" }, (provider.warnings || []).map(function (w) {
                return el("li", { class: "admin-warning", text: w });
            }));

            host.appendChild(el("div", { class: "admin-card", dataset: { provider: provider.slug } }, [head, toggles, warnings]));
        });
        if (!state.providers.length) {
            host.appendChild(el("p", { class: "admin-placeholder", text: "No providers registered." }));
        }
    }

    // ---------------------------------------------------------------- models

    function renderModels() {
        var host = document.querySelector("[data-admin-models]");
        if (!host) { return; }
        clear(host);
        var rows = state.models.map(function (model) {
            var input = el("input", { type: "checkbox" });
            input.checked = !!model.isEnabled;
            input.addEventListener("change", function () {
                var desired = input.checked;
                input.disabled = true;
                api(modelPath(model.provider, model.modelId), { method: "PATCH", body: { isEnabled: desired } })
                    .then(function (data) {
                        if (data.model) {
                            state.models = state.models.map(function (m) {
                                return m.key === data.model.key ? data.model : m;
                            });
                        }
                        toast.success("Saved " + model.name + ".");
                    })
                    .catch(function (err) { input.checked = !desired; failure("Could not save " + model.name)(err); })
                    .then(function () { input.disabled = false; });
            });
            return el("tr", null, [
                el("td", { text: model.name }),
                el("td", { text: model.key }),
                el("td", { text: model.tier }),
                el("td", { text: model.bestFor || "" }),
                el("td", null, [input])
            ]);
        });
        var table = el("table", { class: "admin-table" }, [
            el("thead", null, [el("tr", null, [
                el("th", { text: "Name" }), el("th", { text: "Key" }),
                el("th", { text: "Tier" }), el("th", { text: "Best for" }),
                el("th", { text: "Enabled" })
            ])]),
            el("tbody", null, rows)
        ]);
        host.appendChild(table);
    }

    // ---------------------------------------------------------------- quotas

    function renderQuotas() {
        var host = document.querySelector("[data-admin-quotas]");
        if (!host) { return; }
        clear(host);
        var rows = state.quotas.map(function (rule) {
            var reqs = numberInput(rule.daily_requests);
            var docs = numberInput(rule.daily_documents);
            var toks = numberInput(rule.daily_tokens);
            var save = el("button", { class: "button button--small", type: "button", text: "Save" });
            save.addEventListener("click", function () {
                save.disabled = true;
                var body = {
                    dailyRequests: reqs.value === "" ? null : Number(reqs.value),
                    dailyDocuments: docs.value === "" ? null : Number(docs.value),
                    dailyTokens: toks.value === "" ? null : Number(toks.value)
                };
                api("/quotas/" + encodeURIComponent(rule.tier) + "/" + encodeURIComponent(rule.user_group), {
                    method: "PUT", body: body
                }).then(function (data) {
                    if (data.rule) {
                        state.quotas = state.quotas.map(function (r) {
                            return r.tier === data.rule.tier && r.user_group === data.rule.user_group ? data.rule : r;
                        });
                        renderQuotas();
                    }
                    toast.success("Saved " + rule.tier + " quota.");
                }).catch(failure("Could not save quota")).then(function () { save.disabled = false; });
            });
            return el("tr", null, [
                el("td", { text: rule.tier }),
                el("td", { text: rule.user_group }),
                el("td", null, [reqs]),
                el("td", null, [docs]),
                el("td", null, [toks]),
                el("td", null, [save])
            ]);
        });
        var table = el("table", { class: "admin-table" }, [
            el("thead", null, [el("tr", null, [
                el("th", { text: "Tier" }), el("th", { text: "Group" }),
                el("th", { text: "Requests/day" }), el("th", { text: "Documents/day" }),
                el("th", { text: "Tokens/day" }), el("th", { text: "" })
            ])]),
            el("tbody", null, rows)
        ]);
        host.appendChild(table);
    }

    function numberInput(value) {
        var input = el("input", { type: "number", min: "0", step: "1" });
        input.value = value === null || value === undefined ? "" : String(value);
        return input;
    }

    // ---------------------------------------------------------------- health

    function renderHealth() {
        var host = document.querySelector("[data-admin-health]");
        if (!host) { return; }
        clear(host);
        var rows = state.health.map(function (row) {
            var reset = el("button", { class: "button button--ghost button--small", type: "button", text: "Reset" });
            reset.disabled = row.state === "closed";
            reset.addEventListener("click", function () {
                reset.disabled = true;
                api("/health/" + encodeURIComponent(row.provider) + "/reset", { method: "POST" })
                    .then(function (data) {
                        if (data.provider) {
                            state.health = state.health.map(function (r) {
                                return r.provider === data.provider.provider ? data.provider : r;
                            });
                            renderHealth();
                        }
                        toast.success("Reset " + row.name + ".");
                    }).catch(failure("Could not reset " + row.name)).then(function () { reset.disabled = false; });
            });
            return el("tr", null, [
                el("td", { text: row.name }),
                el("td", null, [badge(row.state, row.state)]),
                el("td", { text: String(row.failures) }),
                el("td", { text: row.hasServerKey ? "yes" : "no" }),
                el("td", null, [reset])
            ]);
        });
        var table = el("table", { class: "admin-table" }, [
            el("thead", null, [el("tr", null, [
                el("th", { text: "Provider" }), el("th", { text: "State" }),
                el("th", { text: "Failures" }), el("th", { text: "Server key" }),
                el("th", { text: "" })
            ])]),
            el("tbody", null, rows)
        ]);
        host.appendChild(table);
    }

    // ------------------------------------------------------------------ load

    function load() {
        Promise.all([
            api("/providers").then(function (d) { state.providers = d.providers || []; renderProviders(); }),
            api("/models").then(function (d) { state.models = d.models || []; renderModels(); }),
            api("/quotas").then(function (d) { state.quotas = d.rules || []; renderQuotas(); }),
            api("/health").then(function (d) { state.health = d.health || []; renderHealth(); })
        ]).catch(failure("Could not load the admin console"));
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", load);
    } else {
        load();
    }
})();

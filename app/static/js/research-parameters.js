(function (root, factory) {
    const api = factory();
    if (typeof module === "object" && module.exports) module.exports = api;
    root.ResearchParameters = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
    "use strict";

    function typedValue(definition, value) {
        if (definition.kind === "integer") {
            const result = Number(value);
            if (!Number.isInteger(result)) throw new TypeError(`${definition.name} must be an integer`);
            return result;
        }
        if (definition.kind === "float") {
            const result = Number(value);
            if (!Number.isFinite(result)) throw new TypeError(`${definition.name} must be finite`);
            return result;
        }
        if (definition.kind === "boolean") {
            if (typeof value === "boolean") return value;
            if (value === "true" || value === "false") return value === "true";
            throw new TypeError(`${definition.name} must be true or false`);
        }
        if (definition.kind === "choice") {
            const found = (definition.choices || []).find(choice => String(choice) === String(value));
            if (found === undefined) throw new TypeError(`${definition.name} is not a permitted choice`);
            return found;
        }
        if (definition.kind === "string") return String(value);
        throw new TypeError(`Unsupported strategy parameter kind: ${definition.kind}`);
    }

    function makePayload(schema, rawValues, searching) {
        const parameters = {}, parameter_space = [];
        for (const definition of schema) {
            const raw = rawValues[definition.name] || {};
            const value = typedValue(definition, raw.value ?? definition.default);
            parameters[definition.name] = value;
            if (!searching) continue;
            if (raw.optimize) {
                if (definition.kind === "choice") {
                    const choices = (raw.choices || []).map(choice => typedValue(definition, choice));
                    if (!choices.length) throw new TypeError(`${definition.name} needs at least one search choice`);
                    parameter_space.push({ name: definition.name, kind: "choice", choices });
                } else if (definition.kind === "integer" || definition.kind === "float") {
                    const minimum = typedValue(definition, raw.minimum);
                    const maximum = typedValue(definition, raw.maximum);
                    const step = typedValue(definition, raw.step);
                    if (maximum < minimum || step <= 0) throw new RangeError(`${definition.name} has an invalid search range`);
                    parameter_space.push({ name: definition.name, kind: definition.kind,
                        minimum, maximum, step });
                } else {
                    throw new TypeError(`${definition.name} cannot be searched as a range`);
                }
            } else {
                parameter_space.push({ name: definition.name, kind: "fixed", value });
            }
        }
        return { parameters, parameter_space };
    }

    return { typedValue, makePayload };
});

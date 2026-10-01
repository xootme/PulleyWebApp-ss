"""
agent_api.py — the v1 HTTP contract every CCT app serves to agents (and CAD
plugins): describe / check / files / quote under /api/v1/. The MCP gateway
(`mcp_gateway`) sits on top of it. Design: docs/AGENT_API.md.

    from cct_common.agent_api import Param, Part, Description, register_agent_api

    DESCRIBE = Description(app="pulleys", title="Timing Pulley Generator", ...,
                           params=[Param("teeth", "integer", "Number of teeth", min=10, ...)],
                           parts=[Part("pulley1", "Pulley 1", ("step", "stl", "svg", "dxf"))])
    register_agent_api(app, DESCRIBE, check=my_check, files=my_files)

`check(query) -> dict` and `files(query, parts, formats) -> [(part, fmt,
path, params)]` receive the **coerced query** — the app's own query keys
as strings, exactly as its routes read `request.args` — so an app reuses its
parsers unchanged. Flask is imported lazily; the coercion is plain Python.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from urllib.parse import urlencode

API_VERSION = 1
TYPES = ("number", "integer", "string", "enum", "boolean")


@dataclass(frozen=True)
class Param:
    name: str
    type: str                               # one of TYPES
    help: str
    unit: str = ""
    default: object = None
    min: float | None = None
    max: float | None = None
    choices: tuple = ()                      # enum values
    choices_by: dict | None = None           # {"other_param": {value: [choices]}}
    group: str = ""
    when: dict | None = None                 # only matters while these params have these values
    per_part: bool = False                   # repeats under Description.prefixes
    wire: tuple = ("1", "0")                 # boolean's (true, false) strings on the wire

    def __post_init__(self):
        if self.type not in TYPES:
            raise ValueError(f"{self.name}: type must be one of {TYPES}")

    def to_json(self) -> dict:
        out = {"name": self.name, "type": self.type, "help": self.help}
        for key in ("unit", "group"):
            if getattr(self, key):
                out[key] = getattr(self, key)
        for key in ("default", "min", "max", "when", "choices_by"):
            if getattr(self, key) is not None:
                out[key] = getattr(self, key)
        if self.choices:
            out["choices"] = list(self.choices)
        if self.per_part:
            out["per_part"] = True
        return out


@dataclass(frozen=True)
class Part:
    id: str
    label: str
    formats: tuple
    when: str = ""                           # in words: when the design has this part


@dataclass
class Description:
    app: str
    title: str
    summary: str
    params: list
    parts: list
    units: str = "mm"
    prefixes: dict = field(default_factory=dict)     # {"p2_": "pulley 2 (with dual=true)"}
    notes: list = field(default_factory=list)
    formats: dict = field(default_factory=lambda: {
        "step": "CAD solid (STEP AP214)", "stl": "3D-print mesh (STL, print compensation applied)",
        "svg": "2D drawing (SVG)", "dxf": "2D drawing (DXF, true lines and arcs)"})
    version: str = ""

    def to_json(self) -> dict:
        return {"app": self.app, "title": self.title, "summary": self.summary,
                "api_version": API_VERSION, "version": self.version, "units": self.units,
                "params": [p.to_json() for p in self.params],
                "parts": [{"id": p.id, "label": p.label, "formats": list(p.formats),
                           **({"when": p.when} if p.when else {})} for p in self.parts],
                "formats": self.formats, "prefixes": self.prefixes, "notes": self.notes}

    def param(self, name: str):
        """(Param, prefix) for a query key, or (None, "")."""
        by_name = {p.name: p for p in self.params}
        if name in by_name:
            return by_name[name], ""
        for prefix in self.prefixes:
            if name.startswith(prefix) and name[len(prefix):] in by_name:
                p = by_name[name[len(prefix):]]
                if p.per_part:
                    return p, prefix
        return None, ""


class AgentError(ValueError):
    """A bad request, with every problem listed."""

    def __init__(self, message: str, details: list | None = None, code: str = "BAD_PARAMS"):
        super().__init__(message)
        self.details = details or []
        self.code = code


def _num(value, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a number, not {value!r}")
    try:
        x = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a number, not {value!r}") from None
    if not math.isfinite(x):
        raise ValueError(f"{name} must be a finite number")
    return x


def _wire_number(x: float) -> str:
    return str(int(x)) if x == int(x) and abs(x) < 1e15 else repr(x)


def coerce(desc: Description, params: dict) -> dict:
    """An agent's JSON parameters as the app's query strings. Every problem
    is collected; unknown names are refused (never silently ignored)."""
    if not isinstance(params, dict):
        raise AgentError("params must be an object of parameter names to values")
    out, problems = {}, []
    for name, value in params.items():
        p, prefix = desc.param(str(name))
        if p is None:
            problems.append(f"unknown parameter {name!r}")
            continue
        if value is None:
            continue
        try:
            if p.type == "boolean":
                if isinstance(value, str):
                    v = value.strip().lower()
                    if v in ("1", "true", "yes", "on"):
                        value = True
                    elif v in ("0", "false", "no", "off", ""):
                        value = False
                if not isinstance(value, bool):
                    raise ValueError(f"{name} must be true or false, not {value!r}")
                out[name] = p.wire[0] if value else p.wire[1]
                continue
            if p.type in ("number", "integer"):
                x = _num(value, name)
                if p.type == "integer":
                    if x != int(x):
                        raise ValueError(f"{name} must be a whole number, not {value!r}")
                    x = int(x)
                if p.min is not None and x < p.min:
                    raise ValueError(f"{name} must be at least {p.min:g}{' ' + p.unit if p.unit else ''}")
                if p.max is not None and x > p.max:
                    raise ValueError(f"{name} must be at most {p.max:g}{' ' + p.unit if p.unit else ''}")
                out[name] = _wire_number(x)
                continue
            s = str(value)
            if p.type == "enum":
                choices = list(p.choices)
                if p.choices_by:
                    (other, table), = p.choices_by.items()
                    key = params.get(prefix + other, next((q.default for q in desc.params if q.name == other), None))
                    choices = list(table.get(str(key), []))
                if s not in [str(c) for c in choices]:
                    raise ValueError(f"{name} must be one of {', '.join(str(c) for c in choices)}; not {s!r}")
            out[name] = s
        except ValueError as e:
            problems.append(str(e))
    if problems:
        raise AgentError("; ".join(problems), problems)
    return out


def file_url(path: str, params: dict, design_id: str | None = None) -> str:
    q = dict(params)
    if design_id:
        q["design_id"] = design_id
    return f"{path}?{urlencode(q)}"


def register_agent_api(app, desc: Description, *, check, files, quote=None, design_id=None,
                       prefix: str = "/api/v1") -> None:
    """The four v1 routes on a Flask app.

    check(query) -> dict                       the design as the app reads it
    files(query, parts, formats) -> [(part, fmt, path, params, name)]
    quote(query, formats) -> dict | None       None: the app doesn't charge
    design_id(query, files) -> str | None      registers the design — every file's
                                               parameters — for the caller (tokens on,
                                               signed in), so one payment covers them all
    """
    from flask import jsonify, request

    def _error(e: Exception, status: int = 400):
        code = getattr(e, "code", "BAD_PARAMS")
        return jsonify({"error": str(e), "code": code,
                        "details": getattr(e, "details", [])}), status

    def _body():
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            raise AgentError("send a JSON object: {\"params\": {...}}")
        return body

    def _list(body, key, allowed):
        v = body.get(key)
        if v is None:
            return list(allowed)
        if not isinstance(v, list) or any(x not in allowed for x in v):
            raise AgentError(f"{key} must be a list drawn from {', '.join(allowed)}")
        return v

    def describe_route():
        return jsonify(desc.to_json())

    def check_route():
        try:
            query = coerce(desc, _body().get("params", {}))
            return jsonify({"params": query, **check(query)})
        except (AgentError, ValueError) as e:
            return _error(e)

    all_formats = sorted({f for p in desc.parts for f in p.formats})
    all_parts = [p.id for p in desc.parts]

    def files_route():
        try:
            body = _body()
            query = coerce(desc, body.get("params", {}))
            parts = _list(body, "parts", all_parts)
            formats = _list(body, "formats", all_formats)
            listed = list(files(query, parts, formats))
            did = design_id(query, listed) if design_id else None
            out = [{"part": part, "format": fmt, "name": name, "url": file_url(path, params, did)}
                   for part, fmt, path, params, name in listed]
            if not out:
                raise AgentError("no files: this design has none of those parts in those formats",
                                 code="NO_FILES")
            return jsonify({"params": query, "design_id": did, "files": out})
        except (AgentError, ValueError) as e:
            return _error(e)

    def quote_route():
        try:
            body = _body()
            query = coerce(desc, body.get("params", {}))
            formats = _list(body, "formats", all_formats)
            q = quote(query, formats) if quote else None
            return jsonify(q if q is not None else {"tokens": False, "cost": 0})
        except (AgentError, ValueError) as e:
            return _error(e)

    app.add_url_rule(f"{prefix}/describe", "agent_describe", describe_route, methods=["GET"])
    app.add_url_rule(f"{prefix}/check", "agent_check", check_route, methods=["POST"])
    app.add_url_rule(f"{prefix}/files", "agent_files", files_route, methods=["POST"])
    app.add_url_rule(f"{prefix}/quote", "agent_quote", quote_route, methods=["POST"])
